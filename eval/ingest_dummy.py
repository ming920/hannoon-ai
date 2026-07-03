#!/usr/bin/env python3
"""더미 기사를 articles + article_ai_results 테이블에 주입하는 스크립트.

이벤트 분류기는 article_ai_results.status='done' 인 기사를 처리 대상으로 읽는다.
이 스크립트는 더미 기사를 articles에 삽입하고 article_ai_results를 status='done'으로
미리 생성해 분류기가 바로 처리할 수 있도록 준비한다.

멱등성: guid 기준으로 articles 중복을 확인하여 재실행 시 기사를 중복 삽입하지 않는다.
         article_ai_results는 save_article_analysis_result 의 ON CONFLICT DO UPDATE로 멱등 처리된다.

사용 예:
    python eval/ingest_dummy.py --database-url "postgresql://..."
    python eval/ingest_dummy.py --database-url "postgresql://..." --articles eval/data/dummy_articles.json
"""

import argparse
import json
import os
import sys

# src/ 경로를 Python 모듈 검색 경로에 추가한다 (수집기·저장소 모듈 임포트용).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from collector.storage import ensure_db, now_iso, save_article_analysis_result

_DEFAULT_ARTICLES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "dummy_articles.json"
)


def _ingest_one(conn, art: dict) -> str:
    """단일 더미 기사를 articles + article_ai_results에 삽입한다.

    guid 기준으로 중복을 확인하고, 이미 존재하는 기사는 'skipped'를 반환한다.
    삽입 성공 시 'inserted'를 반환한다.

    articles 와 article_ai_results 삽입을 하나의 트랜잭션으로 묶어 원자성을 보장한다.
    """
    # guid 중복 검사 — 이미 있으면 스킵한다.
    existing = conn.query_one(
        "SELECT id FROM articles WHERE guid = ?",
        (art["guid"],),
    )
    if existing is not None:
        return "skipped"

    now = now_iso()
    # summary가 비어 있으면 title로 대체한다.
    # normalize_summary 검증(빈 문자열 거부)을 통과하기 위한 방어 처리.
    summary = (art.get("summary") or art.get("title", "")).strip() or "더미 기사"

    with conn.transaction():
        # --- articles INSERT ---
        # PostgresCursor.execute 는 'INSERT INTO articles' 로 시작하는 SQL 에
        # 자동으로 'RETURNING id' 를 붙이고 lastrowid 를 채운다.
        # SQLite 경로에서는 sqlite3.Cursor.lastrowid 가 동일하게 작동한다.
        #
        # 컬럼 출처 (마이그레이션 근거):
        #   - article_status enum ('needs_crawl'|'ready'|'crawl_failed'):
        #       20260503061604_create_articles_table.sql
        #   - content_source enum ('rss'|'crawl'):
        #       20260503061604_create_articles_table.sql
        #   - guid TEXT nullable:
        #       20260525160000_alter_articles_guid_to_text.sql
        #   - embedding, core_content: 분류기가 계산하므로 NULL 로 두고 삽입
        cur = conn.execute(
            """
            INSERT INTO articles (
                feed_url, guid, link, category, title, summary, content,
                content_source, publisher, published_at, bias_type, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?::category, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "eval://dummy",                          # feed_url — 더미 데이터 마커
                art["guid"],                             # guid text
                f"https://eval.dummy/{art['guid']}",     # link (NOT NULL)
                art["category"],                         # category enum: 정치/경제/사회/국제
                art["title"],
                summary,                                 # summary NOT NULL
                art.get("content"),                      # content nullable
                "rss",                                   # content_source enum
                art["publisher"],
                art["published_at"],                     # ISO8601 문자열 → Postgres timestamp 자동 변환
                art["bias_type"],                        # bias_type enum: 진보/중도/보수
                "ready",                                 # article_status enum — 콘텐츠 준비 완료 상태
                now,
                now,
            ),
        )
        article_id = cur.lastrowid

        # --- article_ai_results INSERT (ON CONFLICT DO UPDATE) ---
        # 이벤트 분류기는 article_ai_results.status='done' 행을 읽는다.
        # save_article_analysis_result 는 storage.py 의 공유 헬퍼를 재사용한다
        # (ON CONFLICT(article_id) DO UPDATE 로 멱등 처리됨).
        #
        # article_ai_status enum ('pending'|'done'|'failed'|'event_assigned'):
        #   20260509100000_create_article_ai_results_table.sql +
        #   20260604120000_fix_get_events_topic_id_filter_and_add_event_assigned.sql
        save_article_analysis_result(
            conn,
            article_id=article_id,
            summary=summary,
            keywords=[],
            status="done",
        )

    return "inserted"


def main() -> None:
    parser = argparse.ArgumentParser(description="더미 기사를 DB에 주입합니다.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres 연결 URL (미지정 시 DATABASE_URL 환경변수 사용)",
    )
    parser.add_argument(
        "--articles",
        default=_DEFAULT_ARTICLES,
        help="더미 기사 JSON 파일 경로 (기본값: eval/data/dummy_articles.json)",
    )
    args = parser.parse_args()

    if not args.database_url:
        print(
            "오류: --database-url 또는 DATABASE_URL 환경변수를 지정하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = ensure_db("eval_ingest.db", database_url=args.database_url)

    with open(args.articles, encoding="utf-8") as f:
        articles = json.load(f)

    inserted = 0
    skipped = 0
    errors = 0
    for art in articles:
        try:
            result = _ingest_one(conn, art)
            if result == "inserted":
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            print(f"  [오류] guid={art.get('guid')}: {exc}", file=sys.stderr)
            errors += 1

    conn.close()
    # em-dash(U+2014)는 Windows cp949 콘솔에서 UnicodeEncodeError를 일으켜
    # 주입 성공 후 마지막 출력에서 전체 단계가 실패 처리되므로 사용하지 않는다.
    print(
        f"주입 완료 - 삽입: {inserted}건, 건너뜀(중복): {skipped}건, "
        f"오류: {errors}건  (전체: {len(articles)}건)"
    )


if __name__ == "__main__":
    main()

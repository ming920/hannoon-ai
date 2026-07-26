#!/usr/bin/env python3
"""분류기 출력 테이블을 초기화하여 재실행 환경을 만드는 스크립트.

다음을 초기화한다:
  - event_articles      전체 삭제
  - events              전체 삭제 (자기참조 FK prev_event_id/next_event_id 선 NULL 처리)
  - topic_causes        전체 삭제
  - topics              전체 삭제
  - article_ai_results  전체 삭제
  - articles            guid LIKE 'dummy-%' 만 삭제 (eval 더미 기사만 제거, 실 데이터 보호)

2026-07-12 수정: 이전 버전은 article_ai_results.status를 'done'으로 되돌리고
articles.embedding/core_content만 NULL 처리할 뿐 실제로 기사를 삭제하지 않았다.
그 결과 run_iteration.py를 반복 실행할 때마다 articles 테이블에 더미 기사가 계속
누적되어(예: dev 180건 + holdout 101건이 뒤섞임) 이후 실행의 코퍼스가 오염되는
하니스 버그가 있었다. 이제 dummy- 접두 guid를 가진 기사와 그 article_ai_results를
완전히 삭제해 매 실행이 깨끗한 코퍼스에서 시작하도록 한다.

안전 가드 (3중):
  1. EVAL_ALLOW_DESTRUCTIVE_RESET=1 환경변수가 없으면 실행 거부.
  2. DATABASE_URL 호스트가 localhost/127.0.0.1 이 아니면 거부.
     --allow-remote 플래그로만 우회 가능 (원격 eval DB 사용 시).
  3. --yes 플래그가 없으면 실행 거부.

  run_iteration.py 는 자동으로 EVAL_ALLOW_DESTRUCTIVE_RESET=1 을 env 에 주입해서 호출한다.

사용 예:
    EVAL_ALLOW_DESTRUCTIVE_RESET=1 \\
        python eval/reset_test_db.py --database-url "postgresql://localhost/..." --yes
"""

import argparse
import os
import sys
import urllib.parse

# src/ 경로를 Python 모듈 검색 경로에 추가한다.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from collector.storage import ensure_db

# 초기화 대상 테이블과 컬럼 목록 (배너 메시지용).
_CLEAR_SUMMARY = """\
  - event_articles      : 전체 삭제
  - events              : 전체 삭제 (prev_event_id / next_event_id 자기참조 먼저 해제)
  - topic_causes        : 전체 삭제
  - topics              : 전체 삭제
  - article_ai_results  : 전체 삭제
  - articles            : guid LIKE 'dummy-%' 인 행만 삭제 (eval 더미 기사 전용)"""


def _check_safety_guards(
    database_url: str, allow_remote: bool, clear_summary: str = _CLEAR_SUMMARY
) -> None:
    """프로덕션 DB 오염을 방지하는 안전 가드 3중 검사.

    DB 연결 전에 호출해 연결 비용 없이 빠르게 거부한다.

    clear_summary는 배너에 출력할 "무엇을 지우는지" 문구다. reset_classifier_only.py가
    이 가드를 공유하되 자기 삭제 범위를 정확히 알리도록 파라미터로 뺐다 — 안전 가드를
    복사해 두면 두 벌이 갈라지므로 한 벌만 유지한다.
    """
    # 가드 1: 전용 환경변수 확인
    if not os.environ.get("EVAL_ALLOW_DESTRUCTIVE_RESET"):
        print(
            "오류: EVAL_ALLOW_DESTRUCTIVE_RESET=1 환경변수가 설정되지 않았습니다.\n"
            "이 스크립트는 eval 전용 DB 에서만 실행해야 합니다.\n"
            "  export EVAL_ALLOW_DESTRUCTIVE_RESET=1",
            file=sys.stderr,
        )
        sys.exit(2)

    # 가드 2: 원격 호스트 차단 (localhost / 127.0.0.1 이 아니면 거부)
    try:
        parsed = urllib.parse.urlparse(database_url)
        host = parsed.hostname or ""
    except Exception:
        host = ""
    is_local = host in ("localhost", "127.0.0.1", "")
    if not is_local and not allow_remote:
        print(
            f"오류: DATABASE_URL 호스트({host!r})가 localhost 가 아닙니다.\n"
            "원격/프로덕션 DB 에 대한 파괴적 초기화를 거부합니다.\n"
            "정말 실행하려면 --allow-remote 플래그를 추가하세요 (위험).",
            file=sys.stderr,
        )
        sys.exit(2)

    # 가드 통과 — 배너 출력
    print(
        "!!! 경고: 다음 데이터를 삭제/초기화합니다 !!!\n"
        f"{clear_summary}\n"
        f"  대상 DB 호스트: {host or '(소켓/로컬)'}"
    )


def _reset(conn) -> None:
    """분류기 출력 테이블과 기사 임베딩을 하나의 트랜잭션으로 초기화한다.

    FK 의존 순서 (마이그레이션 근거):
      event_articles.event_id → events  : 20260508163802_create_event_articles_table.sql
      events.topic_id         → topics  : 20260503082036_create_topics_and_events_table.sql
      events.prev_event_id/next_event_id → events (자기참조): 위 마이그레이션 + 20260509091459_rename
      topic_causes.topic_id   → topics  : 20260525120000_add_embeddings_and_topic_causes.sql
    """
    with conn.transaction():
        # 1. event_articles 먼저 삭제 — events FK 의존
        conn.execute("DELETE FROM event_articles")

        # 2. events 자기참조 FK 해제 후 삭제
        #    prev_event_id / next_event_id 는 20260509091459 마이그레이션으로 rename된 컬럼명
        #    (CLASSIFIER_REQUIRED_COLUMNS in storage.py 에서도 확인)
        conn.execute("UPDATE events SET prev_event_id = NULL, next_event_id = NULL")
        conn.execute("DELETE FROM events")

        # 3. topic_causes 삭제 — topics FK 의존
        conn.execute("DELETE FROM topic_causes")

        # 4. topics 삭제 (parent_topic_id 자기참조는 ON DELETE SET NULL 이므로 그냥 삭제 가능)
        conn.execute("DELETE FROM topics")

        # 5. article_ai_results 전체 삭제
        #    article_ai_results.article_id → articles 는 ON DELETE CASCADE 이지만,
        #    articles 삭제를 dummy- guid로만 스코프하므로(아래 6) 그 밖의(레거시) 기사에
        #    남아있을 수 있는 article_ai_results 행까지 명시적으로 비워 재처리 대상에서
        #    확실히 제외한다.
        conn.execute("DELETE FROM article_ai_results")

        # 6. articles 삭제 — eval 더미 기사(guid 접두 'dummy-')만 스코프한다.
        #    이전 버전은 UPDATE로 embedding/core_content만 NULL 처리해 기사 행 자체가
        #    삭제되지 않았고, 그 결과 반복 실행마다 dev/holdout 기사가 같은 코퍼스에
        #    누적되는 오염이 있었다 (2026-07-12 발견). guid LIKE 필터는 SQL 리터럴에
        #    '%'를 그대로 쓰면 안 된다 — PostgresConnection.execute는 params가 없어도
        #    항상 빈 튜플을 psycopg에 전달하므로 '%'가 포맷 지시자로 파싱되어 깨진다
        #    (params가 실제로 존재하는지와 무관). 반드시 '%%'로 이스케이프한다.
        #    event_articles(NO ACTION, 이미 1에서 삭제됨)·article_ai_results(CASCADE,
        #    이미 5에서 삭제됨) 외에 articles를 참조하는 abusing_articles(NO ACTION)는
        #    eval 더미 기사를 참조하지 않음을 확인했다.
        conn.execute("DELETE FROM articles WHERE guid LIKE 'dummy-%%'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="분류기 출력 테이블을 초기화합니다. "
                    "EVAL_ALLOW_DESTRUCTIVE_RESET=1 과 --yes 없이는 실행되지 않습니다."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres 연결 URL (미지정 시 DATABASE_URL 환경변수 사용)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="배너 확인 후 바로 초기화 실행 (비대화형/자동화용 필수 플래그)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="localhost 외 원격 DB 에 대한 초기화를 허용 (eval 전용 원격 DB 사용 시)",
    )
    args = parser.parse_args()

    if not args.database_url:
        print(
            "오류: --database-url 또는 DATABASE_URL 환경변수를 지정하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 안전 가드 — DB 연결 전에 실행해 연결 비용 없이 빠르게 거부한다.
    _check_safety_guards(args.database_url, allow_remote=args.allow_remote)

    if not args.yes:
        print(
            "\n--yes 플래그를 추가해야 실제로 실행됩니다.\n"
            "예: EVAL_ALLOW_DESTRUCTIVE_RESET=1 "
            "python eval/reset_test_db.py --database-url <URL> --yes",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = ensure_db("eval_reset.db", database_url=args.database_url)
    try:
        _reset(conn)
        print(
            "DB 초기화 완료:\n"
            "  event_articles / events / topic_causes / topics / article_ai_results 삭제,\n"
            "  articles: guid LIKE 'dummy-%' 인 행 삭제"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""분류기 출력만 지우고 기사 요약은 보존하는 가벼운 초기화.

`reset_test_db.py`는 `article_ai_results`를 **통째로 삭제**한다. 합성 더미 코퍼스를 매번
새로 만드는 eval 루프에서는 그게 맞지만, `data/seed/`의 실제 기사 2,578건으로 제약 검사를
반복할 때는 치명적이다 — 기사 요약(`article_ai_results.summary`)은 수집기 단계
(`main.py process`)가 LLM으로 만든 비싼 산출물인데, 지우면 반복할 때마다 재생성해야 한다.

이 스크립트는 분류기가 만든 것만 지우고 그 앞단은 건드리지 않는다.

  지움   event_articles / events / topic_causes / topics
  되돌림 article_ai_results.status → 'done'  (행 자체는 보존)
  보존   articles, article_ai_results.summary

`articles.embedding` / `core_content`는 이벤트 분류기가 매 실행 덮어쓰므로 손대지 않는다.

안전 가드는 `reset_test_db.py`와 같은 3중이다 (환경변수 / localhost 전용 / --yes).

사용 예:
    EVAL_ALLOW_DESTRUCTIVE_RESET=1 \\
        python eval/reset_classifier_only.py --database-url "postgresql://localhost/..." --yes
"""

import argparse
import os
import sys

# src/ 경로를 Python 모듈 검색 경로에 추가한다.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector.storage import ensure_db  # noqa: E402

# 안전 가드는 한 벌만 유지한다 — 복사해 두면 두 스크립트가 갈라진다.
from reset_test_db import _check_safety_guards  # noqa: E402

_CLEAR_SUMMARY = """\
  - event_articles      : 전체 삭제
  - events              : 전체 삭제 (prev_event_id / next_event_id 자기참조 먼저 해제)
  - topic_causes        : 전체 삭제
  - topics              : 전체 삭제
  - article_ai_results  : status 만 'done' 으로 되돌림 (행·요약 보존)
  - articles            : 손대지 않음"""

# 초기화 후 남아 있어야 할(=보존 확인용) 테이블과 비어 있어야 할 테이블
_MUST_BE_EMPTY = ("event_articles", "events", "topic_causes", "topics")
_MUST_SURVIVE = ("articles", "article_ai_results")


def reset_classifier_output(conn) -> dict[str, int]:
    """분류기 출력 테이블을 비우고 기사 상태를 재처리 대기로 되돌린다.

    FK 의존 순서는 `reset_test_db._reset`과 같다:
      event_articles.event_id → events
      events.topic_id         → topics
      events.prev_event_id/next_event_id → events (자기참조)
      topic_causes.topic_id   → topics

    반환값은 삭제 전 행 수 스냅샷이라 무엇이 얼마나 지워졌는지 보고할 수 있다.
    """
    before = {t: _count(conn, t) for t in _MUST_BE_EMPTY + _MUST_SURVIVE}

    with conn.transaction():
        conn.execute("DELETE FROM event_articles")
        conn.execute("UPDATE events SET prev_event_id = NULL, next_event_id = NULL")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM topic_causes")
        conn.execute("DELETE FROM topics")
        # 행을 지우지 않고 상태만 되돌린다. status는 Postgres에서 enum이지만 리터럴은
        # 대입 문맥에서 자동 캐스트되므로 파라미터 바인딩과 달리 명시 캐스트가 필요 없다.
        # CURRENT_TIMESTAMP는 SQLite/Postgres 모두 지원하는 표준 표기다.
        conn.execute(
            "UPDATE article_ai_results SET status = 'done', updated_at = CURRENT_TIMESTAMP"
        )

    return before


def _count(conn, table: str) -> int:
    # 컬럼 별칭으로 꺼낸다. SQLite 경로는 sqlite3.Row를 돌려주는데 .values()가 없어서
    # 위치 기반 접근은 Postgres에서만 동작한다 (storage.py의 두 백엔드 공용 규칙).
    row = conn.query_one(f"SELECT count(*) AS n FROM {table}")
    return int(row["n"]) if row else 0


def verify_reset(conn) -> list[str]:
    """초기화 결과를 검증해 문제 문구 목록을 돌려준다 (빈 리스트면 정상)."""
    problems = []
    for table in _MUST_BE_EMPTY:
        n = _count(conn, table)
        if n:
            problems.append(f"{table} 이 비지 않았습니다 ({n}행 남음)")
    pending = conn.query_one(
        "SELECT count(*) AS n FROM article_ai_results WHERE status <> 'done'"
    )
    remaining = int(pending["n"]) if pending else 0
    if remaining:
        problems.append(f"article_ai_results 중 status<>'done' 인 행이 {remaining}건 남았습니다")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(
        description="분류기 출력만 초기화하고 기사 요약은 보존합니다. "
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
        print("오류: --database-url 또는 DATABASE_URL 환경변수를 지정하세요.", file=sys.stderr)
        sys.exit(1)

    _check_safety_guards(
        args.database_url, allow_remote=args.allow_remote, clear_summary=_CLEAR_SUMMARY
    )

    if not args.yes:
        print(
            "\n--yes 플래그를 추가해야 실제로 실행됩니다.\n"
            "예: EVAL_ALLOW_DESTRUCTIVE_RESET=1 "
            "python eval/reset_classifier_only.py --database-url <URL> --yes",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = ensure_db("eval_reset.db", database_url=args.database_url)
    try:
        before = reset_classifier_output(conn)
        problems = verify_reset(conn)

        print("\n분류기 출력 초기화 완료")
        print(f"  삭제: event_articles {before['event_articles']:,} / "
              f"events {before['events']:,} / "
              f"topic_causes {before['topic_causes']:,} / topics {before['topics']:,}")
        print(f"  보존: articles {before['articles']:,} / "
              f"article_ai_results {before['article_ai_results']:,} (status='done'으로 재설정)")

        if problems:
            print("\n[경고] 초기화 후 검증에서 문제가 발견됐습니다:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""로컬 Supabase 에 시드 SQL 을 넣는다 (psql 없이).

`eval/data/seed/*.sql` 은 수 MB 짜리 INSERT 뭉치라 보통 psql 로 넣지만, psql 이 깔려 있지 않은
개발 머신이 많아 psycopg 로 대신 적용한다.

안전 가드
---------
1. DATABASE_URL 호스트가 localhost/127.0.0.1 이 아니면 거부한다(--allow-remote 로만 우회).
2. `--replace` 는 **로컬에서만** 허용한다. --allow-remote 로도 뚫리지 않는다 — 원격에 시드를
   넣는 것과 원격 데이터를 지우는 것은 전혀 다른 일이고, 후자는 이 스크립트의 역할이 아니다.
   (원격/테스트 DB 초기화는 `eval/reset_test_db.py` 가 자체 가드를 갖고 담당한다.)
3. articles 를 넣는 시드인데 articles 가 비어 있지 않으면 거부한다. 시드는 id 를 원본 그대로
   (OVERRIDING SYSTEM VALUE) 넣으므로 PK 가 충돌한다. `--replace` 로만 우회한다.
4. articles 를 넣지 않는 시드(이벤트 시드 등)인데 articles 가 비어 있으면 거부한다. FK 가 깨진다.

`supabase db reset` 직후에도 비어 있지 않다
-------------------------------------------
hannoon-supabase 의 `seed.sql` 이 데모용으로 articles 9건 / events 12건 / topics 4건을 넣는다.
그 기사들의 id 가 1~9 라서 기사 시드(원본 id 1~4761)와 정면으로 충돌한다. 그래서 리셋 직후라도
`--replace` 가 필요하다.

사용:
    python scripts/apply_local_seed.py --replace
    python scripts/apply_local_seed.py --file eval/data/seed/events_2026-07-27.sql
"""

import argparse
import os
import sys
import urllib.parse

import psycopg

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
DEFAULT_SEED = os.path.join(REPO_ROOT, "eval", "data", "seed", "articles_2026-07-20.sql")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# --replace 가 비우는 테이블. FK 의존 순서대로이며, events/topics 는 자기참조가 있어 먼저 끊는다.
# notifications / subscriptions / viewed_events 는 파이프라인 산출물이 아니라 앱 데이터지만,
# events/topics 를 참조하는 FK 가 있어 함께 비워야 한다. 도움말에도 그대로 밝힌다.
CLEARED_TABLES = (
    "abusing_articles",
    "article_jobs",
    "article_ai_results",
    "event_articles",
    "notifications",
    "viewed_events",
    "subscriptions",
    "topic_causes",
    "events",
    "topics",
    "articles",
)
CLEAR_STATEMENTS = (
    "DELETE FROM public.abusing_articles",
    "DELETE FROM public.article_jobs",
    "DELETE FROM public.article_ai_results",
    "DELETE FROM public.event_articles",
    "DELETE FROM public.notifications",
    "DELETE FROM public.viewed_events",
    "DELETE FROM public.subscriptions",
    "DELETE FROM public.topic_causes",
    "UPDATE public.events SET prev_event_id = NULL, next_event_id = NULL",
    "DELETE FROM public.events",
    "UPDATE public.topics SET parent_topic_id = NULL",
    "DELETE FROM public.topics",
    "DELETE FROM public.articles",
)
REPORT_TABLES = ("articles", "events", "event_articles", "article_ai_results")


def seed_targets_articles(sql: str) -> bool:
    """이 시드가 articles 에 INSERT 하는지. 가드 방향이 정반대라 먼저 판별해야 한다."""
    return "INSERT INTO public.articles" in sql or 'INSERT INTO public."articles"' in sql


def is_local_host(host: str) -> bool:
    return host in LOCAL_HOSTS


def check_host_guards(*, host: str, allow_remote: bool, replace: bool) -> str | None:
    """접속 대상만 보고 판단하는 가드. **DB 에 연결하기 전에** 호출한다.

    순수 함수로 둔 이유: 이 스크립트에서 데이터를 지울 수 있는 유일한 경로가 --replace 라,
    "언제 거부하는가"를 DB 없이 테스트로 고정해두려는 것이다.
    """
    local = is_local_host(host)

    if not local and not allow_remote:
        return f"로컬 호스트가 아닙니다({host}). 정말 원격이라면 --allow-remote 를 주세요."

    if replace and not local:
        return (
            f"--replace 는 로컬 DB 에서만 쓸 수 있습니다(받은 호스트: {host}). "
            "--allow-remote 로도 우회되지 않습니다.\n"
            "      원격/테스트 DB 를 초기화하려면 eval/reset_test_db.py 를 쓰세요."
        )

    return None


def check_state_guards(*, seeds_articles: bool, existing_articles: int, replace: bool) -> str | None:
    """DB 상태를 봐야 판단할 수 있는 가드. 연결 후에 호출한다."""
    if not seeds_articles and not existing_articles:
        return (
            "이 시드는 기사를 참조하는데 articles 가 비어 있습니다.\n"
            "      먼저: python scripts/apply_local_seed.py --replace"
        )

    if seeds_articles and existing_articles and not replace:
        return (
            f"articles 에 이미 {existing_articles:,}행이 있습니다. "
            "시드는 id 를 원본 그대로 넣으므로 PK 충돌이 납니다.\n"
            "      supabase db reset 직후라면 seed.sql 데모 데이터(id 1~9)입니다.\n"
            "      비우고 넣으려면: --replace"
        )

    return None


def clear_existing(conn) -> None:
    """CLEARED_TABLES 를 FK 순서대로 비운다. 호출자가 트랜잭션을 관리한다."""
    with conn.cursor() as cur:
        for statement in CLEAR_STATEMENTS:
            cur.execute(statement)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL") or DEFAULT_URL)
    parser.add_argument("--file", default=DEFAULT_SEED)
    parser.add_argument("--allow-remote", action="store_true", help="원격 DB 에 적용을 허용한다(위험).")
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "넣기 전에 다음 테이블을 모두 비운다(로컬 전용): " + ", ".join(CLEARED_TABLES) + ". "
            "notifications/subscriptions/viewed_events 는 앱 데이터지만 events·topics 를 참조하는 "
            "FK 때문에 함께 지워진다. supabase db reset 직후에도 필요하다 — seed.sql 데모 데이터가 "
            "articles id 1~9 를 차지하고 있어서."
        ),
    )
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"거부: 시드 파일이 없습니다 — {args.file}", file=sys.stderr)
        return 2

    host = urllib.parse.urlparse(args.database_url).hostname or ""

    # 접속 대상만으로 판단할 수 있는 것은 연결 전에 끝낸다.
    refusal = check_host_guards(host=host, allow_remote=args.allow_remote, replace=args.replace)
    if refusal:
        print(f"거부: {refusal}", file=sys.stderr)
        return 1

    with open(args.file, encoding="utf-8") as f:
        seed_sql = f.read()
    seeds_articles = seed_targets_articles(seed_sql)

    size_mb = os.path.getsize(args.file) / 1024 / 1024
    print(f"시드 파일: {args.file} ({size_mb:.1f}MB)")
    print(f"대상 DB  : {host}")

    with psycopg.connect(args.database_url, connect_timeout=20, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.articles")
            existing = cur.fetchone()[0]

            refusal = check_state_guards(
                seeds_articles=seeds_articles, existing_articles=existing, replace=args.replace
            )
            if refusal:
                print(f"거부: {refusal}", file=sys.stderr)
                return 1

            if args.replace and existing:
                print(f"--replace: 기존 데이터를 비웁니다 (articles {existing:,}행 등)")
                for statement in CLEAR_STATEMENTS:
                    cur.execute(statement)

            print("적용 중... (수천 개의 INSERT 라 1~2분 걸릴 수 있습니다)")
            cur.execute(seed_sql)

            report = []
            for table in REPORT_TABLES:
                cur.execute(f"SELECT count(*) FROM public.{table}")
                count = cur.fetchone()[0]
                if count:
                    report.append(f"{table} {count:,}행")
        conn.commit()

    print("완료:", " / ".join(report))
    if seeds_articles:
        print("다음 단계: 이벤트까지 필요하면 events 시드를 적용하거나, main.py process 로 직접 만드세요.")
    else:
        print("다음 단계: python classify_topics.py --database-url ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())

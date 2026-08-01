#!/usr/bin/env python3
"""로컬 Supabase 에 기사 시드 SQL 을 넣는다 (psql 없이).

`eval/data/seed/articles_2026-07-20.sql` 은 11MB 짜리 INSERT 뭉치라 보통 psql 로 넣지만,
psql 이 깔려 있지 않은 개발 머신이 많아 psycopg 로 대신 적용한다.

안전 가드:
  1. DATABASE_URL 호스트가 localhost/127.0.0.1 이 아니면 거부한다(--allow-remote 로만 우회).
  2. articles 테이블이 비어 있지 않으면 거부한다. 시드는 id 를 원본 그대로(OVERRIDING SYSTEM
     VALUE) 넣으므로 기존 행이 있으면 PK 충돌로 실패한다. --replace 로만 우회한다.

`supabase db reset` 직후에도 비어 있지 않다
--------------------------------------------
hannoon-supabase 의 `seed.sql` 이 데모용으로 articles 9건 / events 12건 / topics 4건을 넣는다.
그 기사들의 id 가 1~9 라서 이 시드(원본 id 1~4761)와 정면으로 충돌한다. 그래서 리셋 직후라도
`--replace` 가 필요하다. --replace 는 기사·분류 산출물 테이블을 모두 비운 뒤 시드를 넣는다.

사용:
    python scripts/apply_local_seed.py --replace
    python scripts/apply_local_seed.py --replace --file eval/data/seed/articles_test_target_2026-07-20.sql
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

# FK 의존 순서대로 비운다. events/topics 는 자기참조가 있어 먼저 NULL 로 끊는다.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL") or DEFAULT_URL)
    parser.add_argument("--file", default=DEFAULT_SEED)
    parser.add_argument("--allow-remote", action="store_true", help="원격 DB 에 적용을 허용한다(위험).")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="기사·분류 산출물 테이블을 모두 비우고 넣는다. supabase db reset 직후에도 필요하다"
        " (seed.sql 데모 데이터가 id 1~9 를 차지하고 있어서).",
    )
    args = parser.parse_args()

    host = urllib.parse.urlparse(args.database_url).hostname or ""
    if host not in LOCAL_HOSTS and not args.allow_remote:
        print(f"거부: 로컬 호스트가 아닙니다({host}). 정말 원격이라면 --allow-remote 를 주세요.", file=sys.stderr)
        return 2

    if not os.path.exists(args.file):
        print(f"거부: 시드 파일이 없습니다 — {args.file}", file=sys.stderr)
        return 2

    size_mb = os.path.getsize(args.file) / 1024 / 1024
    print(f"시드 파일: {args.file} ({size_mb:.1f}MB)")
    print(f"대상 DB  : {host}")

    with open(args.file, encoding="utf-8") as f:
        seed_sql = f.read()
    # articles 를 넣는 시드일 때만 "비어 있어야 한다" 가드가 의미가 있다.
    # 이벤트 시드(events/event_articles/...)는 오히려 articles 가 이미 있어야 FK 가 성립한다.
    seeds_articles = "INSERT INTO public.articles" in seed_sql or 'INSERT INTO public."articles"' in seed_sql

    with psycopg.connect(args.database_url, connect_timeout=20, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.articles")
            existing = cur.fetchone()[0]
            if not seeds_articles and not existing:
                print(
                    "거부: 이 시드는 기사를 참조하는데 articles 가 비어 있습니다.\n"
                    "      먼저: python scripts/apply_local_seed.py --replace",
                    file=sys.stderr,
                )
                return 1
            if existing and seeds_articles and not args.replace:
                print(
                    f"거부: articles 에 이미 {existing:,}행이 있습니다. "
                    f"시드는 id 를 원본 그대로 넣으므로 PK 충돌이 납니다.\n"
                    f"      supabase db reset 직후라면 seed.sql 데모 데이터(id 1~9)입니다.\n"
                    f"      비우고 넣으려면: --replace",
                    file=sys.stderr,
                )
                return 1

            if args.replace and existing:
                print(f"--replace: 기존 데이터를 비웁니다 (articles {existing:,}행 등)")
                for stmt in CLEAR_STATEMENTS:
                    cur.execute(stmt)

            print("적용 중... (수천 개의 INSERT 라 1~2분 걸릴 수 있습니다)")
            cur.execute(seed_sql)

            report = []
            for table in ("articles", "events", "event_articles", "article_ai_results"):
                cur.execute(f"SELECT count(*) FROM public.{table}")
                n = cur.fetchone()[0]
                if n:
                    report.append(f"{table} {n:,}행")
        conn.commit()

    print("완료:", " / ".join(report))
    if seeds_articles:
        print("다음 단계: 이벤트까지 필요하면 events 시드를 적용하거나, main.py process 로 직접 만드세요.")
    else:
        print("다음 단계: python classify_topics.py --database-url ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())

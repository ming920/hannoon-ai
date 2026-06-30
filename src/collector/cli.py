import argparse
import os

from dotenv import load_dotenv

from .crawler import crawl_articles
from .rss import fetch_feed
from .settings import (
    DEFAULT_AI_BATCH_SIZE,
    DEFAULT_ANALYSIS_MAX_ATTEMPTS,
    DEFAULT_CRAWL_BATCH_SIZE,
    DEFAULT_DB,
    DEFAULT_DOMAIN_DELAY,
    DEFAULT_FEEDS_FILE,
    DEFAULT_LLM_ARTICLE_MODEL,
    DEFAULT_LLM_CLEANUP_MODEL,
    DEFAULT_LLM_SUMMARY_MODEL,
    DEFAULT_MIN_CRAWL_LEN,
)
from .storage import backfill_article_categories, ensure_db
from .utils import load_feed_urls


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB 경로")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres/Supabase DB URL. 기본값은 DATABASE_URL 환경 변수",
    )
    parser.add_argument("--feeds-file", default=DEFAULT_FEEDS_FILE, help="RSS 피드 URL JSON 파일")
    parser.add_argument("--feed", action="append", help="추가 RSS 피드 URL 또는 로컬 파일")
    parser.add_argument("--min-crawl-len", type=int, default=DEFAULT_MIN_CRAWL_LEN)
    parser.add_argument(
        "--crawl-batch-size",
        type=int,
        default=DEFAULT_CRAWL_BATCH_SIZE,
        help="크롤링 배치당 가져올 기사 수",
    )
    parser.add_argument(
        "--ai-batch-size",
        type=int,
        default=DEFAULT_AI_BATCH_SIZE,
        help="AI 분석 배치당 처리할 기사 수",
    )
    parser.add_argument(
        "--analysis-max-attempts",
        type=int,
        default=DEFAULT_ANALYSIS_MAX_ATTEMPTS,
        help="작업을 실패로 확정하기 전 LLM 분석 최대 시도 횟수",
    )
    parser.set_defaults(llm_cleanup=True)
    parser.add_argument(
        "--llm-cleanup",
        dest="llm_cleanup",
        action="store_true",
        help="정제가 필요한 흔적이 있는 크롤링 본문을 LLM으로 정제. 기본 활성화",
    )
    parser.add_argument(
        "--no-llm-cleanup",
        dest="llm_cleanup",
        action="store_false",
        help="크롤링 후 LLM 본문 정제를 비활성화",
    )
    parser.add_argument(
        "--llm-cleanup-model",
        default=DEFAULT_LLM_CLEANUP_MODEL,
        help="크롤링 본문 정제에 사용할 LLM 모델",
    )
    parser.add_argument(
        "--llm-article-model",
        default=DEFAULT_LLM_ARTICLE_MODEL,
        help="기사 분석 기본 LLM 모델",
    )
    parser.add_argument(
        "--llm-summary-model",
        default=DEFAULT_LLM_SUMMARY_MODEL,
        help="기사 요약에 사용할 LLM 모델",
    )
    parser.add_argument("--domain-delay", type=float, default=DEFAULT_DOMAIN_DELAY)
    parser.add_argument("--offline", action="store_true", help="http/https URL 요청을 건너뜀")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS 수집 -> 크롤링 -> LLM 기사 분석 파이프라인")
    add_common_args(parser)

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("fetch", help="RSS 항목만 수집")
    subparsers.add_parser("crawl", help="본문이 필요한 기사만 크롤링")
    subparsers.add_parser("process", help="ready 기사 LLM 분석")
    subparsers.add_parser("run", help="RSS 수집, 크롤링, 정제, 분석 전체 실행")
    return parser


def main() -> int:
    load_dotenv(override=True)
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "run"

    feed_urls = load_feed_urls(args.feeds_file, args.feed)
    if command in {"fetch", "run"} and not feed_urls:
        print("No feeds configured. Provide --feed or config/feeds.json")
        return 1

    if args.crawl_batch_size <= 0:
        print("--crawl-batch-size must be greater than 0")
        return 1
    if args.ai_batch_size <= 0:
        print("--ai-batch-size must be greater than 0")
        return 1
    if args.analysis_max_attempts <= 0:
        print("--analysis-max-attempts must be greater than 0")
        return 1

    text_cleaner = None

    with ensure_db(args.db, database_url=args.database_url) as conn:
        if command in {"fetch", "run"}:
            total = 0
            for feed_url in feed_urls:
                try:
                    count = fetch_feed(conn, feed_url, offline=args.offline)
                except Exception as exc:
                    print(f"[warn] feed failed: {feed_url} ({exc})")
                    continue
                print(f"[feed] {feed_url} -> {count} new items")
                total += count
            backfill_article_categories(conn)
            print(f"[done] RSS items inserted: {total}")

        if command in {"crawl", "run"}:
            updated = crawl_articles(
                conn,
                min_crawl_len=args.min_crawl_len,
                offline=args.offline,
                domain_delay=args.domain_delay,
                crawl_batch_size=args.crawl_batch_size,
                llm_cleanup=args.llm_cleanup,
                llm_cleanup_model=args.llm_cleanup_model,
                text_cleaner=text_cleaner,
            )
            print(f"[done] Crawled items updated: {updated}")

        if command in {"process", "run"}:
            from .article_ai_pipeline import process_pending_articles

            processed = process_pending_articles(
                conn,
                article_model=args.llm_article_model,
                summary_model=args.llm_summary_model,
                batch_size=args.ai_batch_size,
                analysis_max_attempts=args.analysis_max_attempts,
            )
            print(f"[done] AI pipeline completed items: {processed}")

    return 0

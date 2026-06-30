from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from collector.settings import DEFAULT_DB
from collector.storage import ensure_db
from topic_classifier.pipeline import run
from topic_classifier.settings import (
    BATCH_SIZE,
    LLM_MODEL,
    MIN_NET_ARTICLE_COUNT,
    SUBTOPIC_TOP_K,
    SUBTOPICS_ENABLED,
    TOP_K,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM으로 미분류 이벤트를 토픽에 배정")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres/Supabase DB URL. 기본값은 DATABASE_URL 환경 변수",
    )
    parser.add_argument(
        "--min-net-article-count",
        type=int,
        default=MIN_NET_ARTICLE_COUNT,
        help="토픽 배정 대상이 되기 위한 최소 정상 기사 수",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help="한 번에 처리할 최대 이벤트 수",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=TOP_K,
        help="LLM에 보여줄 최대 토픽 후보 수",
    )
    parser.add_argument(
        "--llm-model",
        default=LLM_MODEL,
        help="토픽 배정에 사용할 LLM 모델",
    )
    parser.add_argument(
        "--subtopics",
        dest="subtopics",
        action="store_true",
        default=SUBTOPICS_ENABLED,
        help="토픽 아래 서브토픽 계층 분류를 활성화한다(기본값은 TOPIC_SUBTOPICS_ENABLED)",
    )
    parser.add_argument(
        "--no-subtopics",
        dest="subtopics",
        action="store_false",
        help="서브토픽 계층 분류를 끄고 기존 평면 토픽 배정만 수행한다",
    )
    parser.add_argument(
        "--subtopic-top-k",
        type=int,
        default=SUBTOPIC_TOP_K,
        help="LLM에 보여줄 부모 토픽 내 서브토픽 후보의 최대 수",
    )
    return parser


def main() -> int:
    load_dotenv(override=True)
    args = build_parser().parse_args()

    if args.min_net_article_count < 0:
        print("--min-net-article-count는 0 이상이어야 합니다")
        return 1
    if args.batch_size <= 0:
        print("--batch-size는 0보다 커야 합니다")
        return 1
    if args.top_k <= 0:
        print("--top-k는 0보다 커야 합니다")
        return 1
    if args.subtopic_top_k <= 0:
        print("--subtopic-top-k는 0보다 커야 합니다")
        return 1

    if not args.database_url:
        print(
            "[topic] Postgres 연결이 필요합니다 --database-url 또는 DATABASE_URL 환경변수를 설정하세요\n"
            "토픽 분류는 pgvector가 필요하므로 Postgres 연결이 필수입니다",
            file=sys.stderr,
        )
        return 1

    with ensure_db(
        DEFAULT_DB,
        database_url=args.database_url,
        require_classifier_schema=True,
    ) as conn:
        count = run(
            conn,
            args.min_net_article_count,
            args.batch_size,
            args.top_k,
            args.llm_model,
            subtopics_enabled=args.subtopics,
            subtopic_top_k=args.subtopic_top_k,
        )
        print(f"[done] 토픽 배정 완료: {count}건")

    return 0

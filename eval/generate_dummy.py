#!/usr/bin/env python3
"""
taxonomy → 더미 기사 생성 + 정답 라벨 파일 출력

사용법:
    python eval/generate_dummy.py [옵션]
    python hannoon-ai/eval/generate_dummy.py [옵션]  (상위 디렉터리에서 실행 시)

옵션:
    --taxonomy PATH         gold taxonomy JSON 경로
                            (기본값: <repo>/eval/taxonomy/gold_taxonomy.json)
    --out-dir PATH          출력 디렉터리
                            (기본값: <repo>/eval/data)
    --articles-per-event N  이벤트당 기사 수 오버라이드 (taxonomy의 num_articles 무시)
    --limit-events N        처리할 이벤트 수 제한 (저비용 스모크 테스트용)
    --dry-run               LLM 미호출 — base_facts 기반 결정적 플레이스홀더 생성
                            (API 키 불필요)

출력 파일:
    {out-dir}/dummy_articles.json  — 기사 목록
    {out-dir}/gold_labels.json     — guid → {gold_topic, gold_subtopic, gold_event}

스키마:
    dummy_articles.json 항목:
        guid          str   "dummy-{event_id}-{i}"
        category      str   정치/경제/사회/국제
        title         str
        summary       str   (≤700자 권장)
        content       str
        published_at  str   ISO 8601
        publisher     str
        bias_type     str   진보/중도/보수

    gold_labels.json 항목:
        gold_topic    str   토픽 ID (예: "T001")
        gold_subtopic str   서브토픽 ID (예: "S001")
        gold_event    str   이벤트 ID — gold_event_ref 가 설정된 경우 참조 이벤트 ID
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# hannoon-ai/src 를 모듈 검색 경로에 추가 (LLMClient 임포트용)
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# ---------------------------------------------------------------------------
# 고정 설정값
# ---------------------------------------------------------------------------

# 기사 성향 순환 순서
BIAS_TYPES = ["진보", "중도", "보수"]

# 성향별 대표 매체명 (i % len 으로 순환 배정)
PUBLISHERS_BY_BIAS: dict[str, list[str]] = {
    "진보": ["한겨레", "경향신문", "오마이뉴스"],
    "중도": ["연합뉴스", "YTN", "SBS"],
    "보수": ["조선일보", "중앙일보", "동아일보"],
}

# 기사 간 발행 시간 간격 (결정적 spread를 위해 고정)
_PUBLISH_INTERVAL_HOURS = 3

_DEFAULT_TAXONOMY = str(_REPO_ROOT / "eval" / "taxonomy" / "gold_taxonomy.json")
_DEFAULT_OUT_DIR = str(_REPO_ROOT / "eval" / "data")


# ---------------------------------------------------------------------------
# 드라이런 — 결정적 플레이스홀더 생성 (LLM 미호출)
# ---------------------------------------------------------------------------

def _dry_run_article(
    *,
    event_id: str,
    event_title: str,
    base_facts: str,
    variant_hint: str,
    idx: int,
    bias_type: str,
    publisher: str,
    published_at: str,
    category: str,
) -> dict:
    """LLM 없이 결정적으로 더미 기사를 생성한다."""
    title = f"[더미] {event_title} – {variant_hint}"

    # 요약: base_facts 첫 140자 + 변형 구분자
    facts_head = base_facts[:140].rstrip()
    summary = (
        f"{facts_head}… "
        f"본 기사는 '{variant_hint}' 관점 테스트 기사다. "
        f"({bias_type} 매체, 기사 {idx + 1}번)"
    )

    content = (
        "【자동 생성 테스트 기사 — 드라이런 모드】\n\n"
        f"{base_facts}\n\n"
        f"▶ 보도 관점: {variant_hint}\n"
        f"▶ 매체 성향: {bias_type} / 매체명: {publisher}\n"
        f"▶ 이벤트 ID: {event_id} / 기사 인덱스: {idx}"
    )

    return {
        "title": title,
        "summary": summary,
        "content": content,
        "publisher": publisher,
        "bias_type": bias_type,
        "published_at": published_at,
        "category": category,
    }


# ---------------------------------------------------------------------------
# LLM 생성 경로 (실제 API 호출)
# ---------------------------------------------------------------------------

def _llm_article(
    *,
    client,
    event_title: str,
    base_facts: str,
    variant_hint: str,
    bias_type: str,
    publisher: str,
    published_at: str,
    category: str,
) -> dict:
    """LLMClient.request_json 으로 기사 제목·요약·본문을 생성한다.

    publisher, bias_type, published_at, category 는 우리가 직접 지정하고
    LLM 에는 제목·요약·본문만 요청한다.

    solar-mini 가 content 키를 간헐적으로 누락하는 문제에 대응하기 위해
    최대 3회 재시도하며, 재시도 시 프롬프트에 강화 지시문을 추가한다.
    3회 시도 후에도 content 가 50자 미만이면 summary 를 content 로 대체한다.
    """
    _BASE_PROMPT = (
        "다음 한국 뉴스 이벤트에 대해 지정된 관점·성향의 뉴스 기사를 작성하세요.\n\n"
        f"이벤트: {event_title}\n"
        f"핵심 사실:\n{base_facts}\n\n"
        f"기사 유형/관점: {variant_hint}\n"
        f"매체 성향: {bias_type}\n"
        f"매체명: {publisher}\n"
        f"발행일: {published_at}\n\n"
        "아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):\n"
        '{"title": "기사 제목 (50자 이내)", '
        '"summary": "기사 요약 (4문장, 700자 이내)", '
        '"content": "기사 본문 (3~5문단)"}'
    )
    # content 누락 재시도 시 추가할 강화 지시문
    _RETRY_NUDGE = (
        "\n\n반드시 title, summary, content 세 키를 모두 포함하세요. "
        "content는 3~5문단 본문입니다."
    )
    _MAX_ATTEMPTS = 3
    _MIN_CONTENT_LEN = 50  # content 유효 최소 길이 (자)

    data: dict = {}
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        # 재시도 시 강화 지시문 추가
        prompt = _BASE_PROMPT + (_RETRY_NUDGE if attempt > 0 else "")
        try:
            # content 누락으로 ValueError 가 발생하지 않도록 required_keys 를 최소화
            data = client.request_json(prompt, required_keys={"title", "summary"})
            last_exc = None
        except Exception as exc:  # 네트워크·파싱 등 일시적 오류
            last_exc = exc
            data = {}

        # content 가 충분하면 즉시 반환
        if data.get("content", "") and len(data["content"]) >= _MIN_CONTENT_LEN:
            break
    else:
        # _MAX_ATTEMPTS 회 모두 실패
        if last_exc is not None:
            raise last_exc

    # content 가 여전히 부족하면 summary 로 대체
    content = data.get("content", "")
    if not content or len(content) < _MIN_CONTENT_LEN:
        fallback_content = data.get("summary", "")
        print(
            f"[경고] content 생성 실패 — summary 로 대체 "
            f"(이벤트: {event_title!r}, 매체: {publisher})",
            file=sys.stderr,
        )
        content = fallback_content

    return {
        "title": data.get("title", ""),
        "summary": data.get("summary", ""),
        "content": content,
        "publisher": publisher,
        "bias_type": bias_type,
        "published_at": published_at,
        "category": category,
    }


# ---------------------------------------------------------------------------
# 메인 생성 로직
# ---------------------------------------------------------------------------

def generate(
    taxonomy_path: str,
    out_dir: str,
    articles_per_event_override: int | None,
    limit_events: int | None,
    dry_run: bool,
) -> None:
    """taxonomy JSON 을 읽어 더미 기사와 정답 라벨을 생성한다."""

    taxonomy_file = Path(taxonomy_path)
    if not taxonomy_file.exists():
        sys.exit(f"오류: taxonomy 파일 없음 → {taxonomy_file}")

    with taxonomy_file.open(encoding="utf-8") as f:
        taxonomy = json.load(f)

    # 드라이런이 아닐 때만 LLM 클라이언트 초기화
    # (dry-run 에서는 API 키 없어도 실행 가능)
    client = None
    if not dry_run:
        from openai_client.client import LLMClient  # noqa: PLC0415
        client = LLMClient()

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    dummy_articles: list[dict] = []
    gold_labels: dict[str, dict] = {}

    total_event_count = 0   # taxonomy 에 있는 이벤트 총수
    processed_count = 0     # 실제 처리된 이벤트 수 (limit 적용 후)

    for topic in taxonomy.get("topics", []):
        topic_id: str = topic["id"]

        for subtopic in topic.get("subtopics", []):
            subtopic_id: str = subtopic["id"]

            for event in subtopic.get("events", []):
                total_event_count += 1

                # --limit-events 적용
                if limit_events is not None and processed_count >= limit_events:
                    continue

                event_id: str = event["id"]
                event_title: str = event["title"]
                base_facts: str = event["base_facts"]
                # gold_event_ref 가 없으면 자기 자신 (일반 케이스)
                # time_window 케이스처럼 다른 이벤트를 가리킬 때만 다름
                gold_event_ref: str = event.get("gold_event_ref", event_id)
                base_date_str: str = event.get("base_date", "2026-06-20")
                num_articles: int = articles_per_event_override or event.get("num_articles", 5)
                variants: list[str] = event.get("article_variants_hint", [])
                # cross_category 케이스: 기사별 카테고리 override 목록
                category_overrides: list[str] = event.get("article_category_overrides", [])
                default_category: str = event.get("category", topic["category"])

                base_date = datetime.fromisoformat(base_date_str)

                for i in range(num_articles):
                    # 카테고리: override 목록 우선, 없으면 이벤트 기본 카테고리
                    category = (
                        category_overrides[i]
                        if i < len(category_overrides)
                        else default_category
                    )
                    # 성향·매체: i 를 기준으로 순환 배정
                    bias_type = BIAS_TYPES[i % len(BIAS_TYPES)]
                    publishers = PUBLISHERS_BY_BIAS[bias_type]
                    publisher = publishers[i % len(publishers)]
                    variant_hint = variants[i] if i < len(variants) else f"변형 {i + 1}"
                    # 발행일: base_date 에서 기사마다 _PUBLISH_INTERVAL_HOURS 씩 후 배정
                    published_at = (
                        base_date + timedelta(hours=i * _PUBLISH_INTERVAL_HOURS)
                    ).strftime("%Y-%m-%dT%H:%M:%S")
                    guid = f"dummy-{event_id}-{i}"

                    if dry_run:
                        article_data = _dry_run_article(
                            event_id=event_id,
                            event_title=event_title,
                            base_facts=base_facts,
                            variant_hint=variant_hint,
                            idx=i,
                            bias_type=bias_type,
                            publisher=publisher,
                            published_at=published_at,
                            category=category,
                        )
                    else:
                        article_data = _llm_article(
                            client=client,
                            event_title=event_title,
                            base_facts=base_facts,
                            variant_hint=variant_hint,
                            bias_type=bias_type,
                            publisher=publisher,
                            published_at=published_at,
                            category=category,
                        )

                    dummy_articles.append({"guid": guid, **article_data})
                    # gold_labels: 토픽·서브토픽은 taxonomy 위치에서, 이벤트는 gold_event_ref 에서
                    gold_labels[guid] = {
                        "gold_topic": topic_id,
                        "gold_subtopic": subtopic_id,
                        "gold_event": gold_event_ref,
                    }

                processed_count += 1

    # 출력 파일 쓰기
    articles_file = out_path / "dummy_articles.json"
    labels_file = out_path / "gold_labels.json"

    with articles_file.open("w", encoding="utf-8") as f:
        json.dump(dummy_articles, f, ensure_ascii=False, indent=2)

    with labels_file.open("w", encoding="utf-8") as f:
        json.dump(gold_labels, f, ensure_ascii=False, indent=2)

    # guid 일관성 검사: 두 파일의 guid 집합이 일치해야 함
    article_guids = {a["guid"] for a in dummy_articles}
    label_guids = set(gold_labels.keys())
    mismatch = (article_guids - label_guids) | (label_guids - article_guids)
    if mismatch:
        sys.exit(f"[오류] guid 불일치 발생: {sorted(mismatch)}")

    # 요약 통계
    topics_used = sorted({v["gold_topic"] for v in gold_labels.values()})
    subtopics_used = sorted({v["gold_subtopic"] for v in gold_labels.values()})
    events_used = sorted({v["gold_event"] for v in gold_labels.values()})

    mode_label = "드라이런 (LLM 미호출)" if dry_run else "LLM 생성"
    print(f"\n=== 더미 기사 생성 완료 [{mode_label}] ===")
    print(f"처리 이벤트: {processed_count} / 전체 {total_event_count}개")
    print(f"생성 기사 수: {len(dummy_articles)}")
    print(f"gold_labels  고유 토픽: {len(topics_used)} {topics_used}")
    print(f"             고유 서브토픽: {len(subtopics_used)} {subtopics_used}")
    print(f"             고유 gold_event: {len(events_used)} {events_used}")
    print(f"출력:")
    print(f"  기사   → {articles_file.resolve()}")
    print(f"  라벨   → {labels_file.resolve()}")
    print("guid 일관성 검사: 통과")


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="정답 taxonomy 로부터 더미 기사와 gold_labels 를 생성한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--taxonomy",
        default=_DEFAULT_TAXONOMY,
        metavar="PATH",
        help="gold taxonomy JSON 경로 (기본값: eval/taxonomy/gold_taxonomy.json)",
    )
    parser.add_argument(
        "--out-dir",
        default=_DEFAULT_OUT_DIR,
        metavar="PATH",
        help="출력 디렉터리 (기본값: eval/data)",
    )
    parser.add_argument(
        "--articles-per-event",
        type=int,
        default=None,
        metavar="N",
        help="이벤트당 기사 수 오버라이드 (taxonomy 의 num_articles 무시)",
    )
    parser.add_argument(
        "--limit-events",
        type=int,
        default=None,
        metavar="N",
        help="처리할 이벤트 수 제한 (저비용 스모크 테스트용)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="LLM 을 호출하지 않고 결정적 플레이스홀더를 생성한다 (API 키 불필요)",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    generate(
        taxonomy_path=args.taxonomy,
        out_dir=args.out_dir,
        articles_per_event_override=args.articles_per_event,
        limit_events=args.limit_events,
        dry_run=args.dry_run,
    )

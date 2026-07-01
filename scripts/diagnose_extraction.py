"""RSS 수집 → 크롤링 본문/이미지 추출 품질 진단 하네스 (Phase 0).

목적: 코드를 고치기 전에 "어느 언론사에서 무엇이 깨지는지"를 숫자로 고정한다.
언론사별로 최근 기사를 실제 크롤링해 다음을 측정하고 CSV + 요약표로 남긴다.

  - text_path         : 본문이 어떤 추출 경로로 잡혔는지 (fusion/jtbc/declared/heuristic/fallback)
  - content_len       : 추출된 본문 길이
  - boilerplate_count : 광고/보일러플레이트 키워드 히트 수
  - cleanup_reasons   : should_llm_cleanup 트리거 사유
  - image_url / image_source : 대표 이미지 URL과 그 출처(fusion/declared/article_main/meta_og/none)

개선(Phase 1~) 후 같은 명령으로 재실행해 baseline 대비 회귀를 판정한다.

사용:
    python scripts/diagnose_extraction.py                 # 언론사당 3건, 실제 네트워크
    python scripts/diagnose_extraction.py --per-publisher 5
    python scripts/diagnose_extraction.py --publishers 한겨레,경향신문,동아일보
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

# 저장소는 패키지로 설치하지 않으므로 src를 import path에 추가한다(main.py와 동일 방식).
_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_ROOT)
sys.path.insert(0, os.path.join(_REPO, "src"))

import feedparser  # noqa: E402
import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from collector.settings import USER_AGENT  # noqa: E402
from collector.content_cleaner import (  # noqa: E402
    BOILERPLATE_KEYWORDS,
    should_llm_cleanup,
    strip_boilerplate_footer,
)
from collector.utils import (  # noqa: E402
    extract_article_image_url,
    extract_article_text,
    infer_publisher_metadata,
    load_feed_urls,
    _extract_declared_article_body,
    _extract_declared_article_image,
    _extract_fusion_global_content,
    _extract_fusion_global_image,
    _extract_jtbc_query_content,
    _extract_meta_image,
    _extract_publisher_article_body,
    _first_image_in_node,
    _strip_unwanted,
)


# --- 계측 헬퍼: utils.py 내부 로직을 그대로 재현해 "어떤 경로가 히트했는지"만 판별한다 ---

def detect_text_path(html: str, url: str | None = None) -> str:
    """extract_article_text가 실제로 어떤 추출 경로를 탔는지 판별한다."""
    if _extract_fusion_global_content(html):
        return "fusion"
    if _extract_jtbc_query_content(html):
        return "jtbc"
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return "parse_error"
    if _extract_publisher_article_body(soup, url):
        return "publisher_selector"
    if _extract_declared_article_body(soup):
        return "declared_selector"
    # 범용 점수화 루프를 재현해 heuristic 히트 여부를 확인한다.
    _strip_unwanted(soup)
    best_score = 0
    for selector in ["article", "main", "section", "div"]:
        for node in soup.find_all(selector):
            if node is None:
                continue
            text = " ".join(node.get_text(" ", strip=True).split())
            if len(text) < 200:
                continue
            score = len(text) + len(text.split()) * 5
            if score > best_score:
                best_score = score
    return "heuristic" if best_score else "fallback_wholepage"


def detect_image_source(html: str, url: str) -> str:
    """extract_article_image_url이 어떤 출처에서 이미지를 골랐는지 판별한다."""
    if _extract_fusion_global_image(html, url):
        return "fusion"
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return "parse_error"
    if _extract_declared_article_image(soup, url):
        return "declared_container"
    for selector in ["article", "main"]:
        for node in soup.find_all(selector):
            if _first_image_in_node(node, url):
                return "article_main"
    if _extract_meta_image(soup, url):
        return "meta_og"
    return "none"


def count_boilerplate(text: str) -> int:
    """본문에 남은 보일러플레이트 키워드 종류 수를 센다."""
    lower = text.lower()
    return sum(1 for kw in BOILERPLATE_KEYWORDS if kw.lower() in lower)


# --- 품질 판정 기준: 실측하며 조정할 임계값(여기 숫자가 곧 개선 목표) ---

# 본문이 이보다 짧으면 제목/캡션/광고 영역만 잡혔을 가능성이 높다.
MIN_CONTENT_LEN = 400
# 서로 다른 보일러플레이트 키워드가 이 수를 넘으면 광고/푸터가 본문에 남았다고 본다.
MAX_BOILERPLATE = 3
# 본문 추출이 사실상 실패한 경로(엉뚱한 전체 페이지 덤프/파싱·수집 오류).
BAD_TEXT_PATHS = {"fallback_wholepage", "parse_error", "fetch_error"}
# 대표 이미지로 신뢰도가 높은 출처. article_main은 본문 밖 이미지일 위험이 있어 경고만 남긴다.
TRUSTED_IMAGE_SOURCES = {"fusion", "declared_container", "meta_og"}


def assess_quality(record: dict) -> dict:
    """한 기사 진단 레코드가 '추출 성공'/'이미지 정상'인지 판정한다.

    이 함수가 baseline과 개선 후를 비교하는 판정 기준이다. record는 아래 키를 갖는다:
        publisher, url,
        text_path (fusion/jtbc/declared_selector/heuristic/fallback_wholepage/parse_error),
        content_len (int), boilerplate_count (int), cleanup_reasons (list[str]),
        image_url (str, 빈 문자열이면 미검출),
        image_source (fusion/declared_container/article_main/meta_og/none/parse_error)

    반환: {"extraction_ok": bool, "image_ok": bool, "notes": str}
    """
    notes: list[str] = []

    # 1) 본문 추출 판정: 경로·길이·보일러플레이트를 각각 검사하고, 하나라도 걸리면 실패.
    extraction_ok = True
    text_path = record["text_path"]
    if text_path in BAD_TEXT_PATHS:
        extraction_ok = False
        notes.append(f"bad_path:{text_path}")
    if record["content_len"] < MIN_CONTENT_LEN:
        extraction_ok = False
        notes.append(f"too_short:{record['content_len']}")
    if record["boilerplate_count"] > MAX_BOILERPLATE:
        extraction_ok = False
        notes.append(f"boilerplate_left:{record['boilerplate_count']}")

    # 2) 이미지 판정: 대표 이미지가 있으면 통과하되, 약한 출처는 사유로 표시해 집계 가능하게 한다.
    #    (로고/광고 이미지 오추출은 URL을 눈으로 확인해야 하므로 image_url을 CSV에 남긴다.)
    image_ok = bool(record["image_url"]) and record["image_source"] != "none"
    if not image_ok:
        notes.append("no_image")
    elif record["image_source"] not in TRUSTED_IMAGE_SOURCES:
        notes.append(f"weak_image_source:{record['image_source']}")

    return {
        "extraction_ok": extraction_ok,
        "image_ok": image_ok,
        "notes": "|".join(notes) if notes else "ok",
    }


# --- 수집/크롤링 파이프라인 ---

def fetch_html(url: str, timeout: int = 15) -> str:
    """기사 URL의 원본 HTML을 가져온다(크롤러와 동일한 인코딩 처리)."""
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def group_feeds_by_publisher(feed_urls: list[str]) -> dict[str, list[str]]:
    """피드 URL을 언론사별로 묶는다."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for feed_url in feed_urls:
        publisher, _bias = infer_publisher_metadata(feed_url)
        grouped[publisher].append(feed_url)
    return grouped


def collect_article_links(feeds: list[str], quota: int) -> list[str]:
    """한 언론사의 피드들에서 최근 기사 링크를 quota개까지 모은다(피드 라운드로빈)."""
    per_feed_entries: list[list[str]] = []
    for feed_url in feeds:
        parsed = feedparser.parse(feed_url, agent=USER_AGENT)
        links = [e.get("link") for e in parsed.entries if e.get("link")]
        per_feed_entries.append(links)

    # 카테고리 편중을 막기 위해 피드들에서 번갈아 한 개씩 뽑는다.
    links: list[str] = []
    idx = 0
    while len(links) < quota and any(idx < len(f) for f in per_feed_entries):
        for feed_links in per_feed_entries:
            if idx < len(feed_links):
                links.append(feed_links[idx])
                if len(links) >= quota:
                    break
        idx += 1
    return links


def diagnose(
    feeds_file: str,
    per_publisher: int,
    only_publishers: set[str] | None,
    domain_delay: float,
) -> list[dict]:
    """전체 언론사를 순회하며 진단 레코드 리스트를 만든다."""
    feed_urls = load_feed_urls(feeds_file, None)
    grouped = group_feeds_by_publisher(feed_urls)
    last_hit: dict[str, float] = {}
    records: list[dict] = []

    for publisher, feeds in grouped.items():
        if only_publishers and publisher not in only_publishers:
            continue
        print(f"\n[diagnose] {publisher} -> {len(feeds)} feeds, quota={per_publisher}")
        links = collect_article_links(feeds, per_publisher)
        if not links:
            print(f"[warn] {publisher}: RSS에서 링크를 못 찾음")
            continue

        for url in links:
            host = urlparse(url).netloc
            wait = domain_delay - (time.time() - last_hit.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            last_hit[host] = time.time()

            try:
                html = fetch_html(url)
            except Exception as exc:
                print(f"[warn] fetch 실패: {url} ({exc})")
                records.append(_error_record(publisher, url, f"fetch_error:{exc}"))
                continue

            text = strip_boilerplate_footer(extract_article_text(html, url))
            image_url = extract_article_image_url(html, url)
            _needs_cleanup, cleanup_reasons = should_llm_cleanup(text)
            record = {
                "publisher": publisher,
                "url": url,
                "text_path": detect_text_path(html, url),
                "content_len": len(text),
                "boilerplate_count": count_boilerplate(text),
                "cleanup_reasons": cleanup_reasons,
                "image_url": image_url,
                "image_source": detect_image_source(html, url),
                "content_head": text[:120].replace("\n", " "),
            }
            verdict = assess_quality(record)
            record.update(verdict)
            records.append(record)
            flag = "OK" if verdict["extraction_ok"] else "FAIL"
            img_flag = "img+" if verdict["image_ok"] else "img-"
            print(
                f"  [{flag}/{img_flag}] {record['text_path']:>16} "
                f"len={record['content_len']:>5} bp={record['boilerplate_count']} "
                f"imgsrc={record['image_source']} {url}"
            )
    return records


def _error_record(publisher: str, url: str, note: str) -> dict:
    """크롤링 자체가 실패한 경우의 레코드(품질 판정 없이 실패로 기록)."""
    return {
        "publisher": publisher,
        "url": url,
        "text_path": "fetch_error",
        "content_len": 0,
        "boilerplate_count": 0,
        "cleanup_reasons": [],
        "image_url": "",
        "image_source": "none",
        "content_head": "",
        "extraction_ok": False,
        "image_ok": False,
        "notes": note,
    }


# --- 출력 ---

def write_csv(records: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = [
        "publisher", "url", "text_path", "content_len", "boilerplate_count",
        "cleanup_reasons", "image_url", "image_source", "extraction_ok",
        "image_ok", "notes", "content_head",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["cleanup_reasons"] = "|".join(record.get("cleanup_reasons", []))
            writer.writerow(row)
    print(f"\n[csv] {len(records)} rows -> {path}")


def print_summary(records: list[dict]) -> None:
    """언론사별 성공률/평균 지표 요약표를 stdout에 출력한다."""
    by_pub: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_pub[record["publisher"]].append(record)

    print("\n" + "=" * 78)
    print(f"{'언론사':<10} {'n':>3} {'추출성공':>7} {'이미지':>6} {'평균len':>7} {'평균bp':>6}  주요경로")
    print("-" * 78)
    for publisher in sorted(by_pub):
        rows = by_pub[publisher]
        n = len(rows)
        ok = sum(1 for r in rows if r.get("extraction_ok"))
        img = sum(1 for r in rows if r.get("image_ok"))
        avg_len = sum(r["content_len"] for r in rows) / n
        avg_bp = sum(r["boilerplate_count"] for r in rows) / n
        paths = defaultdict(int)
        for r in rows:
            paths[r["text_path"]] += 1
        top_path = max(paths, key=paths.get)
        print(
            f"{publisher:<10} {n:>3} {ok:>4}/{n:<2} {img:>4}/{n:<1} "
            f"{avg_len:>7.0f} {avg_bp:>6.1f}  {top_path}"
        )
    print("=" * 78)
    total = len(records)
    total_ok = sum(1 for r in records if r.get("extraction_ok"))
    total_img = sum(1 for r in records if r.get("image_ok"))
    print(f"전체: {total}건, 추출성공 {total_ok}/{total}, 이미지정상 {total_img}/{total}")


def main() -> int:
    parser = argparse.ArgumentParser(description="RSS 본문/이미지 추출 품질 진단 (Phase 0)")
    parser.add_argument("--feeds-file", default=os.path.join(_REPO, "config", "feeds.json"))
    parser.add_argument("--per-publisher", type=int, default=3, help="언론사당 표본 기사 수")
    parser.add_argument("--publishers", default="", help="쉼표구분 언론사명(부분집합만 진단)")
    parser.add_argument("--domain-delay", type=float, default=1.5, help="동일 도메인 요청 간 최소 지연(초)")
    parser.add_argument("--output", default="", help="CSV 출력 경로(미지정 시 자동 생성)")
    args = parser.parse_args()

    only = {p.strip() for p in args.publishers.split(",") if p.strip()} or None
    output = args.output or os.path.join(
        _REPO, "data", "diagnostics", f"baseline_{datetime.now():%Y%m%d_%H%M}.csv"
    )

    records = diagnose(args.feeds_file, args.per_publisher, only, args.domain_delay)
    if not records:
        print("[error] 진단 결과가 없습니다.")
        return 1
    print_summary(records)
    write_csv(records, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

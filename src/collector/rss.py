from datetime import datetime
from urllib.parse import urlparse

import feedparser

from .storage import normalize_published, now_iso
from .utils import (
    extract_entry_image_url,
    html_to_text,
    infer_feed_category,
    infer_publisher_metadata,
    resolve_entry_link,
)

# 일부 언론사(예: 한국경제)는 기본 feedparser UA를 403으로 차단하므로 브라우저 UA로 요청한다.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def encode_feed_modified(value) -> str | None:
    """feedparser modified(struct_time)를 DB timestamp에 저장할 ISO 문자열로 변환한다."""
    if not value:
        return None
    try:
        # 운영 feeds.modified_at은 timestamp라 9-tuple을 datetime ISO 문자열로 저장한다.
        return datetime(*value[:6]).isoformat()
    except (TypeError, ValueError):
        return None


def decode_feed_modified(value):
    """DB의 modified_at(Postgres datetime / SQLite ISO 문자열)을 feedparser가 받는 datetime으로 복원한다."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def fetch_feed(conn, feed_url: str, offline: bool) -> int:
    """RSS 피드를 읽어 신규 기사만 저장한다.

    RSS 본문은 언론사마다 품질이 다르므로 본문 처리 기준으로 신뢰하지 않는다.
    링크가 있는 신규 기사는 항상 `needs_crawl` 상태로 저장하고, 크롤링 단계에서
    원문 본문과 기사 이미지를 확정한다. 중복 기사는 GUID를 우선하고, GUID가 없거나
    불안정한 피드는 링크를 대체 키로 사용한다.
    """
    # 이전 수집 시점의 ETag/modified_at을 넘겨 서버가 변경분만 응답할 수 있게 한다.
    row = conn.query_one("SELECT etag, modified_at FROM feeds WHERE url = ?", (feed_url,))
    etag = row["etag"] if row else None
    modified_at = row["modified_at"] if row else None
    modified = decode_feed_modified(modified_at)

    # 오프라인 모드에서는 실수로 외부 HTTP 요청이 나가지 않도록 즉시 건너뛴다.
    if offline and urlparse(feed_url).scheme in {"http", "https"}:
        print(f"[skip] offline mode: {feed_url}")
        return 0

    # feedparser가 URL/로컬 파일을 모두 처리하므로 테스트 피드도 같은 경로로 검증할 수 있다.
    parsed = feedparser.parse(feed_url, etag=etag, modified=modified, agent=USER_AGENT)
    if getattr(parsed, "status", None) == 304:
        conn.execute(
            "UPDATE feeds SET last_checked = ? WHERE url = ?",
            (now_iso(), feed_url),
        )
        return 0
    if getattr(parsed, "bozo", False):
        print(f"[warn] feed parse issue: {feed_url}")

    # 피드 메타데이터는 신규 기사 여부와 무관하게 마지막 확인 시각을 갱신한다.
    parsed_feed_title = getattr(parsed.feed, "title", None)
    category = infer_feed_category(feed_url, parsed_feed_title)
    publisher, bias_type = infer_publisher_metadata(feed_url, parsed_feed_title)
    feed_title = parsed_feed_title or feed_url
    # 피드 메타(etag/modified 포함) 갱신과 기사 INSERT를 한 트랜잭션으로 묶는다.
    # 중간 실패 시 etag만 앞서 갱신돼 다음 실행이 304로 신규 기사를 건너뛰는 유실을 막는다.
    # 루프 안에는 네트워크 호출이 없어 idle-in-transaction 위험은 없다.
    inserted = 0
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO feeds (url, category, publisher, bias_type, title, etag, modified_at, last_checked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                category = excluded.category,
                publisher = excluded.publisher,
                bias_type = excluded.bias_type,
                title = excluded.title,
                etag = excluded.etag,
                modified_at = excluded.modified_at,
                last_checked = excluded.last_checked
            """,
            (
                feed_url,
                category,
                publisher,
                bias_type,
                feed_title,
                getattr(parsed, "etag", None),
                encode_feed_modified(getattr(parsed, "modified", None)),
                now_iso(),
            ),
        )

        for entry in parsed.entries:
            # GUID가 가장 안정적인 식별자이고, 없는 피드는 정규화된 링크를 대체 키로 쓴다.
            guid = entry.get("id") or entry.get("guid")
            link = resolve_entry_link(entry.get("link"), feed_url)
            if not guid and not link:
                continue

            # DB UNIQUE 제약도 있지만, 사전에 건너뛰면 불필요한 예외/롤백 흐름을 피할 수 있다.
            existing = None
            if guid:
                existing = conn.query_one("SELECT id FROM articles WHERE guid = ?", (guid,))
            if not existing and link:
                existing = conn.query_one("SELECT id FROM articles WHERE link = ?", (link,))
            if existing:
                continue

            # RSS 본문이 URL만 담는 피드도 있어 HTML로 보이는 값만 텍스트화한다.
            content_html = ""
            if entry.get("content"):
                content_html = entry.get("content")[0].get("value", "")
            summary_html = entry.get("summary") or entry.get("description") or ""
            content_text = html_to_text(content_html) or html_to_text(summary_html)
            article_image_url = extract_entry_image_url(entry, link or feed_url)

            # RSS content/summary는 신뢰 불가능하므로 링크가 있으면 항상 크롤링한다.
            status = "needs_crawl" if link else "crawl_failed"
            # content_source는 NOT NULL이라 RSS 단계에서는 항상 'rss'로 두고, 크롤 성공 시 'crawl'로 갱신한다.
            content_source = "rss"

            # 원본 HTML 대신 정규화된 텍스트를 저장해 후속 AI 처리 입력을 단순화한다.
            conn.execute(
                """
                INSERT INTO articles (
                    feed_url, guid, link, category, title, publisher, bias_type, published_at, summary, content,
                    article_image_url, content_source, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feed_url,
                    guid,
                    link,
                    category,
                    entry.get("title") or "(제목 없음)",
                    publisher,
                    bias_type,
                    # published_at은 NOT NULL이라 RSS에 날짜가 없으면 수집 시각으로 대체한다.
                    normalize_published(entry.get("published") or entry.get("updated")) or now_iso(),
                    html_to_text(summary_html),
                    content_text,
                    article_image_url or None,
                    content_source,
                    status,
                    now_iso(),
                    now_iso(),
                ),
            )
            inserted += 1

    return inserted

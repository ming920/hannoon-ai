import time
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import requests

from .content_cleaner import should_llm_cleanup, strip_boilerplate_footer
from .settings import DEFAULT_CRAWL_BATCH_SIZE, USER_AGENT
from .storage import enqueue_article_job, now_iso
from .utils import extract_article_data


def fetch_url_article_data(url: str, offline: bool) -> tuple[str | None, str | None]:
    """기사 URL에서 본문 텍스트를 추출한다.

    HTTP/HTTPS URL은 requests로 가져오고, file:// URL은 로컬 HTML 파일을 읽는다.
    로컬 파일 지원은 네트워크 없이도 샘플 RSS와 샘플 HTML로 회귀 테스트를 할 수
    있게 하기 위한 장치다.
    """
    parsed = urlparse(url)
    if parsed.scheme == "file":
        local_path = url2pathname(unquote(parsed.path))
        with open(local_path, "r", encoding="utf-8") as handle:
            html = handle.read()
        return extract_article_data(html, url)

    if offline:
        print(f"[skip] offline mode: {url}")
        return None, None

    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    # 국내 언론사는 응답 헤더의 charset이 비어 있거나 부정확한 경우가 있어 requests 추정값을 우선한다.
    response.encoding = response.apparent_encoding or response.encoding
    return extract_article_data(response.text, url)


def _load_crawl_batch(conn, crawl_batch_size: int) -> list:
    return conn.query(
        """
        SELECT id, link, article_image_url
        FROM articles
        WHERE status = 'needs_crawl' AND link IS NOT NULL
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (crawl_batch_size,),
    )


def _mark_crawl_failed(conn, article_id: int) -> None:
    conn.execute(
        "UPDATE articles SET status = ?, updated_at = ? WHERE id = ?",
        ("crawl_failed", now_iso(), article_id),
    )


def crawl_articles(
    conn,
    min_crawl_len: int,
    offline: bool,
    domain_delay: float,
    crawl_batch_size: int = DEFAULT_CRAWL_BATCH_SIZE,
    llm_cleanup: bool = False,
    llm_cleanup_model: str | None = None,
    text_cleaner=None,
) -> int:
    """`needs_crawl` 상태의 기사 원문을 가져와 DB에 반영한다.

    크롤링 결과가 `min_crawl_len`보다 길면 실제 본문을 확보했다고 보고
    `ready`로 바꾼다. 너무 짧은 텍스트는 제목/캡션/광고 영역만 잡혔을 가능성이
    높으므로 `crawl_failed`로 남긴다. 개별 기사 실패는 전체 배치를 중단하지 않고
    다음 기사로 넘어간다.
    """
    if crawl_batch_size <= 0:
        raise ValueError("crawl_batch_size must be greater than 0.")

    cleaner = text_cleaner
    external_cleaner = text_cleaner is not None
    last_hit: dict[str, float] = {}
    updated = 0
    batch_no = 0

    print(
        "[crawl] start "
        f"(batch_size={crawl_batch_size}, min_crawl_len={min_crawl_len}, "
        f"offline={offline}, llm_cleanup={llm_cleanup or cleaner is not None})"
    )

    while True:
        rows = _load_crawl_batch(conn, crawl_batch_size)
        if not rows:
            break

        batch_no += 1
        batch_ready = 0
        batch_failed = 0
        batch_skipped = 0
        finished_in_batch = 0
        print(f"[crawl-batch] {batch_no} -> loaded {len(rows)} pending items")

        for row in rows:
            article_id, link = row["id"], row["link"]
            fallback_image_url = row["article_image_url"]
            parsed = urlparse(link)
            skipped_offline_http = offline and parsed.scheme in {"http", "https"}
            if skipped_offline_http:
                print(f"[skip] crawl offline: article={article_id} link={link}")
                batch_skipped += 1
                continue

            if parsed.scheme in {"http", "https"}:
                host = parsed.netloc
                last_time = last_hit.get(host, 0)
                wait = domain_delay - (time.time() - last_time)
                if wait > 0:
                    time.sleep(wait)
                last_hit[host] = time.time()

            try:
                text, crawled_image_url = fetch_url_article_data(link, offline=offline)
            except Exception as exc:
                print(f"[warn] crawl failed: article={article_id} link={link} ({exc})")
                _mark_crawl_failed(conn, article_id)
                batch_failed += 1
                finished_in_batch += 1
                continue

            # 저작권/제보 푸터를 먼저 제거해 LLM 정제 판단과 길이 검증이 본문 기준으로 이뤄지게 한다.
            if text:
                text = strip_boilerplate_footer(text)

            if text and (llm_cleanup or external_cleaner):
                needs_cleanup, cleanup_reasons = should_llm_cleanup(text)
                if external_cleaner or needs_cleanup:
                    reason_text = "; ".join(cleanup_reasons) if needs_cleanup else "external_cleaner"
                    print(f"[cleanup] article={article_id} link={link} -> using LLM ({reason_text})")
                    try:
                        if cleaner is None:
                            from .content_cleaner import ArticleTextCleaner

                            cleaner = ArticleTextCleaner(model=llm_cleanup_model)
                        text = cleaner.clean(text)
                        print(f"[cleanup] article={article_id} link={link} -> done ({len(text)} chars)")
                    except Exception as exc:
                        print(f"[warn] cleanup failed: article={article_id} link={link} ({exc})")
                        _mark_crawl_failed(conn, article_id)
                        batch_failed += 1
                        finished_in_batch += 1
                        continue

            if text and len(text) >= min_crawl_len:
                article_image_url = crawled_image_url or fallback_image_url
                with conn.transaction():
                    conn.execute(
                        """
                        UPDATE articles
                        SET content = ?, article_image_url = ?, content_source = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (text, article_image_url, "crawl", "ready", now_iso(), article_id),
                    )
                    enqueue_article_job(conn, article_id)
                image_state = "image=yes" if article_image_url else "image=no"
                print(f"[crawl] article={article_id} link={link} -> ready ({len(text)} chars, {image_state})")
                updated += 1
                batch_ready += 1
                finished_in_batch += 1
            else:
                text_len = len(text) if text else 0
                print(
                    "[warn] crawl failed: "
                    f"article={article_id} link={link} "
                    f"(content too short: {text_len} chars)"
                )
                _mark_crawl_failed(conn, article_id)
                batch_failed += 1
                finished_in_batch += 1

        print(
            f"[crawl-batch] {batch_no} -> "
            f"ready={batch_ready}, failed={batch_failed}, "
            f"skipped={batch_skipped}, total_ready={updated}"
        )

        if finished_in_batch == 0:
            break

    if batch_no == 0:
        print("[crawl] no articles need crawling")
    else:
        print(f"[crawl] completed -> ready={updated}, batches={batch_no}")

    return updated


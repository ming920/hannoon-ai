import json
import os
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import url2pathname

from bs4 import BeautifulSoup


CATEGORY_PATTERNS = {
    "politics": [
        "politics",
        "politic",
        "polrss",
        "정치",
        "segye_politic",
        "sectionid=01",
        "section/65",
    ],
    "economy": [
        "economy",
        "economic",
        "eco",
        "경제",
        "business",
        "30100041",
        "30200030",
        "50200011",
        "sectionid=02",
        "section/66",
        "eto/economy",
    ],
    "society": [
        "society",
        "social",
        "national",
        "soc",
        "사회",
        "50400012",
        "30300018",
        "50100032",
        "sectionid=03",
        "section/67",
        "industry",
        "산업",
        "eto/industry",
    ],
    "international": [
        "international",
        "world",
        "global",
        "kh_world",
        "intrss",
        "국제",
        "세계",
        "sectionid=07",
        "section/68",
        "eto/global",
        "northkorea",
        "north-korea",
        "북한"
    ]
}


PUBLISHER_PATTERNS = [
    ("조선일보", "right", ["chosun.com"]),
    ("연합뉴스", "mid", ["yna.co.kr"]),
    ("연합뉴스TV", "mid", ["yonhapnewstv.co.kr"]),
    ("한국경제", "right", ["hankyung.com"]),
    ("서울신문", "mid", ["seoul.co.kr"]),
    ("오마이뉴스", "left", ["ohmynews.com"]),
    ("매일경제", "right", ["mk.co.kr"]),
    ("경향신문", "left", ["khan.co.kr"]),
    ("국민일보", "right", ["kmib.co.kr"]),
    ("뉴시스", "mid", ["newsis.com"]),
    ("동아일보", "right", ["donga.com"]),
    ("세계일보", "right", ["segye.com"]),
    ("프레시안", "left", ["pressian.com"]),
    ("한겨레", "left", ["hani.co.kr"]),
    ("JTBC", "left", ["jtbc.co.kr"]),
    ("SBS", "mid", ["sbs.co.kr"]),
    ("이투데이", "mid", ["etoday.co.kr"]),
    ("MBN", "right", ["mbn.co.kr"]),
]


# 운영 DB category enum 라벨(정치/경제/사회/국제)에 맞춘 매핑.
CATEGORY_LABELS = {
    "politics": "정치",
    "economy": "경제",
    "society": "사회",
    "international": "국제",
}


def infer_feed_category(feed_url: str, feed_title: str | None = None) -> str:
    """피드 URL과 제목을 기반으로 기사 카테고리(category enum 라벨)를 추론한다."""
    target = f"{feed_url} {feed_title or ''}".lower()
    category_order = [
        "politics",
        "economy",
        "international",
        "society"
    ]
    for category in category_order:
        patterns = CATEGORY_PATTERNS[category]
        if any(pattern.lower() in target for pattern in patterns):
            return CATEGORY_LABELS[category]
    # category enum에는 'other' 슬롯이 없으므로 매칭 실패 시 '사회'로 기본 매핑한다.
    return "사회"


# 운영 DB bias_type enum 라벨(진보/중도/보수)에 맞춘 매핑.
BIAS_LABELS = {
    "left": "진보",
    "mid": "중도",
    "right": "보수",
}


def infer_publisher_metadata(feed_url: str, feed_title: str | None = None) -> tuple[str, str]:
    """피드 URL과 제목으로 언론사 이름과 언론사 기준 성향(bias_type enum 라벨)을 추론한다.

    `bias_type`은 개별 기사 논조 분석 결과가 아니라 언론사 단위 하드코딩 매핑이다.
    PUBLISHER_PATTERNS의 left/mid/right를 운영 enum 라벨 진보/중도/보수로 변환해 반환한다.
    """
    target = f"{feed_url} {feed_title or ''}".lower()
    for publisher, bias_type, patterns in PUBLISHER_PATTERNS:
        if any(pattern.lower() in target for pattern in patterns):
            return publisher, BIAS_LABELS[bias_type]
    return "기타", "중도"


def _looks_like_url(value: str) -> bool:
    """값이 본문 HTML이 아니라 URL인지 확인한다."""
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"}


UNWANTED_IMAGE_TOKENS = {
    "ad",
    "ads",
    "advert",
    "avatar",
    "banner",
    "blank",
    "btn",
    "button",
    "default",
    "facebook",
    "icon",
    "kakao",
    "logo",
    "pixel",
    "profile",
    "share",
    "sns",
    "spacer",
    "sprite",
    "twitter",
}


def _image_url_from_srcset(value: str | None) -> str:
    """srcset 후보 중 가장 앞의 URL을 대표 이미지 후보로 사용한다."""
    if not value:
        return ""
    for candidate in value.split(","):
        url = candidate.strip().split(" ")[0].strip()
        if url:
            return url
    return ""


def _normalize_image_url(value: str | None, base_url: str | None = None) -> str:
    """상대 경로/프로토콜 생략 이미지 URL을 저장 가능한 절대 URL로 정규화한다."""
    if not value:
        return ""
    value = value.strip()
    if not value or value.startswith(("data:", "blob:", "javascript:")):
        return ""
    if value.startswith("//"):
        scheme = urlparse(base_url or "").scheme
        if scheme not in {"http", "https"}:
            scheme = "https"
        value = f"{scheme}:{value}"
    if base_url:
        value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "file"}:
        return ""
    return value


def _is_unwanted_image_url(url: str) -> bool:
    """로고, 아이콘, 광고처럼 기사 대표 이미지로 부적절한 URL을 걸러낸다."""
    parsed = urlparse(url)
    target = f"{parsed.netloc}/{parsed.path}".lower().replace("_", "-").replace(".", "-")
    tokens = {part.strip() for chunk in target.split("/") for part in chunk.split("-") if part.strip()}
    return bool(tokens & UNWANTED_IMAGE_TOKENS)


def _small_image_by_attrs(img) -> bool:
    """명시된 크기가 너무 작으면 아이콘/버튼일 가능성이 높아 제외한다."""
    def parse_int(value) -> int | None:
        if value is None:
            return None
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            return None
        return int(digits)

    width = parse_int(img.get("width"))
    height = parse_int(img.get("height"))
    if width is None or height is None:
        return False
    return width < 120 or height < 80


def _image_url_from_img(img, base_url: str | None = None) -> str:
    """img 태그의 여러 lazy-load 속성에서 실제 이미지 URL을 찾는다."""
    if img is None or _small_image_by_attrs(img):
        return ""
    raw_url = (
        img.get("src")
        or img.get("data-src")
        or img.get("data-original")
        or img.get("data-lazy-src")
        or img.get("data-url")
        or _image_url_from_srcset(img.get("srcset"))
        or _image_url_from_srcset(img.get("data-srcset"))
    )
    image_url = _normalize_image_url(raw_url, base_url)
    if image_url and not _is_unwanted_image_url(image_url):
        return image_url
    return ""


def _first_image_in_node(node, base_url: str | None = None) -> str:
    """본문 컨테이너 안에서 첫 번째 유효 이미지를 찾는다."""
    if node is None:
        return ""
    for img in node.find_all("img"):
        image_url = _image_url_from_img(img, base_url)
        if image_url:
            return image_url
    return ""


def _extract_meta_image(soup: BeautifulSoup, base_url: str | None = None) -> str:
    """본문 이미지가 없을 때 사용할 OpenGraph/Twitter 대표 이미지를 찾는다."""
    selectors = [
        'meta[property="og:image"]',
        'meta[property="og:image:url"]',
        'meta[name="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
        'meta[name="twitter:image:src"]',
        'link[rel="image_src"]',
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node is None:
            continue
        image_url = _normalize_image_url(node.get("content") or node.get("href"), base_url)
        if image_url and not _is_unwanted_image_url(image_url):
            return image_url
    return ""


def extract_entry_image_url(entry, base_url: str | None = None) -> str:
    """RSS entry에 선언된 이미지 URL을 fallback 후보로 추출한다."""
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            if not isinstance(media, dict):
                continue
            image_url = _normalize_image_url(media.get("url") or media.get("href"), base_url)
            if image_url and not _is_unwanted_image_url(image_url):
                return image_url

    for enclosure in entry.get("enclosures", []) or []:
        if not isinstance(enclosure, dict):
            continue
        media_type = (enclosure.get("type") or "").lower()
        if media_type and not media_type.startswith("image/"):
            continue
        image_url = _normalize_image_url(enclosure.get("url") or enclosure.get("href"), base_url)
        if image_url and not _is_unwanted_image_url(image_url):
            return image_url

    for link in entry.get("links", []) or []:
        if not isinstance(link, dict):
            continue
        media_type = (link.get("type") or "").lower()
        rel = (link.get("rel") or "").lower()
        if media_type.startswith("image/") or rel in {"enclosure", "image"}:
            image_url = _normalize_image_url(link.get("href") or link.get("url"), base_url)
            if image_url and not _is_unwanted_image_url(image_url):
                return image_url

    html_parts: list[str] = []
    if entry.get("content"):
        for content in entry.get("content") or []:
            if isinstance(content, dict):
                html_parts.append(content.get("value") or "")
    html_parts.append(entry.get("summary") or entry.get("description") or "")
    for html in html_parts:
        if not html or "<img" not in html.lower():
            continue
        soup = BeautifulSoup(html, "html.parser")
        image_url = _first_image_in_node(soup, base_url)
        if image_url:
            return image_url
    return ""


def html_to_text(html: str | None) -> str:
    """RSS summary/content HTML을 후속 처리용 plain text로 정규화한다."""
    if not html:
        return ""
    if _looks_like_url(html):
        # 일부 RSS 필드는 본문 대신 대표 이미지/기사 URL만 넣어 오므로 본문으로 취급하지 않는다.
        return ""
    if "<" not in html and ">" not in html:
        return " ".join(html.split())
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return " ".join(text.split())


def _strip_unwanted(soup: BeautifulSoup) -> None:
    """본문 후보 탐색 전에 명백한 비본문 영역을 제거한다."""
    # 본문 후보 점수를 왜곡하는 스크립트/내비게이션/광고성 영역을 먼저 제거한다.
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "video"]):
        tag.decompose()
    for tag in soup.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    for tag in soup.find_all(True):
        # decompose된 태그는 attrs가 None이 될 수 있어 재접근 전에 방어한다.
        if tag is None or not hasattr(tag, "get") or tag.attrs is None:
            continue
        classes = " ".join(tag.get("class") or []).lower()
        ident = (tag.get("id") or "").lower()
        hint = f"{classes} {ident}"
        tokens = {part for chunk in hint.replace("_", "-").split() for part in chunk.split("-")}
        unwanted_tokens = {
            "nav",
            "footer",
            "header",
            "sidebar",
            "comment",
            "related",
            "share",
            "ad",
            "ads",
            "banner",
            "player",
            "vod",
        }
        if tokens.intersection(unwanted_tokens):
            tag.decompose()


def _score_text(text: str) -> int:
    """본문 후보의 점수를 계산한다.

    긴 텍스트와 단어 수가 많은 영역이 실제 기사 본문일 가능성이 높다는 단순한
    휴리스틱을 사용한다.
    """
    words = text.split()
    return len(text) + (len(words) * 5)


def _collect_content_element_text(elements: list[dict]) -> list[str]:
    """Arc/Fusion CMS의 중첩 content_elements에서 텍스트 노드만 모은다."""
    # Arc/Fusion 계열 CMS는 본문을 중첩 content_elements JSON으로 제공한다.
    texts: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        content = element.get("content")
        if element.get("type") == "text" and content:
            text = html_to_text(content)
            if text:
                texts.append(text)
        nested = element.get("content_elements")
        if isinstance(nested, list):
            texts.extend(_collect_content_element_text(nested))
    return texts


def _extract_fusion_global_content(html: str) -> str:
    """조선일보 등 Arc/Fusion 기반 페이지에서 본문 JSON을 추출한다."""
    # 조선일보 페이지는 렌더링된 DOM보다 Fusion.globalContent에 본문이 더 안정적으로 들어 있다.
    marker = "Fusion.globalContent="
    start = html.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = html.find(";Fusion.globalContentConfig=", start)
    if end < 0:
        return ""

    try:
        data = json.loads(html[start:end])
    except json.JSONDecodeError:
        return ""

    elements = data.get("content_elements")
    if not isinstance(elements, list):
        return ""
    return " ".join(_collect_content_element_text(elements))


def _extract_jtbc_query_content(html: str) -> str:
    """JTBC Next.js 페이지의 hydration 스크립트에서 articleContent를 추출한다."""
    # JTBC는 Next.js hydration 스크립트의 React Query 캐시에 articleContent를 넣는다.
    marker = '"articleContent":"'
    start = html.find(marker)
    if start < 0:
        return ""
    start += len(marker)

    chars: list[str] = []
    escaped = False
    for char in html[start:]:
        if escaped:
            chars.append("\\" + char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            break
        chars.append(char)

    try:
        content_html = json.loads(f'"{"".join(chars)}"')
    except json.JSONDecodeError:
        return ""
    return html_to_text(content_html)


ARTICLE_BODY_SELECTORS = [
    '[itemprop="articleBody"]',
    "#newsViewArea",
    "#articleBody",
    ".article_body",
    ".article-body",
    ".article-content",
    ".news_body",
    ".news-body",
]


def _extract_declared_article_body(soup: BeautifulSoup) -> str:
    """명시적인 본문 컨테이너가 있으면 범용 점수화보다 먼저 사용한다."""
    for selector in ARTICLE_BODY_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        fragment = BeautifulSoup(str(node), "html.parser")
        _strip_unwanted(fragment)
        text = " ".join(fragment.get_text(" ", strip=True).split())
        if len(text) >= 200:
            return text
    return ""


def _image_from_fusion_element(element: dict, base_url: str | None = None) -> str:
    """Arc/Fusion CMS content element에서 이미지 URL 후보를 찾는다."""
    for key in ("url", "src", "image_url", "canonical_url"):
        image_url = _normalize_image_url(element.get(key), base_url)
        if image_url and not _is_unwanted_image_url(image_url):
            return image_url

    additional = element.get("additional_properties")
    if isinstance(additional, dict):
        for key in ("originalUrl", "fullSizeResizeUrl", "thumbnailResizeUrl"):
            image_url = _normalize_image_url(additional.get(key), base_url)
            if image_url and not _is_unwanted_image_url(image_url):
                return image_url

    nested = element.get("content_elements")
    if isinstance(nested, list):
        return _collect_content_element_image(nested, base_url)
    return ""


def _collect_content_element_image(elements: list[dict], base_url: str | None = None) -> str:
    """중첩된 Fusion content_elements를 순회하며 첫 이미지 후보를 찾는다."""
    for element in elements:
        if not isinstance(element, dict):
            continue
        image_url = _image_from_fusion_element(element, base_url)
        if image_url:
            return image_url
    return ""


def _extract_fusion_global_image(html: str, base_url: str | None = None) -> str:
    """조선일보 등 Fusion.globalContent 기반 페이지에서 대표 이미지를 추출한다."""
    marker = "Fusion.globalContent="
    start = html.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = html.find(";Fusion.globalContentConfig=", start)
    if end < 0:
        return ""

    try:
        data = json.loads(html[start:end])
    except json.JSONDecodeError:
        return ""

    promo_items = data.get("promo_items")
    if isinstance(promo_items, dict):
        for promo in promo_items.values():
            if isinstance(promo, dict):
                image_url = _image_from_fusion_element(promo, base_url)
                if image_url:
                    return image_url

    elements = data.get("content_elements")
    if isinstance(elements, list):
        return _collect_content_element_image(elements, base_url)
    return ""


def _extract_declared_article_image(soup: BeautifulSoup, base_url: str | None = None) -> str:
    """명시적인 기사 본문 컨테이너 안의 이미지를 최우선 후보로 사용한다."""
    for selector in ARTICLE_BODY_SELECTORS:
        node = soup.select_one(selector)
        image_url = _first_image_in_node(node, base_url)
        if image_url:
            return image_url
    return ""


def extract_article_image_url(html: str, page_url: str | None = None) -> str:
    """크롤링한 HTML에서 기사 이미지 URL을 추출한다."""
    fusion_image = _extract_fusion_global_image(html, page_url)
    if fusion_image:
        return fusion_image

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""

    declared_image = _extract_declared_article_image(soup, page_url)
    if declared_image:
        return declared_image

    for selector in ["article", "main"]:
        for node in soup.find_all(selector):
            image_url = _first_image_in_node(node, page_url)
            if image_url:
                return image_url

    meta_image = _extract_meta_image(soup, page_url)
    if meta_image:
        return meta_image
    return ""


def extract_article_data(html: str, page_url: str | None = None) -> tuple[str, str]:
    """크롤러 호출부가 같은 HTML 응답에서 본문과 이미지 URL을 함께 받게 하는 래퍼다."""
    return extract_article_text(html), extract_article_image_url(html, page_url)


def extract_article_text(html: str) -> str:
    """HTML에서 기사 본문 텍스트를 추출한다.

    우선 사이트별로 안정적인 JSON 데이터 구조를 시도한다. 사이트별 구조가 없으면
    article/main/section/div 후보 중 가장 본문에 가까운 영역을 점수화해 선택한다.
    """
    # 사이트 전용 구조를 먼저 시도하고, 실패하면 범용 DOM 휴리스틱으로 떨어진다.
    fusion_text = _extract_fusion_global_content(html)
    if fusion_text:
        return fusion_text
    jtbc_text = _extract_jtbc_query_content(html)
    if jtbc_text:
        return jtbc_text

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""
    declared_text = _extract_declared_article_body(soup)
    if declared_text:
        return declared_text

    _strip_unwanted(soup)

    candidates = []
    for selector in ["article", "main", "section", "div"]:
        candidates.extend(soup.find_all(selector))

    best_text = ""
    best_score = 0
    for node in candidates:
        if node is None:
            continue
        text = node.get_text(" ", strip=True)
        text = " ".join(text.split())
        if len(text) < 200:
            continue
        score = _score_text(text)
        if score > best_score:
            best_text = text
            best_score = score

    if not best_text:
        # 적절한 본문 컨테이너를 못 찾은 경우에도 빈 값 대신 페이지 전체 텍스트를 반환한다.
        container = soup.body or soup
        best_text = " ".join(container.get_text(" ", strip=True).split())

    return best_text


def load_feed_urls(feeds_file: str | None, feeds: list[str] | None) -> list[str]:
    """설정 파일과 CLI 옵션에서 RSS 피드 URL을 읽어 중복 제거 후 반환한다."""
    urls: list[str] = []
    if feeds:
        urls.extend(feeds)
    if feeds_file and os.path.exists(feeds_file):
        # Windows 편집기/PowerShell이 붙이는 UTF-8 BOM도 허용한다.
        with open(feeds_file, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            urls.extend(data)
        elif isinstance(data, dict) and "feeds" in data:
            urls.extend(data["feeds"])
    # 순서를 유지한 채 중복 피드를 제거한다.
    return list(dict.fromkeys(urls))


def resolve_entry_link(link: str | None, feed_source: str) -> str | None:
    """RSS entry link를 크롤러가 처리할 수 있는 절대 URL로 정규화한다."""
    if not link:
        return None
    parsed = urlparse(link)
    if parsed.scheme:
        return link
    if os.path.exists(feed_source):
        # 로컬 RSS 샘플의 상대 링크는 같은 폴더 기준 file:// URL로 바꿔 크롤러가 처리하게 한다.
        base_dir = os.path.dirname(os.path.abspath(feed_source))
        local_path = os.path.join(base_dir, link)
        return Path(local_path).resolve().as_uri()
    parsed_feed = urlparse(feed_source)
    if parsed_feed.scheme == "file":
        # file:// URI는 OS별 로컬 경로 규칙에 맞게 변환해야 한다.
        feed_path = url2pathname(unquote(parsed_feed.path))
        base_dir = os.path.dirname(feed_path)
        local_path = os.path.join(base_dir, link)
        return Path(local_path).resolve().as_uri()
    return urljoin(feed_source, link)

"""언론사 전용 본문 셀렉터(PUBLISHER_ARTICLE_SELECTORS) 회귀 테스트.

baseline 진단에서 heuristic으로 추락하던 연합(.story-news.article)·서울(#articleContent)이
등록 도메인에서 전용 셀렉터로 본문을 잡고, 미등록 도메인은 기존 로직을 그대로 쓰는지 검증한다.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from collector.utils import extract_article_text  # noqa: E402

_BODY = "실제 기사 본문 문장입니다. " * 20  # 200자 이상


class PublisherSelectorTests(unittest.TestCase):
    def test_yna_story_news_selector(self):
        html = (
            "<html><body><div class='header'>메뉴 구독 로그인</div>"
            f"<div class='story-news article'><p>{_BODY}</p></div>"
            "<div class='footer'>저작권자 연합뉴스</div></body></html>"
        )
        text = extract_article_text(html, "https://www.yna.co.kr/view/AKR123")
        self.assertIn("실제 기사 본문 문장", text)
        self.assertNotIn("메뉴 구독 로그인", text)

    def test_seoul_article_content_selector(self):
        html = (
            "<html><body><div class='viewLeftSide'>글씨 크기 조절 닫기</div>"
            f"<div id='articleContent'>{_BODY}</div></body></html>"
        )
        text = extract_article_text(html, "https://www.seoul.co.kr/news/newsView.php?id=1")
        self.assertIn("실제 기사 본문 문장", text)

    def test_unregistered_domain_falls_back_to_generic(self):
        # 미등록 도메인은 전용 셀렉터를 타지 않고 기존 article 휴리스틱으로 처리된다.
        html = f"<html><body><article>{_BODY}</article></body></html>"
        text = extract_article_text(html, "https://example.com/news/1")
        self.assertIn("실제 기사 본문 문장", text)

    def test_no_page_url_is_backward_compatible(self):
        # page_url 미전달 시에도(기존 호출부) 정상 동작해야 한다.
        html = f"<html><body><article>{_BODY}</article></body></html>"
        self.assertIn("실제 기사 본문 문장", extract_article_text(html))


if __name__ == "__main__":
    unittest.main()

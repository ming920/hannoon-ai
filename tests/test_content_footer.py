"""content_cleaner.strip_boilerplate_footer 회귀 테스트.

baseline 진단에서 관찰된 언론사 저작권/제보 푸터가 잘리고, 본문 중간의 우연한
마커 언급(예: "…페이스북을 통해")은 보존되는지 검증한다.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from collector.content_cleaner import strip_boilerplate_footer  # noqa: E402


# 60% 이후 구간에서만 마커를 탐색하므로 본문이 충분히 길어야 한다.
BODY = "이것은 실제 기사 본문 문장입니다. " * 8


class StripBoilerplateFooterTests(unittest.TestCase):
    def test_yonhap_footer_removed(self):
        text = BODY + "제보는 카카오톡 okjebo <저작권자(c) 연합뉴스, 무단 전재-재배포 금지> 2026/07/01 송고"
        cleaned = strip_boilerplate_footer(text)
        self.assertNotIn("제보는", cleaned)
        self.assertNotIn("저작권자", cleaned)
        self.assertTrue(cleaned.endswith("문장입니다."))

    def test_newsis_footer_removed_but_body_facebook_kept(self):
        text = "의원은 페이스북을 통해 밝혔다. " + BODY + "◎공감언론 뉴시스 test@x.com Copyright © NEWSIS.COM, 무단 전재 및 재배포 금지"
        cleaned = strip_boilerplate_footer(text)
        # 본문 중간의 '페이스북'은 보존, 꼬리의 저작권 블록은 제거.
        self.assertIn("페이스북", cleaned)
        self.assertNotIn("Copyright", cleaned)
        self.assertNotIn("◎공감언론", cleaned)

    def test_segye_and_seoul_copyright_removed(self):
        segye = BODY + "박수찬 기자 기자페이지 바로가기 Copyright ⓒ 세계일보. 무단 전재 및 재배포 금지"
        seoul = BODY + "Copyright ⓒ 서울신문 All rights reserved. 무단 전재-재배포"
        self.assertNotIn("Copyright", strip_boilerplate_footer(segye))
        self.assertNotIn("rights reserved", strip_boilerplate_footer(seoul).lower())

    def test_no_footer_is_unchanged(self):
        self.assertEqual(strip_boilerplate_footer(BODY), BODY)

    def test_empty_input(self):
        self.assertEqual(strip_boilerplate_footer(""), "")

    def test_marker_only_body_not_over_trimmed(self):
        # 마커가 본문 앞쪽이라 자르면 본문이 거의 사라지는 경우 원본을 유지한다.
        text = "저작권자 관련 짧은 언급." + " 끝."
        self.assertEqual(strip_boilerplate_footer(text), text)

    def test_copyright_topic_body_marker_without_footer_kept(self):
        # 본문 후반에 '무단 전재'가 정상 언급되지만 실제 푸터(저작권자(ⓒ 등)가 없으면 보존.
        text = BODY + "법원은 저작물의 무단 전재 여부가 이 사건의 핵심 쟁점이라고 밝혔다."
        self.assertEqual(strip_boilerplate_footer(text), text)

    def test_earliest_footer_marker_removed_when_markers_adjacent(self):
        # '제보는' 뒤에 '저작권자('가 이어지는 연합 패턴에서 앞 마커까지 포함해 잘린다.
        text = BODY + "제보는 카카오톡 okjebo 채널 안내입니다 <저작권자(c) 연합뉴스, 무단 전재-재배포 금지>"
        cleaned = strip_boilerplate_footer(text)
        self.assertNotIn("제보는", cleaned)
        self.assertNotIn("저작권자", cleaned)
        self.assertTrue(cleaned.endswith("문장입니다."))

    def test_bare_copyright_symbol_in_body_kept(self):
        # 본문 후반의 맨몸 ⓒ/copyright 언급(언론사명 미동반)은 푸터가 아니므로 보존.
        self.assertEqual(
            strip_boilerplate_footer(BODY + "이 작품에는 ⓒ 표시가 붙어 있었다고 그는 설명했다."),
            BODY + "이 작품에는 ⓒ 표시가 붙어 있었다고 그는 설명했다.",
        )
        self.assertEqual(
            strip_boilerplate_footer(BODY + "해당 조항은 copyright 원칙을 다룬다고 명시했다."),
            BODY + "해당 조항은 copyright 원칙을 다룬다고 명시했다.",
        )

    def test_copyright_symbol_with_publisher_is_footer(self):
        # 기호 뒤에 언론사명이 오면 실제 저작권 푸터로 보고 절단.
        self.assertNotIn("세계일보", strip_boilerplate_footer(BODY + "ⓒ 세계일보. 무단 전재 및 재배포 금지"))
        self.assertNotIn("Copyright", strip_boilerplate_footer(BODY + "Copyright © NEWSIS.COM"))


if __name__ == "__main__":
    unittest.main()

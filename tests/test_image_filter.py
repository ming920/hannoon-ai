"""_is_unwanted_image_url 회귀 테스트.

baseline 진단에서 오추출된 로고/아이콘/기자사진은 배제하되, 슬러그에 우연히
같은 단어(white-house, google-io, ico-regulation, writer-column)가 든 정상 뉴스
사진은 보존되는지 검증한다(코드 리뷰 MAJOR 대응).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from collector.utils import _is_unwanted_image_url  # noqa: E402


class IsUnwantedImageUrlTests(unittest.TestCase):
    def test_icon_extensions_rejected(self):
        self.assertTrue(_is_unwanted_image_url("https://www.hani.co.kr/svg/icons/google.svg"))
        self.assertTrue(_is_unwanted_image_url("https://news.example.com/favicon.ico"))

    def test_placeholder_filenames_rejected(self):
        self.assertTrue(_is_unwanted_image_url("https://img.mbn.co.kr/newmbn/white.PNG"))
        self.assertTrue(_is_unwanted_image_url("https://cdn.example.com/a/blank.png"))
        self.assertTrue(_is_unwanted_image_url("https://cdn.example.com/a/white_01.jpg"))

    def test_reporter_and_writer_dirs_rejected(self):
        self.assertTrue(_is_unwanted_image_url("https://img0.yna.co.kr/reporter/39_185147.jpg"))
        self.assertTrue(_is_unwanted_image_url("https://img.seoul.co.kr/img/n24/writer/s_2024025.png.webp"))

    def test_structural_icon_tokens_still_rejected(self):
        self.assertTrue(_is_unwanted_image_url("https://static.mk.co.kr/css/images/ic_myagent_jb.png"))
        self.assertTrue(_is_unwanted_image_url("https://www.mbn.co.kr/player/videojs/png/ic_caution.png"))
        self.assertTrue(_is_unwanted_image_url("https://cdn.example.com/common/logo.png"))

    def test_legit_news_photos_kept(self):
        # 슬러그에 white/google/ico/writer가 들어도 실제 기사 사진은 보존해야 한다.
        for url in [
            "https://cdn.example.com/2024/white-house-summit.jpg",
            "https://cdn.example.com/tech/google-io-keynote-photo.jpg",
            "https://cdn.example.com/crypto/ico-regulation-bitcoin.jpg",
            "https://cdn.example.com/opinion/writer-column-main.jpg",
            "https://img1.newsis.com/2026/07/01/NISI20260701_0021346255_web.jpg",
            "https://img.hankyung.com/photo/202607/01.44873100.1.jpg",
        ]:
            self.assertFalse(_is_unwanted_image_url(url), url)


if __name__ == "__main__":
    unittest.main()

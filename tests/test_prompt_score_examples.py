"""판단 프롬프트의 score 예시가 리터럴 숫자를 노출하지 않는지 검증하는 회귀 테스트.

JSON 예시에 `"score": 0.93` 처럼 구체적인 숫자를 박아두면 모델이 그 값을 그대로 베낀다.
로컬 시운전(기사 722건)에서 실제로 이 일이 벌어졌다:

    후보가 있었던 이벤트 결정 680건의 점수 분포 → 0.93: 539건 / 0.35: 141건
    서로 다른 값의 개수: 2개 (= 예시에 박힌 두 값), 100%

점수가 상수가 되면 `ASSIGN_SCORE_THRESHOLD` 비교가 항상 같은 결과를 내므로 가드레일의
점수 축이 통째로 죽는다. 실제로 그 실행의 D유형(가드레일 개입)은 0건이었는데, 가드레일이
필요 없어서가 아니라 발동할 수 없어서였다.

문구는 앞으로도 계속 손보게 되므로, "숫자를 다시 넣지 않는다"만 기계적으로 지킨다.

실행: python -m unittest tests.test_prompt_score_examples
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from db.topic_causes import TopicCandidate  # noqa: E402
from event_classifier import prompts as event_prompts  # noqa: E402
from topic_classifier import prompts as topic_prompts  # noqa: E402

# "score": 0.93 / "score":0.9 / "score": 1 처럼 값이 숫자 리터럴인 경우
_LITERAL_SCORE = re.compile(r'"score"\s*:\s*[0-9]')

# 구간 목록 한 줄("- 0.90~1.00: ...", "- 0.75~0.89: ...")의 시작 부분
_SCORE_BAND_LINE = re.compile(r"^- (0\.\d\d)[~ ]", re.MULTILINE)

_CANDIDATE = {
    "id": 1,
    "title": "후보 이벤트",
    "core_content": "후보 핵심 내용",
    "summary": "후보 요약",
    "category": "사회",
    "article_count": 3,
    "distance": 0.31,
}


def _topic_candidate() -> TopicCandidate:
    return TopicCandidate(
        topic_id=1,
        category="사회",
        title="후보 토픽",
        summary="후보 토픽 요약",
        distance=0.42,
        cause_texts=["원인 명사구"],
    )


class ScoreExampleTests(unittest.TestCase):
    """score 를 돌려주는 프롬프트는 모두 자리표시자를 써야 한다."""

    def _prompts(self) -> dict[str, str]:
        return {
            "build_event_assignment_prompt": event_prompts.build_event_assignment_prompt(
                "기사 제목", "기사 요약", "대표 이벤트", "사회", [_CANDIDATE]),
            "build_verify_event_prompt": event_prompts.build_verify_event_prompt(
                "신규 대표 이벤트", "기존 핵심 내용"),
            "build_topic_assignment_prompt": topic_prompts.build_topic_assignment_prompt(
                "이벤트 제목", "이벤트 요약", "원인", "결과", [_topic_candidate()]),
            "build_parent_topic_assignment_prompt": (
                topic_prompts.build_parent_topic_assignment_prompt(
                    "이벤트 제목", "이벤트 요약", "원인", "결과", [_topic_candidate()])),
            "build_subtopic_assignment_prompt": (
                topic_prompts.build_subtopic_assignment_prompt(
                    "상위 토픽", "상위 요약", "이벤트 제목", "이벤트 요약",
                    "원인", "결과", [_topic_candidate()])),
        }

    def test_no_literal_score_value(self):
        for name, text in self._prompts().items():
            with self.subTest(prompt=name):
                found = _LITERAL_SCORE.findall(text)
                self.assertEqual(
                    found, [],
                    f"{name} 의 score 예시에 숫자 리터럴이 있습니다. 모델이 그 값을 "
                    f"그대로 베껴 점수가 상수가 됩니다.",
                )

    def test_placeholder_is_present(self):
        for name, text in self._prompts().items():
            with self.subTest(prompt=name):
                self.assertIn(event_prompts.SCORE_PLACEHOLDER, text)

    def test_scale_is_explained(self):
        # 자리표시자만 두고 기준을 안 주면 점수가 근거 없는 난수가 된다.
        for name, text in self._prompts().items():
            with self.subTest(prompt=name):
                self.assertIn("점수 기준:", text)
                self.assertIn("0.90", text)
                self.assertIn("0.39", text)

    def test_scale_is_defined_once(self):
        """구간 정의는 프롬프트당 한 벌이어야 한다.

        2026-07-31: 구간이 상단 _SCORE_GUIDE 와 하단 "점수 기준"에 두 벌 있었고 경계가
        어긋나 있었다(이벤트 중간대 0.50 vs 0.40, 토픽 두 번째 구간 0.70 vs 0.75).
        모델이 어느 눈금을 따르는지 알 수 없고, 임계값을 어디에 두어야 하는지도 정할 수 없다.
        """
        for name, text in self._prompts().items():
            with self.subTest(prompt=name):
                self.assertEqual(
                    text.count("점수 기준:"), 1,
                    f"{name} 에 '점수 기준:' 섹션이 여러 벌 있습니다.",
                )
                bands = _SCORE_BAND_LINE.findall(text)
                self.assertEqual(
                    len(bands), 4,
                    f"{name} 의 구간 목록이 4줄이 아닙니다(찾은 값: {bands}). "
                    f"구간 정의가 두 벌이거나 일부가 빠졌습니다.",
                )

    def test_placeholder_is_shared_across_modules(self):
        # 두 모듈이 서로 다른 표기를 쓰면 위 검사가 한쪽을 놓친다.
        self.assertEqual(
            event_prompts.SCORE_PLACEHOLDER, topic_prompts.SCORE_PLACEHOLDER
        )


class LiteralScorePatternTests(unittest.TestCase):
    """검사 정규식 자체가 실제 결함 형태를 잡는지 확인한다."""

    def test_detects_the_pattern_that_caused_the_bug(self):
        for bad in ('{"score": 0.93}', '{"score":0.35}', '{"score" : 1}'):
            self.assertTrue(_LITERAL_SCORE.search(bad), bad)

    def test_allows_placeholder_and_prose(self):
        for ok in ('{"score": <0.00~1.00>}', "score 는 0.90 이상이면 ..."):
            self.assertIsNone(_LITERAL_SCORE.search(ok), ok)


if __name__ == "__main__":
    unittest.main()

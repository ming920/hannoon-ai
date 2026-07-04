"""
롤업 프롬프트 절단 회귀 테스트.

_format_topic_events / _format_summary_articles가 [:N] 슬라이스였을 때는
목록이 N건을 넘는 순간 마지막에 append된 신규 항목이 프롬프트에서 잘려,
활성 토픽/이벤트일수록 요약 갱신이 멈추는 버그가 있었다.
최신 N건([-N:])이 유지되는지 검증한다.

항목 표식은 "요약-001"처럼 고정폭 0패딩을 사용한다
("요약1"은 "요약10"의 접두사라 assertIn이 오탐하기 때문).
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from event_classifier.prompts import MAX_SUMMARY_ARTICLES, build_event_summary_prompt
from topic_classifier.prompts import MAX_EVENTS_PER_TOPIC_SUMMARY, build_topic_rollup_prompt


class TopicRollupPromptTruncationTests(unittest.TestCase):
    """build_topic_rollup_prompt가 최신 이벤트 N건을 유지하는지 검증."""

    def _make_events(self, count: int) -> list[dict]:
        return [
            {"event_id": i, "title": f"제목-{i:03d}", "summary": f"요약-{i:03d}"}
            for i in range(1, count + 1)
        ]

    def test_within_limit_keeps_all_events(self):
        """한도 이내면 처음과 마지막 이벤트가 모두 프롬프트에 있어야 한다."""
        events = self._make_events(MAX_EVENTS_PER_TOPIC_SUMMARY)
        prompt = build_topic_rollup_prompt("토픽", events)
        self.assertIn("요약-001", prompt)
        self.assertIn(f"요약-{MAX_EVENTS_PER_TOPIC_SUMMARY:03d}", prompt)

    def test_over_limit_keeps_newest_event(self):
        """한도 초과 시 마지막에 append된 신규 이벤트가 잘리면 안 된다(회귀)."""
        count = MAX_EVENTS_PER_TOPIC_SUMMARY + 3
        prompt = build_topic_rollup_prompt("토픽", self._make_events(count))
        self.assertIn(f"요약-{count:03d}", prompt)

    def test_over_limit_drops_oldest_event(self):
        """한도 초과 시 잘리는 쪽은 가장 오래된 이벤트여야 한다."""
        count = MAX_EVENTS_PER_TOPIC_SUMMARY + 3
        prompt = build_topic_rollup_prompt("토픽", self._make_events(count))
        self.assertNotIn("요약-001", prompt)

    def test_empty_events_shows_fallback_text(self):
        """이벤트가 없으면 '(이벤트 없음)' 안내가 표시되어야 한다."""
        prompt = build_topic_rollup_prompt("토픽", [])
        self.assertIn("(이벤트 없음)", prompt)


class EventSummaryPromptTruncationTests(unittest.TestCase):
    """build_event_summary_prompt가 최신 기사 N건을 유지하는지 검증."""

    def _make_articles(self, count: int) -> list[dict]:
        return [
            {"article_id": i, "title": f"제목-{i:03d}", "summary": f"요약-{i:03d}"}
            for i in range(1, count + 1)
        ]

    def test_within_limit_keeps_all_articles(self):
        """한도 이내면 처음과 마지막 기사가 모두 프롬프트에 있어야 한다."""
        prompt = build_event_summary_prompt("이벤트", self._make_articles(MAX_SUMMARY_ARTICLES))
        self.assertIn("요약-001", prompt)
        self.assertIn(f"요약-{MAX_SUMMARY_ARTICLES:03d}", prompt)

    def test_over_limit_keeps_newest_article(self):
        """한도 초과 시 마지막에 append된 신규 기사가 잘리면 안 된다(회귀)."""
        count = MAX_SUMMARY_ARTICLES + 3
        prompt = build_event_summary_prompt("이벤트", self._make_articles(count))
        self.assertIn(f"요약-{count:03d}", prompt)

    def test_over_limit_drops_oldest_article(self):
        """한도 초과 시 잘리는 쪽은 가장 오래된 기사여야 한다."""
        count = MAX_SUMMARY_ARTICLES + 3
        prompt = build_event_summary_prompt("이벤트", self._make_articles(count))
        self.assertNotIn("요약-001", prompt)


if __name__ == "__main__":
    unittest.main()

"""eval/sweep.py 유닛테스트.

스윕은 설정 수만큼 전체 분류를 반복하므로 API 비용이 곱해진다. 따라서 잘못된 입력을
**실행 전에** 걷어내는 것이 중요하다 — 특히 태그 중복은 run-id 가 겹쳐 앞선 결과를
덮어쓰므로, 돈을 다 쓰고 나서야 결과가 사라진 걸 알게 된다.

실행: python -m unittest tests.test_sweep
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from sweep import (  # noqa: E402
    _width,
    build_run_plan,
    format_summary,
    parse_config,
    read_sweep_rows,
)


def _display_column_of(line: str, needle: str) -> int:
    """줄에서 needle이 시작하는 **화면 열** 위치.

    str.index 는 글자 수라 한글(표시폭 2)이 섞이면 정렬이 맞아도 값이 달라진다.
    정렬 검증에는 표시폭 기준이 맞다.
    """
    return _width(line[: line.index(needle)])


class ParseConfigTests(unittest.TestCase):
    def test_single_override(self):
        self.assertEqual(
            parse_config("assign070:TOPIC_ASSIGN_SCORE_THRESHOLD=0.70"),
            ("assign070", ["TOPIC_ASSIGN_SCORE_THRESHOLD=0.70"]),
        )

    def test_multiple_overrides(self):
        tag, overrides = parse_config("combo:A=1,B=2,C=3")
        self.assertEqual(tag, "combo")
        self.assertEqual(overrides, ["A=1", "B=2", "C=3"])

    def test_baseline_has_no_overrides(self):
        self.assertEqual(parse_config("baseline:"), ("baseline", []))

    def test_whitespace_is_trimmed(self):
        tag, overrides = parse_config("  t : A=1 , B=2 ")
        self.assertEqual(tag, "t")
        self.assertEqual(overrides, ["A=1", "B=2"])

    def test_value_may_contain_colon(self):
        # partition 이라 첫 콜론만 태그 구분자다 (URL 등 값에 콜론이 와도 안전).
        tag, overrides = parse_config("t:URL=http://x:5432/db")
        self.assertEqual(tag, "t")
        self.assertEqual(overrides, ["URL=http://x:5432/db"])

    def test_missing_colon_raises(self):
        with self.assertRaises(ValueError):
            parse_config("태그만있음")

    def test_empty_tag_raises(self):
        with self.assertRaises(ValueError):
            parse_config(":A=1")

    def test_override_without_equals_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parse_config("t:JUSTAKEY")
        self.assertIn("KEY=VALUE", str(ctx.exception))


class BuildRunPlanTests(unittest.TestCase):
    def test_run_ids_are_prefixed(self):
        plan = build_run_plan("s1", ["baseline:", "a:X=1"])
        self.assertEqual([p["run_id"] for p in plan], ["s1-baseline", "s1-a"])

    def test_plan_preserves_order(self):
        plan = build_run_plan("s", ["c:", "a:", "b:"])
        self.assertEqual([p["tag"] for p in plan], ["c", "a", "b"])

    def test_duplicate_tag_raises_before_spending(self):
        # 태그가 겹치면 run-id 가 겹쳐 앞 결과를 덮어쓴다 — 실행 전에 막아야 한다.
        with self.assertRaises(ValueError) as ctx:
            build_run_plan("s1", ["a:X=1", "a:X=2"])
        self.assertIn("중복", str(ctx.exception))

    def test_overrides_are_carried(self):
        plan = build_run_plan("s", ["a:X=1,Y=2"])
        self.assertEqual(plan[0]["overrides"], ["X=1", "Y=2"])


class ReadSweepRowsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "runs.csv"

    def tearDown(self):
        self.dir.cleanup()

    def _write(self, rows):
        with self.path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["run_id", "config_tag", "rate"])
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

    def test_selects_only_sweep_runs_in_plan_order(self):
        self._write([
            {"run_id": "old-x", "config_tag": "x", "rate": "0.1"},
            {"run_id": "s1-b", "config_tag": "b", "rate": "0.5"},
            {"run_id": "s1-a", "config_tag": "a", "rate": "0.3"},
        ])
        rows = read_sweep_rows(self.path, ["s1-a", "s1-b"])
        self.assertEqual([r["run_id"] for r in rows], ["s1-a", "s1-b"])

    def test_missing_runs_are_skipped(self):
        self._write([{"run_id": "s1-a", "config_tag": "a", "rate": "0.3"}])
        rows = read_sweep_rows(self.path, ["s1-a", "s1-실패"])
        self.assertEqual(len(rows), 1)

    def test_duplicate_run_id_keeps_last(self):
        self._write([
            {"run_id": "s1-a", "config_tag": "a", "rate": "0.3"},
            {"run_id": "s1-a", "config_tag": "a", "rate": "0.9"},
        ])
        self.assertEqual(read_sweep_rows(self.path, ["s1-a"])[0]["rate"], "0.9")

    def test_missing_file_returns_empty(self):
        self.assertEqual(read_sweep_rows(Path(self.dir.name) / "없음.csv", ["a"]), [])


class FormatSummaryTests(unittest.TestCase):
    ROWS = [
        {"config_tag": "baseline", "topic_must_rate": "0.42", "topics_total": "120"},
        {"config_tag": "assign070", "topic_must_rate": "0.55", "topics_total": "98"},
    ]
    COLUMNS = ["config_tag", "topic_must_rate", "topics_total"]

    def test_includes_header_and_rows(self):
        text = format_summary(self.ROWS, self.COLUMNS)
        self.assertIn("config_tag", text)
        self.assertIn("baseline", text)
        self.assertIn("assign070", text)

    def test_columns_are_aligned(self):
        text = format_summary(self.ROWS, self.COLUMNS)
        body = [ln for ln in text.splitlines() if "baseline" in ln or "assign070" in ln]
        starts = [_display_column_of(ln, "0.") for ln in body]
        self.assertEqual(len(set(starts)), 1)

    def test_korean_tag_does_not_break_alignment(self):
        # 한글 태그는 글자 수와 표시폭이 다르다. 표시폭으로 패딩하지 않으면 열이 어긋난다.
        rows = [
            {"config_tag": "기준선", "topic_must_rate": "0.42"},
            {"config_tag": "assign070", "topic_must_rate": "0.55"},
        ]
        text = format_summary(rows, ["config_tag", "topic_must_rate"])
        body = [ln for ln in text.splitlines() if "0." in ln]
        starts = [_display_column_of(ln, "0.") for ln in body]
        self.assertEqual(len(set(starts)), 1)

    def test_empty_rows_message(self):
        self.assertIn("없습니다", format_summary([], self.COLUMNS))

    def test_missing_column_renders_blank(self):
        text = format_summary([{"config_tag": "a"}], ["config_tag", "topic_must_rate"])
        self.assertIn("a", text)


if __name__ == "__main__":
    unittest.main()

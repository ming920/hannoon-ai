"""eval/harness_common.py 유닛테스트 — 특히 drain().

drain 은 두 하네스가 공유하는 유일한 "돈을 쓰는" 루프다. 잔량이 줄지 않는데도 계속
재호출하면 같은 실패를 반복하며 API 비용만 태우므로, stuck 감지가 반드시 살아 있어야 한다.
(실제로 이번 파일럿에서 Upstage 크레딧 소진으로 720건이 연속 실패했다 — 그런 상황에서
루프가 멈추지 않으면 피해가 커진다.)

coerce/append_csv/patch_dotenv 등은 두 하네스 테스트가 간접적으로 덮으므로, 여기서는
drain 과 compare_runs 의 파라미터화만 직접 검증한다.

실행: python -m unittest tests.test_harness_common
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from harness_common import compare_runs, drain  # noqa: E402


def _echo_cmd(text: str) -> list:
    """stdout에 한 줄 출력하고 정상 종료하는 명령."""
    return [sys.executable, "-c", f"print({text!r})"]


def _fail_cmd() -> list:
    return [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "drain.log"

    def tearDown(self):
        self.dir.cleanup()

    def _countdown(self, values):
        """호출될 때마다 다음 잔량을 돌려주는 remaining_fn."""
        seq = list(values)
        state = {"i": 0}

        def fn():
            i = min(state["i"], len(seq) - 1)
            state["i"] += 1
            return seq[i]

        return fn

    def test_no_work_returns_immediately(self):
        result = drain(cmd=_echo_cmd("x"), env=os.environ.copy(),
                       remaining_fn=lambda: 0, label="t", log_path=self.log)
        self.assertEqual(result, {"passes": 0, "remaining": 0, "stuck": False})

    def test_drains_until_zero(self):
        # 시작 3 → 패스마다 2, 1, 0
        result = drain(cmd=_echo_cmd("한 줄"), env=os.environ.copy(),
                       remaining_fn=self._countdown([3, 2, 1, 0]),
                       label="t", log_path=self.log)
        self.assertFalse(result["stuck"])
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["passes"], 3)

    def test_stdout_is_captured_to_log(self):
        drain(cmd=_echo_cmd("진단로그"), env=os.environ.copy(),
              remaining_fn=self._countdown([2, 1, 0]), label="t", log_path=self.log)
        text = self.log.read_text(encoding="utf-8")
        self.assertEqual(text.count("진단로그"), 2)

    def test_stuck_when_remaining_does_not_decrease(self):
        # 5 → 5 (줄지 않음) → 즉시 중단해야 한다
        result = drain(cmd=_echo_cmd("x"), env=os.environ.copy(),
                       remaining_fn=self._countdown([5, 5]), label="t", log_path=self.log)
        self.assertTrue(result["stuck"])
        self.assertEqual(result["passes"], 1)
        self.assertEqual(result["remaining"], 5)

    def test_stuck_when_remaining_increases(self):
        result = drain(cmd=_echo_cmd("x"), env=os.environ.copy(),
                       remaining_fn=self._countdown([5, 7]), label="t", log_path=self.log)
        self.assertTrue(result["stuck"])

    def test_partial_progress_then_stuck(self):
        result = drain(cmd=_echo_cmd("x"), env=os.environ.copy(),
                       remaining_fn=self._countdown([10, 6, 6]), label="t", log_path=self.log)
        self.assertTrue(result["stuck"])
        self.assertEqual(result["passes"], 2)
        self.assertEqual(result["remaining"], 6)

    def test_nonzero_exit_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            drain(cmd=_fail_cmd(), env=os.environ.copy(),
                  remaining_fn=self._countdown([2, 1, 0]), label="t", log_path=self.log)
        self.assertIn("종료 코드 3", str(ctx.exception))

    def test_hard_cap_stops_the_loop(self):
        # 잔량이 계속 줄지만 cap 에서 멈춰야 한다
        result = drain(cmd=_echo_cmd("x"), env=os.environ.copy(),
                       remaining_fn=self._countdown([100, 99, 98, 97, 96, 95]),
                       label="t", log_path=self.log, hard_cap=2)
        self.assertTrue(result["stuck"])
        self.assertEqual(result["passes"], 2)

    def test_log_is_truncated_per_run(self):
        self.log.write_text("이전 실행 찌꺼기\n", encoding="utf-8")
        drain(cmd=_echo_cmd("새로운"), env=os.environ.copy(),
              remaining_fn=self._countdown([1, 0]), label="t", log_path=self.log)
        text = self.log.read_text(encoding="utf-8")
        self.assertNotIn("찌꺼기", text)
        self.assertIn("새로운", text)


class CompareRunsParameterizationTests(unittest.TestCase):
    COLUMNS = ["run_id", "rate", "count", "violations"]
    HIGHER = {"rate"}
    LOWER = {"violations"}

    def _compare(self, current, previous):
        return {
            r["column"]: r
            for r in compare_runs(current, previous, columns=self.COLUMNS,
                                  higher_is_better=self.HIGHER, lower_is_better=self.LOWER)
        }

    def test_higher_is_better_column(self):
        rows = self._compare({"rate": 0.9}, {"rate": "0.5"})
        self.assertEqual(rows["rate"]["verdict"], "개선")

    def test_lower_is_better_column(self):
        rows = self._compare({"violations": 2}, {"violations": "5"})
        self.assertEqual(rows["violations"]["verdict"], "개선")

    def test_undirected_column_is_변동(self):
        rows = self._compare({"count": 7}, {"count": "3"})
        self.assertEqual(rows["count"]["verdict"], "변동")

    def test_run_id_is_skipped(self):
        rows = self._compare({"run_id": "b", "rate": 0.5}, {"run_id": "a", "rate": "0.5"})
        self.assertNotIn("run_id", rows)

    def test_string_previous_values_are_coerced(self):
        # CSV는 모든 값을 문자열로 돌려준다. 이걸 놓치면 비교가 통째로 사라진다.
        rows = self._compare({"rate": 0.6}, {"rate": "0.4"})
        self.assertAlmostEqual(rows["rate"]["delta"], 0.2)

    def test_missing_previous_column_is_skipped(self):
        rows = self._compare({"rate": 0.6}, {})
        self.assertEqual(rows, {})

    def test_none_previous_returns_empty(self):
        self.assertEqual(
            compare_runs({"rate": 0.6}, None, columns=self.COLUMNS,
                         higher_is_better=self.HIGHER, lower_is_better=self.LOWER),
            [],
        )


if __name__ == "__main__":
    unittest.main()

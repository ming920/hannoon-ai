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

import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from harness_common import (  # noqa: E402
    MIN_RELIABLE_PAIRS,
    append_csv,
    compare_runs,
    drain,
    gold_change_warning,
    gold_fingerprint,
    migrate_csv_header,
    read_previous_run,
    sample_size_warnings,
)


def _echo_cmd(text: str) -> list:
    """stdout에 한 줄 출력하고 정상 종료하는 명령."""
    return [sys.executable, "-c", f"print({text!r})"]


def _fail_cmd() -> list:
    return [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]


def _stderr_cmd(text: str) -> list:
    """stderr 로 실패를 흘리면서 **종료 코드 0** 으로 끝나는 명령.

    분류기의 실제 동작이다 — 기사 단위 예외를 stderr 에 찍고 continue 한 뒤 정상 종료한다.
    종료 코드만 보면 "정상 종료했는데 잔량이 그대로"라 원인을 되짚을 수 없다.
    """
    return [sys.executable, "-c", f"import sys; sys.stderr.write({text!r} + '\\n')"]


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

    def test_append_mode_keeps_earlier_passes(self):
        # 재개 실행에서 앞선 패스의 진단 로그를 지우면 이미 분류된 기사의 판단 근거가
        # 사라져 채점 결과를 되짚을 수 없다.
        self.log.write_text('{"article_id": 1}\n', encoding="utf-8")
        drain(cmd=_echo_cmd('{"article_id": 2}'), env=os.environ.copy(),
              remaining_fn=self._countdown([1, 0]), label="t",
              log_path=self.log, log_mode="a")
        text = self.log.read_text(encoding="utf-8")
        self.assertIn('"article_id": 1', text)
        self.assertIn('"article_id": 2', text)


class DrainStderrTests(unittest.TestCase):
    """종료 코드 0 + stderr 실패 조합 — 실제로 시운전을 눈멀게 했던 경로.

    분류기가 임베딩 차원 불일치로 전 건 실패했는데 종료 코드는 0이었다. drain 이 stderr 를
    버리는 바람에 "722 → 722, 원인 불명"만 남았다. 원인 문장은 stderr 에 있었다.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "drain.log"
        self.errlog = Path(self.dir.name) / "drain-stderr.log"

    def tearDown(self):
        self.dir.cleanup()

    def _run_stuck(self, message: str) -> str:
        """잔량이 줄지 않는 드레인을 돌리고 사용자에게 출력된 stderr 를 돌려준다."""
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            result = drain(cmd=_stderr_cmd(message), env=os.environ.copy(),
                           remaining_fn=lambda: 5, label="t", log_path=self.log)
        self.assertTrue(result["stuck"])
        return captured.getvalue()

    def test_stderr_is_written_to_file_despite_exit_zero(self):
        self._run_stuck("bge-m3 returned 1024 dimensions")
        self.assertTrue(self.errlog.exists())
        self.assertIn("1024 dimensions", self.errlog.read_text(encoding="utf-8"))

    def test_stall_warning_shows_the_cause(self):
        printed = self._run_stuck("bge-m3 returned 1024 dimensions")
        self.assertIn("잔량이 줄지 않았습니다", printed)
        self.assertIn("1024 dimensions", printed)
        self.assertIn(str(self.errlog), printed)

    def test_stdout_log_stays_free_of_stderr(self):
        # stdout 로그는 diagnose_violations 가 JSONL 로 파싱한다 — stderr 가 섞이면 깨진다.
        self._run_stuck("실패 메시지")
        self.assertNotIn("실패 메시지", self.log.read_text(encoding="utf-8"))

    def test_preview_is_capped_and_remainder_is_counted(self):
        # 전 건이 같은 이유로 실패하는 상황 — 앞 5줄이면 원인 판별에 충분하고,
        # 나머지는 줄 수만 알려준 뒤 전문 경로로 넘긴다.
        printed = self._run_stuck("\n".join(f"기사 {i} 실패" for i in range(9)))
        self.assertIn("총 9줄", printed)
        self.assertIn("기사 0 실패", printed)
        self.assertIn("기사 4 실패", printed)
        self.assertNotIn("기사 5 실패", printed)
        self.assertIn("나머지 4줄", printed)

    def test_silent_failure_is_called_out(self):
        # stderr 조차 없으면 그 사실 자체가 진단이다 — 조용히 넘어가면 안 된다.
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            drain(cmd=_echo_cmd("x"), env=os.environ.copy(),
                  remaining_fn=lambda: 5, label="t", log_path=self.log)
        self.assertIn("stderr 없음", printed.getvalue())

    def test_stderr_log_is_truncated_per_run(self):
        self.errlog.write_text("이전 실행 찌꺼기\n", encoding="utf-8")
        self._run_stuck("새로운 실패")
        text = self.errlog.read_text(encoding="utf-8")
        self.assertNotIn("찌꺼기", text)
        self.assertIn("새로운 실패", text)

    def test_stderr_log_follows_append_mode(self):
        # stdout 로그와 같은 모드를 써야 재개 실행에서 실패 이력이 이어진다.
        self.errlog.write_text("앞선 패스 실패\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            drain(cmd=_stderr_cmd("이번 패스 실패"), env=os.environ.copy(),
                  remaining_fn=lambda: 5, label="t",
                  log_path=self.log, log_mode="a")
        text = self.errlog.read_text(encoding="utf-8")
        self.assertIn("앞선 패스 실패", text)
        self.assertIn("이번 패스 실패", text)


class GoldFingerprintTests(unittest.TestCase):
    """정답이 바뀐 실행끼리 비교하면 '자가 바뀐 것'을 '개선'으로 읽는다.

    검수 라운드를 돌 때마다 정답은 늘어나므로 이 일은 반드시 일어난다. 실제로 검수 1라운드를
    반영하자 **같은 스냅샷**의 must-link 가 84.0% → 88.4% 로 올랐고, 하네스는 그것을 분류기
    변화인 것처럼 트레이드오프 경고까지 붙였다.
    """

    def _gold(self, must, cannot=()):
        return {"event_constraints": {"must_link": list(must),
                                      "cannot_link": list(cannot)}}

    def test_same_constraints_same_fingerprint(self):
        self.assertEqual(gold_fingerprint(self._gold([[1, 2]])),
                         gold_fingerprint(self._gold([[1, 2]])))

    def test_pair_order_does_not_matter(self):
        self.assertEqual(gold_fingerprint(self._gold([[1, 2]])),
                         gold_fingerprint(self._gold([[2, 1]])))

    def test_listing_order_does_not_matter(self):
        self.assertEqual(gold_fingerprint(self._gold([[1, 2], [3, 4]])),
                         gold_fingerprint(self._gold([[3, 4], [1, 2]])))

    def test_added_pair_changes_fingerprint(self):
        self.assertNotEqual(gold_fingerprint(self._gold([[1, 2]])),
                            gold_fingerprint(self._gold([[1, 2], [3, 4]])))

    def test_moving_a_pair_between_must_and_cannot_changes_it(self):
        self.assertNotEqual(gold_fingerprint(self._gold([[1, 2]], [])),
                            gold_fingerprint(self._gold([], [[1, 2]])))

    def test_empty_gold_is_stable(self):
        self.assertEqual(gold_fingerprint({}), gold_fingerprint({}))


class GoldChangeWarningTests(unittest.TestCase):
    def test_warns_when_fingerprint_differs(self):
        (msg,) = gold_change_warning({"gold_fingerprint": "bbb"},
                                     {"gold_fingerprint": "aaa"})
        self.assertIn("채점 기준", msg)
        self.assertIn("aaa", msg)
        self.assertIn("bbb", msg)

    def test_silent_when_same(self):
        self.assertEqual(
            gold_change_warning({"gold_fingerprint": "a"}, {"gold_fingerprint": "a"}), [])

    def test_silent_without_previous_run(self):
        self.assertEqual(gold_change_warning({"gold_fingerprint": "a"}, None), [])

    def test_silent_when_previous_predates_the_column(self):
        # 열이 생기기 전 실행에는 지문이 없다 — 없는 것을 변경으로 오인하면 안 된다.
        self.assertEqual(gold_change_warning({"gold_fingerprint": "a"}, {}), [])


class CsvHeaderMigrationTests(unittest.TestCase):
    """지표 열이 늘어날 때 과거 행이 조용히 어긋나지 않아야 한다.

    헤더를 최초 1회만 쓰는 구조라, 열을 추가하면 새 행이 옛 헤더 아래에 다른 순서로 쌓인다.
    그러면 추세 비교가 엉뚱한 열끼리 이뤄지는데 파일은 멀쩡해 보여서 알아채기 어렵다.
    (실제로 event_must_pairs/event_cannot_pairs 를 추가하면서 이 경로에 걸렸다.)
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.csv = Path(self.dir.name) / "runs.csv"

    def tearDown(self):
        self.dir.cleanup()

    def _read(self) -> tuple:
        with self.csv.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return reader.fieldnames, list(reader)

    def test_new_column_is_backfilled_blank(self):
        append_csv(self.csv, ["run_id", "rate"], {"run_id": "a", "rate": 0.5})
        append_csv(self.csv, ["run_id", "rate", "pairs"],
                   {"run_id": "b", "rate": 0.6, "pairs": 100})
        header, rows = self._read()
        self.assertEqual(header, ["run_id", "rate", "pairs"])
        self.assertEqual(rows[0], {"run_id": "a", "rate": "0.5", "pairs": ""})
        self.assertEqual(rows[1], {"run_id": "b", "rate": "0.6", "pairs": "100"})

    def test_values_do_not_shift_columns(self):
        # 마이그레이션이 없으면 새 행의 rate 값이 옛 헤더의 다른 열로 밀려 들어간다.
        append_csv(self.csv, ["run_id", "rate"], {"run_id": "a", "rate": 0.5})
        append_csv(self.csv, ["run_id", "pairs", "rate"],
                   {"run_id": "b", "pairs": 7, "rate": 0.6})
        _, rows = self._read()
        self.assertEqual(rows[1]["rate"], "0.6")
        self.assertEqual(rows[1]["pairs"], "7")

    def test_dropped_column_is_removed_from_history(self):
        append_csv(self.csv, ["run_id", "old"], {"run_id": "a", "old": 1})
        append_csv(self.csv, ["run_id"], {"run_id": "b"})
        header, rows = self._read()
        self.assertEqual(header, ["run_id"])
        self.assertEqual([r["run_id"] for r in rows], ["a", "b"])

    def test_unchanged_header_is_not_rewritten(self):
        append_csv(self.csv, ["run_id", "rate"], {"run_id": "a", "rate": 0.5})
        self.assertFalse(migrate_csv_header(self.csv, ["run_id", "rate"]))

    def test_previous_run_survives_migration(self):
        # 마이그레이션이 과거 행을 날려버리면 비교 기준선이 사라진다.
        append_csv(self.csv, ["run_id", "rate"], {"run_id": "a", "rate": 0.5})
        append_csv(self.csv, ["run_id", "rate", "pairs"],
                   {"run_id": "b", "rate": 0.6, "pairs": 100})
        prev = read_previous_run(self.csv, exclude_run_id="b")
        self.assertEqual(prev["run_id"], "a")


class SampleSizeWarningTests(unittest.TestCase):
    """얇은 표본의 충족률을 추세로 읽으면 노이즈를 개선으로 착각한다.

    실제 정답에서 이벤트 cannot-link 는 48쌍, 토픽 cannot-link 는 0쌍이었는데 리포트에는
    "31.2%" / "N/A" 만 찍혀 표본 크기가 보이지 않았다. 0쌍과 얇은 표본은 뜻이 다르므로
    (감지 불가 / 값은 나오지만 못 믿음) 문구를 구분한다.
    """

    def test_zero_pairs_says_undetectable(self):
        (msg,) = sample_size_warnings([("토픽 cannot-link", 0)])
        self.assertIn("0쌍", msg)
        self.assertIn("감지할 수 없", msg)

    def test_none_is_treated_as_zero(self):
        # 지표가 아직 계산되지 않은 경우도 "측정 못 함"으로 알려야 한다.
        (msg,) = sample_size_warnings([("토픽 cannot-link", None)])
        self.assertIn("감지할 수 없", msg)

    def test_thin_sample_says_do_not_trend(self):
        (msg,) = sample_size_warnings([("이벤트 cannot-link", 48)])
        self.assertIn("48쌍", msg)
        self.assertIn("추세", msg)
        self.assertNotIn("감지할 수 없", msg)

    def test_sufficient_sample_is_silent(self):
        self.assertEqual(sample_size_warnings([("이벤트 must-link", 1189)]), [])

    def test_boundary_is_not_warned(self):
        self.assertEqual(sample_size_warnings([("x", MIN_RELIABLE_PAIRS)]), [])
        self.assertEqual(len(sample_size_warnings([("x", MIN_RELIABLE_PAIRS - 1)])), 1)

    def test_each_label_gets_its_own_warning(self):
        msgs = sample_size_warnings([("must", 0), ("cannot", 48), ("ok", 500)])
        self.assertEqual(len(msgs), 2)
        self.assertIn("must", msgs[0])
        self.assertIn("cannot", msgs[1])


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

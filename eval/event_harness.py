"""이벤트 분류 반복 검증·개선 하네스.

`topic_harness.py`와 같은 뼈대이고 대상 레이어만 다르다.

  1. 분류기 출력 초기화       기사 요약(article_ai_results.summary)은 보존한다. 요약은
                              수집기가 LLM으로 만든 비싼 산출물이라 매 반복 재생성하면 안 된다.
  2. classify_events 드레인   status='done' 기사가 없어질 때까지 반복 호출하고 stdout
                              (JSONL 판단 로그)을 캡처한다.
  3. 스냅샷 추출 → 4. 제약 충족률 → 5. 위반 원인 진단(A/B/C/D) → 6. 루브릭 교차 확인
  7. results/event_runs.csv 누적 + results/event-<run-id>.md 리포트, 직전 실행 자동 비교

**이벤트 재분류는 토픽 레이어를 무효화한다.** 이벤트 구성이 바뀌면 그 위에 얹힌 토픽도
다시 만들어야 하므로, 초기화가 토픽까지 함께 지운다. 토픽 지표를 보려면 이 하네스를 돌린
뒤 topic_harness.py 를 이어서 돌린다.

**충족률만 보면 속는다**는 점은 토픽과 같다. 여기서는 방향이 둘 다 위험하다.
  모두 한 이벤트로 병합 → must-link 100%, cannot-link 0%
  모두 쪼갬              → cannot-link 100%, must-link 0%
그래서 이벤트 개수와 R-E1(단일기사 이벤트 비율)을 충족률 옆에 항상 싣는다.

사용 예:
    python eval/event_harness.py --database-url "postgresql://localhost/..." \\
        --run-id e-000 --config-tag baseline

    python eval/event_harness.py --database-url "postgresql://localhost/..." \\
        --run-id e-001 --config-tag "distance 0.60" \\
        --set EVENT_DISTANCE_THRESHOLD=0.60 --set EVENT_ASSIGN_SCORE_THRESHOLD=0.85
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _EVAL_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_EVAL_DIR))

from collector.storage import ensure_db  # noqa: E402

import harness_common  # noqa: E402
import rubric_checks  # noqa: E402
from constraint_checks import build_event_cluster_map, evaluate_pairs, load_json  # noqa: E402
from diagnose_violations import (  # noqa: E402
    CAUSE_A,
    CAUSE_B,
    CAUSE_C,
    CAUSE_D,
    CAUSE_NO_LOG,
    parse_decision_logs,
    run_diagnosis,
)
from extract_snapshot import build_snapshot  # noqa: E402
from harness_common import MIN_RELIABLE_PAIRS  # noqa: E402
from harness_common import coerce as _coerce  # noqa: E402
from harness_common import fmt as _fmt  # noqa: E402
from harness_common import patch_dotenv as _patch_dotenv  # noqa: E402
from harness_common import read_previous_run  # noqa: E402
from harness_common import read_settings as _read_settings  # noqa: E402
from reset_classifier_only import reset_classifier_output  # noqa: E402

DEFAULT_GOLD = _EVAL_DIR / "data" / "constraints" / "review_2026-07-20.json"
DEFAULT_OUT_DIR = _EVAL_DIR / "results"
RUNS_CSV = "event_runs.csv"

CSV_COLUMNS = [
    "run_id", "config_tag", "timestamp",
    "gold_fingerprint",
    "event_must_rate", "event_cannot_rate",
    "event_must_pairs", "event_cannot_pairs",
    "events_total", "articles_assigned", "single_article_events",
    "c_a", "c_b", "c_c", "c_d", "c_nolog",
    "rubric_e1_ratio", "rubric_e2",
    "distance_threshold", "assign_score", "candidate_limit", "window_days", "batch_size",
]

_HIGHER_IS_BETTER = {"event_must_rate", "event_cannot_rate"}
_LOWER_IS_BETTER = {"rubric_e1_ratio", "rubric_e2"}


# ══════════════════════════════════════════════════════════════════════════
# DB 조작
# ══════════════════════════════════════════════════════════════════════════


def count_pending_articles(conn) -> int:
    """이벤트 분류 대기 기사 수 (분류기의 대상 선정 조건과 동일해야 한다).

    src/event_classifier/pipeline.py 의 done_articles 쿼리와 같은 조건을 쓴다 —
    어긋나면 드레인이 영원히 끝나지 않거나 너무 일찍 끝난다.
    """
    row = conn.query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM articles a
        JOIN article_ai_results r ON a.id = r.article_id
        WHERE r.status = 'done'
          AND a.content IS NOT NULL
          AND a.content <> ''
        """
    )
    return int(row["cnt"]) if row else 0


def drain_events(
    cmd: list, env: dict, db_url: str, log_path: Path, *, log_mode: str = "w"
) -> dict:
    """대기 기사가 없어질 때까지 이벤트 분류기를 반복 호출하고 stdout을 파일에 모은다."""
    def _remaining() -> int:
        conn = ensure_db("", database_url=db_url)
        try:
            return count_pending_articles(conn)
        finally:
            conn.close()

    return harness_common.drain(
        cmd=cmd, env=env, remaining_fn=_remaining,
        label="이벤트 드레인", log_path=log_path, log_mode=log_mode,
    )


# ══════════════════════════════════════════════════════════════════════════
# 지표 조립 (순수 함수)
# ══════════════════════════════════════════════════════════════════════════


def _rate(pairs, clusters, *, expect_same):
    return _rate_with_size(pairs, clusters, expect_same=expect_same)[0]


def _rate_with_size(pairs, clusters, *, expect_same) -> tuple:
    """(충족률, 채점 가능한 쌍 수)를 함께 돌려준다.

    분모는 두 기사가 **모두** 스냅샷에 있는 쌍만 센다(evaluate_pairs 의 unknown 제외 규칙).
    정답 파일의 전체 쌍 수와 다를 수 있고, 그 차이가 곧 커버리지다.
    """
    result = evaluate_pairs(pairs, clusters, expect_same=expect_same, examples_limit=0)
    return result["rate"], result["satisfied"] + result["violated"]


def _rubric_value(rubric: dict, check_id: str, key: str):
    entry = (rubric or {}).get(check_id) or {}
    if entry.get("skipped"):
        return None
    return entry.get(key)


def collect_metrics(
    *,
    run_id: str,
    config_tag: str,
    timestamp: str,
    gold: dict,
    snapshot: dict,
    diagnosis: dict,
    rubric: dict,
    settings: dict,
) -> dict:
    """CSV 한 줄에 담을 지표를 모은다."""
    clusters = build_event_cluster_map(snapshot.get("events") or [])
    ec = gold.get("event_constraints") or {}
    causes = diagnosis.get("must_link_causes") or {}
    events = snapshot.get("events") or []

    must_rate, must_pairs = _rate_with_size(
        ec.get("must_link"), clusters, expect_same=True)
    cannot_rate, cannot_pairs = _rate_with_size(
        ec.get("cannot_link"), clusters, expect_same=False)

    return {
        "run_id": run_id,
        "config_tag": config_tag,
        "timestamp": timestamp,
        # 정답이 바뀌면 분류기가 그대로여도 충족률이 움직인다 — 비교 가능 여부를 남긴다.
        "gold_fingerprint": harness_common.gold_fingerprint(gold),
        "event_must_rate": must_rate,
        "event_cannot_rate": cannot_rate,
        # 충족률과 함께 기록해 두지 않으면 나중에 CSV만 보고는 표본 크기를 알 수 없다.
        "event_must_pairs": must_pairs,
        "event_cannot_pairs": cannot_pairs,
        "events_total": len(events),
        "articles_assigned": len(clusters),
        # 단일기사 이벤트는 과분할의 가장 직접적인 신호다 (루브릭 R-E1과 같은 관점).
        "single_article_events": sum(
            1 for e in events if len(e.get("articles") or []) == 1
        ),
        "c_a": causes.get(CAUSE_A, 0),
        "c_b": causes.get(CAUSE_B, 0),
        "c_c": causes.get(CAUSE_C, 0),
        "c_d": causes.get(CAUSE_D, 0),
        "c_nolog": causes.get(CAUSE_NO_LOG, 0),
        "rubric_e1_ratio": _rubric_value(rubric, "R-E1", "ratio"),
        "rubric_e2": _rubric_value(rubric, "R-E2", "violations"),
        "distance_threshold": settings.get("EVENT_DISTANCE_THRESHOLD", ""),
        "assign_score": settings.get("EVENT_ASSIGN_SCORE_THRESHOLD", ""),
        "candidate_limit": settings.get("EVENT_CANDIDATE_LIMIT", ""),
        "window_days": settings.get("EVENT_CANDIDATE_WINDOW_DAYS", ""),
        "batch_size": settings.get("EVENT_BATCH_SIZE", ""),
    }


def detect_warnings(current: dict, previous: dict | None) -> list[str]:
    """충족률 숫자만으로는 놓치는 실패 양상을 잡아낸다."""
    warnings = []
    warnings.extend(harness_common.gold_change_warning(current, previous))

    if current["events_total"] == 0:
        warnings.append("이벤트가 0개입니다 — 분류가 실행되지 않았을 수 있습니다.")
        return warnings

    single_ratio = current["single_article_events"] / current["events_total"]
    if single_ratio > 0.7:
        warnings.append(
            f"이벤트의 {single_ratio * 100:.0f}%가 단일기사입니다. 과분할이 의심됩니다 "
            "— must-link 충족률이 낮다면 거리 임계값부터 보세요."
        )

    # 표본이 작은 충족률을 추세로 읽으면 노이즈를 개선으로 착각한다. cannot-link 정답은
    # 검수에서 "이 기사는 빼야 한다"고 명시한 건에서만 나오므로 특히 얇다.
    warnings.extend(
        harness_common.sample_size_warnings(
            [("이벤트 must-link", current.get("event_must_pairs")),
             ("이벤트 cannot-link", current.get("event_cannot_pairs"))]
        )
    )

    if previous:
        prev_events = _coerce(previous.get("events_total"))
        cur_must = current["event_must_rate"]
        prev_must = _coerce(previous.get("event_must_rate"))
        cur_cannot = current["event_cannot_rate"]
        prev_cannot = _coerce(previous.get("event_cannot_rate"))

        if (
            isinstance(prev_events, (int, float)) and prev_events
            and isinstance(cur_must, float) and isinstance(prev_must, float)
            and cur_must > prev_must
            and current["events_total"] < prev_events * 0.8
        ):
            warnings.append(
                f"must-link 충족률이 올랐지만 이벤트 수가 {prev_events} → "
                f"{current['events_total']}로 20% 넘게 줄었습니다. 과병합으로 점수를 "
                "샀을 가능성이 높습니다."
            )

        if (
            isinstance(cur_must, float) and isinstance(prev_must, float)
            and isinstance(cur_cannot, float) and isinstance(prev_cannot, float)
            and cur_must > prev_must and cur_cannot < prev_cannot
        ):
            warnings.append(
                "must-link는 올랐는데 cannot-link가 내렸습니다 — 병합 쪽으로 치우친 "
                "트레이드오프입니다. 순이득인지 확인하세요."
            )

    return warnings


def compare_runs(current: dict, previous: dict | None) -> list[dict]:
    """직전 실행 대비 변화를 열별로 계산한다 (이벤트 하네스의 열 정의를 적용)."""
    return harness_common.compare_runs(
        current, previous, columns=CSV_COLUMNS,
        higher_is_better=_HIGHER_IS_BETTER, lower_is_better=_LOWER_IS_BETTER,
    )


def append_csv(path: Path, row: dict) -> None:
    """실행 결과를 CSV에 한 줄 덧붙인다 (헤더는 최초 1회)."""
    harness_common.append_csv(path, CSV_COLUMNS, row)


def _pairs_cell(size) -> str:
    """채점된 쌍 수. 표본이 얇으면 숫자 옆에서 바로 드러나게 한다.

    충족률만 찍으면 "31.2%"가 1,000쌍짜리인지 48쌍짜리인지 구분되지 않는다.
    """
    if not size:
        return "0쌍 ⚠️ 측정 불가"
    if size < MIN_RELIABLE_PAIRS:
        return f"{size:,}쌍 ⚠️ 표본 부족"
    return f"{size:,}쌍"


def format_report(metrics: dict, diagnosis: dict, comparison: list, warnings: list) -> str:
    """마크다운 상세 리포트를 만든다."""
    meta = diagnosis.get("meta") or {}
    single = metrics["single_article_events"]
    total = metrics["events_total"] or 1
    lines = [
        f"# 이벤트 하네스 실행: {metrics['run_id']}",
        "",
        f"- 설정 태그: `{metrics['config_tag']}`",
        f"- 실행 시각: {metrics['timestamp']}",
        f"- 파라미터: distance={metrics['distance_threshold']} / "
        f"assign={metrics['assign_score']} / top_k={metrics['candidate_limit']} / "
        f"window={metrics['window_days']}일",
        "",
        "## 제약 충족률",
        "",
        "| 제약 | 충족률 | 채점된 쌍 |",
        "|---|---|---|",
        f"| 이벤트 must-link | {_fmt(metrics['event_must_rate'])} | "
        f"{_pairs_cell(metrics['event_must_pairs'])} |",
        f"| 이벤트 cannot-link | {_fmt(metrics['event_cannot_rate'])} | "
        f"{_pairs_cell(metrics['event_cannot_pairs'])} |",
        "",
        "## 구조 지표 (충족률만으로는 못 보는 것)",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 이벤트 개수 | {metrics['events_total']} |",
        f"| 배정된 기사 | {metrics['articles_assigned']} |",
        f"| 단일기사 이벤트 | {single} ({single / total * 100:.1f}%) |",
        f"| R-E1 단일기사 이벤트 비율 | {_fmt(metrics['rubric_e1_ratio'])} |",
        f"| R-E2 유사 제목 이벤트 쌍 | {_fmt(metrics['rubric_e2'])} |",
        "",
        "## must-link 위반 원인",
        "",
        f"위반 {meta.get('must_link_violated', 0):,} / {meta.get('must_link_total', 0):,}쌍",
        "",
        "| 원인 | 건수 |",
        "|---|---|",
        f"| A. 후보 검색에 없음 (거리 임계값) | {metrics['c_a']} |",
        f"| B. 프롬프트에서 절삭 | {metrics['c_b']} |",
        f"| C. LLM이 다른 사건으로 판단 | {metrics['c_c']} |",
        f"| D. 가드레일이 assign을 뒤집음 | {metrics['c_d']} |",
        f"| ?. 진단 불가 | {metrics['c_nolog']} |",
    ]

    if comparison:
        lines += ["", "## 직전 실행 대비", "",
                  "| 항목 | 이전 | 현재 | 변화 | 판정 |", "|---|---|---|---|---|"]
        for row in comparison:
            lines.append(
                f"| {row['column']} | {_fmt(row['previous'])} | {_fmt(row['current'])} | "
                f"{row['delta']:+.4f} | {row['verdict']} |"
            )

    if warnings:
        lines += ["", "## ⚠️ 경고", ""] + [f"- {w}" for w in warnings]

    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(description="이벤트 분류를 반복 검증·개선하는 하네스.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config-tag", default="")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--set", action="append", dest="overrides", metavar="KEY=VALUE",
        help="이번 실행에 한해 .env 를 임시 패치한다 (실행 후 복원)",
    )
    parser.add_argument(
        "--skip-classify", action="store_true",
        help="초기화·분류를 건너뛰고 현재 DB 상태로 채점만 한다",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="초기화 없이 남은 기사만 이어서 분류한다 (중단된 드레인 재개)",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("오류: --database-url 또는 DATABASE_URL 이 필요합니다.", file=sys.stderr)
        sys.exit(1)

    if args.skip_classify and args.resume:
        print("오류: --skip-classify 는 분류를 하지 않으므로 --resume 와 함께 쓸 수 없습니다.",
              file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dotenv_path = _REPO_ROOT / ".env"
    log_path = out_dir / f"event-{args.run_id}-classifier.log"
    snapshot_path = out_dir / f"event-{args.run_id}-snapshot.json"

    original_env_text = None
    try:
        if args.overrides:
            original_env_text = _patch_dotenv(dotenv_path, args.overrides)
            print(f"[설정] .env 임시 패치: {', '.join(args.overrides)}")

        settings = _read_settings(dotenv_path)

        if not args.skip_classify:
            if args.resume:
                # 초기화하면 이미 분류한 기사를 다시 태우게 된다. 중단이 잦은 대량 실행에서
                # 그건 곧 "영원히 못 끝냄"이므로, 남은 잔량만 이어서 돌린다.
                print("[재개] 초기화를 건너뛰고 남은 기사만 이어서 분류합니다.")
            else:
                conn = ensure_db("", database_url=args.database_url)
                try:
                    before = reset_classifier_output(conn)
                finally:
                    conn.close()
                print(f"[초기화] 분류기 출력 삭제 (이벤트 {before['events']:,} / "
                      f"토픽 {before['topics']:,}), "
                      f"기사 요약 {before['article_ai_results']:,}건 보존")

            drain = drain_events(
                [sys.executable, str(_REPO_ROOT / "classify_events.py"),
                 "--database-url", args.database_url],
                os.environ.copy(), args.database_url, log_path,
                log_mode="a" if args.resume else "w",
            )
            if drain["stuck"]:
                print(f"경고: 미처리 기사 {drain['remaining']}건이 남았습니다.", file=sys.stderr)
        else:
            print("[건너뜀] 초기화·분류 없이 현재 DB 상태로 채점합니다.")

        conn = ensure_db("", database_url=args.database_url)
        try:
            snapshot = build_snapshot(
                conn, snapshot_date=datetime.now().isoformat(timespec="seconds")
            )
            rubric = rubric_checks.run_all_checks(conn)
        finally:
            conn.close()
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

        gold = load_json(args.gold)
        logs, order = ({}, {})
        if log_path.exists():
            logs, order = parse_decision_logs(log_path.read_text(encoding="utf-8"))
        diagnosis = run_diagnosis(gold, snapshot, logs, order)

        metrics = collect_metrics(
            run_id=args.run_id, config_tag=args.config_tag,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            gold=gold, snapshot=snapshot, diagnosis=diagnosis,
            rubric=rubric, settings=settings,
        )
    finally:
        if original_env_text is not None:
            dotenv_path.write_text(original_env_text, encoding="utf-8")
            print("[설정] .env 원본 복원")

    csv_path = out_dir / RUNS_CSV
    previous = read_previous_run(csv_path, args.run_id)
    comparison = compare_runs(metrics, previous)
    warnings = detect_warnings(metrics, previous)

    append_csv(csv_path, metrics)
    report_path = out_dir / f"event-{args.run_id}.md"
    report_path.write_text(
        format_report(metrics, diagnosis, comparison, warnings), encoding="utf-8"
    )

    print("\n" + "═" * 64)
    print(f"이벤트 하네스 결과: {args.run_id}  ({args.config_tag or '태그 없음'})")
    print("═" * 64)
    print(f"  must-link  충족률 : {_fmt(metrics['event_must_rate'])}"
          f"  ({_pairs_cell(metrics['event_must_pairs'])})")
    print(f"  cannot-link 충족률 : {_fmt(metrics['event_cannot_rate'])}"
          f"  ({_pairs_cell(metrics['event_cannot_pairs'])})")
    print(f"  이벤트 개수        : {metrics['events_total']:,} "
          f"(단일기사 {metrics['single_article_events']:,})")
    # 진단 불가(?)를 빼면 "A 0 / B 0 / C 0 / D 0" 이 "위반 원인 없음"으로 읽힌다.
    # --skip-classify 로 재채점하면 분류기 로그가 없어 전부 ?로 떨어지는데, 그 상태가
    # 원인이 사라진 것처럼 보이면 안 된다.
    nolog = metrics.get("c_nolog") or 0
    print(f"  위반 원인          : A {metrics['c_a']} / B {metrics['c_b']} / "
          f"C {metrics['c_c']} / D {metrics['c_d']}"
          + (f" / ? {nolog} (진단 불가)" if nolog else ""))

    if comparison:
        # 정답이 바뀌었으면 개선/악화 판정 자체가 성립하지 않는다. 경고만 덧붙이면
        # 초록색 "개선" 딱지가 먼저 눈에 들어와 경고를 이긴다.
        comparable = not harness_common.gold_change_warning(metrics, previous)
        suffix = "" if comparable else "  (채점 기준이 달라 비교 불가)"
        print(f"\n  직전 실행({previous.get('run_id')}) 대비:{suffix}")
        for row in comparison:
            if row["verdict"] == "유지":
                continue
            verdict = row["verdict"] if comparable else "—"
            print(f"    {row['column']:<22} {_fmt(row['previous'])} → "
                  f"{_fmt(row['current'])}  {verdict}")
    else:
        print("\n  (비교할 직전 실행이 없습니다 — 이번이 기준선입니다)")

    for warning in warnings:
        print(f"\n  ⚠️  {warning}")

    print(f"\n  리포트: {report_path}")
    print(f"  누적  : {csv_path}")
    if not args.skip_classify:
        # 초기화든 재개든 이벤트가 새로 매겨졌으므로 기존 토픽 레이어는 유효하지 않다.
        # --skip-classify 는 DB를 건드리지 않으므로 해당 없다.
        print("  ※ 이벤트가 새로 매겨져 토픽 레이어는 유효하지 않습니다. 토픽 지표가 "
              "필요하면 topic_harness.py 를 이어서 돌리세요.")
    print("═" * 64)


if __name__ == "__main__":
    main()

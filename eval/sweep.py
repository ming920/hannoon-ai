"""여러 설정을 연속 실행하고 결과를 한 표로 모으는 스윕 러너.

하네스 한 번 실행은 설정 하나를 잰다. 개선은 설정 여럿을 비교해야 하므로, 그 반복을
무인으로 돌리는 것이 이 스크립트다.

    python eval/sweep.py --harness topic --database-url "postgresql://localhost/..." \\
        --run-prefix s1 \\
        --config "baseline:" \\
        --config "assign070:TOPIC_ASSIGN_SCORE_THRESHOLD=0.70" \\
        --config "dist060:TOPIC_DISTANCE_THRESHOLD=0.60,TOPIC_ASSIGN_SCORE_THRESHOLD=0.85"

각 `--config` 는 `태그:KEY=VAL,KEY=VAL` 형식이다. 오버라이드가 비면 현재 .env 그대로
(기준선) 돌린다. run-id 는 `<prefix>-<태그>` 로 만들어진다.

**--dry-run 을 먼저 돌려보길 권한다.** 스윕은 설정 수만큼 전체 분류를 반복하므로 API
비용이 곱해진다. 무엇이 몇 번 돌지 확인한 뒤 실행하는 편이 안전하다.

실행이 끝나면 하네스가 쌓아온 CSV에서 이번 스윕의 행만 골라 비교표를 출력한다. 하네스의
열 정의를 그대로 쓰므로 이벤트/토픽 어느 쪽이든 같은 방식으로 동작한다.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import unicodedata
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVAL_DIR))

HARNESSES = {
    "topic": {"module": "topic_harness", "script": "topic_harness.py"},
    "event": {"module": "event_harness", "script": "event_harness.py"},
}

# 스윕 요약표에 싣는 열 (하네스별). 전체 CSV 열은 너무 넓어 핵심만 고른다.
_SUMMARY_COLUMNS = {
    "topic": ["config_tag", "topic_must_rate", "topics_total", "rubric_t1",
              "t_propagated", "t_a", "t_c", "t_d"],
    "event": ["config_tag", "event_must_rate", "event_cannot_rate", "events_total",
              "single_article_events", "c_a", "c_b", "c_c", "c_d"],
}


# ══════════════════════════════════════════════════════════════════════════
# 순수 함수
# ══════════════════════════════════════════════════════════════════════════


def parse_config(spec: str) -> tuple[str, list[str]]:
    """`태그:KEY=VAL,KEY=VAL` 을 (태그, 오버라이드 목록)으로 나눈다.

    오버라이드가 비면 현재 .env 그대로 돌리는 기준선이다.
    """
    if ":" not in spec:
        raise ValueError(
            f"잘못된 --config 형식: {spec!r}. '태그:KEY=VAL,KEY=VAL' 이어야 합니다 "
            "(기준선은 '태그:' 처럼 콜론 뒤를 비웁니다)."
        )
    tag, _, rest = spec.partition(":")
    tag = tag.strip()
    if not tag:
        raise ValueError(f"--config 의 태그가 비었습니다: {spec!r}")
    overrides = [kv.strip() for kv in rest.split(",") if kv.strip()]
    for kv in overrides:
        if "=" not in kv:
            raise ValueError(f"오버라이드는 KEY=VALUE 여야 합니다: {kv!r} (in {spec!r})")
    return tag, overrides


def build_run_plan(prefix: str, specs: list[str]) -> list[dict]:
    """--config 목록을 실행 계획으로 바꾼다. 중복 태그는 거부한다."""
    plan, seen = [], set()
    for spec in specs:
        tag, overrides = parse_config(spec)
        if tag in seen:
            raise ValueError(f"태그가 중복됩니다: {tag!r}. run-id 가 겹쳐 결과를 덮어씁니다.")
        seen.add(tag)
        plan.append({"tag": tag, "run_id": f"{prefix}-{tag}", "overrides": overrides})
    return plan


def read_sweep_rows(csv_path: Path, run_ids: list[str]) -> list[dict]:
    """하네스 CSV에서 이번 스윕의 행만, 계획 순서대로 고른다."""
    if not csv_path.exists():
        return []
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    by_id = {}
    for row in rows:            # 같은 run_id가 여러 번이면 마지막 것을 쓴다
        by_id[row.get("run_id")] = row
    return [by_id[rid] for rid in run_ids if rid in by_id]


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def format_summary(rows: list[dict], columns: list[str]) -> str:
    """스윕 결과를 정렬된 표 문자열로 만든다."""
    if not rows:
        return "  (요약할 결과가 없습니다)"
    widths = [
        max(_width(col), max(_width(str(r.get(col, ""))) for r in rows)) + 2
        for col in columns
    ]
    lines = ["  " + "".join(_pad(c, w) for c, w in zip(columns, widths))]
    lines.append("  " + "-" * (sum(widths) - 2))
    for row in rows:
        lines.append(
            "  " + "".join(_pad(str(row.get(c, "")), w) for c, w in zip(columns, widths))
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="여러 설정으로 하네스를 연속 실행하고 결과를 비교한다."
    )
    parser.add_argument("--harness", choices=sorted(HARNESSES), required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--run-prefix", required=True, help="run-id 접두사 (예: s1)")
    parser.add_argument(
        "--config", action="append", dest="configs", required=True, metavar="TAG:K=V,K=V",
        help="설정 하나 (반복 지정). 기준선은 'baseline:' 처럼 콜론 뒤를 비웁니다.",
    )
    parser.add_argument("--out-dir", default=str(_EVAL_DIR / "results"))
    parser.add_argument(
        "--dry-run", action="store_true",
        help="실행 계획만 출력한다. API 비용이 설정 수만큼 곱해지므로 먼저 확인하길 권한다.",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="설정 하나가 실패해도 나머지를 계속 실행한다 (기본: 중단)",
    )
    args = parser.parse_args()

    try:
        plan = build_run_plan(args.run_prefix, args.configs)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)

    harness = HARNESSES[args.harness]
    script = _EVAL_DIR / harness["script"]
    out_dir = Path(args.out_dir)

    print("═" * 64)
    print(f"스윕 계획: {args.harness} 하네스 / {len(plan)}개 설정")
    print("═" * 64)
    for i, item in enumerate(plan, 1):
        overrides = ", ".join(item["overrides"]) or "(현재 .env 그대로)"
        print(f"  {i}. {item['run_id']:<24} {overrides}")
    print(f"\n  ⚠️  전체 분류가 {len(plan)}회 반복됩니다. API 비용도 그만큼 곱해집니다.")

    if args.dry_run:
        print("\n  --dry-run 이므로 실행하지 않고 종료합니다.")
        print("═" * 64)
        return

    if not args.database_url:
        print("\n오류: --database-url 이 필요합니다.", file=sys.stderr)
        sys.exit(1)

    failures = []
    for i, item in enumerate(plan, 1):
        print("\n" + "─" * 64)
        print(f"[{i}/{len(plan)}] {item['run_id']}")
        print("─" * 64)
        cmd = [
            sys.executable, str(script),
            "--database-url", args.database_url,
            "--run-id", item["run_id"],
            "--config-tag", item["tag"],
            "--out-dir", str(out_dir),
        ]
        for kv in item["overrides"]:
            cmd += ["--set", kv]

        result = subprocess.run(cmd)
        if result.returncode != 0:
            failures.append(item["run_id"])
            print(f"\n[실패] {item['run_id']} 종료 코드 {result.returncode}", file=sys.stderr)
            if not args.continue_on_error:
                print("중단합니다 (--continue-on-error 로 계속할 수 있습니다).",
                      file=sys.stderr)
                break

    # ── 스윕 요약 ──────────────────────────────────────────────────────
    module = __import__(harness["module"])
    csv_path = out_dir / module.RUNS_CSV
    rows = read_sweep_rows(csv_path, [p["run_id"] for p in plan])

    print("\n" + "═" * 64)
    print(f"스윕 요약 ({len(rows)}/{len(plan)} 완료)")
    print("═" * 64)
    print(format_summary(rows, _SUMMARY_COLUMNS[args.harness]))
    if failures:
        print(f"\n  실패한 설정: {', '.join(failures)}")
    print(f"\n  누적 CSV: {csv_path}")
    print("  ※ 충족률만 보고 고르지 마세요. 구조 지표(개수·루브릭)가 같이 나빠졌다면")
    print("     점수를 산 것입니다. 각 실행의 .md 리포트에 경고가 있는지 확인하세요.")
    print("═" * 64)

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

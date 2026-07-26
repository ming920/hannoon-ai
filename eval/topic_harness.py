"""토픽 분류 반복 검증·개선 하네스.

한 번 실행하면 다음을 끝까지 돌리고 결과를 누적한다.

  1. 토픽 레이어만 초기화        이벤트는 보존한다. 토픽 레버의 효과만 분리해서 보기 위함이며,
                                 이벤트 재분류 비용도 들지 않는다.
  2. classify_topics 드레인      미배정 이벤트가 0이 될 때까지 반복 호출하고 stdout(JSONL
                                 판단 로그)을 파일로 캡처한다.
  3. 스냅샷 추출                 extract_snapshot 으로 DB → JSON.
  4. 제약 충족률                 constraint_checks 로 정답 대비 4개 충족률.
  5. 위반 원인 진단              diagnose_violations 로 A/B/C/D + 이벤트 전파 분리.
  6. 루브릭 교차 확인            rubric_checks 로 R-T1(중복 토픽) 등 과병합 신호.
  7. 결과 누적                   results/topic_runs.csv 에 한 줄, results/topic-<run-id>.md 에
                                 상세 리포트. 직전 실행과 자동 비교한다.

**충족률만 보면 반드시 속는다.** 정답의 토픽 cannot-link 제약은 0쌍이라, 모든 이벤트를 한
토픽에 몰아넣어도 must-link 충족률은 100%가 나온다. 그래서 토픽 개수와 루브릭 R-T1을 같은
표에 함께 싣고, 충족률이 올랐는데 토픽 수가 급감하면 경고한다.

사용 예:
    # 기준선 확립
    python eval/topic_harness.py --database-url "postgresql://localhost/..." \\
        --run-id t-000 --config-tag baseline

    # 파라미터를 바꿔 재실행 (직전 실행과 자동 비교)
    python eval/topic_harness.py --database-url "postgresql://localhost/..." \\
        --run-id t-001 --config-tag "assign 0.70" \\
        --set TOPIC_ASSIGN_SCORE_THRESHOLD=0.70

    # 이미 분류된 DB에서 채점만 다시
    python eval/topic_harness.py --run-id t-001-rescore --skip-classify

`--set` 은 .env 를 임시 패치했다가 실행 후 복원한다. 분류기가 load_dotenv(override=True) 를
쓰므로 프로세스 환경변수 주입은 효과가 없다 — 이 플래그가 유일하게 동작하는 방법이다.
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
from constraint_checks import evaluate_pairs  # noqa: E402
from harness_common import coerce as _coerce  # noqa: E402
from harness_common import fmt as _fmt  # noqa: E402
from harness_common import patch_dotenv as _patch_dotenv  # noqa: E402
from harness_common import read_previous_run  # noqa: E402
from harness_common import read_settings as _read_settings  # noqa: E402
from constraint_checks import (  # noqa: E402
    build_event_cluster_map,
    build_topic_cluster_map,
    load_json,
)
from diagnose_violations import (  # noqa: E402
    T_A_NOT_RETRIEVED,
    T_B_TRUNCATED,
    T_C_LLM_REJECTED,
    T_D_GUARDRAIL,
    T_EVENT_PROPAGATED,
    T_NO_LOG,
    T_SUBTOPIC_SPLIT,
    parse_topic_logs,
    run_diagnosis,
)
from extract_snapshot import build_snapshot  # noqa: E402

DEFAULT_GOLD = _EVAL_DIR / "data" / "constraints" / "review_2026-07-20.json"
DEFAULT_OUT_DIR = _EVAL_DIR / "results"
RUNS_CSV = "topic_runs.csv"

# 실행 간 비교에 쓰는 열. 순서를 바꾸면 기존 CSV와 어긋나므로 뒤에만 추가할 것.
CSV_COLUMNS = [
    "run_id", "config_tag", "timestamp",
    "topic_must_rate", "topic_cannot_rate",
    "event_must_rate", "event_cannot_rate",
    "topics_total", "events_total", "events_assigned",
    "t_propagated", "t_subtopic_split", "t_a", "t_b", "t_c", "t_d", "t_nolog",
    "rubric_t1", "rubric_t2", "rubric_s1_ratio",
    "subtopic_mode", "assign_score", "subtopic_assign_score",
    "distance_threshold", "candidate_limit", "dup_sim_threshold",
]

# 값이 클수록 좋은 열 (비교 표에서 방향 판정용)
_HIGHER_IS_BETTER = {
    "topic_must_rate", "topic_cannot_rate", "event_must_rate", "event_cannot_rate",
}
# 값이 작을수록 좋은 열
_LOWER_IS_BETTER = {"rubric_t1", "rubric_t2", "rubric_s1_ratio"}


# ══════════════════════════════════════════════════════════════════════════
# DB 조작 (토픽 레이어 한정)
# ══════════════════════════════════════════════════════════════════════════


def reset_topic_layer(conn) -> int:
    """이벤트 레이어를 보존한 채 토픽 레이어만 초기화하고, 보존된 이벤트 수를 반환한다.

    FK-안전 순서로 한 트랜잭션에서 실행한다.
      1. UPDATE events SET topic_id = NULL   이벤트 ↔ 토픽 연결 해제
      2. DELETE FROM topic_causes            토픽 원인 벡터
      3. DELETE FROM topics                  토픽

    event_articles / article_ai_results / 임베딩은 건드리지 않는다.
    (run_iteration.py 에 합성 루프용 동등 구현이 있다. 이쪽은 conn을 받아 테스트 가능하게 뒀다.)
    """
    row = conn.query_one("SELECT COUNT(*) AS cnt FROM events")
    event_count = int(row["cnt"]) if row else 0
    if event_count == 0:
        raise ValueError(
            "events 테이블이 비어 있습니다. 토픽 하네스는 이벤트가 이미 분류된 DB에서만 "
            "쓸 수 있습니다 — classify_events.py 를 먼저 돌리세요."
        )
    with conn.transaction():
        conn.execute("UPDATE events SET topic_id = NULL")
        conn.execute("DELETE FROM topic_causes")
        conn.execute("DELETE FROM topics")
    return event_count


def count_unassigned_events(conn, min_net: int) -> int:
    """토픽 미배정 이벤트 수 (topic_id IS NULL, article_count >= min_net)."""
    row = conn.query_one(
        "SELECT COUNT(*) AS cnt FROM events WHERE topic_id IS NULL AND article_count >= ?",
        (min_net,),
    )
    return int(row["cnt"]) if row else 0


def drain_topics(cmd: list, env: dict, db_url: str, min_net: int, log_path: Path) -> dict:
    """미배정 이벤트가 없어질 때까지 토픽 분류기를 반복 호출하고 stdout을 파일에 모은다."""
    def _remaining() -> int:
        conn = ensure_db("", database_url=db_url)
        try:
            return count_unassigned_events(conn, min_net)
        finally:
            conn.close()

    return harness_common.drain(
        cmd=cmd, env=env, remaining_fn=_remaining,
        label=f"토픽 드레인 (article_count >= {min_net})", log_path=log_path,
    )


# ══════════════════════════════════════════════════════════════════════════
# 지표 조립 (순수 함수 — DB·LLM 의존 없음)
# ══════════════════════════════════════════════════════════════════════════


def _rate(pairs, clusters, *, expect_same):
    return evaluate_pairs(pairs, clusters, expect_same=expect_same, examples_limit=0)["rate"]


def _rubric_value(rubric: dict, check_id: str, key: str):
    """루브릭 결과에서 값을 안전하게 꺼낸다 (검사가 skipped면 None)."""
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
    event_clusters = build_event_cluster_map(snapshot.get("events") or [])
    topic_clusters = build_topic_cluster_map(snapshot.get("topics") or [])
    ec = gold.get("event_constraints") or {}
    tc = gold.get("topic_constraints") or {}
    causes = diagnosis.get("topic_must_link_causes") or {}
    events = snapshot.get("events") or []

    return {
        "run_id": run_id,
        "config_tag": config_tag,
        "timestamp": timestamp,
        "topic_must_rate": _rate(tc.get("must_link"), topic_clusters, expect_same=True),
        "topic_cannot_rate": _rate(tc.get("cannot_link"), topic_clusters, expect_same=False),
        "event_must_rate": _rate(ec.get("must_link"), event_clusters, expect_same=True),
        "event_cannot_rate": _rate(ec.get("cannot_link"), event_clusters, expect_same=False),
        "topics_total": len(snapshot.get("topics") or []),
        "events_total": len(events),
        "events_assigned": sum(
            1 for t in snapshot.get("topics") or [] for _ in t.get("events") or []
        ),
        "t_propagated": causes.get(T_EVENT_PROPAGATED, 0),
        "t_subtopic_split": causes.get(T_SUBTOPIC_SPLIT, 0),
        "t_a": causes.get(T_A_NOT_RETRIEVED, 0),
        "t_b": causes.get(T_B_TRUNCATED, 0),
        "t_c": causes.get(T_C_LLM_REJECTED, 0),
        "t_d": causes.get(T_D_GUARDRAIL, 0),
        "t_nolog": causes.get(T_NO_LOG, 0),
        "rubric_t1": _rubric_value(rubric, "R-T1", "violations"),
        "rubric_t2": _rubric_value(rubric, "R-T2", "violations"),
        "rubric_s1_ratio": _rubric_value(rubric, "R-S1", "ratio"),
        "subtopic_mode": settings.get("SUBTOPIC_MODE", ""),
        "assign_score": settings.get("TOPIC_ASSIGN_SCORE_THRESHOLD", ""),
        "subtopic_assign_score": settings.get("TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD", ""),
        "distance_threshold": settings.get("TOPIC_DISTANCE_THRESHOLD", ""),
        "candidate_limit": settings.get("TOPIC_CANDIDATE_LIMIT", ""),
        "dup_sim_threshold": settings.get("TOPIC_DUP_SIM_THRESHOLD", ""),
    }


def detect_warnings(current: dict, previous: dict | None) -> list[str]:
    """숫자만으로는 놓치기 쉬운 실패 양상을 잡아낸다."""
    warnings = []

    if current["topics_total"] == 0:
        warnings.append("토픽이 0개입니다 — 분류가 실행되지 않았을 수 있습니다.")

    if current["topic_cannot_rate"] is None:
        warnings.append(
            "토픽 cannot-link 제약이 없어 과병합을 충족률로 감지할 수 없습니다. "
            "topics_total 과 rubric_t1 을 함께 보세요."
        )

    if previous:
        # CSV에서 읽은 값은 전부 문자열이므로 반드시 숫자로 되돌린 뒤 비교한다.
        # 이걸 빼면 아래 과병합 경고가 CSV 경로에서 영원히 발화하지 않는다.
        prev_topics = _coerce(previous.get("topics_total"))
        cur_rate = current["topic_must_rate"]
        prev_rate = _coerce(previous.get("topic_must_rate"))
        if (
            isinstance(prev_topics, (int, float)) and prev_topics
            and isinstance(cur_rate, float) and isinstance(prev_rate, float)
            and cur_rate > prev_rate
            and current["topics_total"] < prev_topics * 0.8
        ):
            warnings.append(
                f"충족률이 올랐지만 토픽 수가 {prev_topics} → {current['topics_total']}로 "
                "20% 넘게 줄었습니다. 과병합으로 점수를 샀을 가능성이 높습니다."
            )

    if current["t_propagated"] and current["t_subtopic_split"] + current["t_a"] + \
            current["t_c"] + current["t_d"] == 0:
        warnings.append(
            "토픽 위반이 전부 이벤트 분류 실패의 전파입니다. 토픽 레버를 아무리 만져도 "
            "개선되지 않습니다 — 이벤트 분류를 먼저 고치세요."
        )

    return warnings


def compare_runs(current: dict, previous: dict | None) -> list[dict]:
    """직전 실행 대비 변화를 열별로 계산한다 (토픽 하네스의 열 정의를 적용)."""
    return harness_common.compare_runs(
        current, previous, columns=CSV_COLUMNS,
        higher_is_better=_HIGHER_IS_BETTER, lower_is_better=_LOWER_IS_BETTER,
    )


# ══════════════════════════════════════════════════════════════════════════
# 결과 누적
# ══════════════════════════════════════════════════════════════════════════


def append_csv(path: Path, row: dict) -> None:
    """실행 결과를 CSV에 한 줄 덧붙인다 (헤더는 최초 1회)."""
    harness_common.append_csv(path, CSV_COLUMNS, row)


def format_report(metrics: dict, diagnosis: dict, comparison: list, warnings: list) -> str:
    """마크다운 상세 리포트를 만든다."""
    meta = diagnosis.get("meta") or {}
    lines = [
        f"# 토픽 하네스 실행: {metrics['run_id']}",
        "",
        f"- 설정 태그: `{metrics['config_tag']}`",
        f"- 실행 시각: {metrics['timestamp']}",
        f"- 서브토픽 모드: `{metrics['subtopic_mode'] or '(미기록)'}`",
        f"- 파라미터: assign={metrics['assign_score']} / "
        f"sub_assign={metrics['subtopic_assign_score']} / "
        f"distance={metrics['distance_threshold']} / top_k={metrics['candidate_limit']}",
        "",
        "## 제약 충족률",
        "",
        "| 제약 | 충족률 |",
        "|---|---|",
        f"| 토픽 must-link | {_fmt(metrics['topic_must_rate'])} |",
        f"| 토픽 cannot-link | {_fmt(metrics['topic_cannot_rate'])} |",
        f"| 이벤트 must-link (참고, 이번 실행에서 불변) | {_fmt(metrics['event_must_rate'])} |",
        f"| 이벤트 cannot-link (참고) | {_fmt(metrics['event_cannot_rate'])} |",
        "",
        "## 구조 지표 (충족률만으로는 못 보는 것)",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 토픽 개수 | {metrics['topics_total']} |",
        f"| 이벤트 개수 | {metrics['events_total']} |",
        f"| 토픽에 배정된 이벤트 | {metrics['events_assigned']} |",
        f"| R-T1 중복·고유사 토픽 쌍 | {_fmt(metrics['rubric_t1'])} |",
        f"| R-T2 카테고리형 토픽 제목 | {_fmt(metrics['rubric_t2'])} |",
        f"| R-S1 단일 이벤트 서브토픽 비율 | {_fmt(metrics['rubric_s1_ratio'])} |",
        "",
        "## 토픽 must-link 위반 원인",
        "",
        f"위반 {meta.get('topic_must_link_violated', 0):,} / "
        f"{meta.get('topic_must_link_total', 0):,}쌍",
        "",
        "| 원인 | 건수 |",
        "|---|---|",
        f"| 0. 이벤트 분류 실패의 전파 | {metrics['t_propagated']} |",
        f"| 1. 부모 동일·서브토픽 갈림 | {metrics['t_subtopic_split']} |",
        f"| A. 부모 후보 미회수 | {metrics['t_a']} |",
        f"| B. 프롬프트 절삭 | {metrics['t_b']} |",
        f"| C. LLM 거절 | {metrics['t_c']} |",
        f"| D. 가드레일 개입 | {metrics['t_d']} |",
        f"| ?. 진단 불가 | {metrics['t_nolog']} |",
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
    parser = argparse.ArgumentParser(
        description="토픽 분류를 반복 검증·개선하는 하네스."
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--gold", default=str(DEFAULT_GOLD), help="검수 정답 JSON")
    parser.add_argument("--run-id", required=True, help="이번 실행 식별자 (예: t-001)")
    parser.add_argument("--config-tag", default="", help="설정 설명 태그 (CSV 기록용)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--set", action="append", dest="overrides", metavar="KEY=VALUE",
        help="이번 실행에 한해 .env 를 임시 패치한다 (반복 지정 가능, 실행 후 복원)",
    )
    parser.add_argument(
        "--skip-classify", action="store_true",
        help="초기화·분류를 건너뛰고 현재 DB 상태로 채점만 한다",
    )
    parser.add_argument(
        "--min-net", type=int, default=None,
        help="토픽 배정 자격 기준 article_count (기본: TOPIC_MIN_NET_ARTICLE_COUNT 또는 5)",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("오류: --database-url 또는 DATABASE_URL 이 필요합니다.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dotenv_path = _REPO_ROOT / ".env"
    log_path = out_dir / f"topic-{args.run_id}-classifier.log"
    snapshot_path = out_dir / f"topic-{args.run_id}-snapshot.json"

    original_env_text = None
    try:
        if args.overrides:
            original_env_text = _patch_dotenv(dotenv_path, args.overrides)
            print(f"[설정] .env 임시 패치: {', '.join(args.overrides)}")

        settings = _read_settings(dotenv_path)
        min_net = args.min_net or int(settings.get("TOPIC_MIN_NET_ARTICLE_COUNT", 5))

        if not args.skip_classify:
            conn = ensure_db("", database_url=args.database_url)
            try:
                preserved = reset_topic_layer(conn)
            finally:
                conn.close()
            print(f"[초기화] 이벤트 {preserved:,}개 보존, 토픽 레이어만 삭제")

            drain = drain_topics(
                [sys.executable, str(_REPO_ROOT / "classify_topics.py"),
                 "--database-url", args.database_url],
                os.environ.copy(), args.database_url, min_net, log_path,
            )
            if drain["stuck"]:
                print(f"경고: 미배정 이벤트 {drain['remaining']}건이 남았습니다.",
                      file=sys.stderr)
        else:
            print("[건너뜀] 초기화·분류 없이 현재 DB 상태로 채점합니다.")

        # 스냅샷 + 루브릭은 같은 연결에서 읽는다.
        conn = ensure_db("", database_url=args.database_url)
        try:
            snapshot = build_snapshot(
                conn, snapshot_date=datetime.now().isoformat(timespec="seconds")
            )
            rubric = rubric_checks.run_all_checks(conn)
        finally:
            conn.close()
        snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
        )

        gold = load_json(args.gold)
        topic_logs, topic_order = ({}, {})
        if log_path.exists():
            topic_logs, topic_order = parse_topic_logs(
                log_path.read_text(encoding="utf-8")
            )
        diagnosis = run_diagnosis(gold, snapshot, {}, {}, topic_logs, topic_order)

        metrics = collect_metrics(
            run_id=args.run_id,
            config_tag=args.config_tag,
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
    report_path = out_dir / f"topic-{args.run_id}.md"
    report_path.write_text(
        format_report(metrics, diagnosis, comparison, warnings), encoding="utf-8"
    )

    # ── 콘솔 요약 ──────────────────────────────────────────────────────
    print("\n" + "═" * 64)
    print(f"토픽 하네스 결과: {args.run_id}  ({args.config_tag or '태그 없음'})")
    print("═" * 64)
    print(f"  토픽 must-link 충족률 : {_fmt(metrics['topic_must_rate'])}")
    print(f"  토픽 개수             : {metrics['topics_total']:,}  "
          f"(이벤트 {metrics['events_total']:,})")
    print(f"  R-T1 중복 토픽 쌍     : {_fmt(metrics['rubric_t1'])}")
    print(f"  위반 원인             : 전파 {metrics['t_propagated']} / "
          f"서브분할 {metrics['t_subtopic_split']} / A {metrics['t_a']} / "
          f"B {metrics['t_b']} / C {metrics['t_c']} / D {metrics['t_d']}")

    if comparison:
        print(f"\n  직전 실행({previous.get('run_id')}) 대비:")
        for row in comparison:
            if row["verdict"] == "유지":
                continue
            print(f"    {row['column']:<22} {_fmt(row['previous'])} → "
                  f"{_fmt(row['current'])}  {row['verdict']}")
    else:
        print("\n  (비교할 직전 실행이 없습니다 — 이번이 기준선입니다)")

    for warning in warnings:
        print(f"\n  ⚠️  {warning}")

    print(f"\n  리포트: {report_path}")
    print(f"  누적  : {csv_path}")
    print("═" * 64)


if __name__ == "__main__":
    main()

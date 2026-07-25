"""제약 위반의 원인을 분류기 로그와 대조해 A/B/C/D 유형으로 진단한다.

`constraint_checks.py`는 "어떤 기사 쌍이 틀렸는가"까지만 알려준다. 같은 위반이라도
원인이 넷이고 처방이 서로 다르기 때문에, 그것만으로는 무엇을 고쳐야 할지 알 수 없다.
이 스크립트는 `classify_events.py`가 남긴 판단 로그와 위반 목록을 조인해 원인을 가른다.

  A. 후보 검색에 없음      상대 이벤트가 pgvector 후보 목록에 아예 없었다
                           → EVENT_DISTANCE_THRESHOLD 완화
  B. 프롬프트에서 절삭     후보에는 있었지만 LLM에게 보여주지 않았다
                           → EVENT_CANDIDATE_LIMIT > MAX_EVENT_CANDIDATES 불일치
  C. LLM이 거절            LLM이 보고도 다른 사건이라 판단했다
                           → 배정 프롬프트 수정
  D. 가드레일이 뒤집음     LLM은 배정하려 했는데 점수 미달/부정 표현으로 create 강제
                           → EVENT_ASSIGN_SCORE_THRESHOLD 완화

이벤트 제약만 다룬다. 토픽 제약은 토픽 분류기 로그가 필요한데 아직 같은 진단 필드를
남기지 않는다.

사용 예:
  # 1) 분류기 로그를 파일로 받는다 (stdout이 JSONL)
  python classify_events.py --database-url "postgresql://..." > events.log

  # 2) 정답 + 결과 스냅샷 + 로그를 대조한다
  python eval/diagnose_violations.py `
      eval/data/constraints/review_2026-07-20.json snapshot.json events.log

  # 3) 기계 판독 출력
  python eval/diagnose_violations.py 정답.json snapshot.json events.log --json
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from constraint_checks import (  # noqa: E402
    build_event_cluster_map,
    evaluate_pairs,
    load_json,
)

# 원인 코드와 사람이 읽는 이름
CAUSE_A = "A_NOT_RETRIEVED"
CAUSE_B = "B_TRUNCATED"
CAUSE_C = "C_LLM_REJECTED"
CAUSE_D = "D_GUARDRAIL"
CAUSE_NO_LOG = "UNKNOWN_NO_LOG"

MUST_CAUSES = (CAUSE_A, CAUSE_B, CAUSE_C, CAUSE_D, CAUSE_NO_LOG)

_CAUSE_LABELS = {
    CAUSE_A: "A. 후보 검색에 없음 (거리 임계값)",
    CAUSE_B: "B. 검색됐으나 프롬프트에서 절삭",
    CAUSE_C: "C. LLM이 다른 사건으로 판단",
    CAUSE_D: "D. 가드레일이 LLM의 assign을 뒤집음",
    CAUSE_NO_LOG: "?. 로그에 없어 진단 불가",
}

_CAUSE_FIXES = {
    CAUSE_A: "EVENT_DISTANCE_THRESHOLD 를 올려 후보 회수를 늘린다",
    CAUSE_B: "MAX_EVENT_CANDIDATES(prompts.py) 를 EVENT_CANDIDATE_LIMIT 이상으로 맞춘다",
    CAUSE_C: "build_event_assignment_prompt 를 수정한다 (임계값으로는 안 고쳐진다)",
    CAUSE_D: "EVENT_ASSIGN_SCORE_THRESHOLD 를 낮춘다",
}


# ══════════════════════════════════════════════════════════════════════════
# 순수 함수 — 로그 파싱 / 원인 판정 / 임계값 시뮬레이션
# ══════════════════════════════════════════════════════════════════════════


def parse_decision_logs(text: str) -> tuple[dict[int, dict], dict[int, int]]:
    """분류기 stdout에서 판단 로그(JSONL)만 골라 읽는다.

    `[event] 처리 대상 기사: N건` 같은 사람용 출력이 섞여 있으므로 JSON 파싱에
    실패하는 줄과 `article_id`가 없는 객체는 건너뛴다.

    반환: (기사id → 로그, 기사id → 처리 순번). 순번은 어느 기사가 먼저 처리돼
    이벤트를 선점했는지 판정하는 데 쓴다.
    """
    logs: dict[int, dict] = {}
    order: dict[int, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or "article_id" not in entry:
            continue
        article_id = entry["article_id"]
        if article_id in order:  # 같은 기사가 두 번 나오면 첫 처리를 기준으로 삼는다
            continue
        order[article_id] = len(order)
        logs[article_id] = entry
    return logs, order


def find_candidate(log: dict, event_id: int) -> dict | None:
    """로그의 후보 목록에서 특정 이벤트를 찾는다. 없으면 None."""
    for candidate in log.get("candidates") or []:
        if candidate.get("event_id") == event_id:
            return candidate
    return None


def diagnose_must_link(
    pair: list[int],
    clusters: dict[int, int],
    logs: dict[int, dict],
    order: dict[int, int],
) -> dict:
    """must-link 위반 한 건의 원인을 가른다.

    나중에 처리된 기사가 "먼저 자리잡은 이벤트에 붙을 기회"를 가졌던 쪽이므로,
    그쪽 로그를 보고 왜 안 붙었는지 판정한다.
    """
    a, b = pair[0], pair[1]
    if a not in order or b not in order:
        missing = [x for x in (a, b) if x not in order]
        return {"pair": [a, b], "cause": CAUSE_NO_LOG, "missing_from_log": missing}

    later, earlier = (a, b) if order[a] > order[b] else (b, a)
    target_event = clusters.get(earlier)
    log = logs[later]

    base = {
        "pair": [a, b],
        "later_article": later,
        "earlier_article": earlier,
        "target_event": target_event,
        "landed_event": clusters.get(later),
    }

    candidate = find_candidate(log, target_event)
    if candidate is None:
        return {**base, "cause": CAUSE_A, "candidate_count": log.get("candidate_count", 0)}

    base["distance"] = candidate.get("distance")
    if not candidate.get("shown_to_llm", True):
        rank = [c.get("event_id") for c in log["candidates"]].index(target_event)
        return {**base, "cause": CAUSE_B, "rank": rank}

    decision = log.get("llm_decision") or {}
    score = decision.get("score")
    if log.get("overridden") and decision.get("event_id") == target_event:
        return {**base, "cause": CAUSE_D, "llm_score": score,
                "llm_reason": decision.get("reason")}

    return {**base, "cause": CAUSE_C, "llm_action": decision.get("action"),
            "llm_chose_event": decision.get("event_id"), "llm_score": score,
            "llm_reason": decision.get("reason")}


def diagnose_cannot_link(
    pair: list[int],
    clusters: dict[int, int],
    logs: dict[int, dict],
    order: dict[int, int],
) -> dict:
    """cannot-link 위반(붙지 말아야 할 둘이 같은 이벤트) 한 건을 기록한다.

    원인은 하나뿐이라(배정이 일어났다) 유형 분류 대신 배정 당시의 점수·거리를 남긴다.
    이 점수 분포가 곧 "임계값을 얼마로 올리면 막히는가"의 근거가 된다.
    """
    a, b = pair[0], pair[1]
    if a not in order or b not in order:
        return {"pair": [a, b], "cause": CAUSE_NO_LOG}

    later, earlier = (a, b) if order[a] > order[b] else (b, a)
    log = logs[later]
    decision = log.get("llm_decision") or {}
    candidate = find_candidate(log, clusters.get(earlier))
    return {
        "pair": [a, b],
        "later_article": later,
        "merged_into_event": clusters.get(later),
        "llm_score": decision.get("score"),
        "llm_reason": decision.get("reason"),
        "distance": (candidate or {}).get("distance"),
    }


def simulate_score_threshold(
    guardrail_violations: list[dict],
    satisfied_cannot_link: list[dict],
    current_threshold: float,
    steps: list[float],
) -> list[dict]:
    """점수 임계값을 낮췄을 때의 양방향 효과를 계산한다.

    낮추면: D 유형 위반이 구제된다 (LLM의 assign이 살아남으므로)
    낮추면: 가드레일이 막아주던 과병합이 통과해 cannot-link가 깨질 수 있다

    두 수치를 같이 봐야 최적점을 고를 수 있다. 단 이것은 **1차 근사**다 —
    실제로 임계값을 바꾸면 이벤트 구성 자체가 달라져 이후 기사의 후보 목록도
    연쇄적으로 변한다. 최종 확인은 재실행으로 해야 한다.
    """
    rows = []
    for threshold in steps:
        rescued = sum(
            1 for v in guardrail_violations
            if isinstance(v.get("llm_score"), (int, float)) and v["llm_score"] >= threshold
        )
        broken = sum(
            1 for s in satisfied_cannot_link
            if isinstance(s.get("llm_score"), (int, float)) and s["llm_score"] >= threshold
        )
        rows.append(
            {
                "threshold": threshold,
                "is_current": abs(threshold - current_threshold) < 1e-9,
                "must_link_rescued": rescued,
                "cannot_link_broken": broken,
                "net": rescued - broken,
            }
        )
    return rows


def find_guardrail_saves(
    satisfied_cannot_pairs: list,
    clusters: dict[int, int],
    logs: dict[int, dict],
    order: dict[int, int],
) -> list[dict]:
    """가드레일 덕분에 지켜진 cannot-link 쌍을 찾는다.

    임계값을 낮추면 깨질 후보들이다. `evaluate_pairs`는 충족 쌍을 돌려주지 않으므로
    호출부에서 충족 쌍 목록을 따로 넘겨받아 판정한다.
    """
    saves = []
    for pair in satisfied_cannot_pairs:
        a, b = pair[0], pair[1]
        if a not in order or b not in order:
            continue
        later, earlier = (a, b) if order[a] > order[b] else (b, a)
        log = logs[later]
        decision = log.get("llm_decision") or {}
        # 가드레일이 뒤집지 않았다면 임계값을 낮춰도 결과가 바뀌지 않는다.
        if not log.get("overridden"):
            continue
        if decision.get("event_id") != clusters.get(earlier):
            continue
        saves.append({"pair": [a, b], "llm_score": decision.get("score")})
    return saves


def satisfied_pairs(pairs: list, clusters: dict[int, int], *, expect_same: bool) -> list:
    """충족된 쌍만 골라낸다 (evaluate_pairs는 위반만 돌려주므로 보완용)."""
    out = []
    for pair in pairs or []:
        if not pair or len(pair) < 2:
            continue
        ca, cb = clusters.get(pair[0]), clusters.get(pair[1])
        if ca is None or cb is None:
            continue
        if (ca == cb) == expect_same:
            out.append(pair)
    return out


def run_diagnosis(gold: dict, snapshot: dict, logs: dict, order: dict) -> dict:
    """전체 진단을 수행해 집계와 사례를 담은 결과 dict를 만든다."""
    clusters = build_event_cluster_map(snapshot.get("events") or [])
    event_c = gold.get("event_constraints") or {}
    must_pairs = event_c.get("must_link") or []
    cannot_pairs = event_c.get("cannot_link") or []

    big = len(must_pairs) + len(cannot_pairs) + 1  # 사례를 자르지 않기 위한 상한
    must_eval = evaluate_pairs(must_pairs, clusters, expect_same=True, examples_limit=big)
    cannot_eval = evaluate_pairs(cannot_pairs, clusters, expect_same=False, examples_limit=big)

    must_diag = [
        diagnose_must_link(v["pair"], clusters, logs, order)
        for v in must_eval["violation_examples"]
    ]
    cannot_diag = [
        diagnose_cannot_link(v["pair"], clusters, logs, order)
        for v in cannot_eval["violation_examples"]
    ]

    counts = {cause: 0 for cause in MUST_CAUSES}
    for d in must_diag:
        counts[d["cause"]] += 1

    guardrail_violations = [d for d in must_diag if d["cause"] == CAUSE_D]
    truncated = [d for d in must_diag if d["cause"] == CAUSE_B]
    saves = find_guardrail_saves(
        satisfied_pairs(cannot_pairs, clusters, expect_same=False), clusters, logs, order
    )

    return {
        "meta": {
            "gold_reviewer": gold.get("reviewer"),
            "articles_in_log": len(logs),
            "articles_in_snapshot": len(clusters),
            "must_link_total": must_eval["total"],
            "must_link_violated": must_eval["violated"],
            "cannot_link_total": cannot_eval["total"],
            "cannot_link_violated": cannot_eval["violated"],
        },
        "must_link_causes": counts,
        "must_link_diagnoses": must_diag,
        "cannot_link_diagnoses": cannot_diag,
        "truncation_ranks": sorted(d["rank"] for d in truncated if "rank" in d),
        "guardrail_saves": saves,
    }


# ══════════════════════════════════════════════════════════════════════════
# 출력
# ══════════════════════════════════════════════════════════════════════════


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _rpad(text: str, width: int) -> str:
    return " " * max(0, width - _width(text)) + text


def print_report(results: dict, threshold: float, examples_limit: int) -> None:
    meta = results["meta"]
    counts = results["must_link_causes"]
    diagnosed = sum(v for k, v in counts.items() if k != CAUSE_NO_LOG)
    total_violated = meta["must_link_violated"]

    print("═" * 64)
    print("이벤트 제약 위반 원인 진단")
    print("═" * 64)
    print(f"  검수자 {meta['gold_reviewer']} / 로그 {meta['articles_in_log']:,}건 "
          f"/ 스냅샷 기사 {meta['articles_in_snapshot']:,}건")
    print(f"  must-link  위반 {total_violated:,} / {meta['must_link_total']:,}쌍")
    print(f"  cannot-link 위반 {meta['cannot_link_violated']:,} / {meta['cannot_link_total']:,}쌍")

    if total_violated == 0:
        print("\n  위반이 없습니다.")
        print("═" * 64)
        return

    print(f"\n  must-link 위반 {total_violated:,}건 중 진단 가능 {diagnosed:,}건")
    print(f"  {_pad('원인', 38)}{_rpad('건수', 8)}{_rpad('비율', 9)}")
    print("  " + "-" * 55)
    for cause in MUST_CAUSES:
        n = counts[cause]
        if n == 0:
            continue
        ratio = f"{n / total_violated * 100:.1f}%"
        print(f"  {_pad(_CAUSE_LABELS[cause], 38)}{_rpad(f'{n:,}', 8)}{_rpad(ratio, 9)}")

    dominant = max(
        (c for c in MUST_CAUSES if c != CAUSE_NO_LOG), key=lambda c: counts[c]
    )
    if counts[dominant]:
        print(f"\n  ▶ 가장 큰 원인: {_CAUSE_LABELS[dominant]}")
        print(f"    처방: {_CAUSE_FIXES[dominant]}")

    # ── 처방 시뮬레이션 ────────────────────────────────────────────────
    ranks = results["truncation_ranks"]
    if ranks:
        need = max(ranks) + 1
        print("\n[시뮬레이션 1] 프롬프트 절삭 (B 유형)")
        print(f"  MAX_EVENT_CANDIDATES 를 {need} 이상으로 올리면 "
              f"B 유형 {len(ranks)}건이 전부 구제됩니다.")
        print(f"  (절삭된 정답 후보의 순위: {ranks[:10]}{' ...' if len(ranks) > 10 else ''})")

    guardrail = [d for d in results["must_link_diagnoses"] if d["cause"] == CAUSE_D]
    if guardrail:
        steps = [round(threshold - i * 0.05, 2) for i in range(0, 5)]
        steps = [s for s in steps if s > 0]
        rows = simulate_score_threshold(guardrail, results["guardrail_saves"], threshold, steps)
        print("\n[시뮬레이션 2] 배정 점수 임계값 (D 유형)")
        print(f"  {_rpad('임계값', 8)}{_rpad('must 구제', 12)}{_rpad('cannot 깨짐', 13)}{_rpad('순이득', 9)}")
        print("  " + "-" * 42)
        for row in rows:
            mark = "  ← 현재" if row["is_current"] else ""
            threshold_text = f"{row['threshold']:.2f}"
            rescued_text = f"+{row['must_link_rescued']}"
            broken_text = f"-{row['cannot_link_broken']}"
            net_text = f"{row['net']:+d}"
            print(
                f"  {_rpad(threshold_text, 8)}{_rpad(rescued_text, 12)}"
                f"{_rpad(broken_text, 13)}{_rpad(net_text, 9)}{mark}"
            )
        print("\n  ※ 1차 근사입니다. 임계값을 바꾸면 이벤트 구성이 달라져 이후 기사의")
        print("     후보 목록도 연쇄적으로 변합니다. 최종 확인은 재실행으로 하세요.")

    # ── 대표 사례 ─────────────────────────────────────────────────────
    for cause in (CAUSE_A, CAUSE_B, CAUSE_C, CAUSE_D):
        samples = [d for d in results["must_link_diagnoses"] if d["cause"] == cause]
        if not samples:
            continue
        print(f"\n[{_CAUSE_LABELS[cause]}] 상위 {min(examples_limit, len(samples))}건")
        for d in samples[:examples_limit]:
            line = (f"    - 기사 {d['later_article']} → 이벤트 {d['target_event']} 에 못 붙음"
                    f" (실제 {d['landed_event']})")
            if d.get("distance") is not None:
                line += f" | 거리 {d['distance']}"
            if d.get("llm_score") is not None:
                line += f" | 점수 {d['llm_score']}"
            print(line)
            if d.get("llm_reason"):
                print(f"        LLM: {d['llm_reason'][:70]}")

    print("\n" + "═" * 64)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="제약 위반의 원인을 분류기 로그와 대조해 A/B/C/D 유형으로 진단한다."
    )
    parser.add_argument("gold", help="검수 정답 JSON (constraints-v1)")
    parser.add_argument("target", help="재분류 결과 스냅샷 JSON")
    parser.add_argument("log", help="classify_events.py 의 stdout (JSONL)")
    parser.add_argument(
        "--assign-score-threshold",
        type=float,
        default=0.80,
        help="로그를 만든 실행에 적용됐던 EVENT_ASSIGN_SCORE_THRESHOLD (기본: 0.80). "
             "시뮬레이션의 기준점으로만 쓴다.",
    )
    parser.add_argument("--json", action="store_true", help="전체 결과를 JSON으로 출력한다.")
    parser.add_argument(
        "--examples-limit", type=int, default=5, help="원인별 대표 사례 출력 개수 (기본: 5)"
    )
    args = parser.parse_args()

    gold = load_json(args.gold)
    snapshot = load_json(args.target)
    logs, order = parse_decision_logs(Path(args.log).read_text(encoding="utf-8"))

    if not logs:
        print(
            f"[오류] {args.log} 에서 판단 로그를 찾지 못했습니다. "
            "classify_events.py 의 stdout 을 그대로 저장한 파일인지 확인하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    results = run_diagnosis(gold, snapshot, logs, order)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results, args.assign_score_threshold, args.examples_limit)


if __name__ == "__main__":
    main()

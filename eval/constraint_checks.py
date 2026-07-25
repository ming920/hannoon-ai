"""사람이 검수한 정답(`constraints-v1`)과 재분류 결과 스냅샷을 대조해 제약 충족률을 산출한다.

`eval/metrics.py`가 클러스터링 지표(BCubed/ARI/NMI)를 전역 점수로 재는 것과 달리, 이쪽은
검수자가 직접 매긴 **기사 쌍 제약**을 하나씩 판정한다. 어떤 기사 쌍이 왜 틀렸는지가 그대로
나오므로 회귀 원인 추적에 쓴다.

판정 규칙:
  must-link   (같이 있어야)  → 두 기사가 같은 이벤트/토픽이면 충족, 다르면 위반
  cannot-link (떨어져야)     → 두 기사가 다른 이벤트/토픽이면 충족, 같으면 위반
  둘 중 하나라도 결과 스냅샷에 없으면 "불가"로 세고 **충족률 분모에서 제외**한다.

  충족률 = 충족 / (충족 + 위반)

통과 판정은 "위반 0"이 아니라 **기준선 대비 충족률 하락 없음**이다. LLM 군집화가 사람이 매긴
2,500여 쌍을 전부 맞출 수는 없으므로, 위반 0을 게이트로 쓰면 첫날부터 영구 실패한다.
`--baseline`을 주면 기준선과 비교해 하락 시 종료 코드 1을 반환한다.

사용 예:
  # 1) 측정만 (종료 코드 항상 0)
  python eval/constraint_checks.py eval/data/constraints/review_2026-07-20.json snapshot.json

  # 2) 현재 결과를 기준선으로 저장
  python eval/constraint_checks.py 정답.json snapshot.json \
      --write-baseline eval/data/constraints/baseline.json

  # 3) 회귀 게이트 (기준선 대비 하락 시 exit 1)
  python eval/constraint_checks.py 정답.json snapshot.json \
      --baseline eval/data/constraints/baseline.json

  # 4) 기계 판독 출력
  python eval/constraint_checks.py 정답.json snapshot.json --json

결과 스냅샷 JSON을 만드는 추출 SQL은 `eval/data/constraints/README.md`에 있다.

검사 ID:
  C-EM  이벤트 must-link  (같은 이벤트여야)
  C-EC  이벤트 cannot-link(다른 이벤트여야)
  C-TM  토픽   must-link  (같은 토픽이어야)
  C-TC  토픽   cannot-link(다른 토픽이어야)
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

SCHEMA_VERSION = "constraints-v1"
DEFAULT_EXAMPLES_LIMIT = 5

CHECK_IDS = ("C-EM", "C-EC", "C-TM", "C-TC")

_CHECK_LABELS = {
    "C-EM": "이벤트 must-link  (같이 있어야)",
    "C-EC": "이벤트 cannot-link(떨어져야)",
    "C-TM": "토픽   must-link  (같은 토픽)",
    "C-TC": "토픽   cannot-link(다른 토픽)",
}


# ══════════════════════════════════════════════════════════════════════════
# 순수 함수 — 클러스터 맵 구성 / 제약 판정 (파일·DB 의존 없음, import 가능)
# ══════════════════════════════════════════════════════════════════════════


def build_event_cluster_map(events: list[dict]) -> dict[int, int]:
    """결과 스냅샷의 events[]에서 `기사 id → 이벤트 id` 맵을 만든다.

    한 기사가 여러 이벤트에 중복 등장하면 마지막 것이 남는다(정상 파이프라인에서는
    event_articles가 기사당 한 행이라 발생하지 않는다).
    """
    mapping: dict[int, int] = {}
    for event in events or []:
        event_id = event.get("id")
        if event_id is None:
            continue
        for article in event.get("articles") or []:
            article_id = article.get("id")
            if article_id is not None:
                mapping[article_id] = event_id
    return mapping


def build_topic_cluster_map(topics: list[dict]) -> dict[int, int]:
    """결과 스냅샷의 topics[]에서 `기사 id → 토픽 id` 맵을 만든다.

    토픽 소속은 `topics[].events[].article_ids`를 통해 간접적으로 결정된다.
    """
    mapping: dict[int, int] = {}
    for topic in topics or []:
        topic_id = topic.get("id")
        if topic_id is None:
            continue
        for event in topic.get("events") or []:
            for article_id in event.get("article_ids") or []:
                mapping[article_id] = topic_id
    return mapping


def evaluate_pairs(
    pairs: list,
    cluster_map: dict[int, int],
    *,
    expect_same: bool,
    examples_limit: int = DEFAULT_EXAMPLES_LIMIT,
) -> dict:
    """기사 쌍 제약을 판정해 충족/위반/불가를 센다.

    expect_same=True  → must-link  (같은 클러스터여야 충족)
    expect_same=False → cannot-link(다른 클러스터여야 충족)

    두 기사 중 하나라도 cluster_map에 없으면 "불가"(unknown)로 세고 충족률 분모에서 뺀다.
    """
    satisfied = 0
    violations: list[dict] = []
    unknown: list[dict] = []

    for pair in pairs or []:
        if not pair or len(pair) < 2:
            continue
        a, b = pair[0], pair[1]
        ca = cluster_map.get(a)
        cb = cluster_map.get(b)

        if ca is None or cb is None:
            unknown.append(
                {
                    "pair": [a, b],
                    "missing": [x for x, c in ((a, ca), (b, cb)) if c is None],
                }
            )
            continue

        if (ca == cb) == expect_same:
            satisfied += 1
        else:
            violations.append({"pair": [a, b], "clusters": [ca, cb]})

    return {
        "total": len(pairs or []),
        "satisfied": satisfied,
        "violated": len(violations),
        "unknown": len(unknown),
        "rate": satisfaction_rate(satisfied, len(violations)),
        "violation_examples": violations[:examples_limit],
        "unknown_examples": unknown[:examples_limit],
    }


def satisfaction_rate(satisfied: int, violated: int) -> float | None:
    """충족률 = 충족 / (충족 + 위반). 분모가 0이면 None(N/A)."""
    denom = satisfied + violated
    if denom == 0:
        return None
    return satisfied / denom


def run_all_checks(
    gold: dict,
    snapshot: dict,
    examples_limit: int = DEFAULT_EXAMPLES_LIMIT,
) -> dict:
    """정답과 결과 스냅샷을 대조해 4개 검사 결과를 모두 산출한다."""
    event_map = build_event_cluster_map(snapshot.get("events") or [])
    topic_map = build_topic_cluster_map(snapshot.get("topics") or [])

    event_c = gold.get("event_constraints") or {}
    topic_c = gold.get("topic_constraints") or {}

    results: dict = {
        "meta": {
            "schema_version": gold.get("schema_version"),
            "gold_snapshot_date": gold.get("snapshot_date"),
            "gold_reviewer": gold.get("reviewer"),
            "gold_exported_at": gold.get("exported_at"),
            "target_snapshot_date": snapshot.get("snapshot_date"),
            "articles_in_events": len(event_map),
            "articles_in_topics": len(topic_map),
            "events_in_snapshot": len(snapshot.get("events") or []),
            "topics_in_snapshot": len(snapshot.get("topics") or []),
        },
        "C-EM": evaluate_pairs(
            event_c.get("must_link"), event_map, expect_same=True, examples_limit=examples_limit
        ),
        "C-EC": evaluate_pairs(
            event_c.get("cannot_link"), event_map, expect_same=False, examples_limit=examples_limit
        ),
        "C-TM": evaluate_pairs(
            topic_c.get("must_link"), topic_map, expect_same=True, examples_limit=examples_limit
        ),
        "C-TC": evaluate_pairs(
            topic_c.get("cannot_link"), topic_map, expect_same=False, examples_limit=examples_limit
        ),
    }
    results["review_memos"] = collect_review_memos(gold)
    return results


def collect_review_memos(gold: dict) -> list[dict]:
    """검수 로그에서 메모가 달린 항목만 추린다.

    메모는 검수 당시의 이벤트 id(`ref_id`) 기준이라, 위반한 기사 쌍에 자동으로 매칭할 수는
    없다(`constraints-v1`에 기사→원본이벤트 맵이 없다). 참고용으로 따로 출력한다.
    """
    return [
        {
            "unit": entry.get("unit"),
            "ref_id": entry.get("ref_id"),
            "label": entry.get("label"),
            "memo": entry.get("memo"),
            "excluded_articles": entry.get("excluded_articles") or [],
        }
        for entry in gold.get("review_log") or []
        if entry.get("memo")
    ]


def extract_baseline(results: dict) -> dict:
    """비교에 쓸 충족률만 뽑아 기준선 형식으로 만든다."""
    return {
        "gold_snapshot_date": results["meta"].get("gold_snapshot_date"),
        "target_snapshot_date": results["meta"].get("target_snapshot_date"),
        "rates": {cid: results[cid]["rate"] for cid in CHECK_IDS},
        "counts": {
            cid: {
                "satisfied": results[cid]["satisfied"],
                "violated": results[cid]["violated"],
                "unknown": results[cid]["unknown"],
            }
            for cid in CHECK_IDS
        },
    }


def compare_with_baseline(
    results: dict, baseline: dict, tolerance: float = 0.0
) -> tuple[bool, list[dict]]:
    """기준선과 충족률을 비교한다.

    tolerance는 허용 하락폭(비율, 0.01 = 1%p). 기본 0.0은 엄격 비교다.
    반환: (통과 여부, 검사별 비교 상세)
    """
    base_rates = (baseline or {}).get("rates") or {}
    rows: list[dict] = []
    ok = True

    for cid in CHECK_IDS:
        current = results[cid]["rate"]
        base = base_rates.get(cid)

        if base is None or current is None:
            rows.append({"check": cid, "baseline": base, "current": current,
                         "delta": None, "status": "비교불가"})
            continue

        delta = current - base
        regressed = delta < -tolerance
        if regressed:
            ok = False
        rows.append(
            {
                "check": cid,
                "baseline": base,
                "current": current,
                "delta": delta,
                "status": "회귀" if regressed else ("개선" if delta > 0 else "유지"),
            }
        )

    return ok, rows


# ══════════════════════════════════════════════════════════════════════════
# 입출력
# ══════════════════════════════════════════════════════════════════════════


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _display_width(text: str) -> int:
    """터미널 표시 폭. 한글 등 East Asian Wide 문자는 2칸으로 센다.

    파이썬의 `{:<34}` 포맷은 글자 수로 세기 때문에 한글이 섞인 표는 열이 어긋난다.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad_right(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _pad_left(text: str, width: int) -> str:
    return " " * max(0, width - _display_width(text)) + text


def _fmt_rate(v: float | None) -> str:
    return "N/A" if v is None else f"{v * 100:.1f}%"


def _fmt_delta(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:+.1f}%p"


def print_report(results: dict, examples_limit: int = DEFAULT_EXAMPLES_LIMIT) -> None:
    """콘솔에 한국어 요약 리포트를 출력한다."""
    meta = results["meta"]
    print("═" * 64)
    print("제약 충족률 리포트")
    print("═" * 64)
    print(f"  정답: {meta.get('gold_snapshot_date')} 스냅샷 / 검수자 {meta.get('gold_reviewer')}")
    print(f"  대상: {meta.get('target_snapshot_date')} 스냅샷 "
          f"(이벤트 {meta.get('events_in_snapshot')}개, 토픽 {meta.get('topics_in_snapshot')}개)")
    print()
    print(
        "  " + _pad_right("제약", 34) + _pad_left("충족", 6)
        + _pad_left("위반", 6) + _pad_left("불가", 6) + _pad_left("충족률", 10)
    )
    print("  " + "-" * 62)
    for cid in CHECK_IDS:
        r = results[cid]
        print(
            "  " + _pad_right(_CHECK_LABELS[cid], 34)
            + _pad_left(str(r["satisfied"]), 6)
            + _pad_left(str(r["violated"]), 6)
            + _pad_left(str(r["unknown"]), 6)
            + _pad_left(_fmt_rate(r["rate"]), 10)
        )
    print()
    print("  충족률 = 충족 / (충족 + 위반). '불가'(결과에 기사가 없음)는 분모에서 제외.")

    for cid in CHECK_IDS:
        r = results[cid]
        if not r["violation_examples"]:
            continue
        print(f"\n[{cid}] {_CHECK_LABELS[cid]} — 위반 상위 {len(r['violation_examples'])}건")
        for v in r["violation_examples"]:
            a, b = v["pair"]
            ca, cb = v["clusters"]
            if ca == cb:
                print(f"    - 기사 {a} / {b}: 둘 다 {ca}에 묶임 (떨어져야 하는데 붙음)")
            else:
                print(f"    - 기사 {a} / {b}: 각각 {ca} / {cb}로 갈림 (같이 있어야 하는데 흩어짐)")

    memos = results.get("review_memos") or []
    if memos:
        print(f"\n[참고] 검수 메모 {len(memos)}건 "
              "(검수 당시 이벤트 id 기준 — 위반 쌍과 자동 연결되지 않음)")
        for m in memos[:examples_limit]:
            memo = (m["memo"] or "").replace("\n", " / ")
            print(f"    - {m['unit']} {m['ref_id']} [{m['label']}] {memo}")
        if len(memos) > examples_limit:
            print(f"    ... 외 {len(memos) - examples_limit}건 (--examples-limit로 더 볼 수 있음)")

    print("\n" + "═" * 64)


def print_baseline_comparison(rows: list[dict], ok: bool, tolerance: float) -> None:
    print("\n" + "═" * 64)
    print(f"기준선 대비 비교 (허용 하락폭 {tolerance * 100:.1f}%p)")
    print("═" * 64)
    print(
        "  " + _pad_right("제약", 34) + _pad_left("기준선", 9)
        + _pad_left("현재", 9) + _pad_left("변화", 10) + "  판정"
    )
    print("  " + "-" * 62)
    for row in rows:
        print(
            "  " + _pad_right(_CHECK_LABELS[row["check"]], 34)
            + _pad_left(_fmt_rate(row["baseline"]), 9)
            + _pad_left(_fmt_rate(row["current"]), 9)
            + _pad_left(_fmt_delta(row["delta"]), 10)
            + "  " + row["status"]
        )
    print()
    print("  ✅ 통과: 기준선 대비 하락 없음" if ok else "  ❌ 실패: 기준선 대비 충족률이 하락했습니다")
    print("═" * 64)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="검수 정답(constraints-v1)과 재분류 결과 스냅샷의 제약 충족률을 산출한다."
    )
    parser.add_argument("gold", help="검수 정답 JSON (schema_version=constraints-v1)")
    parser.add_argument("target", help="재분류 결과 스냅샷 JSON (추출 SQL 산출물)")
    parser.add_argument(
        "--baseline",
        help="기준선 JSON. 주면 충족률을 비교해 하락 시 종료 코드 1을 반환한다.",
    )
    parser.add_argument(
        "--write-baseline",
        metavar="PATH",
        help="현재 결과의 충족률을 기준선 JSON으로 저장한다.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="기준선 대비 허용 하락폭(%%p). 기본 0.0(엄격). "
             "실행 간 변동이 크면 관측된 변동폭만큼 올려 쓴다.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="콘솔 요약 대신 전체 결과를 JSON으로 표준출력에 출력한다.",
    )
    parser.add_argument(
        "--examples-limit",
        type=int,
        default=DEFAULT_EXAMPLES_LIMIT,
        help=f"검사별 위반 사례 출력 개수 (기본: {DEFAULT_EXAMPLES_LIMIT})",
    )
    args = parser.parse_args()

    gold = load_json(args.gold)
    if gold.get("schema_version") != SCHEMA_VERSION:
        print(
            f"[경고] 정답 schema_version이 '{gold.get('schema_version')}'입니다 "
            f"(기대: '{SCHEMA_VERSION}'). 판정 결과가 부정확할 수 있습니다.",
            file=sys.stderr,
        )

    snapshot = load_json(args.target)
    results = run_all_checks(gold, snapshot, examples_limit=args.examples_limit)

    if args.write_baseline:
        path = Path(args.write_baseline)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(extract_baseline(results), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[기록] 기준선을 저장했습니다: {path}", file=sys.stderr)

    comparison = None
    exit_code = 0
    if args.baseline:
        baseline = load_json(args.baseline)
        ok, rows = compare_with_baseline(results, baseline, tolerance=args.tolerance)
        comparison = {"passed": ok, "tolerance": args.tolerance, "rows": rows}
        exit_code = 0 if ok else 1

    if args.json:
        payload = dict(results)
        if comparison is not None:
            payload["baseline_comparison"] = comparison
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_report(results, examples_limit=args.examples_limit)
        if comparison is not None:
            print_baseline_comparison(comparison["rows"], comparison["passed"], args.tolerance)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

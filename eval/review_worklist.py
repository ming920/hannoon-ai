"""검수 대기열을 만들고, 채워진 결과를 제약(constraints)으로 되돌린다.

## 왜 필요한가

정답 파일의 cannot-link 제약은 48쌍이 전부다. 검수자가 "이 기사는 이 이벤트가 아니다"라고
**명시한** 6개 이벤트에서만 나오기 때문이다. 그래서 과병합(붙이면 안 될 것을 붙임)을 사실상
측정할 수 없다 — 토픽은 아예 0쌍이다.

must-link 는 반대로 흔하다. "이 묶음 맞다"(ok) 판정 하나가 기사 N건짜리 이벤트를 통째로
승인하면 N(N-1)/2 쌍이 한 번에 생기기 때문이다. 검수 형식의 이 비대칭이 지표를 한쪽 눈만
뜨게 만들었다.

이 도구는 검수자의 시간을 cannot-link 가 나올 만한 곳에 집중시킨다.

## 두 단계

    build   현재 분류 결과에서 "의심스러운 병합"을 골라 검수 대기열을 만든다
    merge   검수자가 채운 대기열을 제약 쌍으로 바꿔 정답 파일에 합친다

## 표본 편향 (중요)

의심스러운 것만 골라 검수하면 그 결과로 잰 충족률은 **무작위 표본보다 나쁘게** 나온다.
어려운 사례만 모아놨으니 당연하다. 그래서 대기열에 무작위 대조군(control)을 섞고
selection 필드로 구분해 둔다 — 나중에 대조군만으로도 따로 잴 수 있어야
"진짜 나빠졌는지"와 "어려운 것만 봤는지"를 가를 수 있다.

## 실행

    python eval/review_worklist.py build \\
        --snapshot eval/results/event-local-e000-snapshot.json \\
        --log eval/results/event-local-e000-classifier.log \\
        --out-dir eval/data/review

    # (사람이 worklist.json 의 verdict 를 채운다)

    python eval/review_worklist.py merge \\
        --reviewed eval/data/review/worklist.json \\
        --constraints eval/data/constraints/review_2026-07-20.json \\
        --out eval/data/constraints/review_2026-07-27.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from itertools import combinations
from pathlib import Path

SCHEMA_VERSION = "worklist-v1"

# 이 거리 이상에서 붙은 기사를 "약한 병합"으로 본다. 로컬 시운전에서 오판 사례의 정답/오답
# 후보가 모두 0.42~0.44 구간에 몰려 있었다 — 이 부근부터 임베딩이 변별력을 잃는다.
WEAK_DISTANCE = 0.40

DEFAULT_TARGETED = 20
DEFAULT_CONTROL = 5

VERDICT_CORRECT = "correct"
VERDICT_EXCLUDE = "exclude"
VERDICT_UNSURE = "unsure"
VERDICTS = (VERDICT_CORRECT, VERDICT_EXCLUDE, VERDICT_UNSURE)


# ══════════════════════════════════════════════════════════════════════════
# 로그에서 "붙은 거리" 복원
# ══════════════════════════════════════════════════════════════════════════


def assign_distances(log_text: str) -> dict[int, float]:
    """기사 → 그 기사가 배정될 때 선택된 이벤트와의 거리.

    create 로 끝난 기사는 비교 대상이 없으므로 넣지 않는다(약한 병합이 아니다).
    """
    out: dict[int, float] = {}
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("final_action") != "assign":
            continue
        chosen = (entry.get("llm_decision") or {}).get("event_id")
        for cand in entry.get("candidates") or []:
            if cand.get("event_id") == chosen and cand.get("distance") is not None:
                out[entry["article_id"]] = float(cand["distance"])
                break
    return out


# ══════════════════════════════════════════════════════════════════════════
# 대기열 구성 (순수 함수)
# ══════════════════════════════════════════════════════════════════════════


def score_event(event: dict, distances: dict[int, float]) -> dict:
    """이벤트 하나의 의심도를 잰다.

    약하게 붙은 기사가 많을수록, 그리고 가장 약한 결합이 멀수록 의심스럽다.
    단일기사 이벤트는 애초에 병합이 없으므로 과병합 후보가 아니다.
    """
    articles = event.get("articles") or []
    weak = [
        a for a in articles
        if distances.get(a["id"]) is not None and distances[a["id"]] >= WEAK_DISTANCE
    ]
    known = [distances[a["id"]] for a in articles if distances.get(a["id"]) is not None]
    return {
        "event_id": event["id"],
        "title": event.get("title") or "",
        "size": len(articles),
        "weak_count": len(weak),
        "max_distance": max(known) if known else None,
        "eligible": len(articles) >= 2,
        "anchor_suspect": anchor_is_suspect(len(weak), len(known)),
    }


# 거리는 "이벤트를 만든 기준 기사와 얼마나 먼가"이다. 그래서 다수가 멀다면 다수가 이상한
# 것이 아니라 **기준 기사 쪽이 이질적**일 가능성이 크다. 이 구분을 표시하지 않으면 대기열이
# "다수를 빼라"고 유도하게 되고, 그렇게 만들어진 정답은 조용히 틀린 기준이 된다.
ANCHOR_SUSPECT_RATIO = 0.6


def anchor_is_suspect(weak_count: int, known_count: int) -> bool:
    if known_count < 3:      # 표본이 너무 작으면 다수·소수를 논할 수 없다
        return False
    return weak_count / known_count > ANCHOR_SUSPECT_RATIO


def select_events(events: list, distances: dict[int, float], *,
                  targeted: int, control: int, seed: int) -> list[dict]:
    """의심 상위 N개 + 무작위 대조군 M개를 고른다.

    대조군은 의심 목록에 안 뽑힌 이벤트에서만 고른다 — 겹치면 대조군의 의미가 없다.
    seed 를 받아 같은 입력에 같은 대기열이 나오게 한다(재현 가능한 검수).
    """
    scored = [score_event(e, distances) for e in events]
    eligible = [s for s in scored if s["eligible"]]

    ranked = sorted(
        eligible,
        key=lambda s: (s["weak_count"], s["max_distance"] or 0, s["size"]),
        reverse=True,
    )
    picked = [s for s in ranked if s["weak_count"] > 0][:targeted]
    picked_ids = {s["event_id"] for s in picked}

    rest = [s for s in eligible if s["event_id"] not in picked_ids]
    rng = random.Random(seed)
    sample = rng.sample(rest, min(control, len(rest)))

    for item in picked:
        item["selection"] = "targeted"
    for item in sample:
        item["selection"] = "control"
    return picked + sorted(sample, key=lambda s: s["event_id"])


def known_conflicts(article_ids: list, prior_cannot_link: set) -> list:
    """이 이벤트 안에 이미 '분리' 판정을 받은 쌍이 있는지 찾는다.

    이걸 미리 보여주지 않으면 검수자가 모르고 correct 를 눌렀다가 merge 단계에서야
    모순으로 거부당한다. 그때는 어느 판단을 되돌려야 하는지도 알기 어렵다.
    """
    return [
        list(pair) for pair in sorted(prior_cannot_link)
        if pair[0] in set(article_ids) and pair[1] in set(article_ids)
    ]


def build_worklist(snapshot: dict, distances: dict[int, float], *,
                   targeted: int, control: int, seed: int,
                   prior_constraints: dict | None = None) -> dict:
    events = {e["id"]: e for e in snapshot.get("events") or []}
    selected = select_events(list(events.values()), distances,
                             targeted=targeted, control=control, seed=seed)

    prior_cannot = {
        tuple(sorted(p))
        for p in ((prior_constraints or {}).get("event_constraints") or {}).get(
            "cannot_link") or []
    }

    items = []
    for entry in selected:
        event = events[entry["event_id"]]
        article_ids = [a["id"] for a in event.get("articles") or []]
        items.append({
            "known_conflicts": known_conflicts(article_ids, prior_cannot),
            "event_id": entry["event_id"],
            "event_title": entry["title"],
            "selection": entry["selection"],
            "size": entry["size"],
            "weak_count": entry["weak_count"],
            "anchor_suspect": entry["anchor_suspect"],
            # ↓ 검수자가 채우는 칸
            "verdict": None,
            "not_belonging": [],
            "memo": "",
            "articles": [
                {
                    "id": a["id"],
                    "title": a.get("title") or "",
                    "publisher": a.get("publisher") or "",
                    "distance": distances.get(a["id"]),
                }
                for a in event.get("articles") or []
            ],
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": snapshot.get("snapshot_date"),
        "weak_distance": WEAK_DISTANCE,
        "seed": seed,
        "verdict_options": list(VERDICTS),
        "how_to_fill": (
            "각 항목의 verdict 를 셋 중 하나로 채우세요. "
            f"'{VERDICT_CORRECT}' = 이 기사들은 모두 같은 사건이다. "
            f"'{VERDICT_EXCLUDE}' = 일부가 다른 사건이다(not_belonging 에 기사 id 나열). "
            f"'{VERDICT_UNSURE}' = 판단 유보(제약을 만들지 않음). "
            "확신이 없으면 unsure 로 두세요 — 틀린 정답이 없는 정답보다 나쁩니다."
        ),
        "items": items,
    }


# ══════════════════════════════════════════════════════════════════════════
# 사람이 읽는 대기열
# ══════════════════════════════════════════════════════════════════════════


def format_worklist_markdown(worklist: dict) -> str:
    items = worklist["items"]
    targeted = [i for i in items if i["selection"] == "targeted"]
    control = [i for i in items if i["selection"] == "control"]

    lines = [
        "# 이벤트 검수 대기열",
        "",
        f"- 스냅샷: {worklist.get('snapshot_date')}",
        f"- 의심 항목 {len(targeted)}건 + 무작위 대조군 {len(control)}건",
        f"- 약한 병합 기준: 거리 ≥ {worklist['weak_distance']}",
        "",
        "## 어떻게 답하나",
        "",
        "함께 생성된 `worklist.json` 의 `verdict` 를 채우세요.",
        "",
        "| verdict | 뜻 | 추가 입력 |",
        "|---|---|---|",
        f"| `{VERDICT_CORRECT}` | 이 기사들은 모두 같은 사건이다 | — |",
        f"| `{VERDICT_EXCLUDE}` | 일부가 다른 사건이다 | `not_belonging` 에 기사 id |",
        f"| `{VERDICT_UNSURE}` | 판단 유보 | — |",
        "",
        "> 확신이 없으면 `unsure` 로 두세요. **틀린 정답은 없는 정답보다 나쁩니다.**",
        "> 대조군(control)은 의심스럽지 않아 보여도 건너뛰지 마세요 — 편향 보정용입니다.",
        "",
    ]

    for section, entries in (("의심 항목", targeted), ("무작위 대조군", control)):
        if not entries:
            continue
        lines += [f"## {section}", ""]
        for order, item in enumerate(entries, 1):
            lines += [
                f"### {order}. E{item['event_id']} — {item['event_title'][:60]}",
                "",
                f"기사 {item['size']}건 / 약한 병합 {item['weak_count']}건",
                "",
            ]
            if item.get("known_conflicts"):
                pairs = ", ".join(f"{a}↔{b}" for a, b in item["known_conflicts"][:6])
                more = len(item["known_conflicts"]) - 6
                lines += [
                    f"> 🔒 **이전 검수에서 '다른 사건'으로 판정된 쌍이 이 안에 있습니다**"
                    f" ({len(item['known_conflicts'])}쌍): {pairs}"
                    + (f" 외 {more}쌍" if more > 0 else ""),
                    "> `correct` 로 답하면 이전 판정과 모순되어 반영이 거부됩니다."
                    " 판단이 바뀌었다면 메모에 이유를 남겨 주세요.",
                    "",
                ]
            if item.get("anchor_suspect"):
                lines += [
                    "> **다수가 ⚠️ 입니다.** 거리는 이 이벤트를 만든 기준 기사(`생성`)와의"
                    " 거리이므로, 다수가 멀다는 것은 다수가 이상한 게 아니라 **기준 기사"
                    " 쪽이 이질적**이라는 뜻일 수 있습니다.",
                    "> ⚠️ 가 붙은 기사들을 통째로 빼기 전에, 기준 기사가 이 묶음에 속하는지"
                    " 먼저 보세요.",
                    "",
                ]
            lines += [
                "| 기사 id | 거리 | 발행처 | 제목 |",
                "|---|---|---|---|",
            ]
            for art in sorted(
                item["articles"],
                key=lambda a: (a["distance"] is None, -(a["distance"] or 0)),
            ):
                dist = "생성" if art["distance"] is None else f"{art['distance']:.3f}"
                mark = " ⚠️" if (art["distance"] or 0) >= worklist["weak_distance"] else ""
                lines.append(
                    f"| {art['id']} | {dist}{mark} | {art['publisher'][:12]} | "
                    f"{art['title'][:56]} |"
                )
            lines.append("")
    lines += [
        "---",
        "",
        "`거리 = 생성`은 이 기사가 이 이벤트를 **만든** 기사라는 뜻입니다 "
        "(비교 대상이 없어 거리가 없습니다).",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 채워진 대기열 → 제약 쌍
# ══════════════════════════════════════════════════════════════════════════


def pairs_from_item(item: dict) -> tuple[list, list]:
    """검수 항목 하나에서 (must_link, cannot_link) 쌍을 만든다.

    correct  → 이벤트 안 모든 쌍이 must_link
    exclude  → 빠진 기사 × 남은 기사 = cannot_link, 남은 기사끼리는 must_link
    unsure   → 아무것도 만들지 않는다
    """
    verdict = item.get("verdict")
    ids = [a["id"] for a in item.get("articles") or []]

    if verdict == VERDICT_CORRECT:
        return [sorted(p) for p in combinations(sorted(ids), 2)], []

    if verdict == VERDICT_EXCLUDE:
        excluded = [i for i in item.get("not_belonging") or [] if i in ids]
        kept = [i for i in ids if i not in set(excluded)]
        cannot = [sorted([a, b]) for a in excluded for b in kept]
        # 빠진 기사끼리는 서로 같은 사건일 수도, 아닐 수도 있다 — 근거가 없으므로 만들지 않는다.
        must = [sorted(p) for p in combinations(sorted(kept), 2)]
        return must, cannot

    return [], []


def merge_into_constraints(constraints: dict, worklist: dict) -> dict:
    """검수 결과를 기존 정답 파일에 합친다 (원본은 수정하지 않는다)."""
    merged = json.loads(json.dumps(constraints))
    ec = merged.setdefault("event_constraints", {})
    existing_must = {tuple(sorted(p)) for p in ec.get("must_link") or []}
    existing_cannot = {tuple(sorted(p)) for p in ec.get("cannot_link") or []}

    added_must, added_cannot = set(), set()
    counts = {v: 0 for v in VERDICTS}
    counts["(미기입)"] = 0
    source_of = {}  # 쌍 → 그 쌍을 만든 이벤트 (충돌 시 어디를 고쳐야 하는지 알려주려고)

    for item in worklist.get("items") or []:
        verdict = item.get("verdict")
        counts[verdict if verdict in counts else "(미기입)"] += 1
        must, cannot = pairs_from_item(item)
        for pair in must:
            source_of.setdefault(tuple(pair), item.get("event_id"))
        added_must.update(tuple(p) for p in must)
        added_cannot.update(tuple(p) for p in cannot)

    # 모순 방지: 같은 쌍이 must 와 cannot 양쪽에 들어가면 그 쌍은 무엇으로 채점해도 틀린다.
    conflicts = sorted((added_cannot | existing_cannot) & (added_must | existing_must))
    if conflicts:
        by_event = {}
        for pair in conflicts:
            by_event.setdefault(source_of.get(pair, "(출처 불명)"), []).append(pair)
        detail = "; ".join(
            f"{f'E{eid}' if eid is not None else '(출처 불명)'}: "
            f"{len(pairs)}쌍(예 {pairs[0][0]}↔{pairs[0][1]})"
            for eid, pairs in sorted(by_event.items(), key=lambda kv: str(kv[0]))[:5]
        )
        raise ValueError(
            f"모순: must-link 와 cannot-link 에 동시에 들어가는 쌍이 {len(conflicts)}개 "
            f"있습니다 — {detail}. 해당 이벤트의 verdict 를 다시 보세요(이전 검수에서 이미 "
            f"'다른 사건'으로 판정된 쌍을 correct 로 묶으면 이렇게 됩니다)."
        )

    ec["must_link"] = [list(p) for p in sorted(existing_must | added_must)]
    ec["cannot_link"] = [list(p) for p in sorted(existing_cannot | added_cannot)]

    merged["_review_rounds"] = (merged.get("_review_rounds") or []) + [{
        "schema_version": worklist.get("schema_version"),
        "snapshot_date": worklist.get("snapshot_date"),
        "verdicts": counts,
        "added_must_link": len(added_must - existing_must),
        "added_cannot_link": len(added_cannot - existing_cannot),
    }]
    return merged


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_build(args) -> None:
    snapshot = _load(args.snapshot)
    distances = assign_distances(Path(args.log).read_text(encoding="utf-8"))
    prior = _load(args.constraints) if args.constraints else None
    worklist = build_worklist(snapshot, distances, targeted=args.targeted,
                              control=args.control, seed=args.seed,
                              prior_constraints=prior)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "worklist.json"
    md_path = out_dir / "worklist.md"
    json_path.write_text(json.dumps(worklist, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(format_worklist_markdown(worklist), encoding="utf-8")

    items = worklist["items"]
    articles = sum(i["size"] for i in items)
    targeted = sum(1 for i in items if i["selection"] == "targeted")
    print(f"검수 대기열 {len(items)}건 (의심 {targeted} + 대조군 {len(items) - targeted})")
    print(f"  기사 {articles}건, 약한 병합 {sum(i['weak_count'] for i in items)}건")
    conflicted = [i for i in items if i.get("known_conflicts")]
    if conflicted:
        print(f"  이전 검수와 충돌 가능 항목 {len(conflicted)}건 — 대기열에 표시했습니다")
    elif not args.constraints:
        print("  (--constraints 를 주면 이전 검수와 충돌할 항목을 미리 표시합니다)")
    print(f"  사람용: {md_path}")
    print(f"  입력용: {json_path}")
    print("\n  전부 exclude 로 판정될 경우 최대 생성 가능한 cannot-link 쌍: "
          f"{sum(i['weak_count'] * (i['size'] - i['weak_count']) for i in items):,}쌍")


def cmd_merge(args) -> None:
    worklist = _load(args.reviewed)
    constraints = _load(args.constraints)
    if worklist.get("schema_version") != SCHEMA_VERSION:
        print(f"오류: worklist 스키마가 {SCHEMA_VERSION} 이 아닙니다.", file=sys.stderr)
        sys.exit(1)

    filled = sum(1 for i in worklist.get("items") or [] if i.get("verdict"))
    if not filled:
        print("오류: verdict 가 채워진 항목이 없습니다.", file=sys.stderr)
        sys.exit(1)

    try:
        merged = merge_into_constraints(constraints, worklist)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)

    Path(args.out).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    round_info = merged["_review_rounds"][-1]
    before_c = len(constraints.get("event_constraints", {}).get("cannot_link") or [])
    after_c = len(merged["event_constraints"]["cannot_link"])
    before_m = len(constraints.get("event_constraints", {}).get("must_link") or [])
    after_m = len(merged["event_constraints"]["must_link"])
    print(f"검수 {filled}건 반영: {round_info['verdicts']}")
    print(f"  must-link   {before_m:,} → {after_m:,}쌍")
    print(f"  cannot-link {before_c:,} → {after_c:,}쌍")
    print(f"  저장: {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="분류 결과에서 검수 대기열을 만든다")
    b.add_argument("--snapshot", required=True)
    b.add_argument("--log", required=True)
    b.add_argument("--out-dir", default="eval/data/review")
    b.add_argument("--constraints", default=None,
                   help="기존 정답 파일. 주면 이전 검수와 충돌할 항목을 미리 표시한다")
    b.add_argument("--targeted", type=int, default=DEFAULT_TARGETED)
    b.add_argument("--control", type=int, default=DEFAULT_CONTROL)
    b.add_argument("--seed", type=int, default=20260727,
                   help="대조군 추출 시드 (같은 값이면 같은 대기열)")
    b.set_defaults(func=cmd_build)

    m = sub.add_parser("merge", help="채워진 대기열을 정답 파일에 합친다")
    m.add_argument("--reviewed", required=True)
    m.add_argument("--constraints", required=True)
    m.add_argument("--out", required=True)
    m.set_defaults(func=cmd_merge)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

"""개선을 시도하기 **전에** "이 방향으로 좋아질 수 있는가"를 먼저 답한다.

`eval/constraint_checks.py`는 충족률이 얼마인지 알려주고, `eval/diagnose_violations.py`는
위반 하나하나가 왜 생겼는지(후보 검색 실패인지 LLM 오판인지) 귀속시킨다. 둘 다 "지금 무엇이
틀렸나"를 본다. 이 파일은 다른 질문을 다룬다 — **그 틀린 것을 고칠 수 있는 종류의 문제인가.**

이 도구가 필요했던 이유:

  이벤트 must-link 위반을 줄이려고 배정 프롬프트·임계값·후보 순서·1:1 판정까지 다섯 가지를
  시도했고 전부 순손실이었다. 나중에 상한을 재보니 **정답대로 기사를 완벽히 재배정해도**
  충족 쌍이 34쌍 줄었다. 기사를 옮겨 고치는 접근 자체가 불가능했던 것이고, 그 사실은
  다섯 번을 시도하기 전에 5초면 알 수 있었다.

답하는 질문 네 가지:

  1. 정답끼리 모순인가        must 로 이어진 기사들 사이에 cannot 이 있으면 어떤 군집화로도
                              동시에 만족시킬 수 없다. 있으면 정답을 먼저 고쳐야 한다.
  2. 정답대로 묶으면 몇 %인가  must 연결요소를 그대로 군집으로 삼았을 때의 충족률.
                              달성 가능한 상한이며, 여기가 낮으면 목표 자체가 잘못됐다.
  3. 얼마나 가로지르나          한 군집이 여러 정답 사건을 담으면 과병합, 한 사건이 여러 군집에
                              흩어지면 과분할. 둘이 동시에 크면 경계가 어긋난 것이다.
  4. 옮겨서 고칠 수 있나        위반 쌍의 기사를 상대 군집으로 옮길 때 얻는 쌍과 잃는 쌍을 센다.
                              합이 음수면 **재배정 계열의 개선안은 전부 무의미하다** —
                              쪼개고 다시 묶는(재군집) 쪽으로 가야 한다.

사용 예:
  python eval/diagnose_structure.py eval/data/constraints/review_2026-07-20.json snapshot.json
  python eval/diagnose_structure.py 정답.json snapshot.json --unit topic
  python eval/diagnose_structure.py 정답.json snapshot.json --json

`--unit event`(기본)는 이벤트 제약을, `--unit topic`은 토픽 제약을 본다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# constraint_checks 와 같은 디렉터리에서 실행되므로 상대 import 없이 부른다
sys.path.insert(0, str(Path(__file__).resolve().parent))

from constraint_checks import (  # noqa: E402
    SCHEMA_VERSION,
    build_event_cluster_map,
    build_topic_cluster_map,
    load_json,
    satisfaction_rate,
)


# ══════════════════════════════════════════════════════════════════════════
# 순수 함수 — 파일·DB 의존 없음
# ══════════════════════════════════════════════════════════════════════════


def usable_pairs(pairs: list, cluster_map: dict[int, int]) -> list[tuple[int, int]]:
    """양쪽 기사가 모두 결과에 있는 쌍만 남긴다(충족률 분모와 같은 기준)."""
    out: list[tuple[int, int]] = []
    for pair in pairs or []:
        if not pair or len(pair) < 2:
            continue
        a, b = pair[0], pair[1]
        if a in cluster_map and b in cluster_map:
            out.append((a, b))
    return out


def build_gold_clusters(must_pairs: list[tuple[int, int]]) -> dict[int, int]:
    """must-link 를 전이적으로 이어 `기사 id → 정답 사건 id`를 만든다.

    must 는 "같은 이벤트여야"이므로 a~b, b~c 가 must 면 a~c 도 같은 이벤트다. 따라서
    연결요소 하나가 정답이 말하는 사건 하나다. 대표 id 는 요소 안 최소 기사 id 를 쓴다.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in must_pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups: dict[int, list[int]] = defaultdict(list)
    for article in parent:
        groups[find(article)].append(article)
    return {article: min(members) for members in groups.values() for article in members}


def find_contradictions(
    gold_clusters: dict[int, int], cannot_pairs: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """must 로 이어진 기사들 사이에 걸린 cannot 쌍 — 정답끼리의 논리적 모순."""
    return [
        (a, b)
        for a, b in cannot_pairs
        if a in gold_clusters and b in gold_clusters and gold_clusters[a] == gold_clusters[b]
    ]


def score(
    assignment: dict[int, object],
    must_pairs: list[tuple[int, int]],
    cannot_pairs: list[tuple[int, int]],
) -> dict:
    """배정 하나를 제약에 대입해 충족 수와 충족률을 낸다."""
    must_ok = sum(1 for a, b in must_pairs if assignment[a] == assignment[b])
    cannot_ok = sum(1 for a, b in cannot_pairs if assignment[a] != assignment[b])
    return {
        "must_satisfied": must_ok,
        "must_total": len(must_pairs),
        "must_rate": satisfaction_rate(must_ok, len(must_pairs) - must_ok),
        "cannot_satisfied": cannot_ok,
        "cannot_total": len(cannot_pairs),
        "cannot_rate": satisfaction_rate(cannot_ok, len(cannot_pairs) - cannot_ok),
    }


def ideal_assignment(
    cluster_map: dict[int, int], gold_clusters: dict[int, int]
) -> dict[int, object]:
    """정답 사건을 그대로 군집으로 삼는다. 정답이 없는 기사는 현재 배정을 유지한다.

    문자열 키를 쓰는 이유는 현재 이벤트 id(정수)와 절대 섞이지 않게 하기 위해서다.
    """
    return {
        article: (f"gold:{gold_clusters[article]}" if article in gold_clusters else current)
        for article, current in cluster_map.items()
    }


def crosstab(cluster_map: dict[int, int], gold_clusters: dict[int, int]) -> dict:
    """현재 군집과 정답 사건이 서로 가로지르는 정도를 센다.

    과병합 = 한 군집이 여러 정답 사건의 기사를 담음
    과분할 = 한 정답 사건이 여러 군집으로 흩어짐
    """
    golds_in_cluster: dict[int, set] = defaultdict(set)
    clusters_in_gold: dict[int, set] = defaultdict(set)
    for article, gold in gold_clusters.items():
        current = cluster_map.get(article)
        if current is None:
            continue
        golds_in_cluster[current].add(gold)
        clusters_in_gold[gold].add(current)

    merged = Counter(len(v) for v in golds_in_cluster.values())
    split = Counter(len(v) for v in clusters_in_gold.values())
    return {
        "clusters_touched": len(golds_in_cluster),
        "gold_events": len(clusters_in_gold),
        "over_merged": sum(n for size, n in merged.items() if size > 1),
        "over_split": sum(n for size, n in split.items() if size > 1),
        "golds_per_cluster": dict(sorted(merged.items())),
        "clusters_per_gold": dict(sorted(split.items())),
    }


def move_tradeoff(
    cluster_map: dict[int, int], must_pairs: list[tuple[int, int]]
) -> dict:
    """위반 쌍의 기사를 상대 군집으로 옮기면 순이득이 나는지 센다.

    기사 하나는 보통 여러 must 이웃을 갖는다. 위반 쌍 하나를 고치려고 옮기면 **지금 같은
    군집에 있어서 충족 중인 다른 이웃들과 갈라진다.** 합이 음수면 어떤 재배정 방식으로도
    (프롬프트·임계값·후보 순서·판정 방식 무엇을 바꾸든) 충족 쌍을 늘릴 수 없다.

    위반 쌍마다 "그 하나만 고친다면"을 독립적으로 세어 합산한다. 여러 기사를 동시에 옮기는
    경우와 정확히 같지는 않지만(같은 쌍이 여러 번 세어질 수 있다), 부호는 신뢰할 수 있다 —
    한 번에 하나씩 고쳐도 손해고 한꺼번에 고쳐도 손해라면 방향 자체가 막힌 것이다.
    """
    neighbours: dict[int, list[int]] = defaultdict(list)
    for a, b in must_pairs:
        neighbours[a].append(b)
        neighbours[b].append(a)

    gains = losses = 0
    moves = 0
    for a, b in must_pairs:
        if cluster_map[a] == cluster_map[b]:
            continue
        moves += 1
        # a 를 b 의 군집으로 옮긴다고 가정
        target, origin = cluster_map[b], cluster_map[a]
        gains += sum(1 for n in neighbours[a] if cluster_map[n] == target)
        losses += sum(1 for n in neighbours[a] if cluster_map[n] == origin)
    return {
        "violated_pairs": moves,
        "pairs_gained": gains,
        "pairs_lost": losses,
        "net": gains - losses,
    }


def diagnose(gold: dict, snapshot: dict, unit: str = "event") -> dict:
    """정답과 결과 스냅샷을 대조해 구조 진단 전체를 산출한다."""
    if unit == "topic":
        cluster_map = build_topic_cluster_map(snapshot.get("topics") or [])
        constraints = gold.get("topic_constraints") or {}
    else:
        cluster_map = build_event_cluster_map(snapshot.get("events") or [])
        constraints = gold.get("event_constraints") or {}

    must = usable_pairs(constraints.get("must_link"), cluster_map)
    cannot = usable_pairs(constraints.get("cannot_link"), cluster_map)
    gold_clusters = build_gold_clusters(must)

    return {
        "meta": {
            "unit": unit,
            "articles_in_snapshot": len(cluster_map),
            "must_pairs": len(must),
            "cannot_pairs": len(cannot),
            "articles_with_gold": len(gold_clusters),
        },
        "contradictions": [list(p) for p in find_contradictions(gold_clusters, cannot)],
        "current": score(cluster_map, must, cannot),
        "ideal": score(ideal_assignment(cluster_map, gold_clusters), must, cannot),
        "crosstab": crosstab(cluster_map, gold_clusters),
        "move_tradeoff": move_tradeoff(cluster_map, must),
    }


# ══════════════════════════════════════════════════════════════════════════
# 출력
# ══════════════════════════════════════════════════════════════════════════


def _rate(v: float | None) -> str:
    return "N/A" if v is None else f"{v * 100:.1f}%"


def print_report(result: dict) -> None:
    meta, cur, ideal = result["meta"], result["current"], result["ideal"]
    ct, mt = result["crosstab"], result["move_tradeoff"]
    unit_label = "토픽" if meta["unit"] == "topic" else "이벤트"

    print("═" * 64)
    print(f"구조 진단 — {unit_label} 제약")
    print("═" * 64)
    print(f"  기사 {meta['articles_in_snapshot']}건 · must {meta['must_pairs']:,}쌍 · "
          f"cannot {meta['cannot_pairs']}쌍")

    print("\n[1] 정답끼리 모순인가")
    contra = result["contradictions"]
    if contra:
        print(f"  ❌ 모순 {len(contra)}쌍 — must 로 이어진 기사들 사이에 cannot 이 걸려 있다.")
        print("     어떤 군집화로도 동시에 만족시킬 수 없다. 정답을 먼저 고쳐야 한다.")
        for a, b in contra[:5]:
            print(f"       기사 {a} / {b}")
    else:
        print("  ✅ 모순 없음 — 정답은 동시에 달성 가능하다.")

    print("\n[2] 정답대로 묶으면 (달성 가능한 상한)")
    print(f"  현재   must {_rate(cur['must_rate']):>6} ({cur['must_satisfied']:,}/{cur['must_total']:,})"
          f"   cannot {_rate(cur['cannot_rate']):>6} ({cur['cannot_satisfied']}/{cur['cannot_total']})")
    print(f"  상한   must {_rate(ideal['must_rate']):>6} ({ideal['must_satisfied']:,}/{ideal['must_total']:,})"
          f"   cannot {_rate(ideal['cannot_rate']):>6} ({ideal['cannot_satisfied']}/{ideal['cannot_total']})")

    print("\n[3] 현재 경계가 정답을 얼마나 가로지르나")
    print(f"  {unit_label} {ct['clusters_touched']}개 중 여러 정답 사건을 담은 것 {ct['over_merged']}개 (과병합)")
    print(f"  정답 사건 {ct['gold_events']}개 중 여러 {unit_label}로 흩어진 것 {ct['over_split']}개 (과분할)")
    print(f"    한 {unit_label}가 담은 정답 사건 수: {ct['golds_per_cluster']}")
    print(f"    한 사건이 흩어진 {unit_label} 수:   {ct['clusters_per_gold']}")
    print("  ※ 정답 사건은 must 로만 만든다. must 가 없는 기사(cannot 만 걸린 기사)는 여기 안 잡히므로,")
    print("    과병합의 실제 크기는 위 개수가 아니라 [2]의 cannot 충족률로 읽어야 한다.")

    print("\n[4] 기사를 옮겨서 고칠 수 있나")
    print(f"  위반 {mt['violated_pairs']}쌍을 상대 군집으로 옮기면 "
          f"얻는 쌍 +{mt['pairs_gained']} · 잃는 쌍 -{mt['pairs_lost']} → 순 {mt['net']:+d}")
    if mt["net"] > 0:
        print("  ✅ 재배정으로 이득이 난다 — 배정 로직(프롬프트·임계값·후보) 개선이 유효하다.")
    else:
        print("  ❌ 재배정은 순손실이다. 배정 로직을 어떻게 고쳐도 충족 쌍은 늘지 않는다.")
        print(f"     {unit_label} 경계 자체가 어긋나 있으므로 쪼개고 다시 묶는 재군집이 필요하다.")
    print("\n" + "═" * 64)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="개선을 시도하기 전에 그 방향으로 좋아질 수 있는지 먼저 진단한다."
    )
    parser.add_argument("gold", help="검수 정답 JSON (schema_version=constraints-v1)")
    parser.add_argument("target", help="재분류 결과 스냅샷 JSON")
    parser.add_argument("--unit", choices=("event", "topic"), default="event",
                        help="진단 대상 (기본: event)")
    parser.add_argument("--json", action="store_true", help="콘솔 요약 대신 JSON 출력")
    args = parser.parse_args()

    gold = load_json(args.gold)
    if gold.get("schema_version") != SCHEMA_VERSION:
        print(f"[경고] 정답 schema_version이 '{gold.get('schema_version')}'입니다 "
              f"(기대: '{SCHEMA_VERSION}').", file=sys.stderr)

    result = diagnose(gold, load_json(args.target), unit=args.unit)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()

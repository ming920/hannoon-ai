"""클러스터링 품질 지표 모음 — 순수 Python stdlib(math, collections)만 사용한다.

외부 의존성(sklearn, numpy 등) 없이 B-cubed, ARI, NMI, Homogeneity, Completeness,
V-measure 등 주요 클러스터링 지표를 구현한다.

설계 정책 (evaluate_level):
  gold에는 있지만 pred에 없는 아이템은 각자를 별도 싱글톤 예측 클러스터로 취급한다.
  이 처리는 예측 누락을 패널티로 반영하기 위한 것이다.
  coverage 지표는 실제 교집합 비율(누락 처리 전)을 별도로 보고한다.
"""
from __future__ import annotations

import math
from collections import defaultdict

# ── 내부 헬퍼 ──────────────────────────────────────────────────────────────


def _comb2(n: int) -> int:
    """C(n, 2): 쌍 개수 계산 헬퍼."""
    return n * (n - 1) // 2


def _cluster_sizes(mapping: dict) -> dict:
    """라벨 → 아이템 수 매핑을 반환한다."""
    sizes: dict = defaultdict(int)
    for lbl in mapping.values():
        sizes[lbl] += 1
    return dict(sizes)


def _contingency(pred: dict, gold: dict) -> dict[tuple, int]:
    """예측 라벨·정답 라벨 쌍별 교집합 크기(n_ij)를 반환한다."""
    table: dict[tuple, int] = defaultdict(int)
    for item, p_lbl in pred.items():
        g_lbl = gold.get(item)
        if g_lbl is not None:
            table[(p_lbl, g_lbl)] += 1
    return dict(table)


def _entropy(sizes: dict, n: int) -> float:
    """라벨 크기 dict로부터 엔트로피를 계산한다."""
    h = 0.0
    for size in sizes.values():
        if size > 0:
            p = size / n
            h -= p * math.log(p)
    return h


def _conditional_entropy(outer: dict, inner: dict, n: int) -> float:
    """H(outer | inner): inner 클러스터가 주어졌을 때 outer의 조건부 엔트로피.

    예: outer=gold, inner=pred → H(gold|pred) = 동질성 계산용.
    """
    inner_to_outer: dict[object, dict[object, int]] = defaultdict(lambda: defaultdict(int))
    common = set(outer) & set(inner)
    for item in common:
        inner_to_outer[inner[item]][outer[item]] += 1

    inner_sizes = _cluster_sizes({item: inner[item] for item in common})

    h = 0.0
    for i_lbl, outer_counts in inner_to_outer.items():
        i_size = inner_sizes[i_lbl]
        for _o_lbl, n_ij in outer_counts.items():
            if n_ij > 0 and i_size > 0:
                h -= (n_ij / n) * math.log(n_ij / i_size)
    return h


# ── B-cubed ─────────────────────────────────────────────────────────────────


def bcubed(pred: dict, gold: dict) -> tuple[float, float, float]:
    """B-cubed precision, recall, F1을 계산한다.

    Args:
        pred: item_id → 예측 클러스터 라벨
        gold: item_id → 정답 클러스터 라벨

    Returns:
        (precision, recall, f1) 튜플. 교집합이 비어 있으면 (0, 0, 0).
    """
    items = set(pred) & set(gold)
    if not items:
        return 0.0, 0.0, 0.0

    pred_clusters: dict[object, set] = defaultdict(set)
    gold_clusters: dict[object, set] = defaultdict(set)
    for item in items:
        pred_clusters[pred[item]].add(item)
        gold_clusters[gold[item]].add(item)

    total_p = 0.0
    total_r = 0.0
    for item in items:
        p_clus = pred_clusters[pred[item]]
        g_clus = gold_clusters[gold[item]]
        correct = len(p_clus & g_clus)
        total_p += correct / len(p_clus)
        total_r += correct / len(g_clus)

    n = len(items)
    precision = total_p / n
    recall = total_r / n
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


# ── ARI ─────────────────────────────────────────────────────────────────────


def adjusted_rand_index(pred: dict, gold: dict) -> float:
    """Adjusted Rand Index(ARI)를 계산한다.

    완전 일치 → 1.0, 무작위 기대치 → ~0.0, 역방향 군집 → 음수.
    """
    items = set(pred) & set(gold)
    n = len(items)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0

    p = {item: pred[item] for item in items}
    g = {item: gold[item] for item in items}

    contingency = _contingency(p, g)
    p_sizes = _cluster_sizes(p)
    g_sizes = _cluster_sizes(g)

    sum_comb_n = sum(_comb2(v) for v in contingency.values())
    sum_comb_a = sum(_comb2(v) for v in p_sizes.values())
    sum_comb_b = sum(_comb2(v) for v in g_sizes.values())
    total_comb = _comb2(n)

    if total_comb == 0:
        return 1.0

    expected = (sum_comb_a * sum_comb_b) / total_comb
    denom = 0.5 * (sum_comb_a + sum_comb_b) - expected
    if denom == 0.0:
        # 모든 아이템이 단일 클러스터이거나 각자 자신만의 클러스터인 퇴화 케이스
        return 1.0 if abs(sum_comb_n - expected) < 1e-12 else 0.0
    return (sum_comb_n - expected) / denom


# ── MI / NMI ─────────────────────────────────────────────────────────────────


def mutual_information(pred: dict, gold: dict) -> float:
    """상호 정보량(MI)을 계산한다."""
    items = set(pred) & set(gold)
    n = len(items)
    if n == 0:
        return 0.0

    p = {item: pred[item] for item in items}
    g = {item: gold[item] for item in items}
    contingency = _contingency(p, g)
    p_sizes = _cluster_sizes(p)
    g_sizes = _cluster_sizes(g)

    mi = 0.0
    for (p_lbl, g_lbl), n_ij in contingency.items():
        if n_ij == 0:
            continue
        p_ij = n_ij / n
        p_i = p_sizes[p_lbl] / n
        p_j = g_sizes[g_lbl] / n
        mi += p_ij * math.log(p_ij / (p_i * p_j))
    return mi


def normalized_mutual_information(pred: dict, gold: dict) -> float:
    """NMI(산술 평균 정규화)를 계산한다.

    NMI = 2 * MI / (H(pred) + H(gold))
    두 엔트로피 합이 0이면 퇴화 케이스로 1.0을 반환한다.
    """
    items = set(pred) & set(gold)
    n = len(items)
    if n == 0:
        return 0.0

    p = {item: pred[item] for item in items}
    g = {item: gold[item] for item in items}
    mi = mutual_information(p, g)
    h_pred = _entropy(_cluster_sizes(p), n)
    h_gold = _entropy(_cluster_sizes(g), n)
    denom = h_pred + h_gold
    if denom == 0.0:
        return 1.0
    return 2.0 * mi / denom


# ── Homogeneity / Completeness / V-measure ──────────────────────────────────


def homogeneity(pred: dict, gold: dict) -> float:
    """동질성(Homogeneity): 각 예측 클러스터가 하나의 정답 클래스만 담는 정도.

    H(gold) = 0 이면(정답이 단일 클래스) 1.0 반환.
    """
    items = set(pred) & set(gold)
    n = len(items)
    if n == 0:
        return 0.0

    g = {item: gold[item] for item in items}
    p = {item: pred[item] for item in items}
    h_c = _entropy(_cluster_sizes(g), n)
    if h_c == 0.0:
        return 1.0
    h_c_given_k = _conditional_entropy(g, p, n)
    return 1.0 - h_c_given_k / h_c


def completeness(pred: dict, gold: dict) -> float:
    """완전성(Completeness): 같은 정답 클래스가 같은 클러스터에 모이는 정도.

    H(pred) = 0 이면(예측이 단일 클러스터) 1.0 반환.
    """
    items = set(pred) & set(gold)
    n = len(items)
    if n == 0:
        return 0.0

    g = {item: gold[item] for item in items}
    p = {item: pred[item] for item in items}
    h_k = _entropy(_cluster_sizes(p), n)
    if h_k == 0.0:
        return 1.0
    h_k_given_c = _conditional_entropy(p, g, n)
    return 1.0 - h_k_given_c / h_k


def v_measure(pred: dict, gold: dict) -> float:
    """V-measure: homogeneity와 completeness의 조화 평균."""
    h = homogeneity(pred, gold)
    c = completeness(pred, gold)
    if h + c == 0.0:
        return 0.0
    return 2.0 * h * c / (h + c)


# ── 진단 카운트 ─────────────────────────────────────────────────────────────


def cluster_diagnostics(pred: dict, gold: dict) -> dict:
    """클러스터링 품질 진단 통계를 반환한다.

    Returns:
        num_pred_clusters   : 예측 클러스터 수
        num_gold_clusters   : 정답 클러스터 수
        singleton_rate      : 아이템 1개짜리 예측 클러스터 비율 (over-create 지표)
        over_split_count    : 2개 이상 예측 클러스터에 분산된 정답 클러스터 수
        over_merge_count    : 2개 이상 정답 클래스를 혼합한 예측 클러스터 수
        duplicate_pred_clusters : 동일 단일 정답 클러스터에만 대응되는 예측 클러스터 수에서 1을 뺀 합
                                  (중복 토픽/이벤트 생성 프록시)
    """
    items = set(pred) & set(gold)
    if not items:
        return {
            "num_pred_clusters": 0,
            "num_gold_clusters": 0,
            "singleton_rate": 0.0,
            "over_split_count": 0,
            "over_merge_count": 0,
            "duplicate_pred_clusters": 0,
        }

    p = {item: pred[item] for item in items}
    g = {item: gold[item] for item in items}

    pred_to_items: dict[object, set] = defaultdict(set)
    for item, lbl in p.items():
        pred_to_items[lbl].add(item)

    gold_to_items: dict[object, set] = defaultdict(set)
    for item, lbl in g.items():
        gold_to_items[lbl].add(item)

    num_pred = len(pred_to_items)
    num_gold = len(gold_to_items)

    # 싱글톤 비율: 예측 클러스터 중 아이템 1개짜리 비율
    singleton_rate = (
        sum(1 for s in pred_to_items.values() if len(s) == 1) / num_pred
        if num_pred > 0 else 0.0
    )

    # over-split: 정답 클러스터가 2개 이상 예측 클러스터에 분산된 경우
    over_split_count = sum(
        1
        for g_items in gold_to_items.values()
        if len({p[item] for item in g_items}) > 1
    )

    # over-merge: 예측 클러스터가 2개 이상 정답 클래스를 혼합한 경우
    over_merge_count = sum(
        1
        for p_items in pred_to_items.values()
        if len({g[item] for item in p_items}) > 1
    )

    # 중복 예측 클러스터: 동일 단일 정답 클러스터에만 속하는 예측 클러스터를 정답별로 묶어
    # 2개 이상이면 (개수-1)을 중복으로 센다.
    gold_fingerprint: dict[object, list] = defaultdict(list)
    for p_lbl, p_items in pred_to_items.items():
        gold_labels_set = {g[item] for item in p_items}
        if len(gold_labels_set) == 1:
            (g_lbl,) = gold_labels_set
            gold_fingerprint[g_lbl].append(p_lbl)

    duplicate_pred_clusters = sum(
        len(p_lbls) - 1
        for p_lbls in gold_fingerprint.values()
        if len(p_lbls) > 1
    )

    return {
        "num_pred_clusters": num_pred,
        "num_gold_clusters": num_gold,
        "singleton_rate": singleton_rate,
        "over_split_count": over_split_count,
        "over_merge_count": over_merge_count,
        "duplicate_pred_clusters": duplicate_pred_clusters,
    }


# ── 레벨 통합 평가 ──────────────────────────────────────────────────────────


def evaluate_level(pred: dict, gold: dict) -> dict:
    """한 레벨(이벤트/서브토픽/토픽)에 대한 전체 지표를 계산한다.

    gold에는 있지만 pred에 없는 아이템은 각자를 별도 싱글톤 예측 클러스터로 취급한다.
    이 처리는 예측 누락을 패널티로 반영하기 위한 것이다.

    Args:
        pred: item_id → 예측 라벨
        gold: item_id → 정답 라벨

    Returns:
        dict with: ari, nmi, homogeneity, completeness, v_measure,
                   bcubed_precision, bcubed_recall, bcubed_f1, coverage, diagnostics
    """
    if not gold:
        return {
            "ari": 0.0,
            "nmi": 0.0,
            "homogeneity": 0.0,
            "completeness": 0.0,
            "v_measure": 0.0,
            "bcubed_precision": 0.0,
            "bcubed_recall": 0.0,
            "bcubed_f1": 0.0,
            "coverage": 0.0,
            "diagnostics": cluster_diagnostics({}, {}),
            "covered_only": {
                "ari": 0.0,
                "bcubed_precision": 0.0,
                "bcubed_recall": 0.0,
                "bcubed_f1": 0.0,
                "diagnostics": cluster_diagnostics({}, {}),
            },
            "note": "gold가 비어 있음",
        }

    # coverage: 실제 예측이 있는 아이템 비율 (누락 처리 전 기준)
    covered = set(gold) & set(pred)
    coverage = len(covered) / len(gold)

    # gold에는 있지만 pred에 없는 아이템 → 고유 싱글톤 예측 클러스터로 추가
    augmented_pred = dict(pred)
    _singleton_counter = 0
    for item in gold:
        if item not in augmented_pred:
            augmented_pred[item] = f"__missing_{_singleton_counter}__"
            _singleton_counter += 1

    bp, br, bf = bcubed(augmented_pred, gold)
    diag = cluster_diagnostics(augmented_pred, gold)

    # covered-only 지표: 실제 배정된 아이템만으로 계산한 군집 품질.
    # 유령 싱글턴(__missing_N__) 주입이 singleton_rate·num_pred·F1을 왜곡하므로,
    # coverage 효과(미배정 패널티)와 군집 품질 효과를 분리해서 볼 수 있게 한다.
    covered_pred = {item: pred[item] for item in covered}
    covered_gold = {item: gold[item] for item in covered}
    if covered:
        cbp, cbr, cbf = bcubed(covered_pred, covered_gold)
        covered_only = {
            "ari": adjusted_rand_index(covered_pred, covered_gold),
            "bcubed_precision": cbp,
            "bcubed_recall": cbr,
            "bcubed_f1": cbf,
            "diagnostics": cluster_diagnostics(covered_pred, covered_gold),
        }
    else:
        covered_only = {
            "ari": 0.0,
            "bcubed_precision": 0.0,
            "bcubed_recall": 0.0,
            "bcubed_f1": 0.0,
            "diagnostics": cluster_diagnostics({}, {}),
        }

    return {
        "ari": adjusted_rand_index(augmented_pred, gold),
        "nmi": normalized_mutual_information(augmented_pred, gold),
        "homogeneity": homogeneity(augmented_pred, gold),
        "completeness": completeness(augmented_pred, gold),
        "v_measure": v_measure(augmented_pred, gold),
        "bcubed_precision": bp,
        "bcubed_recall": br,
        "bcubed_f1": bf,
        "coverage": coverage,
        "diagnostics": diag,
        "covered_only": covered_only,
    }


# ── 셀프 테스트 ─────────────────────────────────────────────────────────────


def _selftest() -> None:
    """지표 함수의 정합성을 검증한다. 실패 시 AssertionError를 발생시킨다."""

    # ── 1) 오라클 테스트: pred == gold → 모든 주요 지표 1.0 ──────────────
    gold_oracle = {"a": "X", "b": "X", "c": "Y", "d": "Y", "e": "Z"}
    pred_oracle = dict(gold_oracle)
    res = evaluate_level(pred_oracle, gold_oracle)
    assert abs(res["ari"] - 1.0) < 1e-9, f"Oracle ARI: {res['ari']}"
    assert abs(res["bcubed_f1"] - 1.0) < 1e-9, f"Oracle B-cubed F1: {res['bcubed_f1']}"
    assert abs(res["v_measure"] - 1.0) < 1e-9, f"Oracle V-measure: {res['v_measure']}"
    assert abs(res["coverage"] - 1.0) < 1e-9, f"Oracle coverage: {res['coverage']}"
    assert res["diagnostics"]["over_split_count"] == 0, "Oracle over_split"
    assert res["diagnostics"]["over_merge_count"] == 0, "Oracle over_merge"
    # 완전 일치 시 NMI = 1.0 (MI = H(pred) = H(gold) → NMI = 2H/2H = 1)
    assert abs(res["nmi"] - 1.0) < 1e-9, f"Oracle NMI: {res['nmi']}"

    # ── 1-1) covered-only: 미배정 아이템의 유령 싱글턴 왜곡을 분리 ─────────
    # c가 미배정인데 gold X={a,b,c} 다항목 클러스터의 일원 → augmented는 recall
    # 패널티를 받고, covered-only(배정분 {a,b,d}는 완벽 군집)는 1.0이어야 함.
    # (미배정 아이템이 gold 싱글턴이면 유령 싱글턴이 완벽 매치가 되어 패널티가 없다.)
    gold_cov = {"a": "X", "b": "X", "c": "X", "d": "Y"}
    pred_cov = {"a": 1, "b": 1, "d": 2}
    res_cov = evaluate_level(pred_cov, gold_cov)
    assert abs(res_cov["coverage"] - 3 / 4) < 1e-9, f"covered coverage: {res_cov['coverage']}"
    co = res_cov["covered_only"]
    assert abs(co["bcubed_f1"] - 1.0) < 1e-9, f"covered-only F1: {co['bcubed_f1']}"
    assert res_cov["bcubed_f1"] < 1.0, "augmented F1은 미배정 패널티를 반영해야 함"
    assert co["diagnostics"]["num_pred_clusters"] == 2, "covered-only는 유령 싱글턴 제외"
    assert res_cov["diagnostics"]["num_pred_clusters"] == 3, "augmented는 유령 싱글턴 포함"

    # ── 2) 의도적 역 군집화: 정답 분포와 반대 → 낮은 점수 ───────────────
    # pred가 정답 클러스터를 섞어놓음: X,Y,Z → 두 그룹 P,Q로 재배치
    pred_bad = {"a": "P", "b": "Q", "c": "Q", "d": "P", "e": "P"}
    res_bad = evaluate_level(pred_bad, gold_oracle)
    assert res_bad["ari"] < 0.5, f"Bad ARI should be < 0.5: {res_bad['ari']}"
    assert res_bad["bcubed_f1"] < 0.9, f"Bad B-cubed F1 should be < 0.9: {res_bad['bcubed_f1']}"
    assert res_bad["v_measure"] < res["v_measure"], "Bad V-measure should be < oracle"

    # ── 3) 손 계산 검증 (B-cubed + ARI) ────────────────────────────────
    # pred={a:0, b:0, c:1}, gold={a:0, b:1, c:1}
    #
    # B-cubed:
    #   pred clusters: 0→{a,b}, 1→{c}
    #   gold clusters: 0→{a},   1→{b,c}
    #   a: prec=|{a}|/|{a,b}|=1/2, recall=|{a}|/|{a}|=1/1
    #   b: prec=|{b}|/|{a,b}|=1/2, recall=|{b}|/|{b,c}|=1/2
    #   c: prec=|{c}|/|{c}|=1/1,   recall=|{c}|/|{b,c}|=1/2
    #   avg_prec = (1/2+1/2+1)/3 = 2/3
    #   avg_rec  = (1+1/2+1/2)/3 = 2/3
    #   F1 = 2*(2/3)*(2/3)/((2/3)+(2/3)) = 2/3
    pred_small = {"a": 0, "b": 0, "c": 1}
    gold_small = {"a": 0, "b": 1, "c": 1}
    bp, br, bf = bcubed(pred_small, gold_small)
    assert abs(bp - 2 / 3) < 1e-9, f"Small B-cubed precision: {bp}"
    assert abs(br - 2 / 3) < 1e-9, f"Small B-cubed recall: {br}"
    assert abs(bf - 2 / 3) < 1e-9, f"Small B-cubed F1: {bf}"

    # ARI 손 계산:
    #   pred_sizes={0:2, 1:1}, gold_sizes={0:1, 1:2}
    #   contingency={(0,0):1, (0,1):1, (1,1):1}
    #   sum_comb_n = C(1,2)+C(1,2)+C(1,2) = 0
    #   sum_comb_a = C(2,2)+C(1,2) = 1
    #   sum_comb_b = C(1,2)+C(2,2) = 1
    #   total_comb = C(3,2) = 3
    #   expected = (1*1)/3 = 1/3
    #   denom = 0.5*(1+1) - 1/3 = 2/3
    #   ARI = (0 - 1/3) / (2/3) = -1/2
    ari_small = adjusted_rand_index(pred_small, gold_small)
    assert abs(ari_small - (-0.5)) < 1e-9, f"Small ARI: {ari_small}"

    # NMI 손 계산 (pred_small/gold_small, n=3):
    #   contingency={(0,0):1, (0,1):1, (1,1):1}
    #   pred_sizes={0:2,1:1}  gold_sizes={0:1,1:2}
    #   MI = (1/3)*ln((1/3)/[(2/3)*(1/3)]) + (1/3)*ln((1/3)/[(2/3)*(2/3)])
    #        + (1/3)*ln((1/3)/[(1/3)*(2/3)])
    #      = (1/3)*ln(3/2) + (1/3)*ln(3/4) + (1/3)*ln(3/2)
    #      = (2/3)*ln(3/2) + (1/3)*ln(3/4)
    #   H(pred)=H(gold) = (2/3)*ln(3/2) + (1/3)*ln(3)  [by symmetry]
    #   NMI = 2*MI / (H(pred)+H(gold)) = MI / H(pred)
    expected_nmi_small = (
        (2 / 3) * math.log(3 / 2) + (1 / 3) * math.log(3 / 4)
    ) / (
        (2 / 3) * math.log(3 / 2) + (1 / 3) * math.log(3)
    )
    nmi_small = normalized_mutual_information(pred_small, gold_small)
    assert abs(nmi_small - expected_nmi_small) < 1e-9, (
        f"Small NMI: {nmi_small} vs expected {expected_nmi_small}"
    )

    # ── 4) 진단 통계 검증 ────────────────────────────────────────────────
    # pred={a:0, b:1, c:2} (모두 싱글톤), gold={a:0, b:0, c:0} (단일 클러스터)
    pred_diag = {"a": 0, "b": 1, "c": 2}
    gold_diag = {"a": 0, "b": 0, "c": 0}
    diag = cluster_diagnostics(pred_diag, gold_diag)
    assert diag["singleton_rate"] == 1.0, f"Singleton rate: {diag['singleton_rate']}"
    assert diag["over_split_count"] == 1, f"Over-split count: {diag['over_split_count']}"
    assert diag["over_merge_count"] == 0, f"Over-merge count: {diag['over_merge_count']}"

    # over-merge 검증: pred={a:0, b:0} 가 gold_0과 gold_1을 혼합
    pred_merge = {"a": 0, "b": 0}
    gold_merge = {"a": "G0", "b": "G1"}
    diag_m = cluster_diagnostics(pred_merge, gold_merge)
    assert diag_m["over_merge_count"] == 1, f"Over-merge count: {diag_m['over_merge_count']}"

    # ── 5) coverage 및 누락 패널티 검증 ─────────────────────────────────
    # pred에 c가 없음 → c는 싱글톤으로 추가되어 패널티 반영
    pred_partial = {"a": 0, "b": 0}
    gold_cov = {"a": 0, "b": 0, "c": 0}
    res_partial = evaluate_level(pred_partial, gold_cov)
    assert abs(res_partial["coverage"] - 2 / 3) < 1e-9, f"Coverage: {res_partial['coverage']}"
    # 누락 c가 싱글톤 클러스터로 패널티 반영 → bcubed_f1 < 1.0
    assert res_partial["bcubed_f1"] < 1.0, (
        f"Partial bcubed_f1 should be < 1: {res_partial['bcubed_f1']}"
    )

    # ── 6) 중복 예측 클러스터(duplicate_pred_clusters) 검증 ─────────────
    # gold={a:G, b:G, c:G}에 대해 pred={a:0, b:1, c:2}: 세 pred 클러스터 모두 G에만 속함
    pred_dup = {"a": 0, "b": 1, "c": 2}
    gold_dup = {"a": "G", "b": "G", "c": "G"}
    diag_dup = cluster_diagnostics(pred_dup, gold_dup)
    # G에 대응되는 싱글톤 pred 클러스터 3개 → duplicate = 3-1 = 2
    assert diag_dup["duplicate_pred_clusters"] == 2, (
        f"Duplicate pred clusters: {diag_dup['duplicate_pred_clusters']}"
    )

    # ── 7) 빈 pred 엣지 케이스 ──────────────────────────────────────────
    # pred={}이면 크래시 없이 coverage==0.0이 되어야 한다.
    gold_5 = {f"item{i}": "G0" for i in range(5)}
    res_empty_pred = evaluate_level({}, gold_5)
    assert res_empty_pred["coverage"] == 0.0, (
        f"Empty pred coverage: {res_empty_pred['coverage']}"
    )
    # gold가 비어 있지 않으므로 'note' 키는 없어야 한다
    assert "note" not in res_empty_pred, "Empty pred should not produce 'note'"

    # ── 8) 전체 병합(all-merge) 엣지 케이스 ──────────────────────────────
    # 모든 아이템이 단일 예측 클러스터 → h_k=0 early-return → completeness=1.0
    # 정답은 각자 다른 클래스 → homogeneity=0, v_measure=0
    #
    # pred={a:0, b:0, c:0}, gold={a:G0, b:G1, c:G2}
    # completeness: H(pred)=0 (단일 클러스터) → 1.0
    # homogeneity:
    #   H(gold) = -3*(1/3)*ln(1/3) = ln(3)
    #   H(gold|pred): 클러스터 0에 n_ij=1씩 세 gold 클래스
    #     = -3*(1/3)*ln(1/3 / 3) = -3*(1/3)*ln(1/9) = ln(9)/...
    #     actually = -sum (n_ij/n)*ln(n_ij/i_size)
    #              = -3*(1/3)*ln(1/3) = ln(3)   (i_size=3, n_ij=1 세 번)
    #   homogeneity = 1 - ln(3)/ln(3) = 0
    pred_all_merge = {"a": 0, "b": 0, "c": 0}
    gold_all_merge = {"a": "G0", "b": "G1", "c": "G2"}
    res_am = evaluate_level(pred_all_merge, gold_all_merge)
    assert abs(res_am["completeness"] - 1.0) < 1e-9, (
        f"All-merge completeness: {res_am['completeness']}"
    )
    assert abs(res_am["homogeneity"]) < 1e-9, (
        f"All-merge homogeneity: {res_am['homogeneity']}"
    )
    assert abs(res_am["v_measure"]) < 1e-9, (
        f"All-merge v_measure: {res_am['v_measure']}"
    )

    print("PASS")


if __name__ == "__main__":
    _selftest()

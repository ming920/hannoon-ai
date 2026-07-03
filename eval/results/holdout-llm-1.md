# 평가 리포트: holdout-llm-1

- **config_tag**: holdout full run, llm subtopic baseline (unseen data)
- **timestamp**: 2026-07-03T16:49:47Z

---

## 이벤트 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | 0.8998 |
| NMI | 0.9682 |
| V-measure | 0.9682 |
| Homogeneity | 0.9792 |
| Completeness | 0.9575 |
| B-cubed Precision | 0.9560 |
| B-cubed Recall | 0.9221 |
| B-cubed F1 | 0.9387 |

### 진단

- **num_pred_clusters**: 23
- **num_gold_clusters**: 19
- **singleton_rate**: 0.21739130434782608
- **over_split_count**: 4
- **over_merge_count**: 1
- **duplicate_pred_clusters**: 4

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.9560
- **B-cubed Recall**: 0.9221
- **B-cubed F1**: 0.9387
- **ARI**: 0.8998
- **num_pred_clusters**: 23
- **singleton_rate**: 0.21739130434782608
- **over_split_count**: 4
- **over_merge_count**: 1

---

## 서브토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.7826 |
| ARI | 0.1421 |
| NMI | 0.6861 |
| V-measure | 0.6861 |
| Homogeneity | 0.6144 |
| Completeness | 0.7768 |
| B-cubed Precision | 0.5109 |
| B-cubed Recall | 0.7464 |
| B-cubed F1 | 0.6066 |

### 진단

- **num_pred_clusters**: 9
- **num_gold_clusters**: 11
- **singleton_rate**: 0.5555555555555556
- **over_split_count**: 3
- **over_merge_count**: 3
- **duplicate_pred_clusters**: 2

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.3750
- **B-cubed Recall**: 1.0000
- **B-cubed F1**: 0.5455
- **ARI**: 0.2064
- **num_pred_clusters**: 4
- **singleton_rate**: 0.0
- **over_split_count**: 0
- **over_merge_count**: 3

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.7826 |
| ARI | 0.4046 |
| NMI | 0.6972 |
| V-measure | 0.6972 |
| Homogeneity | 0.7046 |
| Completeness | 0.6900 |
| B-cubed Precision | 0.6630 |
| B-cubed Recall | 0.6957 |
| B-cubed F1 | 0.6790 |

### 진단

- **num_pred_clusters**: 8
- **num_gold_clusters**: 5
- **singleton_rate**: 0.625
- **over_split_count**: 3
- **over_merge_count**: 2
- **duplicate_pred_clusters**: 3

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.5694
- **B-cubed Recall**: 1.0000
- **B-cubed F1**: 0.7257
- **ARI**: 0.5128
- **num_pred_clusters**: 3
- **singleton_rate**: 0.0
- **over_split_count**: 0
- **over_merge_count**: 2

---

## 계층 정합성

- **hierarchy_consistency**: 1.0000

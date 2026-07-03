# 평가 리포트: holdout-emb-1

- **config_tag**: holdout topics-only, embedding subtopic tau 0.55 run 1
- **timestamp**: 2026-07-03T16:58:48Z

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
| ARI | 0.2714 |
| NMI | 0.7966 |
| V-measure | 0.7966 |
| Homogeneity | 0.7960 |
| Completeness | 0.7971 |
| B-cubed Precision | 0.6870 |
| B-cubed Recall | 0.7174 |
| B-cubed F1 | 0.7018 |

### 진단

- **num_pred_clusters**: 12
- **num_gold_clusters**: 11
- **singleton_rate**: 0.5
- **over_split_count**: 3
- **over_merge_count**: 4
- **duplicate_pred_clusters**: 3

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.6000
- **B-cubed Recall**: 0.9444
- **B-cubed F1**: 0.7338
- **ARI**: 0.4041
- **num_pred_clusters**: 7
- **singleton_rate**: 0.14285714285714285
- **over_split_count**: 1
- **over_merge_count**: 4

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.7826 |
| ARI | 0.4160 |
| NMI | 0.6765 |
| V-measure | 0.6765 |
| Homogeneity | 0.6996 |
| Completeness | 0.6549 |
| B-cubed Precision | 0.6667 |
| B-cubed Recall | 0.6522 |
| B-cubed F1 | 0.6593 |

### 진단

- **num_pred_clusters**: 8
- **num_gold_clusters**: 5
- **singleton_rate**: 0.625
- **over_split_count**: 3
- **over_merge_count**: 3
- **duplicate_pred_clusters**: 2

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.5741
- **B-cubed Recall**: 0.9259
- **B-cubed F1**: 0.7087
- **ARI**: 0.5372
- **num_pred_clusters**: 3
- **singleton_rate**: 0.0
- **over_split_count**: 1
- **over_merge_count**: 3

---

## 계층 정합성

- **hierarchy_consistency**: 0.8333

# 평가 리포트: holdout-emb065-1

- **config_tag**: holdout topics-only, embedding subtopic tau 0.65 (robust tau from dual-dataset sweep)
- **timestamp**: 2026-07-03T17:08:40Z

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
| ARI | 0.1692 |
| NMI | 0.8489 |
| V-measure | 0.8489 |
| Homogeneity | 0.9640 |
| Completeness | 0.7583 |
| B-cubed Precision | 0.9420 |
| B-cubed Recall | 0.5435 |
| B-cubed F1 | 0.6893 |

### 진단

- **num_pred_clusters**: 20
- **num_gold_clusters**: 11
- **singleton_rate**: 0.9
- **over_split_count**: 7
- **over_merge_count**: 1
- **duplicate_pred_clusters**: 8

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.9259
- **B-cubed Recall**: 0.7222
- **B-cubed F1**: 0.8115
- **ARI**: 0.3417
- **num_pred_clusters**: 15
- **singleton_rate**: 0.8666666666666667
- **over_split_count**: 5
- **over_merge_count**: 1

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.7826 |
| ARI | 0.5334 |
| NMI | 0.7840 |
| V-measure | 0.7840 |
| Homogeneity | 0.8488 |
| Completeness | 0.7283 |
| B-cubed Precision | 0.8261 |
| B-cubed Recall | 0.6957 |
| B-cubed F1 | 0.7553 |

### 진단

- **num_pred_clusters**: 9
- **num_gold_clusters**: 5
- **singleton_rate**: 0.5555555555555556
- **over_split_count**: 3
- **over_merge_count**: 1
- **duplicate_pred_clusters**: 4

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.7778
- **B-cubed Recall**: 1.0000
- **B-cubed F1**: 0.8750
- **ARI**: 0.7022
- **num_pred_clusters**: 4
- **singleton_rate**: 0.0
- **over_split_count**: 0
- **over_merge_count**: 1

---

## 계층 정합성

- **hierarchy_consistency**: 1.0000

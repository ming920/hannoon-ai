# 평가 리포트: holdout-emb-2

- **config_tag**: holdout topics-only, embedding subtopic tau 0.55 run 2
- **timestamp**: 2026-07-03T17:03:34Z

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
| ARI | 0.3064 |
| NMI | 0.8011 |
| V-measure | 0.8011 |
| Homogeneity | 0.7862 |
| Completeness | 0.8166 |
| B-cubed Precision | 0.6725 |
| B-cubed Recall | 0.7464 |
| B-cubed F1 | 0.7075 |

### 진단

- **num_pred_clusters**: 11
- **num_gold_clusters**: 11
- **singleton_rate**: 0.45454545454545453
- **over_split_count**: 3
- **over_merge_count**: 4
- **duplicate_pred_clusters**: 2

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.5815
- **B-cubed Recall**: 1.0000
- **B-cubed F1**: 0.7354
- **ARI**: 0.4442
- **num_pred_clusters**: 6
- **singleton_rate**: 0.0
- **over_split_count**: 0
- **over_merge_count**: 4

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

# 평가 리포트: iter1-c4c5-a

- **config_tag**: iter1: C-4 guardrail fix + C-5 subtopic separation (topics-only minnet3) run A
- **timestamp**: 2026-07-03T13:31:24Z

---

## 이벤트 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | 0.7582 |
| NMI | 0.9385 |
| V-measure | 0.9385 |
| Homogeneity | 1.0000 |
| Completeness | 0.8841 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 0.7706 |
| B-cubed F1 | 0.8704 |

### 진단

- **num_pred_clusters**: 44
- **num_gold_clusters**: 25
- **singleton_rate**: 0.3409090909090909
- **over_split_count**: 10
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 19

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 1.0000
- **B-cubed Recall**: 0.7706
- **B-cubed F1**: 0.8704
- **ARI**: 0.7582
- **num_pred_clusters**: 44
- **singleton_rate**: 0.3409090909090909
- **over_split_count**: 10
- **over_merge_count**: 0

---

## 서브토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.6136 |
| ARI | 0.1639 |
| NMI | 0.7196 |
| V-measure | 0.7196 |
| Homogeneity | 0.7587 |
| Completeness | 0.6843 |
| B-cubed Precision | 0.6364 |
| B-cubed Recall | 0.4968 |
| B-cubed F1 | 0.5580 |

### 진단

- **num_pred_clusters**: 23
- **num_gold_clusters**: 14
- **singleton_rate**: 0.782608695652174
- **over_split_count**: 10
- **over_merge_count**: 5
- **duplicate_pred_clusters**: 9

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.4074
- **B-cubed Recall**: 0.9259
- **B-cubed F1**: 0.5658
- **ARI**: 0.3204
- **num_pred_clusters**: 6
- **singleton_rate**: 0.16666666666666666
- **over_split_count**: 2
- **over_merge_count**: 5

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.6136 |
| ARI | 0.3237 |
| NMI | 0.6612 |
| V-measure | 0.6612 |
| Homogeneity | 0.9086 |
| Completeness | 0.5196 |
| B-cubed Precision | 0.9030 |
| B-cubed Recall | 0.3864 |
| B-cubed F1 | 0.5412 |

### 진단

- **num_pred_clusters**: 22
- **num_gold_clusters**: 5
- **singleton_rate**: 0.7727272727272727
- **over_split_count**: 4
- **over_merge_count**: 2
- **duplicate_pred_clusters**: 15

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.8420
- **B-cubed Recall**: 0.8420
- **B-cubed F1**: 0.8420
- **ARI**: 0.7588
- **num_pred_clusters**: 5
- **singleton_rate**: 0.0
- **over_split_count**: 2
- **over_merge_count**: 2

---

## 계층 정합성

- **hierarchy_consistency**: 0.5926

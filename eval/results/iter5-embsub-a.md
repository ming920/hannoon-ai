# 평가 리포트: iter5-embsub-a

- **config_tag**: iter5: embedding subtopic mode (greedy-max tau 0.55, LLM-free sub) run A
- **timestamp**: 2026-07-03T15:55:52Z

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
| ARI | 0.1974 |
| NMI | 0.8172 |
| V-measure | 0.8172 |
| Homogeneity | 0.9704 |
| Completeness | 0.7058 |
| B-cubed Precision | 0.9470 |
| B-cubed Recall | 0.4058 |
| B-cubed F1 | 0.5682 |

### 진단

- **num_pred_clusters**: 35
- **num_gold_clusters**: 14
- **singleton_rate**: 0.8
- **over_split_count**: 13
- **over_merge_count**: 2
- **duplicate_pred_clusters**: 19

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.9136
- **B-cubed Recall**: 0.7778
- **B-cubed F1**: 0.8402
- **ARI**: 0.6010
- **num_pred_clusters**: 18
- **singleton_rate**: 0.6111111111111112
- **over_split_count**: 5
- **over_merge_count**: 2

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

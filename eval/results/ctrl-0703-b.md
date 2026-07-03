# 평가 리포트: ctrl-0703-b

- **config_tag**: control: baseline config re-run for topic-om drift check, run B
- **timestamp**: 2026-07-03T15:23:27Z

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
| ARI | 0.1995 |
| NMI | 0.7433 |
| V-measure | 0.7433 |
| Homogeneity | 0.7837 |
| Completeness | 0.7068 |
| B-cubed Precision | 0.6530 |
| B-cubed Recall | 0.5346 |
| B-cubed F1 | 0.5879 |

### 진단

- **num_pred_clusters**: 23
- **num_gold_clusters**: 14
- **singleton_rate**: 0.782608695652174
- **over_split_count**: 9
- **over_merge_count**: 5
- **duplicate_pred_clusters**: 9

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.4346
- **B-cubed Recall**: 1.0000
- **B-cubed F1**: 0.6059
- **ARI**: 0.3808
- **num_pred_clusters**: 6
- **singleton_rate**: 0.16666666666666666
- **over_split_count**: 0
- **over_merge_count**: 5

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.6136 |
| ARI | 0.4069 |
| NMI | 0.7182 |
| V-measure | 0.7182 |
| Homogeneity | 1.0000 |
| Completeness | 0.5603 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 0.4319 |
| B-cubed F1 | 0.6032 |

### 진단

- **num_pred_clusters**: 23
- **num_gold_clusters**: 5
- **singleton_rate**: 0.782608695652174
- **over_split_count**: 4
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 18

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 1.0000
- **B-cubed Recall**: 0.9407
- **B-cubed F1**: 0.9695
- **ARI**: 0.9587
- **num_pred_clusters**: 6
- **singleton_rate**: 0.16666666666666666
- **over_split_count**: 1
- **over_merge_count**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 0.8148

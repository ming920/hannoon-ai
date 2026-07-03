# 평가 리포트: iter2-c5only-a

- **config_tag**: iter2: C-5 subtopic separation only, guardrail restored (topics-only minnet3) run A
- **timestamp**: 2026-07-03T13:39:22Z

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
| ARI | 0.2098 |
| NMI | 0.7624 |
| V-measure | 0.7624 |
| Homogeneity | 0.8239 |
| Completeness | 0.7094 |
| B-cubed Precision | 0.7235 |
| B-cubed Recall | 0.5119 |
| B-cubed F1 | 0.5996 |

### 진단

- **num_pred_clusters**: 25
- **num_gold_clusters**: 14
- **singleton_rate**: 0.76
- **over_split_count**: 10
- **over_merge_count**: 4
- **duplicate_pred_clusters**: 11

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.5494
- **B-cubed Recall**: 0.9630
- **B-cubed F1**: 0.6996
- **ARI**: 0.4217
- **num_pred_clusters**: 8
- **singleton_rate**: 0.25
- **over_split_count**: 1
- **over_merge_count**: 4

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.6136 |
| ARI | 0.3945 |
| NMI | 0.7150 |
| V-measure | 0.7150 |
| Homogeneity | 1.0000 |
| Completeness | 0.5565 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 0.4369 |
| B-cubed F1 | 0.6081 |

### 진단

- **num_pred_clusters**: 23
- **num_gold_clusters**: 5
- **singleton_rate**: 0.7391304347826086
- **over_split_count**: 4
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 18

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 1.0000
- **B-cubed Recall**: 0.9111
- **B-cubed F1**: 0.9535
- **ARI**: 0.9372
- **num_pred_clusters**: 6
- **singleton_rate**: 0.0
- **over_split_count**: 1
- **over_merge_count**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 0.8148

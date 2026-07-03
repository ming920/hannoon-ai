# 평가 리포트: ctrl-0703-a

- **config_tag**: control: baseline config re-run for topic-om drift check, run A
- **timestamp**: 2026-07-03T15:20:50Z

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
| ARI | 0.2167 |
| NMI | 0.7610 |
| V-measure | 0.7610 |
| Homogeneity | 0.8139 |
| Completeness | 0.7146 |
| B-cubed Precision | 0.7076 |
| B-cubed Recall | 0.5346 |
| B-cubed F1 | 0.6091 |

### 진단

- **num_pred_clusters**: 24
- **num_gold_clusters**: 14
- **singleton_rate**: 0.75
- **over_split_count**: 9
- **over_merge_count**: 4
- **duplicate_pred_clusters**: 11

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.5235
- **B-cubed Recall**: 1.0000
- **B-cubed F1**: 0.6872
- **ARI**: 0.4236
- **num_pred_clusters**: 7
- **singleton_rate**: 0.14285714285714285
- **over_split_count**: 0
- **over_merge_count**: 4

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.6136 |
| ARI | 0.3693 |
| NMI | 0.7059 |
| V-measure | 0.7059 |
| Homogeneity | 1.0000 |
| Completeness | 0.5455 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 0.4109 |
| B-cubed F1 | 0.5825 |

### 진단

- **num_pred_clusters**: 24
- **num_gold_clusters**: 5
- **singleton_rate**: 0.75
- **over_split_count**: 4
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 19

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 1.0000
- **B-cubed Recall**: 0.8519
- **B-cubed F1**: 0.9200
- **ARI**: 0.8924
- **num_pred_clusters**: 7
- **singleton_rate**: 0.14285714285714285
- **over_split_count**: 2
- **over_merge_count**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 0.6296

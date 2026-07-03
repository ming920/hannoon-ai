# 평가 리포트: iter2-c5only-b

- **config_tag**: iter2: C-5 subtopic separation only, guardrail restored (topics-only minnet3) run B
- **timestamp**: 2026-07-03T13:41:55Z

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
| ARI | 0.2038 |
| NMI | 0.7579 |
| V-measure | 0.7579 |
| Homogeneity | 0.8162 |
| Completeness | 0.7074 |
| B-cubed Precision | 0.7008 |
| B-cubed Recall | 0.5271 |
| B-cubed F1 | 0.6016 |

### 진단

- **num_pred_clusters**: 25
- **num_gold_clusters**: 14
- **singleton_rate**: 0.8
- **over_split_count**: 9
- **over_merge_count**: 5
- **duplicate_pred_clusters**: 11

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.5123
- **B-cubed Recall**: 0.9630
- **B-cubed F1**: 0.6688
- **ARI**: 0.4057
- **num_pred_clusters**: 8
- **singleton_rate**: 0.375
- **over_split_count**: 1
- **over_merge_count**: 5

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.6136 |
| ARI | 0.3566 |
| NMI | 0.7000 |
| V-measure | 0.7000 |
| Homogeneity | 1.0000 |
| Completeness | 0.5385 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 0.3997 |
| B-cubed F1 | 0.5711 |

### 진단

- **num_pred_clusters**: 25
- **num_gold_clusters**: 5
- **singleton_rate**: 0.8
- **over_split_count**: 4
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 20

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 1.0000
- **B-cubed Recall**: 0.8222
- **B-cubed F1**: 0.9024
- **ARI**: 0.8690
- **num_pred_clusters**: 8
- **singleton_rate**: 0.375
- **over_split_count**: 3
- **over_merge_count**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 0.4444

# 평가 리포트: iter5-embsub-b

- **config_tag**: iter5: embedding subtopic mode (greedy-max tau 0.55, LLM-free sub) run B
- **timestamp**: 2026-07-03T15:57:48Z

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
| ARI | 0.2159 |
| NMI | 0.8238 |
| V-measure | 0.8238 |
| Homogeneity | 0.9751 |
| Completeness | 0.7132 |
| B-cubed Precision | 0.9545 |
| B-cubed Recall | 0.4361 |
| B-cubed F1 | 0.5987 |

### 진단

- **num_pred_clusters**: 35
- **num_gold_clusters**: 14
- **singleton_rate**: 0.8285714285714286
- **over_split_count**: 12
- **over_merge_count**: 1
- **duplicate_pred_clusters**: 20

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 0.9259
- **B-cubed Recall**: 0.8148
- **B-cubed F1**: 0.8668
- **ARI**: 0.6281
- **num_pred_clusters**: 18
- **singleton_rate**: 0.6666666666666666
- **over_split_count**: 4
- **over_merge_count**: 1

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.6136 |
| ARI | 0.3820 |
| NMI | 0.7090 |
| V-measure | 0.7090 |
| Homogeneity | 1.0000 |
| Completeness | 0.5492 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 0.4179 |
| B-cubed F1 | 0.5895 |

### 진단

- **num_pred_clusters**: 24
- **num_gold_clusters**: 5
- **singleton_rate**: 0.7916666666666666
- **over_split_count**: 4
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 19

### covered-only (배정된 아이템만, 유령 싱글턴 제외)

- **B-cubed Precision**: 1.0000
- **B-cubed Recall**: 0.8815
- **B-cubed F1**: 0.9370
- **ARI**: 0.9151
- **num_pred_clusters**: 7
- **singleton_rate**: 0.2857142857142857
- **over_split_count**: 2
- **over_merge_count**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 0.6296

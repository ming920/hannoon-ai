# 평가 리포트: smoke-001

- **config_tag**: baseline-evt045-topic050
- **timestamp**: 2026-07-01T13:31:04Z

---

## 이벤트 레벨

| 지표 | 값 |
|---|---|
| coverage | 0.1087 |
| ARI | 0.1065 |
| NMI | 0.6790 |
| V-measure | 0.6790 |
| Homogeneity | 1.0000 |
| Completeness | 0.5140 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 0.2246 |
| B-cubed F1 | 0.3669 |

### 진단

- **num_pred_clusters**: 42
- **num_gold_clusters**: 7
- **singleton_rate**: 0.9761904761904762
- **over_split_count**: 7
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 35

---

## 서브토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | 1.0000 |
| NMI | 1.0000 |
| V-measure | 1.0000 |
| Homogeneity | 1.0000 |
| Completeness | 1.0000 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 1.0000 |
| B-cubed F1 | 1.0000 |

### 진단

- **num_pred_clusters**: 1
- **num_gold_clusters**: 1
- **singleton_rate**: 1.0
- **over_split_count**: 0
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 0

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | 1.0000 |
| NMI | 1.0000 |
| V-measure | 1.0000 |
| Homogeneity | 1.0000 |
| Completeness | 1.0000 |
| B-cubed Precision | 1.0000 |
| B-cubed Recall | 1.0000 |
| B-cubed F1 | 1.0000 |

### 진단

- **num_pred_clusters**: 1
- **num_gold_clusters**: 1
- **singleton_rate**: 1.0
- **over_split_count**: 0
- **over_merge_count**: 0
- **duplicate_pred_clusters**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 1.0000

---

## 경고

- [경고] ★★★ 이벤트 레벨 coverage=0.1087 < 1.0 ★★★ — 일부 기사가 DB에서 이벤트에 배정되지 않았습니다. 배치가 완전히 완료됐는지 확인하세요. 지표 해석에 주의가 필요합니다.
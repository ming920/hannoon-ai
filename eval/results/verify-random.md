# 평가 리포트: verify-random

- **config_tag**: verify
- **timestamp**: 2026-07-01T12:32:30Z

---

## 이벤트 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | 0.2533 |
| NMI | 0.7778 |
| V-measure | 0.7778 |
| Homogeneity | 0.7778 |
| Completeness | 0.7778 |
| B-cubed Precision | 0.7500 |
| B-cubed Recall | 0.7500 |
| B-cubed F1 | 0.7500 |

### 진단

- **num_pred_clusters**: 5
- **num_gold_clusters**: 5
- **singleton_rate**: 0.4
- **over_split_count**: 2
- **over_merge_count**: 2
- **duplicate_pred_clusters**: 1

---

## 서브토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | -0.1538 |
| NMI | 0.6516 |
| V-measure | 0.6516 |
| Homogeneity | 0.7372 |
| Completeness | 0.5838 |
| B-cubed Precision | 0.8000 |
| B-cubed Recall | 0.6000 |
| B-cubed F1 | 0.6857 |

### 진단

- **num_pred_clusters**: 4
- **num_gold_clusters**: 3
- **singleton_rate**: 0.75
- **over_split_count**: 2
- **over_merge_count**: 1
- **duplicate_pred_clusters**: 1

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | -0.2500 |
| NMI | 0.0206 |
| V-measure | 0.0206 |
| Homogeneity | 0.0206 |
| Completeness | 0.0206 |
| B-cubed Precision | 0.5333 |
| B-cubed Recall | 0.5333 |
| B-cubed F1 | 0.5333 |

### 진단

- **num_pred_clusters**: 2
- **num_gold_clusters**: 2
- **singleton_rate**: 0.0
- **over_split_count**: 2
- **over_merge_count**: 2
- **duplicate_pred_clusters**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 0.0000

---

## 경고

- 이벤트 3의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S3', 'S1'} (대표값: 'S1', 다수결/first-wins)
- 이벤트 0의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S3', 'S4'} (대표값: 'S3', 다수결/first-wins)
- 이벤트 3의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T2', 'T1'} (대표값: 'T1', 다수결/first-wins)
- 이벤트 0의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T3', 'T2'} (대표값: 'T2', 다수결/first-wins)
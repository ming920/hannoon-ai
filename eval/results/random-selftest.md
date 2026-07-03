# 평가 리포트: random-selftest

- **config_tag**: random
- **timestamp**: 2026-07-03T11:47:53Z

---

## 이벤트 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | 0.0092 |
| NMI | 0.4265 |
| V-measure | 0.4265 |
| Homogeneity | 0.4265 |
| Completeness | 0.4265 |
| B-cubed Precision | 0.1800 |
| B-cubed Recall | 0.1872 |
| B-cubed F1 | 0.1835 |

### 진단

- **num_pred_clusters**: 25
- **num_gold_clusters**: 25
- **singleton_rate**: 0.0
- **over_split_count**: 25
- **over_merge_count**: 25
- **duplicate_pred_clusters**: 0

---

## 서브토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | 0.0334 |
| NMI | 0.6786 |
| V-measure | 0.6786 |
| Homogeneity | 0.7077 |
| Completeness | 0.6518 |
| B-cubed Precision | 0.5733 |
| B-cubed Recall | 0.5000 |
| B-cubed F1 | 0.5342 |

### 진단

- **num_pred_clusters**: 13
- **num_gold_clusters**: 11
- **singleton_rate**: 0.46153846153846156
- **over_split_count**: 6
- **over_merge_count**: 7
- **duplicate_pred_clusters**: 1

---

## 토픽 레벨

| 지표 | 값 |
|---|---|
| coverage | 1.0000 |
| ARI | -0.0208 |
| NMI | 0.2431 |
| V-measure | 0.2431 |
| Homogeneity | 0.2431 |
| Completeness | 0.2431 |
| B-cubed Precision | 0.3593 |
| B-cubed Recall | 0.3447 |
| B-cubed F1 | 0.3518 |

### 진단

- **num_pred_clusters**: 5
- **num_gold_clusters**: 5
- **singleton_rate**: 0.0
- **over_split_count**: 5
- **over_merge_count**: 5
- **duplicate_pred_clusters**: 0

---

## 계층 정합성

- **hierarchy_consistency**: 0.0000

---

## 경고

- 이벤트 23의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S011', 'S012', 'S002', 'S001', 'S006'} (대표값: 'S012', 다수결/first-wins)
- 이벤트 4의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S010', 'S012', 'S001', 'S014', 'S005', 'S009', 'S006', 'S003'} (대표값: 'S001', 다수결/first-wins)
- 이벤트 2의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S011', 'S010', 'S001', 'S005', 'S003'} (대표값: 'S001', 다수결/first-wins)
- 이벤트 6의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S001', 'S014', 'S013', 'S006', 'S003'} (대표값: 'S001', 다수결/first-wins)
- 이벤트 16의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S010', 'S002', 'S001', 'S003'} (대표값: 'S003', 다수결/first-wins)
- 이벤트 3의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S007', 'S006', 'S010', 'S001'} (대표값: 'S010', 다수결/first-wins)
- 이벤트 9의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S011', 'S007', 'S002', 'S001', 'S014', 'S003'} (대표값: 'S001', 다수결/first-wins)
- 이벤트 8의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S011', 'S010', 'S008', 'S002'} (대표값: 'S004', 다수결/first-wins)
- 이벤트 20의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S007', 'S012', 'S008', 'S002', 'S013', 'S005'} (대표값: 'S012', 다수결/first-wins)
- 이벤트 0의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S012', 'S002', 'S006', 'S003'} (대표값: 'S004', 다수결/first-wins)
- 이벤트 15의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S011', 'S002', 'S005', 'S006', 'S003'} (대표값: 'S002', 다수결/first-wins)
- 이벤트 22의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S011', 'S007', 'S008', 'S002', 'S014', 'S005', 'S006'} (대표값: 'S011', 다수결/first-wins)
- 이벤트 11의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S011', 'S007', 'S010', 'S008', 'S002', 'S005'} (대표값: 'S011', 다수결/first-wins)
- 이벤트 24의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S007', 'S010', 'S002', 'S013', 'S005'} (대표값: 'S007', 다수결/first-wins)
- 이벤트 7의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S011', 'S014', 'S013', 'S005', 'S006'} (대표값: 'S013', 다수결/first-wins)
- 이벤트 19의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S005', 'S013', 'S004', 'S003'} (대표값: 'S005', 다수결/first-wins)
- 이벤트 10의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S011', 'S007', 'S010', 'S012', 'S008', 'S005', 'S009', 'S006', 'S003'} (대표값: 'S003', 다수결/first-wins)
- 이벤트 13의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S012', 'S013', 'S005', 'S009', 'S006'} (대표값: 'S006', 다수결/first-wins)
- 이벤트 12의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S011', 'S007', 'S010', 'S003'} (대표값: 'S003', 다수결/first-wins)
- 이벤트 21의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S013', 'S006', 'S003'} (대표값: 'S004', 다수결/first-wins)
- 이벤트 18의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S007', 'S010', 'S013', 'S003'} (대표값: 'S007', 다수결/first-wins)
- 이벤트 17의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S013', 'S009', 'S006', 'S003'} (대표값: 'S003', 다수결/first-wins)
- 이벤트 14의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S007', 'S010', 'S012', 'S006'} (대표값: 'S007', 다수결/first-wins)
- 이벤트 5의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S004', 'S012', 'S014', 'S013', 'S009'} (대표값: 'S004', 다수결/first-wins)
- 이벤트 1의 기사들이 서로 다른 gold_subtopic 라벨을 가짐: {'S007', 'S013', 'S014', 'S010'} (대표값: 'S013', 다수결/first-wins)
- 이벤트 23의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T004', 'T005'} (대표값: 'T001', 다수결/first-wins)
- 이벤트 4의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T001', 'T004', 'T005'} (대표값: 'T004', 다수결/first-wins)
- 이벤트 2의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T001', 'T002', 'T004'} (대표값: 'T001', 다수결/first-wins)
- 이벤트 6의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T002', 'T005'} (대표값: 'T001', 다수결/first-wins)
- 이벤트 16의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T001', 'T002', 'T004'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 3의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T004'} (대표값: 'T003', 다수결/first-wins)
- 이벤트 9의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T001', 'T004', 'T005'} (대표값: 'T001', 다수결/first-wins)
- 이벤트 8의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T002', 'T004'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 20의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T005'} (대표값: 'T005', 다수결/first-wins)
- 이벤트 0의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T002', 'T005'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 15의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T002', 'T004'} (대표값: 'T001', 다수결/first-wins)
- 이벤트 22의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T004', 'T005'} (대표값: 'T003', 다수결/first-wins)
- 이벤트 11의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T002', 'T004'} (대표값: 'T004', 다수결/first-wins)
- 이벤트 24의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T001', 'T004', 'T005'} (대표값: 'T001', 다수결/first-wins)
- 이벤트 7의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T004', 'T005'} (대표값: 'T005', 다수결/first-wins)
- 이벤트 19의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T001', 'T002', 'T005'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 10의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T001', 'T004', 'T005'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 13의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T001', 'T004', 'T005'} (대표값: 'T003', 다수결/first-wins)
- 이벤트 12의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T004'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 21의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T005'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 18의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T004', 'T005'} (대표값: 'T003', 다수결/first-wins)
- 이벤트 17의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T004', 'T005'} (대표값: 'T002', 다수결/first-wins)
- 이벤트 14의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T002', 'T004', 'T005'} (대표값: 'T003', 다수결/first-wins)
- 이벤트 5의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T002', 'T004', 'T005'} (대표값: 'T005', 다수결/first-wins)
- 이벤트 1의 기사들이 서로 다른 gold_topic 라벨을 가짐: {'T003', 'T004', 'T005'} (대표값: 'T005', 다수결/first-wins)
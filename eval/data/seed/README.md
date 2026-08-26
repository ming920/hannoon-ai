# 시드 데이터

로컬에서 분류 실험을 돌리기 위한 스키마와 결과 데이터.

| 파일 | 내용 |
|------|------|
| `schema_min.sql` | 파이프라인이 실제로 쓰는 테이블만 추린 최소 스키마 |
| `subtopics_t5c_2026-08-25.sql` | **서브토픽 v2 결과** (토픽 5 / 서브토픽 20 / 귀속 68) |

## 서브토픽 v2 결과 (`subtopics_t5c_2026-08-25.sql`)

`eval/results/` 는 gitignore 라 실행 결과가 저장소로 나가지 않는다. 서브토픽이 실제로 어떻게
묶였는지 확인하려면 이 파일을 로컬 DB 에 넣는 게 가장 빠르다.

| 테이블 | 행 수 |
|---|---|
| `topics` | 5 (실험용 재구성 토픽, id 90001~) |
| `subtopics` | 20 |
| `subtopic_events` | 68 (이벤트당 1.26 — 한 이벤트가 여러 서브토픽에 들어간다) |

파일 하나에 다 들어 있다.

- `subtopics` / `subtopic_events` DDL — **아직 마이그레이션에 없는 제안 스키마다**
  (`docs/subtopic_v2_schema_proposal.md`). 이 파일이 `CREATE TABLE IF NOT EXISTS` 로 만든다
- 재구성 토픽과 `events.topic_id` 연결
- 헤더 주석에 프롬프트를 어떻게 짰는지 요약
- 확인 쿼리 — 다중 귀속, 단독 이벤트, 서브토픽 커버리지 비율
- 되돌리기 SQL

### ⚠️ 기사·이벤트 시드가 먼저 필요하다

이 파일의 귀속 68쌍은 실제 이벤트 id(1325~1546)를 참조한다. `events` 행이 없으면 FK 로 실패한다.
**기사/이벤트 시드와 적용 스크립트는 이 브랜치에 없다** — `feat/constraint-eval-harness`
(PR #7)에 있고, 프로덕션 코드 변경과 함께 묶여 있어 이 브랜치에서 분리했다.

| 필요한 것 | 어디에 |
|---|---|
| `articles_2026-07-20.sql` (기사 2,578건) | PR #7 |
| `events_2026-07-27.sql` (이벤트 222건) | PR #7 |
| `scripts/apply_local_seed.py` | PR #7 |

그쪽이 develop 에 들어온 뒤 아래 순서로 적용한다.

```bash
python scripts/apply_local_seed.py --replace                                          # 1) 기사
python scripts/apply_local_seed.py --file eval/data/seed/events_2026-07-27.sql         # 2) 이벤트
python scripts/apply_local_seed.py --file eval/data/seed/subtopics_t5c_2026-08-25.sql  # 3) 서브토픽
```

DB 없이 결과만 보고 싶다면 SQL 파일을 그대로 열어도 된다 — 서브토픽 이름과 귀속이
토픽별로 주석과 함께 정리돼 있다.

### 알아둘 것

- **토픽 5개는 프로덕션 토픽이 아니다.** 서브토픽 로직만 따로 보려고 이벤트를 다시 묶은
  것이다 — 토픽이 잡탕이면 서브토픽도 잡탕이 되어 원인을 격리할 수 없다.
- `events.topic_id` 를 이 토픽들로 UPDATE 한다. 시드의 `topic_id` 는 전부 NULL 이라
  덮어쓸 것이 없다.
- id 를 명시 지정하므로 파일 끝에서 `setval` 로 시퀀스를 끌어올린다. 이 줄이 빠지면 이후
  자동 INSERT 가 PK 충돌한다.

## 더 볼 것

| 문서 | 내용 |
|---|---|
| `docs/subtopic_prompt_guide.md` | 프롬프트를 어떻게 짰는지 — 골격, 규칙 10개, 체크리스트 |
| `docs/subtopic_design_v2.md` | 설계 정의와 실험 이력 |
| `docs/subtopic_v2_schema_proposal.md` | DB 스키마 제안 (미적용) |

이 결과의 전수 감사 적합률은 **69.1%**(47/68쌍)다.

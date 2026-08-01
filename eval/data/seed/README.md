# 원천 기사 시드 데이터

2026-07-20 로컬 DB 기준 `articles` INSERT 문. `embedding` 컬럼은 제외했다(파이프라인에서 재생성).
`../constraints/`의 검수 정답이 전제하는 기사 코퍼스를 재현하는 용도다.

| 파일 | 건수 | 크기 | 설명 |
|------|------|------|------|
| `articles_2026-07-20.sql` | 2,578 | 11MB | **전체.** 정답 검증에는 이 파일을 쓴다 |
| `events_2026-07-27.sql` | 222 / 722 / 722 | 1.5MB | **이벤트 데이터.** 토픽 분류 담당자용 — 아래 참고 |
| `articles_test_target_2026-07-20.sql` | 313 | 1.4MB | 부분집합. 아래 경고 참고 |
| `test_target_article_ids_2026-07-20.txt` | 313 | 1.8KB | 위 부분집합의 기사 id 목록 |

## ⚠️ 부분집합(313건)만으로는 정답을 검증할 수 없다

`articles_test_target_2026-07-20.sql`은 "라벨링된 100개 이벤트에 속한 기사"로 추출됐지만,
**검수 정답의 제약이 실제로 참조하는 기사는 764개**이고 그중 **474개(62%)가 이 313건 목록에
없다**. 이 파일만 넣고 `constraint_checks.py`를 돌리면 제약의 대부분이 "불가"로 빠져 충족률이
크게 왜곡된다.

| 용도 | 써야 할 파일 |
|------|-------------|
| 정답 대비 제약 검증 / 기준선 측정 | `articles_2026-07-20.sql` (전체 2,578건) |
| 파이프라인 스모크 테스트 (빠른 확인) | `articles_test_target_2026-07-20.sql` (313건) |

## 전제 조건

- 로컬 supabase가 떠 있고 마이그레이션이 최신까지 적용돼 있어야 한다: `supabase db reset`
- `public.articles` 테이블이 **비어 있어야** 한다. id를 원본 그대로 넣으므로
  (`OVERRIDING SYSTEM VALUE`), 기존 행이 있으면 PK 충돌로 실패한다.

## 이벤트 시드 (`events_2026-07-27.sql`) — 토픽 분류 담당자용

토픽 분류의 입력은 **기사가 아니라 이벤트**다. 기사만 넣으면 `classify_topics.py` 가 볼 대상이
없고, 기사에서 이벤트를 만들려면 요약(`main.py process`) + 이벤트 분류를 다 돌려야 한다
(로컬 30B 기준 며칠). 그래서 2026-07-27 로컬 실행(`local-e002`) 결과를 그대로 넘긴다.

| 테이블 | 행 수 |
|---|---|
| `events` | 222 |
| `event_articles` | 722 |
| `article_ai_results` | 722 (모두 `status='event_assigned'`) |

넘기면서 비운 것:

- **`embedding` 제외** — 받는 쪽 임베딩 모델로 재생성한다. 이 실행은 bge-m3 를 4096 으로 패딩한
  벡터라 Upstage 벡터와 좌표계가 다르다. 섞이면 거리 비교가 무의미해진다.
- **`topic_id` / `prev_event_id` / `next_event_id` 를 NULL 로** — 받는 쪽이 토픽 분류를 처음부터
  하는 게 목적이라, 이미 배정된 상태(38건)를 넘기면 실험이 오염된다.
- `topics` / `topic_causes` 는 아예 포함하지 않았다(같은 이유).

적용 순서가 중요하다. 기사가 먼저 있어야 FK 가 성립한다.

```bash
python scripts/apply_local_seed.py --replace                        # 1) 기사 2,578건
python scripts/apply_local_seed.py --file eval/data/seed/events_2026-07-27.sql   # 2) 이벤트
```

넣고 나면 기사 5건 이상인 이벤트 **38건**이 토픽 분류 대상이 된다.

## 적용

Supabase Dashboard의 SQL Editor에 파일 내용을 그대로 붙여넣고 실행한다.
로컬이라면 psql 없이 `python scripts/apply_local_seed.py` 를 쓰는 편이 편하다.

**두 SQL을 같이 적용하면 안 된다** — 부분집합이 전체의 하위집합이라 겹치는 id에서 PK 충돌이 난다.
둘 중 하나만 고를 것.

## 시퀀스(id) 처리

- id를 원본 그대로 넣기 위해 `OVERRIDING SYSTEM VALUE`를 사용했다. `articles.id`는
  `GENERATED ALWAYS AS IDENTITY`라 기본적으로는 직접 값을 넣을 수 없는데, 이 옵션으로 우회한다.
- 이렇게 직접 값을 넣으면 identity 시퀀스(`articles_id_seq`)는 갱신되지 않는다. 그대로 두면 이후 새
  기사 INSERT 시 시퀀스가 이미 쓰인 id(1~4761)를 다시 내려줘서 PK 충돌이 난다.
- 그래서 파일 마지막 줄에 `setval(pg_get_serial_sequence('public.articles','id'), max(id))`를 넣어
  시퀀스를 4761로 맞춰준다. **이 줄이 빠지면 반드시 직접 실행해야 한다.**

## eval 하네스와의 공존

`../../reset_test_db.py`는 `articles`에서 `guid LIKE 'dummy-%'`인 행만 삭제한다(실 데이터 보호).
따라서 여기서 넣은 실제 기사는 eval 반복 실행(`run_iteration.py`)으로 지워지지 않는다.
원격 DB에 대해서는 `--allow-remote` 없이는 실행이 거부된다.

# 원천 기사 시드 데이터

2026-07-20 로컬 DB 기준 `articles` INSERT 문. `embedding` 컬럼은 제외했다(파이프라인에서 재생성).
`../constraints/`의 검수 정답이 전제하는 기사 코퍼스를 재현하는 용도다.

| 파일 | 건수 | 크기 | 설명 |
|------|------|------|------|
| `articles_2026-07-20.sql` | 2,578 | 11MB | **전체.** 정답 검증에는 이 파일을 쓴다 |
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

## 적용

Supabase Dashboard의 SQL Editor에 파일 내용을 그대로 붙여넣고 실행한다.

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

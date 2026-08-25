# 서브토픽 v2 스키마 제안 (미적용)

**아직 적용하지 않았다.** 프로덕션 DB 변경은 건별 승인이 필요하므로 설계안까지만 쓴다.
근거가 되는 실험은 `docs/subtopic_design_v2.md` 를 볼 것.

## 왜 v1 인프라를 그대로 못 쓰는가

v1(`20260701120000_add_topics_parent_topic_id.sql`)은 `topics.parent_topic_id` 로 2단 계층을
만들고 **`events.topic_id` 가 leaf(서브토픽)만 가리키게** 한다. 이 구조의 전제는 한 이벤트가
정확히 하나의 서브토픽에 속한다는 것이다.

v2 는 **하나의 이벤트가 여러 서브토픽에 동시에 속한다.** 실측으로도 이벤트당 평균 1.4개,
전체의 42.6% 가 둘 이상에 속한다. 단일 FK 로는 표현할 수 없다.

또 v1 은 서브토픽을 토픽과 같은 테이블에 담는데, v2 의 서브토픽은 토픽과 성격이 다르다 —
제목·요약·임베딩·prev/next 체인을 갖는 독립 개체가 아니라, **한 토픽 안에서만 의미를 갖는
탐색용 묶음**이다. 토픽 테이블에 섞으면 `parent_topic_id IS NULL` 조건이 모든 토픽 쿼리에
따라붙고, 토픽에만 있어야 할 컬럼이 서브토픽 행에서는 늘 비어 있게 된다.

## 제안

```sql
-- 서브토픽: 한 토픽에 속하는 탐색 관점
CREATE TABLE subtopics (
  id           bigserial PRIMARY KEY,
  topic_id     bigint NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  name         text   NOT NULL,
  perspective  text,              -- '대상' | '역할' | '토픽고유' (분류에 쓰지 않는 관찰용 라벨)
  reason       text,              -- 생성 근거. 재생성 시 이어받기 컨텍스트로 넘긴다
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (topic_id, name)         -- 같은 토픽 안에서 이름은 유일. 이어받기가 이름으로 매칭한다
);
CREATE INDEX subtopics_topic_id_idx ON subtopics (topic_id);

-- 이벤트 ↔ 서브토픽 다대다
CREATE TABLE subtopic_events (
  subtopic_id  bigint NOT NULL REFERENCES subtopics(id) ON DELETE CASCADE,
  event_id     bigint NOT NULL REFERENCES events(id)    ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (subtopic_id, event_id)
);
CREATE INDEX subtopic_events_event_id_idx ON subtopic_events (event_id);
```

### 설계 근거

- **`events.topic_id` 는 그대로 둔다.** 이벤트는 여전히 토픽 하나에 속하고, 서브토픽 귀속은
  그 위에 얹히는 별도 축이다. 기존 평면 동작이 깨지지 않으므로 롤백이 쉽다.
- **`UNIQUE (topic_id, name)`** — 재생성 이어받기가 이름으로 기존 관점을 매칭하므로 같은 토픽
  안에서 이름이 겹치면 안 된다. 실험에서도 이름 중복을 검증 단계에서 버리고 있다.
- **단독 이벤트는 테이블을 만들지 않는다.** `subtopic_events` 에 한 줄도 없는 이벤트가 곧 단독
  이벤트다. 별도 플래그를 두면 귀속이 생겼을 때 두 곳을 고쳐야 하고 어긋날 수 있다.
- **`perspective` 는 분류에 쓰지 않는다.** 실측에서 라벨이 "역할" 로 편중되고 온도에 따라 흔들려
  신뢰할 수 없다. 관찰용으로만 저장한다.
- **서브토픽에 임베딩을 두지 않는다.** v2 의 귀속은 이벤트를 하나씩 LLM 에 묻는 방식이고
  pgvector 검색을 쓰지 않는다. 필요해지면 그때 추가한다.

### 재생성을 어떻게 반영할 것인가

이어받기(`--carry-over`)로 이름이 유지되므로, 재생성 결과를 쓸 때 **테이블을 비우고 다시 넣으면
안 된다.** 그러면 id 가 바뀌어 이름을 유지한 의미가 사라진다(북마크·딥링크·클라이언트 캐시가 끊긴다).

```
1. 토픽의 기존 subtopics 를 name 으로 인덱싱한다.
2. 재생성 결과의 각 관점을 name 으로 매칭한다.
   - 있으면 → 그 행을 재사용(id 유지), reason/updated_at 만 갱신
   - 없으면 → INSERT
3. 재생성 결과에 없는 기존 서브토픽 → DELETE (CASCADE 로 귀속도 정리된다)
4. subtopic_events 는 해당 서브토픽 기준으로 교체한다.
```

전부 한 트랜잭션 안에서 처리한다(`with conn.transaction():`, 중첩 금지).

### 조회

`get_topic_timeline` RPC(v1, `20260702120000`)는 leaf 토픽 전제라 그대로 못 쓴다. v2 용으로
토픽 하나의 서브토픽 목록과 각 서브토픽의 이벤트를 함께 주는 RPC 가 따로 필요하다.
단독 이벤트는 `NOT EXISTS (SELECT 1 FROM subtopic_events WHERE event_id = e.id)` 로 뽑는다.

## 적용 순서 (승인 후)

1. `hannoon-supabase` 에 마이그레이션 추가 → 로컬 `supabase db reset` 으로 검증
2. `src/db/subtopics.py` 신규 — 위 upsert 절차를 담는다
3. 파이프라인 연결은 **토픽 분류가 안정된 뒤**. 토픽 품질이 서브토픽 품질의 상한이라
   (`subtopic_design_v2.md` 확인사항 1) 순서를 바꾸면 원인 격리가 안 된다
4. v1 인프라(`topics.parent_topic_id`, `get_topic_timeline`)는 **지우지 않는다** — v2 가
   프로덕션에서 검증될 때까지 되돌릴 자리로 남겨둔다

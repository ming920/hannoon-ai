# 토픽 분류 개요

토픽 분류는 아직 토픽이 없는 이벤트를 기존 토픽에 배정하거나 새 토픽으로 생성하는
배치 파이프라인입니다. 후보 검색은 Supabase pgvector와 Upstage 임베딩을 사용하고,
최종 배정 판단은 설정된 LLM이 수행합니다.

저장되는 `topic_causes.cause_embedding` 벡터는
`solar-embedding-1-large-passage`로 만들고, 후보 검색에 사용하는 질의 벡터는
`solar-embedding-1-large-query`로 만듭니다. 두 모델 모두 4096차원이므로
`topic_causes.cause_embedding` 컬럼은 `vector(4096)`이어야 합니다.

## 처리 흐름

1. `events`에서 `topic_id`가 없는 이벤트를 가져옵니다.
2. LLM으로 이벤트의 핵심 `cause`와 `result`를 추출합니다.
3. `cause`를 Upstage `solar-embedding-1-large-query`로 임베딩합니다.
4. `topic_causes.cause_embedding`에서 pgvector 유사도 검색으로 후보 토픽을 찾습니다.
5. 후보가 있으면 LLM이 기존 토픽 배정(`assign`) 또는 새 토픽 생성(`create`)을 결정합니다.
6. 후보가 없으면 불필요한 LLM 판단 호출 없이 새 토픽을 생성합니다.
7. 토픽 매핑, 토픽 제목/요약 갱신, cause/result 누적, 이벤트 체인 연결을 하나의 트랜잭션으로 저장합니다.

## 권장 설정

```env
LLM_TOPIC_MODEL=solar-pro3
EMBEDDING_MODEL=solar-embedding-1-large-passage
EMBEDDING_PASSAGE_MODEL=solar-embedding-1-large-passage
EMBEDDING_QUERY_MODEL=solar-embedding-1-large-query
EMBEDDING_DIMENSIONS=4096
TOPIC_BATCH_SIZE=10
TOPIC_CANDIDATE_LIMIT=12
TOPIC_MIN_NET_ARTICLE_COUNT=5
```

토픽 배정은 잘못 묶이면 장기 데이터 구조가 꼬이므로 `solar-pro3`를 권장합니다.
반면 기사 본문 정제와 요약처럼 호출량이 많은 작업은 `solar-mini`를
사용해 비용을 줄이는 구성이 좋습니다.

## 실행

```powershell
python classify_topics.py --database-url "postgresql://..."
```

`.env`에 `DATABASE_URL`이 설정되어 있으면 아래처럼 실행할 수 있습니다.

```powershell
python classify_topics.py
```

## 서브토픽(계층) 분류

`--subtopics`(또는 `TOPIC_SUBTOPICS_ENABLED=true`)로 토픽 아래 서브토픽 2단계 계층
분류를 켤 수 있습니다. 켜면 처리 흐름이 다음과 같이 확장됩니다.

1. 부모(최상위) 토픽을 `parent_topic_id IS NULL` 후보로만 좁혀 assign-or-create 합니다.
2. 부모가 기존 토픽이면 그 부모 스코프 안에서 서브토픽 후보를 검색해 assign-or-create
   합니다. 부모를 새로 만들면 그 아래 서브토픽이 없으므로 LLM 호출 없이 서브토픽을
   바로 생성합니다(후보 0개 → create 패턴 재사용).
3. `events.topic_id`는 항상 leaf인 서브토픽만 가리키고, prev/next 체인도 서브토픽
   단위로 연결됩니다. 부모는 `topics.parent_topic_id` 자기참조로 재귀 조회합니다.

관련 설정:

```env
TOPIC_SUBTOPICS_ENABLED=true
TOPIC_SUBTOPIC_CANDIDATE_LIMIT=12
```

서브토픽 기능은 `topics.parent_topic_id` 컬럼이 필요하므로
`migrations/0001_topics_parent_topic_id.sql`을 운영 DB에 먼저 적용해야 합니다.

## 주의사항

- 토픽 분류는 pgvector 검색이 필요하므로 Supabase/Postgres 연결이 필수입니다.
- `topic_causes` 테이블과 `cause_embedding vector(4096)` 컬럼이 미리 준비되어 있어야 합니다.
- 기존 768차원 임베딩과 4096차원 Upstage 임베딩은 서로 비교할 수 없으므로, 모델을 바꾼 뒤에는 저장 벡터를 다시 생성해야 합니다.

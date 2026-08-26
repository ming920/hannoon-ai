# Hannoon AI

RSS 뉴스 기사를 수집하고, 원문을 크롤링한 뒤 Upstage LLM과 임베딩으로 기사 분석,
이벤트 묶기, 토픽 묶기를 수행하는 파이프라인입니다.

현재 기본 전략은 비용과 품질을 나눠 쓰는 방식입니다.

- 기사 본문 정제, 요약, 어뷰징 판단: `solar-mini`
- 이벤트/토픽 배정처럼 장기 데이터 구조에 영향을 주는 판단: `solar-pro3`
- 후보 검색용 임베딩: Upstage `solar-embedding-1-large-query`
- DB 저장용 임베딩: Upstage `solar-embedding-1-large-passage`

## 전체 흐름

```text
RSS 수집
-> 기사 원문 크롤링
-> 필요한 경우에만 LLM 본문 정제
-> LLM 요약 + 어뷰징 판단
-> 기사 앞부분 query 임베딩
-> Supabase pgvector로 기존 이벤트 후보 검색
-> solar-pro3로 기존 이벤트 배정 또는 새 이벤트 생성
-> 이벤트 cause/result 추출
-> cause query 임베딩으로 기존 토픽 후보 검색
-> solar-pro3로 기존 토픽 배정 또는 새 토픽 생성
-> passage 임베딩 저장
```

후보 검색 결과가 하나도 없으면 `solar-pro3` 배정 판단 호출을 생략하고 바로 새
이벤트/토픽을 만듭니다. 후보가 있을 때만 고급 모델로 판단합니다.

## 설치

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
```

`.env`에 최소한 아래 값을 설정합니다.

```env
UPSTAGE_API_KEY=your_upstage_api_key
LLM_BASE_URL=https://api.upstage.ai/v1
DATABASE_URL=your_postgres_database_url
```

## 로컬 개발 환경

이벤트/토픽 분류까지 로컬에서 돌리려면 **pgvector 가 있는 Postgres 가 필요합니다.**
SQLite 는 기사 수집·크롤링·요약까지만 됩니다(`events`/`topics` 테이블을 만들지 않습니다).

스키마는 이 저장소가 아니라 형제 저장소 **`hannoon-supabase`** 가 소유합니다. 로컬 Supabase 기동,
기사 시드 투입, Upstage / 로컬 Ollama 두 경로의 `.env` 설정까지 전체 절차는
**[docs/local-setup.md](docs/local-setup.md)** 에 있습니다.

## 권장 모델 설정

```env
LLM_DEFAULT_MODEL=solar-mini
LLM_CLEANUP_MODEL=solar-mini
LLM_ARTICLE_MODEL=solar-mini
LLM_SUMMARY_MODEL=solar-mini

LLM_TOPIC_EVENT_MODEL=solar-pro3
LLM_EVENT_MODEL=solar-pro3
LLM_TOPIC_MODEL=solar-pro3

EMBEDDING_MODEL=solar-embedding-1-large-passage
EMBEDDING_PASSAGE_MODEL=solar-embedding-1-large-passage
EMBEDDING_QUERY_MODEL=solar-embedding-1-large-query
EMBEDDING_DIMENSIONS=4096
```

본문 전체를 자주 넣는 작업은 `solar-mini`로 두고, 이벤트/토픽 배정처럼 잘못되면
DB 구조가 오래 꼬이는 판단만 `solar-pro3`를 사용합니다.

## 실행

전체 기사 수집/크롤링/분석:

```powershell
python main.py run
```

단계별 실행:

```powershell
python main.py fetch
python main.py crawl
python main.py process
```

이벤트 분류:

```powershell
python classify_events.py --database-url "postgresql://..."
```

토픽 분류:

```powershell
python classify_topics.py --database-url "postgresql://..."
```

주요 옵션:

- `--no-llm-cleanup`: 크롤링 본문 LLM 정제를 끕니다.
- `--llm-cleanup-model`: 본문 정제 모델을 지정합니다.
- `--llm-summary-model`: 요약 모델을 지정합니다.
- `--ai-batch-size`: 한 번에 LLM 분석할 기사 수입니다.
- `--analysis-max-attempts`: 기사 분석 실패 확정 전 재시도 횟수입니다.
- `--batch-size`: 이벤트/토픽 분류 배치 크기입니다.
- `--top-k`: LLM에 보여줄 후보 수입니다.

## 데이터베이스 주의사항

로컬 SQLite는 RSS 수집, 크롤링, 기사 분석 개발용입니다. 이벤트/토픽 분류는
Supabase/Postgres와 pgvector가 필요합니다.

Upstage 대형 임베딩은 4096차원입니다. 임베딩을 저장하는 pgvector 컬럼은
`vector(4096)`이어야 합니다.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE articles DROP COLUMN IF EXISTS embedding;
ALTER TABLE articles ADD COLUMN embedding vector(4096);

ALTER TABLE events DROP COLUMN IF EXISTS embedding;
ALTER TABLE events ADD COLUMN embedding vector(4096);

ALTER TABLE topic_causes DROP COLUMN IF EXISTS cause_embedding;
ALTER TABLE topic_causes ADD COLUMN cause_embedding vector(4096);
```

기존 768차원 로컬 임베딩과 Upstage 4096차원 임베딩은 서로 비교할 수 없습니다.
스키마를 바꾼 뒤에는 기존 저장 벡터를 다시 생성해야 합니다.

운영 DB에는 최소한 다음 테이블이 준비되어 있어야 합니다.

- `feeds`
- `articles`
- `article_jobs`
- `article_ai_results`
- `events`
- `topics`
- `topic_causes`
- `event_articles`

현재 애플리케이션은 운영 DB에서 테이블을 자동 생성하지 않습니다. Supabase
migration에서 스키마를 먼저 적용한 뒤 실행해야 합니다.

## 비용을 줄이는 동작

- 크롤링 본문 LLM 정제는 광고, 공유 UI, 저작권 문구 등 정제 흔적이 있을 때만 호출합니다.
- 이벤트/토픽 후보가 없으면 `solar-pro3` 배정 판단을 생략하고 바로 새 항목을 만듭니다.

## 주요 파일

- `main.py`: RSS 수집/크롤링/기사 분석 CLI 진입점
- `classify_events.py`: 기사에서 이벤트를 배정하거나 생성하는 CLI 진입점
- `classify_topics.py`: 이벤트를 토픽에 배정하거나 생성하는 CLI 진입점
- `src/collector/`: RSS 수집, 크롤링, 기사 LLM 분석
- `src/event_classifier/`: 이벤트 추출과 이벤트 배정
- `src/topic_classifier/`: 토픽 배정과 토픽 cause/result 누적
- `src/db/`: Postgres/Supabase repository 함수
- `src/embedding.py`: Upstage Embedding API 래퍼
- `src/openai_client/`: Upstage/OpenAI 호환 Chat Completions 클라이언트

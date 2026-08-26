# 로컬 개발 환경 구축

팀원이 자기 머신에서 **이벤트/토픽 분류까지** 돌릴 수 있게 만드는 절차다.

기사 수집·크롤링·요약만 할 거라면 SQLite 로도 되지만(`storage.py` 가 테이블을 자동 생성한다),
**이벤트/토픽 분류는 pgvector 가 필요해서 반드시 Postgres 여야 한다.** SQLite 자동 생성 대상은
`feeds / articles / article_jobs / article_ai_results` 넷뿐이고 `events / topics` 계열이 없다.

## 전제

| 도구 | 확인 |
|---|---|
| Docker Desktop | 실행 중이어야 한다 (`docker ps` 가 응답) |
| Supabase CLI | `supabase --version` (2.x 기준으로 작성) |
| Python 3.12 + 의존성 | `python -m pip install -r requirements.txt` |
| 저장소 두 개 | `hannoon-ai` 와 **`hannoon-supabase` 를 같은 부모 디렉터리에** 클론 |

스키마는 이 저장소가 아니라 **형제 저장소 `hannoon-supabase` 가 소유**한다.
여기 있는 `migrations/` 는 참고용 스테이징이라 그것만 보고 DB 를 만들 수 없다.

```
부모디렉터리/
├── hannoon-ai/          ← 파이프라인 코드 (이 저장소)
└── hannoon-supabase/    ← 스키마·마이그레이션·RLS
```

## 1. 스키마 올리기

```bash
cd ../hannoon-supabase
supabase start          # 처음이면 컨테이너 이미지를 받느라 몇 분 걸린다
supabase db reset       # migrations/ 38개를 순서대로 적용 + seed.sql
```

끝나면 이렇게 뜬다. 뒤에서 쓰니 적어두자.

| 항목 | 값 |
|---|---|
| DB | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` |
| Studio (웹 콘솔) | http://127.0.0.1:54323 |
| API | http://127.0.0.1:54321 |

`supabase db reset` 은 **DB 를 통째로 다시 만든다.** 로컬에 쌓아둔 분류 결과가 날아가므로,
스키마를 갱신할 때 말고는 함부로 돌리지 말 것.

## 2. 기사 시드 넣기

스키마만으론 분류할 게 없다. 검수 정답(`eval/data/constraints/`)이 전제하는 기사 코퍼스를 넣는다.

```bash
cd ../hannoon-ai
python scripts/apply_local_seed.py --replace
```

기본값이 `eval/data/seed/articles_2026-07-20.sql`(2,578건, 11MB)이다. psql 없이 psycopg 로 넣는다.

> **`--replace` 가 없으면 리셋 직후에도 실패한다.** `hannoon-supabase` 의 `seed.sql` 이 데모용으로
> articles 9건 / events 12건 / topics 4건을 넣는데, 그 기사 id 가 1~9 라서 이 시드(원본 id 1~4761)와
> 정면으로 충돌하기 때문이다. `--replace` 는 기사·분류 산출물 테이블을 FK 순서대로 비운 뒤 넣는다.

데모 데이터를 남겨두면 **분류 품질 측정이 오염된다** — 이벤트 12개와 토픽 4개가 이미 있는 상태로
집계되므로 이벤트 개수·단일기사 비율 같은 지표가 실제와 달라진다. `--replace` 로 지우고 시작할 것.

### 토픽 분류부터 할 거라면 이벤트 시드도 넣는다

토픽 분류의 입력은 기사가 아니라 **이벤트**다. 기사만 넣으면 `classify_topics.py` 가 볼 대상이 없고,
기사에서 이벤트를 만들려면 요약 + 이벤트 분류를 전부 돌려야 한다(로컬 30B 기준 며칠). 이미 만들어둔
이벤트를 넣어 그 단계를 건너뛴다.

```bash
python scripts/apply_local_seed.py --file eval/data/seed/events_2026-07-27.sql
```

`events` 222 / `event_articles` 722 / `article_ai_results` 722 가 들어가고, 그중 기사 5건 이상인
**38건이 토픽 분류 대상**이 된다. `embedding` 과 `topic_id` 는 비워서 넣으므로 받는 쪽 임베딩 모델로
재생성되고 토픽은 처음부터 분류된다. 자세한 건 `eval/data/seed/README.md`.

> 빠른 스모크용 부분집합(313건)도 있지만 **정답 검증에는 쓸 수 없다** — 검수 제약이 참조하는
> 기사의 62%가 그 목록에 없어서 충족률이 크게 왜곡된다. 자세한 건 `eval/data/seed/README.md`.

## 3. `.env` 만들기

```bash
cp .env.example .env
```

⚠️ **`.env` 가 셸 환경변수를 이깁니다.** 코드가 `load_dotenv(override=True)` 로 읽기 때문에
`EMBEDDING_MODEL=... python main.py` 처럼 앞에 붙여도 `.env` 값으로 덮어써진다. 반드시 파일을 고칠 것.

공통으로 DB 만 로컬로 바꾼다.

```env
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

그다음 LLM·임베딩을 둘 중 하나로 고른다.

### 경로 A — Upstage API (프로덕션과 동일)

`.env.example` 기본값 그대로 두고 키만 넣으면 된다.

```env
UPSTAGE_API_KEY=<발급받은 키>
```

프로덕션과 같은 모델·같은 4096차원 임베딩이라 **로컬 측정 결과를 프로덕션과 바로 비교할 수 있다.**
대신 호출량만큼 비용이 나간다.

### 경로 B — 로컬 Ollama (비용 0)

모델을 받는다. `qwen3:30b-instruct` 는 18GB 라 디스크와 메모리를 확인할 것.

```bash
ollama pull qwen3:30b-instruct
ollama pull bge-m3
```

**임베딩 프록시를 반드시 띄워야 한다.** 스키마가 `vector(4096)` 인데 `bge-m3` 는 1024차원이라
그대로는 들어가지 않는다. 프록시가 뒤에 0을 채워 4096 으로 맞춘다 — 제로 패딩은 코사인 거리를
바꾸지 않으므로(내적과 노름이 그대로) 후보 검색 결과가 왜곡되지 않는다.

```bash
python scripts/local_embedding_proxy.py     # 11500 포트, 계속 떠 있어야 한다
```

`.env`:

```env
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=local
LLM_DEFAULT_MODEL=qwen3:30b-instruct
LLM_CLEANUP_MODEL=qwen3:30b-instruct
LLM_ARTICLE_MODEL=qwen3:30b-instruct
LLM_SUMMARY_MODEL=qwen3:30b-instruct
LLM_TOPIC_EVENT_MODEL=qwen3:30b-instruct
LLM_EVENT_MODEL=qwen3:30b-instruct
LLM_TOPIC_MODEL=qwen3:30b-instruct

EMBEDDING_BASE_URL=http://127.0.0.1:11500/v1
EMBEDDING_API_KEY=local
EMBEDDING_MODEL=bge-m3
EMBEDDING_PASSAGE_MODEL=bge-m3
EMBEDDING_QUERY_MODEL=bge-m3
EMBEDDING_DIMENSIONS=4096
```

> **경로 A 와 B 의 벡터를 한 DB 에 섞지 말 것.** 패딩된 bge-m3 벡터와 진짜 solar-embedding 벡터는
> 좌표계가 달라서, 섞이면 거리 비교가 무의미해진다. 방식을 바꾸려면 `supabase db reset` 부터.
> 30B 모델은 느리다 — 기사 700여 건 기준 이벤트 분류에 약 3시간 걸린 사례가 있다.

## 4. 돌려보기

```bash
python main.py process          # 대기 중인 기사 요약 (status='done' 으로)
python classify_events.py       # 이벤트 분류 (DATABASE_URL 사용)
python classify_topics.py       # 토픽 분류
```

⚠️ **`main.py process` 는 대상 전체를 처리한다** — 제한 옵션이 없다. 위 시드를 그대로 넣으면
`ready` 상태 기사가 2,405건이라, 로컬 30B 모델로는 며칠 단위가 된다. 처음 확인할 때는
**경로 A(Upstage)로 돌리거나**, 시드를 313건 부분집합으로 넣어 규모를 줄이는 편이 낫다.

```bash
python scripts/apply_local_seed.py --replace \
    --file eval/data/seed/articles_test_target_2026-07-20.sql
```

`classify_events.py` 는 요약이 끝난(`article_ai_results.status='done'`) 기사만 대상으로 하므로,
`process` 를 돌리기 전에는 "처리 대상 기사: 0건" 이 정상이다. DB 연결만 먼저 확인하고 싶을 때 쓰면 된다.

결과 확인은 Studio(http://127.0.0.1:54323) 에서 `events`, `event_articles`, `topics` 를 보면 된다.

정답 대비 품질을 재려면 평가 하네스를 쓴다.

```bash
python eval/event_harness.py --database-url "postgresql://postgres:postgres@127.0.0.1:54322/postgres" \
    --run-id my-001 --config-tag "내 실험"
```

자세한 사용법은 `eval/README.md`.

## 자주 막히는 지점

| 증상 | 원인과 해결 |
|---|---|
| `... returned 1024 dimensions, but EMBEDDING_DIMENSIONS is 4096` | 프록시(11500)가 안 떠 있거나 `EMBEDDING_BASE_URL` 이 11434 를 직접 가리킨다 |
| `.env` 를 고쳤는데 반영이 안 됨 | 셸 환경변수로 덮어쓰려 한 경우다. `load_dotenv(override=True)` 라 `.env` 파일이 이긴다 |
| 시드 적용이 "articles 에 이미 N행" 으로 거부됨 | `--replace` 를 붙인다. `supabase db reset` 직후에도 `seed.sql` 데모 9건이 있어서 정상이다 |
| `main.py process` 가 끝나지 않음 | 제한 옵션이 없어 대상 전체(2,405건)를 돈다. 313건 부분집합으로 다시 시드하거나 Upstage 로 돌린다 |
| 이벤트 분류가 "처리 대상 없음" | 요약이 안 끝났다. `python main.py process` 를 먼저 |
| 토픽 분류가 거의 아무것도 안 함 | 정상일 수 있다. 기사 5건 이상인 이벤트만 대상이다(`TOPIC_MIN_NET_ARTICLE_COUNT`) |
| 서브토픽 관련 SQL 에러 | `topics.parent_topic_id` 가 필요하다. 마이그레이션이 최신인지 확인 (`supabase db reset`) |

## 검증 이력

2026-07-31, macOS / Docker 29.6.1 / Supabase CLI 2.109.1 기준으로 이 문서의 1~4단계를 실제로 실행해
확인했다.

- `supabase db reset` 이 마이그레이션 38개를 모두 적용했다.
- 그렇게 재현한 스키마를 프로덕션과 대조한 결과 **테이블 13개가 완전히 일치**했고, 컬럼 차이는
  `topics.parent_topic_id` 하나뿐이었다 — 로컬에는 있고 프로덕션에는 없다. 즉
  `20260701120000_add_topics_parent_topic_id.sql` 이 **프로덕션에 아직 적용되지 않았다.**
  서브토픽 기능(`TOPIC_SUBTOPICS_ENABLED=true`)을 프로덕션에서 켜려면 이 마이그레이션이 먼저다.
- `scripts/apply_local_seed.py --replace` 로 2,578행이 들어갔고 identity 시퀀스도 4762 로 맞았다.
- `classify_events.py` 가 로컬 DB 에 붙어 정상 동작했다("처리 대상 기사: 0건" — 요약 전이라 정상).
- 임베딩 경로는 `bge-m3` + `scripts/local_embedding_proxy.py` 로 `embed_query`/`embed_passage` 가
  4096차원을 반환하는 것까지 확인했다.

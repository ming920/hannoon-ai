# 평가 하네스 (eval/)

더미 데이터 기반 클러스터링 품질 측정 및 반복 개선 도구.  
**기사 → 이벤트 → 서브토픽 → 토픽** 3단 파이프라인의 분류 품질을  
정답 라벨 대비 정량 지표(ARI, NMI, B-cubed F1 등)로 자동 측정한다.

---

## 핵심 결과 요약 (2026-07-03 홀드아웃 검증 완료)

미본 도메인 홀드아웃 세트(5토픽 / 11서브토픽 / 19이벤트 / 101기사)로 최종 검증하여
**서브토픽 임베딩 모드 + τ=0.65 를 기본값으로 채택**했다.
서브토픽 배정 단계의 LLM 호출이 0회가 되어 결정론적이고 저비용이다.

| 채택 기본값 | 값 |
|---|---|
| `TOPIC_SUBTOPIC_MODE` | `embedding` |
| `TOPIC_SUBTOPIC_SIM_THRESHOLD` | `0.65` |

### 홀드아웃 서브토픽 모드 비교

이벤트 레벨은 세 구성 공통: **B³ F1 0.939 · ARI 0.900 · singleton 21.7%** (2런 재현 동일).

| 구성 | 서브토픽 B³ F1 | 서브토픽 covered 정밀도 | 토픽 B³ F1 | 계층 정합성 |
|---|---|---|---|---|
| LLM 모드 (2런) | 0.61 · 0.61 | 0.38 · 0.41 | 0.68 · 0.76 | 1.00 · 1.00 |
| 임베딩 τ=0.55 (2런) | 0.70 · 0.71 | 0.60 · 0.58 | 0.66 · 0.76 | 0.83 · 1.00 |
| **임베딩 τ=0.65 (채택)** | 0.69 | **0.93** | 0.76 | **1.00** |

- LLM 모드는 개발셋에서 관찰된 배정 붕괴가 홀드아웃에서도 재현됐다 (covered 정밀도 0.38~0.41).
- τ=0.55 는 홀드아웃에서 과병합 발생 (covered 정밀도 0.58~0.60) →
  개발셋·홀드아웃 **두 데이터셋 동시 스위프**로 강건점 τ=0.65 를 선정.
- τ=0.65 라이브 재검증: covered 정밀도 0.926 / F1 0.812 / over-merge 1 —
  사전 등록 기준 전 항목 통과.

### 개발셋 개선 여정 (8토픽 소규모 세트)

| 시점 | 이벤트 B³ F1 | 이벤트 singleton | 서브토픽 B³ F1 | 토픽 B³ F1 |
|---|---|---|---|---|
| 초기 전량 실행 (smoke-002) | 0.80 | 50.0% | 0.44 | 0.25 |
| 튜닝 최종 (ctrl-0703 ×2) | 0.87 | 34.1% | 0.59~0.61 | 0.58~0.60 |
| 홀드아웃 최종 구성 | 0.94 | 21.7% | 0.69 | 0.76 |

> 실행별 상세 리포트(`eval/results/*.md`)와 누적표(`metrics.csv`)는 재생성 가능한
> 산출물이므로 **git 에 추적하지 않는다** (`.gitignore` 등록). `run_iteration.py`
> 실행 시 자동 생성되며, 위 표의 수치는 2026-07-03 실행분에서 추출했다.

---

## 사전 요건

### 1. 로컬 pgvector DB 기동

**권장 방법: Supabase CLI** — 전체 마이그레이션(트리거 포함)이 자동 적용된다.

```powershell
# Supabase CLI 설치 후 hannoon-supabase/ 디렉터리에서
supabase start
# → DB URL 출력: postgresql://postgres:postgres@localhost:54322/postgres
```

> **bare docker pgvector 사용 시 주의**: `update_event_counts_on_article_insert`
> 트리거가 없어 `events.article_count` 가 0 에 머물 수 있다.
> `run_iteration.py` 가 `classify_events` 직후 article_count 를 실제 행 수로
> 재동기화하여 이를 방어하지만, 가능하면 `supabase start` 를 사용할 것을 권장한다.

```powershell
# 차선책 — bare docker pgvector (트리거 없음, run_iteration 이 재동기화함)
docker run -d --name hannoon-pg \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  pgvector/pgvector:pg16
# 이후 hannoon-supabase/supabase/migrations 를 psql 로 수동 적용
```

### 2. 환경변수 설정 (`.env`)

```dotenv
# Upstage API 키 (임베딩 + LLM 실제 호출 — generate_dummy --dry-run 시 불필요)
UPSTAGE_API_KEY=up_...

# 로컬 pgvector 연결 URL
DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres
```

### 3. Python 의존성 설치

```powershell
pip install -r hannoon-ai/requirements.txt
```

---

## 전체 실행 흐름

### 단계 0 — 더미 데이터 준비 (최초 1회 또는 데이터 변경 시)

```powershell
# 1. gold_taxonomy.json 설계 (인간 협업 지점)
#    eval/taxonomy/gold_taxonomy.json 에 정답 트리를 작성한다.

# 2a. LLM 없이 결정적 플레이스홀더 생성 (API 키 불필요 — 개발·테스트용)
python eval/generate_dummy.py --dry-run

# 2b. 실제 Upstage LLM 으로 기사 생성 (UPSTAGE_API_KEY 필요)
python eval/generate_dummy.py

# 이벤트 수 제한 (저비용 스모크 테스트 — taxonomy 에서 N 개 이벤트만 처리)
python eval/generate_dummy.py --dry-run --limit-events 3

# 경로 명시 (기본값: eval/taxonomy/gold_taxonomy.json → eval/data/)
python eval/generate_dummy.py \
  --taxonomy eval/taxonomy/gold_taxonomy.json \
  --out-dir  eval/data \
  --dry-run
```

출력: `eval/data/dummy_articles.json`, `eval/data/gold_labels.json`

### 단계 1 — 한 사이클 실행

```powershell
python eval/run_iteration.py \
  --database-url "postgresql://postgres:postgres@localhost:54322/postgres" \
  --run-id       run-001 \
  --config-tag   "baseline-threshold-045"
```

`run_iteration.py` 내부 실행 순서:

| # | 내용 | 주요 동작 |
|---|---|---|
| 1 | `eval/reset_test_db.py --yes` | 분류기 출력 초기화 |
| 2 | `eval/ingest_dummy.py` | 더미 기사 DB 주입 |
| 3 | `classify_events.py` (EVENT_BATCH_SIZE=N) | 이벤트 분류 — 전량 드레인 |
| 4 | [인-프로세스] article_count 재동기화 | 트리거 없는 DB 방어 |
| 5 | `classify_topics.py` (TOPIC_BATCH_SIZE=N, TOPIC_SUBTOPICS_ENABLED=true) | 토픽 분류 |
| 6 | `eval/evaluate.py` | 지표 산출 → results/ 저장 |

### 단계 2 — 파라미터 튜닝 후 재실행

```powershell
# 임계값을 낮춰 재실행 (이벤트 over-create 감소 목표)
EVENT_DISTANCE_THRESHOLD=0.40 python eval/run_iteration.py \
  --database-url "postgresql://..." \
  --run-id       run-002 \
  --config-tag   "threshold-040"
```

---

## 하네스 신뢰성 자기검증 (오라클 테스트)

파이프라인을 실행하기 전에 **지표 계산 자체가 올바른지** 먼저 검증한다.  
이 두 테스트를 먼저 통과시켜야 metrics.py 버그로 인한 잘못된 개선 판단을 방지할 수 있다.

### 오라클 테스트 (3레벨 모두 ARI = B-cubed F1 = 1.0 이어야 함)

gold_labels 를 그대로 예측값으로 사용하면 모든 레벨의 지표가 1.0 이 나와야 한다.  
`--use-gold-as-pred` 플래그는 **DB 연결 없이** gold_labels.json 만으로 수행한다 (구현 완료).

```powershell
python eval/evaluate.py `
    --use-gold-as-pred `
    --gold eval/data/gold_labels.json `
    --run-id oracle-test --config-tag oracle

# 기대(검증됨): 이벤트/서브토픽/토픽 3레벨 모두 ARI=1.0000, bcubed_f1=1.0000, 계층 정합성=1.0000
```

### 랜덤 테스트 (지표가 무작위 배정에 민감한지 확인)

```powershell
python eval/evaluate.py `
    --random-pred --seed 42 `
    --gold eval/data/gold_labels.json `
    --run-id random-test --config-tag random

# 기대(검증됨): ARI << 1.0 (예: 0.25 / -0.15), bcubed_f1 도 1.0 미만 — 지표가 품질에 민감함을 확인
```

---

## results/ 디렉터리 구조

```
eval/results/
  metrics.csv          # 실행별 지표 누적 테이블 (한 줄 = 한 실행)
  run-001.md           # 실행별 상세 리포트
  run-002.md
  ...
```

> 이 디렉터리는 **git 에 추적하지 않는다** (`.gitignore` 등록 — 실행 시
> `evaluate.py` 가 자동 생성). 채택 근거가 되는 핵심 수치는 상단
> [핵심 결과 요약](#핵심-결과-요약-2026-07-03-홀드아웃-검증-완료) 참고.

### metrics.csv 열 설명

아래 컬럼명은 `evaluate.py` 의 `CSV_COLUMNS` 리스트에서 직접 추출한 값이다.

| 열 | 설명 |
|---|---|
| `run_id` | 실행 식별자 (`--run-id` 인자) |
| `config_tag` | 파라미터/프롬프트 구성 태그 |
| `timestamp` | 실행 시각 (UTC ISO8601) |
| `coverage_event` | 이벤트 레벨: gold 기사 중 예측 이벤트가 배정된 비율 |
| `ari_event` | 이벤트 레벨: Adjusted Rand Index |
| `nmi_event` | 이벤트 레벨: Normalized Mutual Information |
| `v_measure_event` | 이벤트 레벨: V-measure |
| `bcubed_f1_event` | 이벤트 레벨: B-cubed F1 (핵심 지표) |
| `singleton_rate_event` | 이벤트 레벨: 단일기사 이벤트 비율 (낮을수록 over-create 개선) |
| `over_split_event` | 이벤트 레벨: over-split 이벤트 수 |
| `coverage_subtopic` | 서브토픽 레벨: coverage |
| `ari_subtopic` | 서브토픽 레벨: ARI |
| `nmi_subtopic` | 서브토픽 레벨: NMI |
| `v_measure_subtopic` | 서브토픽 레벨: V-measure |
| `bcubed_f1_subtopic` | 서브토픽 레벨: B-cubed F1 |
| `coverage_topic` | 토픽 레벨: coverage |
| `ari_topic` | 토픽 레벨: ARI |
| `nmi_topic` | 토픽 레벨: NMI |
| `v_measure_topic` | 토픽 레벨: V-measure |
| `bcubed_f1_topic` | 토픽 레벨: B-cubed F1 |
| `hierarchy_consistency` | 계층 정합성: 서브토픽→토픽 관계 일치율 |

### 레벨별 지표 의미

- **이벤트 레벨**: 각 기사가 올바른 이벤트로 묶였는지 측정.
  `singleton_rate_event`(낮을수록)와 `bcubed_f1_event`(높을수록)이 핵심.
- **서브토픽 레벨**: 이벤트가 올바른 서브토픽으로 묶였는지 측정.
  `TOPIC_SUBTOPICS_ENABLED=true` 일 때만 의미 있음.
- **토픽 레벨**: 이벤트가 올바른 최상위 토픽으로 묶였는지 측정.
- **계층 정합성**: 각 이벤트의 `(서브토픽 → 부모 토픽)` 예측이
  정답 `(gold_subtopic → gold_topic)` 관계와 일치하는 비율.

### 실행 결과 비교 예시

```powershell
python -c "
import csv
with open('eval/results/metrics.csv') as f:
    for row in csv.DictReader(f):
        print(row['run_id'], row['config_tag'],
              'bcubed_f1_event=', row['bcubed_f1_event'],
              'singleton%=',      row['singleton_rate_event'])
"
```

---

## 반복 개선 방법

### 튜닝 가능한 파라미터 (환경변수)

`src/event_classifier/settings.py` 와 `src/topic_classifier/settings.py` 에서 기본값 확인.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `EVENT_DISTANCE_THRESHOLD` | 0.45 | 이벤트 후보 검색 거리 임계값 (낮을수록 엄격) |
| `EVENT_ASSIGN_SCORE_THRESHOLD` | 0.75 | assign 결정 최소 점수 |
| `EVENT_TOP_K` | (설정값) | pgvector 후보 검색 수 |
| `TOPIC_DISTANCE_THRESHOLD` | 0.50 | 토픽 후보 검색 거리 임계값 |
| `TOPIC_MIN_NET_ARTICLE_COUNT` | 5 | 토픽 분류 대상 이벤트 최소 기사 수 |
| `TOPIC_SUBTOPICS_ENABLED` | false | 서브토픽(3단 계층) 활성화 (`run_iteration` 이 자동 주입) |

### 프롬프트 파일 위치

| 파일 | 역할 |
|---|---|
| `src/event_classifier/prompts.py` | 이벤트 추출·assign 판단 프롬프트 |
| `src/topic_classifier/prompts.py` | 토픽 cause/result 추출·assign 프롬프트 |

over-create 를 줄이려면 `prompts.py` 의 assign 판단 지침을 강화하고
`EVENT_DISTANCE_THRESHOLD` 를 낮춰 더 많은 후보를 확보한다.

### 개선 사이클 예시

```powershell
# 베이스라인
python eval/run_iteration.py --database-url "postgresql://..." \
  --run-id run-001 --config-tag "baseline"

# 임계값 조정
EVENT_DISTANCE_THRESHOLD=0.40 python eval/run_iteration.py \
  --database-url "postgresql://..." \
  --run-id run-002 --config-tag "threshold-040"

# 프롬프트 수정 후
python eval/run_iteration.py --database-url "postgresql://..." \
  --run-id run-003 --config-tag "prompt-v2-assign"
```

---

## 제약 검사 — 사람 검수 정답 대비 회귀 테스트

위의 지표(ARI/B-cubed/NMI)가 **합성 더미 데이터**에 대한 전역 품질 점수라면, `constraint_checks.py`는
**사람이 직접 검수한 실제 기사 쌍 제약**을 하나씩 판정한다. 어떤 기사 쌍이 왜 틀렸는지가 그대로
나오므로 회귀 원인 추적에 쓴다. 두 계열은 대체가 아니라 보완 관계다.

| | 클러스터링 지표 (`evaluate.py`) | 제약 검사 (`constraint_checks.py`) |
|---|---|---|
| 정답 출처 | `generate_dummy.py`가 만든 합성 라벨 | 검수자가 손으로 매긴 실제 기사 쌍 |
| 입력 | Postgres DB 직접 조회 | JSON 파일 2개 (DB 불필요) |
| 산출 | ARI / B-cubed / NMI / V-measure | must-link·cannot-link 충족률 + 위반 쌍 목록 |
| 통과 기준 | (게이트 아님) | 기준선 대비 충족률 하락 없음 |

```powershell
# 1) 측정만 (종료 코드 항상 0)
python eval/constraint_checks.py eval/data/constraints/review_2026-07-20.json snapshot.json

# 2) 현재 결과를 기준선으로 저장
python eval/constraint_checks.py `
    eval/data/constraints/review_2026-07-20.json snapshot.json `
    --write-baseline eval/data/constraints/baseline.json

# 3) 회귀 게이트 (기준선 대비 하락 시 종료 코드 1)
python eval/constraint_checks.py `
    eval/data/constraints/review_2026-07-20.json snapshot.json `
    --baseline eval/data/constraints/baseline.json
```

**통과 기준은 "위반 0"이 아니다.** must_link만 이벤트 1,244쌍 + 토픽 1,309쌍이라 LLM 군집화가 전부
맞출 수는 없고, 그 기준으로는 게이트가 첫날부터 영구 실패해 무용지물이 된다. 실행 간 변동이
관측되면 `--tolerance 0.02`처럼 허용 하락폭을 준다.

### 토픽 반복 하네스 — `topic_harness.py`

위 단계를 하나로 묶어 반복 실행하고 결과를 누적한다. 토픽 분류를 개선할 때는 이걸 쓴다.

```powershell
# 1회차 — 기준선
python eval/topic_harness.py --database-url "postgresql://localhost/..." `
    --run-id t-000 --config-tag baseline

# 2회차 — 파라미터를 바꿔 재실행 (직전 실행과 자동 비교)
python eval/topic_harness.py --database-url "postgresql://localhost/..." `
    --run-id t-001 --config-tag "assign 0.70" `
    --set TOPIC_ASSIGN_SCORE_THRESHOLD=0.70

# 분류 없이 채점만 다시
python eval/topic_harness.py --run-id t-001-rescore --skip-classify
```

한 번 실행하면 **토픽 레이어만 초기화**(이벤트 보존) → `classify_topics` 드레인(stdout을
진단 로그로 캡처) → 스냅샷 추출 → 충족률 → 원인 진단 → 루브릭 교차 확인 →
`results/topic_runs.csv` 한 줄 누적 + `results/topic-<run-id>.md` 리포트까지 간다.

**이벤트를 보존하는 게 핵심이다.** 이벤트 레이어를 고정해야 충족률 변화가 토픽 레버의
효과라고 말할 수 있고, 이벤트 재분류 API 비용도 들지 않는다.

`--set`은 `.env`를 임시 패치했다가 실행 후 복원한다. 분류기가
`load_dotenv(override=True)`를 쓰므로 셸 `export`는 무시된다 — 이게 유일하게 듣는 방법이다.

#### 충족률만 보면 반드시 속는다

정답의 토픽 cannot-link 제약은 **0쌍**이다. 모든 이벤트를 한 토픽에 몰아넣어도 must-link
충족률은 100%가 나온다. 그래서 하네스는 리포트에 **토픽 개수와 R-T1(중복 토픽)을 충족률
바로 옆에** 싣고, 충족률이 올랐는데 토픽 수가 20% 넘게 줄면 경고한다.

```
⚠️ 충족률이 올랐지만 토픽 수가 100 → 60로 20% 넘게 줄었습니다.
   과병합으로 점수를 샀을 가능성이 높습니다.
```

위반이 전부 `이벤트 분류 실패의 전파`로 나오면 그것도 경고한다 — 그 경우 토픽 레버를
아무리 만져도 개선되지 않으므로 이벤트 분류를 먼저 고쳐야 한다.

### 이벤트 하네스 — `event_harness.py`

같은 뼈대로 이벤트 레이어를 잰다. 초기화가 기사 요약은 보존하되 **토픽까지 지운다** —
이벤트 구성이 바뀌면 그 위의 토픽도 다시 만들어야 하기 때문이다. 토픽 지표가 필요하면
이 하네스를 돌린 뒤 `topic_harness.py`를 이어서 돌린다.

```powershell
python eval/event_harness.py --database-url "postgresql://localhost/..." `
    --run-id e-000 --config-tag baseline
```

이벤트는 실패가 **양방향**이라 경고가 더 중요하다. 모두 병합하면 must-link 100%,
모두 쪼개면 cannot-link 100%가 나온다. 그래서 ⑴ 단일기사 이벤트 70% 초과 ⑵ 충족률 상승 +
이벤트 수 20% 이상 감소 ⑶ must-link 상승 + cannot-link 하락 세 가지를 경고한다.

### 여러 설정을 한 번에 — `sweep.py`

개선은 설정 여럿을 비교해야 한다. 스윕은 그 반복을 무인으로 돌린다.

```powershell
# 먼저 계획만 확인 (비용이 설정 수만큼 곱해지므로 권장)
python eval/sweep.py --harness topic --run-prefix s1 `
    --config "baseline:" `
    --config "assign070:TOPIC_ASSIGN_SCORE_THRESHOLD=0.70" `
    --config "combo:TOPIC_DISTANCE_THRESHOLD=0.60,TOPIC_ASSIGN_SCORE_THRESHOLD=0.85" `
    --dry-run

# 실제 실행 (--dry-run 만 빼고 --database-url 추가)
```

끝나면 이번 스윕의 실행들만 골라 비교표를 출력한다.

```
  config_tag  topic_must_rate  topics_total
  -----------------------------------------
  기준선      0.42             120
  assign070   0.55             98
  거리완화    0.51             134
```

**충족률만 보고 고르면 안 된다.** 위 예에서 `assign070`이 충족률은 가장 높지만 토픽 수가
120 → 98로 줄었다 — 과병합으로 점수를 샀을 수 있다. 각 실행의 `.md` 리포트에 경고가
찍혔는지 반드시 확인하라.

### 반복 실험 시 초기화는 `reset_classifier_only.py`

`reset_test_db.py`는 `article_ai_results`를 **통째로 삭제**한다. 합성 더미를 매번 새로 만드는
루프에서는 맞지만, 실제 기사 2,578건으로 제약 검사를 반복할 때 쓰면 기사 요약(수집기 단계가
LLM으로 만든 비싼 산출물)까지 날아가 매 반복 재생성해야 한다.

```powershell
EVAL_ALLOW_DESTRUCTIVE_RESET=1 `
  python eval/reset_classifier_only.py --database-url "postgresql://localhost/..." --yes
```

분류기 출력(`event_articles`/`events`/`topic_causes`/`topics`)만 지우고
`article_ai_results.status`를 `'done'`으로 되돌린다. 행과 요약은 그대로 남는다.
안전 가드는 `reset_test_db.py`와 동일한 3중이다.

비교 대상 `snapshot.json`을 뽑는 추출 SQL과 입력 형식은 `data/constraints/README.md`에,
원천 기사 시딩 절차는 `data/seed/README.md`에 있다.

### 위반 원인 진단 — 무엇을 고쳐야 하는지 찾기

충족률은 "얼마나 틀렸나"까지만 알려준다. 같은 must-link 위반이라도 원인이 넷이고
**처방이 서로 다르다.** `diagnose_violations.py`가 분류기 로그와 대조해 그 넷을 가른다.

| 원인 | 무슨 일이 있었나 | 처방 |
|---|---|---|
| **A** | 상대 이벤트가 pgvector 후보에 아예 없었다 | `EVENT_DISTANCE_THRESHOLD` 완화 |
| **B** | 후보엔 있었지만 프롬프트에서 잘려 LLM이 못 봤다 | `MAX_EVENT_CANDIDATES`(prompts.py) 상향 |
| **C** | LLM이 보고도 다른 사건이라 판단했다 | 배정 프롬프트 수정 |
| **D** | LLM은 붙이려 했는데 가드레일이 뒤집었다 | `EVENT_ASSIGN_SCORE_THRESHOLD` 완화 |

```powershell
# 1) 분류기 로그를 파일로 받는다 (stdout이 JSONL)
python classify_events.py --database-url "postgresql://..." > events.log
python classify_topics.py --database-url "postgresql://..." > topics.log

# 2) 정답 + 스냅샷 + 로그를 대조한다
python eval/diagnose_violations.py `
    eval/data/constraints/review_2026-07-20.json snapshot.json events.log `
    --topic-log topics.log
```

**B 유형은 이 도구 없이는 찾을 수 없다.** `EVENT_CANDIDATE_LIMIT`(기본 12)이
`MAX_EVENT_CANDIDATES`(8)보다 커서, 정답 이벤트가 9~12위에 오면 LLM은 그 후보를 본 적도
없는데 "다른 사건으로 판단함"으로 기록된다. 임계값을 아무리 만져도 안 고쳐진다.

출력에는 처방 시뮬레이션도 포함된다 — B를 전부 구제할 `MAX_EVENT_CANDIDATES` 최소값과,
점수 임계값을 낮출 때의 **양방향 트레이드오프**(must-link 구제 vs cannot-link 파손)다.
단 임계값 변경은 이벤트 구성 자체를 바꿔 이후 후보 목록에 연쇄하므로 **1차 근사**이고,
최종 확인은 재실행으로 해야 한다.

#### 토픽 위반은 먼저 "이벤트 탓인지"부터 가른다

`--topic-log`를 주면 토픽 제약도 진단한다. 여기서 첫 갈래가 가장 중요하다.

**같은 이벤트에 속한 기사는 토픽도 반드시 같다**(`events.topic_id`가 하나뿐이므로).
따라서 토픽 must-link 위반은 두 기사가 **다른 이벤트에 갔다**는 뜻이고, 그 분리 자체가
정답에 어긋난다면(이벤트 must-link도 위반) **토픽 레버로는 절대 고쳐지지 않는다.**

| 원인 | 의미 | 처방 |
|---|---|---|
| **0** | 이벤트 분류 실패의 전파 | 토픽 말고 이벤트를 먼저 고친다 |
| **1** | 부모는 같은데 서브토픽에서 갈림 | `SUBTOPIC_ASSIGN_SCORE_THRESHOLD` 또는 `TOPIC_SUBTOPIC_SIM_THRESHOLD` |
| **A** | 부모 후보 검색에 없음 | `TOPIC_DISTANCE_THRESHOLD` 상향 |
| **B** | 프롬프트에서 절삭 | `MAX_CANDIDATES`(topic_classifier/prompts.py) 상향 |
| **C** | LLM이 다른 토픽으로 판단 | `build_parent_topic_assignment_prompt` 수정 |
| **D** | 가드레일이 뒤집음 | `TOPIC_ASSIGN_SCORE_THRESHOLD` 하향 |

1번(서브토픽 분할)은 `decided_by`를 함께 보여준다. `SUBTOPIC_MODE=embedding`이면 LLM
배정 판단이 아예 없으므로 프롬프트·점수 레버가 적용되지 않고 유사도 임계값만 유효하다.

> ⚠️ 정답의 **토픽 cannot-link 제약은 0쌍**이다. 모든 기사를 한 토픽에 몰아넣어도
> 충족률은 만점으로 나온다. 토픽 과병합은 이 정답으로 감지할 수 없으니 `rubric_checks.py`의
> R-T1(중복 토픽)과 토픽 개수를 반드시 함께 보라.

---

## 규모 단계적 확장 절차

| 단계 | 기사 수 | 정답 이벤트 수 | 목적 |
|---|---|---|---|
| **소규모** | ~60건 | ~8개 | 하네스·지표 동작 검증 (빠르고 저렴) |
| **중규모** | ~300건 | ~40개 | 지표 신뢰도 확인, 개선 신호 검증 |
| **대규모** | 1,000건+ | 100개+ | 실운영 근접 품질 측정 |

각 단계에서 지표가 기대 방향으로 움직이는지 확인한 뒤 다음 단계로 확장한다.

대규모 실행 전에 `src/embedding.py` 에 텍스트 해시 기반 임베딩 캐시를 추가하면
동일 텍스트 재실행 시 임베딩 API 비용을 절감할 수 있다 (임베딩은 결정적).

---

## 파일 구조

```
eval/
  taxonomy/
    gold_taxonomy.json       # 정답 트리 (인간 설계 — JSON 형식)
  data/
    dummy_articles.json      # generate_dummy.py 출력
    gold_labels.json         # article_guid → {gold_topic, gold_subtopic, gold_event}
    constraints/             # 사람 검수 정답 (constraints-v1) — 재생성 불가, 덮어쓰지 말 것
    seed/                    # 원천 기사 INSERT 덤프 (전체 2,578건 / 부분집합 313건)
  results/                   # (git 미추적 — 실행 시 자동 생성)
    metrics.csv              # 실행별 지표 누적 (CSV_COLUMNS 순서)
    <run-id>.md              # 실행별 상세 리포트
  generate_dummy.py          # taxonomy → (LLM 또는 --dry-run) → dummy + gold
  ingest_dummy.py            # 더미 기사 DB 주입 (guid 중복 검사로 멱등)
  reset_test_db.py           # 분류기 출력 + 더미 기사 초기화 (합성 루프용, 3중 안전 가드)
  reset_classifier_only.py   # 분류기 출력만 초기화 — 기사 요약 보존 (실제 코퍼스 반복용)
  metrics.py                 # 레벨별 지표 계산 (순수 함수)
  evaluate.py                # DB 예측 읽기 + gold 비교 → 리포트 + CSV 행 추가
  extract_snapshot.py        # 분류 결과 DB → 제약 검사 입력 JSON (읽기 전용)
  harness_common.py          # 두 하네스 공통 배관 (CSV 누적·비교·드레인·.env 패치)
  topic_harness.py           # 토픽 반복 검증·개선 하네스 (초기화→분류→채점→진단→누적)
  event_harness.py           # 이벤트 반복 검증·개선 하네스 (요약 보존, 토픽까지 초기화)
  sweep.py                   # 여러 설정을 연속 실행하고 한 표로 비교
  rubric_checks.py           # 엔티티 정의 루브릭 위반 산출 (DB 스냅샷)
  constraint_checks.py       # 사람 검수 제약 충족률 + 기준선 회귀 게이트 (JSON 입력, DB 불필요)
  diagnose_violations.py     # 위반 원인을 분류기 로그와 대조해 A/B/C/D로 진단 + 처방 시뮬레이션
  run_iteration.py           # 전체 사이클 오케스트레이션
  README.md                  # 이 파일
```

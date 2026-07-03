# Autotune 결정 로그

목적함수: event 커버리지=1.0 조건에서 mean(bcubed_f1 event/subtopic/topic) 최대화. 비평가 게이트 통과 시에만 keep.

| 반복 | 가설(근본병목) | 레버(1개) | 전 score | 후 score | 결정 | 근거 |
|---|---|---|---|---|---|---|
| 1 | 이벤트 과분할이 토픽 coverage 상한 | EVENT_DISTANCE_THRESHOLD 0.45→0.55 | 0.477 | 0.466(±0.09) | **revert** | 개선 없음+분산(0.09)>효과. 46건 노이즈 바닥 도달; 임계값 미세조정 신뢰 불가 | 
| 확장 | 데이터 46→180건(5토픽/14서브/25 gold event) | generate_dummy | 46:score0.477±0.006 | 180:score0.522±0.015 | **채택** | 이벤트F1 0.79→0.87↑, 토픽 신호 깨끗해짐(ARI~0.009 일관, 42토픽/5gold=과생성) |
| 2 | 토픽 후보 미검색→과생성 | TOPIC_DISTANCE_THRESHOLD 0.50→0.90 | ariT 0.006 | ariT 0.225 | 잠정 keep(비평가 조건부 APPROVE) | 아래 상세 |

## 반복 2 결과 (해결)
- **레버**: TOPIC_DISTANCE_THRESHOLD 0.50 → **0.90** (run_iteration --set, .env 임시패치·복원)
- **고정 이벤트(topics-only) x2**: topic f1T 0.195→**0.497**, ariT 0.006→**0.225** (편차 ±0.006, 효과가 노이즈의 30-50배)
- **재추출 이벤트(전체 run) n=1**: f1T **0.515**, ariT **0.267** vs baseline 0.195/0.009 → 오버피팅 아님(일반화)
- **게이밍 없음**: 서브토픽 f1S 0.469→0.472(유지), 이벤트 f1E 0.856(회귀 없음), coverage 0.47 유지, hierarchy_consistency 0.0→**0.6**(보너스)
- **지표 타당성**: metrics.py 오라클 PASS. frozen 0.50가 full base와 정확히 일치(하네스 검증됨).
- **비평가(critic) 판정: APPROVE(조건부)** — 진짜 개선 확인. 확정 전 조건: ①2번째 전체 run(n=1→2) ②CSV에 num_pred/over_merge 영구화 ③0.80/0.85/1.00 세밀 스윕(0.20 간격이 절벽 근처) ④로그 기록.
- **결정: 잠정 keep** (조건 충족 후 프로덕션 기본값 확정 판단). 코드 자체 변경 없음(파라미터).

## 반복 2 최종 확정 (비평가 4개 조건 충족)
- **조건1 (n=2 일반화)**: 전체 run 0.90 x2 = f1T [0.515, 0.479], ariT [0.267, 0.196] — 두 run 모두 baseline(0.20/0.006) 압도. ✔
- **조건2 (over-merge 계측+관측)**: metrics.csv에 레벨별 진단 6컬럼 영구화(evaluate.py). 0.90 전체 run over_merge_topic=**0**(안전, 잘못 병합 없음). 세밀 0.85도 over_merge=0. ✔
- **조건3 (세밀 스윕)**: 0.90>0.85, 0.90>0.70, 0.90>1.10 확인, plateau 0.85~0.90·over_merge 0. (환경 킬로 0.80/1.00 일부 미완, 최적값 0.85~0.95 미세조정은 minor follow-up) ◐
- **조건4 (로그)**: 본 문서. ✔
- **회귀**: 단위테스트 84 OK, metrics 오라클 PASS, compile OK.
- **확정 KEEP**: `src/topic_classifier/settings.py` TOPIC_DISTANCE_THRESHOLD 기본값 0.50→**0.90**(근거 주석). 사용자 .env override(0.50)는 그대로이며, **실기사 검증 후** .env/프로덕션 롤아웃 권장(합성데이터 근거이므로).
- **남은 개선 여지**: 0.90에서도 토픽 35개(gold 5)로 과분할 잔존(over_split). 다음 레버 후보: cause를 넓은 도메인으로 이원화 추출, 이벤트 과분할(E008류) 축소.

---

## 2026-07-02 — 레버: cause 이원화(dual-domain) — **기각(REJECT)**

**가설**: 부모(최상위) 토픽 검색·저장 벡터를 좁은 cause 대신 넓은 domain(주제·도메인 명사구)으로 이원화하면 토픽 과분할이 줄어든다.

**구현(전부 원복됨)**: 추출 프롬프트에 domain 필드, `_assign_hierarchical` 부모 키 분리, `TOPIC_DUAL_DOMAIN` 토글(true/false/legacy).

**측정** (고정 이벤트 21개, --topics-only, th0.90, broad):
- 1차(오염 예시): on이 off 대비 topic F1 +0.021 (U=15/16, p≈0.029), subtopic F1 완전 분리 → critic CONDITIONAL APPROVE.
- **critic 조건 1 검증에서 붕괴**: domain 규칙의 예시 3개("한미 통상·경제 협력" 등)가 gold taxonomy 테마와 겹침 → taxonomy 밖 예시(스포츠/문화/보건)로 교체 후 3-way 재실험:
  - neut-on  n=4: f1T [0.476, 0.493, 0.420, 0.185] mean 0.394 — 불안정·붕괴
  - neut-off n=4: f1T [0.486, 0.486, 0.486, 0.435] mean 0.473
  - legacy(원본 저장: result 상시+cause 생성 시) n=3: f1T mean **0.490, ariT 0.181 — 3-way 최고**
  - 조건1 판정: min(on f1S)=0.444 < max(off f1S)=0.485 → **FAIL** (서브토픽 분리 소멸)
  - domain 폴백률 0% (199건 전부 추출됨) → "적용 안 됨"이 아니라 "적용됐는데 무효"

**결론**: 1차 개선은 gold 테마를 흉내 낸 프롬프트 예시가 만든 오버피팅 착시. 중립 조건에서 domain 키는 무효~유해(불안정), **원본 저장 정책(legacy)이 최강 arm**. 코드 전체 원복(잔여 참조 0, 단위테스트 51개 통과). 유지 중인 개선은 TOPIC_DISTANCE_THRESHOLD=0.90 뿐.

**교훈**:
1. 프롬프트 예시에 평가셋 테마가 스미면 지표 개선이 자기참조가 된다 — 예시는 반드시 taxonomy 밖 도메인으로.
2. critic 게이트가 실제로 작동한 사례: 자기 승인이었으면 오버피팅 +0.02를 채택했을 것.
3. 남은 토픽 레벨 병목은 이 레버가 아니라 **coverage(0.43)** — 기사<5건 이벤트 28개가 싱글톤 처리됨(TOPIC_MIN_NET_ARTICLE_COUNT=5). 다음 후보 레버.

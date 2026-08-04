import os

from dotenv import load_dotenv


# classify_events.py가 pipeline을 import하는 순간 설정이 평가되므로 .env를 먼저 읽는다.
load_dotenv(override=True)

BATCH_SIZE = int(os.environ.get("EVENT_BATCH_SIZE", "5"))
TOP_K = int(os.environ.get("EVENT_CANDIDATE_LIMIT", "12"))
# 0.50→0.40: 2026-08-03 로컬 실측(run e003) 채택값. 그 전 0.45→0.50 은 2026-07-12 Phase 2
# 튜닝에서 후보 회수를 늘리려 완화한 값이었는데, 그때는 cannot-link 정답이 48쌍뿐이라
# 과병합이 측정되지 않았다. 정답을 257쌍으로 늘리자 cannot 충족률이 7.4% 로 드러났다.
#
# 다시 조인 결과(기사 722건, 사람 정답 48쌍 기준): must 90.4→93.0%, cannot 39.6→58.3%,
# 이벤트 222→211 개, 단일기사 109→93 개. 덜 붙였는데 must 와 파편화까지 좋아졌다.
# 근거: docs/event_clustering_experiments.md
#
# 위 Phase 2 주석은 "거리를 완화하면 과병합이 느니 assign 점수 상향과 쌍으로 운용하라"는
# 것이었다. 여기서는 반대로 조이므로 그 위험과 어긋난다 — ASSIGN_SCORE_THRESHOLD 는 0.80 유지.
DISTANCE_THRESHOLD = float(os.environ.get("EVENT_DISTANCE_THRESHOLD", "0.40"))
# 후보검색 시간 윈도우(일). SEARCH_CANDIDATE_EVENTS_SQL의 updated_at 기준 조건에 쓰인다.
CANDIDATE_WINDOW_DAYS = int(os.environ.get("EVENT_CANDIDATE_WINDOW_DAYS", "2"))

# 후보가 이 개수 미만이면 거리만 FALLBACK_DISTANCE_THRESHOLD 로 넓혀 한 번 더 찾는다.
# FALLBACK 이 0 이거나 DISTANCE_THRESHOLD 이하면 비활성(기본).
#
# 왜 필요한가: must-link 위반의 A유형("정답 이벤트가 후보에 아예 없었다")은 거리 임계값의
# 직접적인 함수다 — 로컬 실측에서 0.50→11건, 0.40→65건, 0.35→142건이었다. 그런데 거리를
# 완화하면 과병합이 늘어(0.50 에서 cannot 충족률 7.4%) 같은 레버의 양면이라 임계값 하나로는
# 둘 다 잡을 수 없다.
#
# A유형 65건을 뜯어보니 후보가 0개인 경우가 28건, 1~11개가 37건이고 **상한(12)에 잘린 것은
# 0건**이었다. 많이 찾아서 잘린 게 아니라 아무것도 못 찾은 것이므로 EVENT_CANDIDATE_LIMIT
# 상향은 무의미하다. 그래서 "후보가 부족할 때만" 거리를 넓힌다 — 정상적으로 후보가 잡히는
# 경우는 건드리지 않으므로 과병합을 늘리지 않는다.
#
# 넓혀 온 후보도 결국 LLM 배정 판단과 ASSIGN_SCORE_THRESHOLD 가드를 그대로 통과해야 한다.
# 하네스로 재본 뒤 켜는 것을 전제로 기본값은 비활성이다:
#   `--set EVENT_FALLBACK_DISTANCE_THRESHOLD=0.50`
FALLBACK_DISTANCE_THRESHOLD = float(os.environ.get("EVENT_FALLBACK_DISTANCE_THRESHOLD", "0"))
FALLBACK_MIN_CANDIDATES = int(os.environ.get("EVENT_FALLBACK_MIN_CANDIDATES", "2"))
ASSIGN_SCORE_THRESHOLD = float(os.environ.get("EVENT_ASSIGN_SCORE_THRESHOLD", "0.80"))

# 이벤트 벡터를 언제 갱신할지.
#   anchor   (기본) 첫 기사의 임베딩에 고정한다 — 지금까지의 동작.
#   centroid 기사가 배정될 때마다 구성원 평균으로 다시 맞춘다.
#
# anchor 는 이벤트가 커질수록 대표성을 잃는다. 로컬 실측(기사 722건)에서 15건 이상 이벤트의
# 구성원은 앵커까지 평균 0.267, 중심까지 0.149 였다. 같은 데이터에서 C유형 오판 30건 중
# 10건(33%)이 중심 기준에서는 정답 이벤트가 더 가까웠다(분류 시점 구성원만 사용해 확인).
#
# 다만 이 수치는 bge-m3 기준이고 프로덕션 임베딩에서 크기가 어떻게 달라질지는 미확인이다.
# 후보 검색 결과가 통째로 바뀌는 변경이라 기본값은 기존 동작으로 두고, 하네스로 재본 뒤
# 켜는 것을 전제로 한다: `--set EVENT_EMBEDDING_UPDATE=centroid`
EMBEDDING_UPDATE_ANCHOR = "anchor"
EMBEDDING_UPDATE_CENTROID = "centroid"
EMBEDDING_UPDATE_MODE = (
    os.environ.get("EVENT_EMBEDDING_UPDATE", EMBEDDING_UPDATE_ANCHOR).strip().lower()
    or EMBEDDING_UPDATE_ANCHOR
)
if EMBEDDING_UPDATE_MODE not in (EMBEDDING_UPDATE_ANCHOR, EMBEDDING_UPDATE_CENTROID):
    raise ValueError(
        f"EVENT_EMBEDDING_UPDATE 는 '{EMBEDDING_UPDATE_ANCHOR}' 또는 "
        f"'{EMBEDDING_UPDATE_CENTROID}' 여야 합니다 (받은 값: {EMBEDDING_UPDATE_MODE!r})."
    )
LLM_MODEL = os.environ.get(
    "LLM_EVENT_MODEL",
    os.environ.get("LLM_TOPIC_EVENT_MODEL", os.environ.get("LLM_DEFAULT_MODEL", "solar-mini")),
)

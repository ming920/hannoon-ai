import os

from dotenv import load_dotenv


# classify_events.py가 pipeline을 import하는 순간 설정이 평가되므로 .env를 먼저 읽는다.
load_dotenv(override=True)

BATCH_SIZE = int(os.environ.get("EVENT_BATCH_SIZE", "5"))
TOP_K = int(os.environ.get("EVENT_CANDIDATE_LIMIT", "12"))
# 0.45→0.50, 0.75→0.80: 2026-07-12 Phase 2 dev 튜닝(run p2-v2-dist050-score080) 채택값.
# 거리 완화(후보 회수↑)를 assign 점수 상향(과병합 억제)과 쌍으로 운용한다 — 한쪽만 바꾸면
# 정밀도 가드(B³ P ≥ 0.93)가 깨진다. 근거: .omc/research/phase2-event-tuning-20260712.md
DISTANCE_THRESHOLD = float(os.environ.get("EVENT_DISTANCE_THRESHOLD", "0.50"))
# 후보검색 시간 윈도우(일). SEARCH_CANDIDATE_EVENTS_SQL의 updated_at 기준 조건에 쓰인다.
CANDIDATE_WINDOW_DAYS = int(os.environ.get("EVENT_CANDIDATE_WINDOW_DAYS", "2"))
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

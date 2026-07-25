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
LLM_MODEL = os.environ.get(
    "LLM_EVENT_MODEL",
    os.environ.get("LLM_TOPIC_EVENT_MODEL", os.environ.get("LLM_DEFAULT_MODEL", "solar-mini")),
)

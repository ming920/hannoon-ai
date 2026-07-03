import os

from dotenv import load_dotenv


# classify_topics.py가 CLI 모듈을 import할 때 환경 변수 기반 기본값이 확정된다.
load_dotenv(override=True)

MIN_NET_ARTICLE_COUNT = int(os.environ.get("TOPIC_MIN_NET_ARTICLE_COUNT", "5"))
BATCH_SIZE = int(os.environ.get("TOPIC_BATCH_SIZE", "10"))
TOP_K = int(os.environ.get("TOPIC_CANDIDATE_LIMIT", "12"))
# 서브토픽(계층) 분류 활성화 여부와 서브토픽 후보 검색 개수.
# 미지정 시 SUBTOPIC_TOP_K는 TOP_K와 동일하게 동작한다(아래 pipeline.run 참고).
SUBTOPICS_ENABLED = os.environ.get("TOPIC_SUBTOPICS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
SUBTOPIC_TOP_K = int(os.environ.get("TOPIC_SUBTOPIC_CANDIDATE_LIMIT", str(TOP_K)))
# 더미 평가(eval/results/AUTOTUNE_LOG.md) 결과 0.90이 토픽 클러스터링을 크게 개선(topic B-cubed F1 0.20→0.50, ARI 0.01→0.23, over_merge=0). 실기사 검증 후 프로덕션 롤아웃 권장.
DISTANCE_THRESHOLD = float(os.environ.get("TOPIC_DISTANCE_THRESHOLD", "0.90"))
ASSIGN_SCORE_THRESHOLD = float(os.environ.get("TOPIC_ASSIGN_SCORE_THRESHOLD", "0.75"))
# 서브토픽 전용 assign 점수 임계값. 부모(recall 우선)와 서브(과병합 방지)의 요구가
# 상반되므로 분리한다. 미지정 시 ASSIGN_SCORE_THRESHOLD와 동일(기존 동작 유지).
SUBTOPIC_ASSIGN_SCORE_THRESHOLD = float(
    os.environ.get("TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD", str(ASSIGN_SCORE_THRESHOLD))
)
# 서브토픽 배정 방식: "llm"(기존 assign-or-create 프롬프트) 또는
# "embedding"(이벤트 임베딩 코사인 최근접 — LLM 호출 없음, 결정론적).
SUBTOPIC_MODE = os.environ.get("TOPIC_SUBTOPIC_MODE", "llm").strip().lower()
# embedding 모드에서 기존 서브토픽에 편입하기 위한 최소 코사인 유사도.
# 오프라인 스위프(2026-07-03) 기준 0.45~0.60이 플래토, 0.55에서 P 0.926/om 1.
SUBTOPIC_SIM_THRESHOLD = float(os.environ.get("TOPIC_SUBTOPIC_SIM_THRESHOLD", "0.55"))
LLM_MODEL = os.environ.get(
    "LLM_TOPIC_MODEL",
    os.environ.get("LLM_TOPIC_EVENT_MODEL", os.environ.get("LLM_DEFAULT_MODEL", "solar-mini")),
)

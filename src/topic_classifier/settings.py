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
LLM_MODEL = os.environ.get(
    "LLM_TOPIC_MODEL",
    os.environ.get("LLM_TOPIC_EVENT_MODEL", os.environ.get("LLM_DEFAULT_MODEL", "solar-mini")),
)

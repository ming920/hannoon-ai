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
# 서브토픽 배정 방식: "embedding"(이벤트 임베딩 코사인 최근접 — LLM 호출 없음,
# 결정론적) 또는 "llm"(기존 assign-or-create 프롬프트).
# embedding이 원본·홀드아웃 두 더미 세트 모두에서 llm을 상회해 기본값으로 채택
# (홀드아웃 검증 holdout-emb065-1: covered P 0.926/F1 0.812/om 1 vs llm F1 0.557).
SUBTOPIC_MODE = os.environ.get("TOPIC_SUBTOPIC_MODE", "embedding").strip().lower()
# embedding 모드에서 기존 서브토픽에 편입하기 위한 최소 코사인 유사도.
# 두 데이터셋 동시 스위프 기준 0.65가 강건점(0.65~0.70 플래토): 원본 P 1.00/om 0,
# 홀드아웃 P 0.93/om 1. 0.55는 홀드아웃에서 과병합(P 0.58)이라 기각.
SUBTOPIC_SIM_THRESHOLD = float(os.environ.get("TOPIC_SUBTOPIC_SIM_THRESHOLD", "0.65"))
# create 직전 중복 방지 가드(R-T1/R-S4 대응): 동일 스코프(최상위 또는 같은 부모 아래) 내
# 기존 토픽과 difflib 제목 유사도가 이 값 이상이면 create를 assign으로 강등한다.
# 0.85는 eval/rubric_checks.py R-T1(토픽 중복)·R-S4(서브토픽-부모 동일범위) 판정 임계값과
# 동일하게 맞춰, 파이프라인의 예방 가드와 사후 진단 기준이 같은 눈금을 쓰도록 한다.
TOPIC_DUP_SIM_THRESHOLD = float(os.environ.get("TOPIC_DUP_SIM_THRESHOLD", "0.85"))
LLM_MODEL = os.environ.get(
    "LLM_TOPIC_MODEL",
    os.environ.get("LLM_TOPIC_EVENT_MODEL", os.environ.get("LLM_DEFAULT_MODEL", "solar-mini")),
)

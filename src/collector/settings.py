import os

from dotenv import load_dotenv


# CLI 진입점보다 먼저 settings가 import될 수 있으므로 여기서 .env를 선로딩한다.
load_dotenv(override=True)

DEFAULT_DB = os.path.join("data", "news.db")
DEFAULT_FEEDS_FILE = os.path.join("config", "feeds.json")
DEFAULT_MIN_CRAWL_LEN = 400
DEFAULT_CRAWL_BATCH_SIZE = 20
DEFAULT_AI_BATCH_SIZE = 20
DEFAULT_ANALYSIS_MAX_ATTEMPTS = 3
DEFAULT_DOMAIN_DELAY = 2.0

DEFAULT_LLM_MODEL = os.environ.get("LLM_DEFAULT_MODEL", "solar-mini")
DEFAULT_LLM_CLEANUP_MODEL = os.environ.get("LLM_CLEANUP_MODEL", DEFAULT_LLM_MODEL)
DEFAULT_LLM_ARTICLE_MODEL = os.environ.get("LLM_ARTICLE_MODEL", DEFAULT_LLM_MODEL)
DEFAULT_LLM_SUMMARY_MODEL = os.environ.get("LLM_SUMMARY_MODEL", DEFAULT_LLM_ARTICLE_MODEL)

USER_AGENT = "NoiseFreeRSSBot/0.1 (+https://example.invalid)"

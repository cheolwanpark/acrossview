"""Configuration constants for the news crawler."""

from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "news.db"
CSV_PATH = PROJECT_ROOT / "news-list.csv"
LOG_PATH = DATA_DIR / "crawler.log"

# Crawling settings
DEFAULT_CONCURRENT = 5
RATE_LIMIT_PER_DOMAIN = 1.0  # seconds between requests to same domain
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3

# HTTP settings
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Retryable HTTP status codes
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Permanent failure status codes (don't retry)
PERMANENT_FAILURE_CODES = {403, 404, 410, 451}

# Embedding settings
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768
EMBEDDING_BATCH_SIZE = 100  # API supports up to 100 texts per request
EMBEDDING_BATCH_DELAY = 2.0  # seconds between batches for rate limiting

# LLM settings
LLM_MODEL = "gemini-2.5-flash-lite"
LLM_MAX_RETRIES = 3

"""Centralized constants for timeouts, retries, and other magic numbers"""

# ===== API TIMEOUTS (in seconds) =====
API_TIMEOUT_STANDARD = 10.0  # Standard API calls (agent execution)
API_TIMEOUT_HEALTH_CHECK = 5.0  # Health check endpoint
API_TIMEOUT_LLM_CALL = 30.0  # LLM provider calls

# ===== MODEL-SPECIFIC TIMEOUTS (in milliseconds) =====
MODEL_TIMEOUT_OLLAMA = 5000  # Ollama: local, fast
MODEL_TIMEOUT_CLAUDE = 30000  # Claude: remote, standard
MODEL_TIMEOUT_GPT4 = 30000  # GPT-4: remote, standard
MODEL_TIMEOUT_GEMINI = 30000  # Gemini: remote, standard

# ===== RETRY CONFIGURATION =====
MAX_RETRIES = 3  # Maximum retry attempts for API calls
RETRY_BACKOFF_FACTOR = 2  # Exponential backoff multiplier

# ===== REQUEST LIMITS =====
MAX_REQUEST_SIZE_BYTES = 1000000  # 1 MB
MAX_TAGS = 10  # Maximum tags per content
MAX_CATEGORIES = 5  # Maximum categories per content
MAX_TASK_NAME_LENGTH = 200  # Maximum task name length
MAX_DESCRIPTION_LENGTH = 1000  # Maximum description length

# ===== WAIT/POLLING CONFIGURATION =====
TASK_POLL_INTERVAL = 5  # Seconds between task status polls
TASK_POLL_MAX_ATTEMPTS = 12  # Maximum poll attempts (5 seconds * 12 = 60 seconds)

# ===== DATABASE =====
DB_CONNECTION_TIMEOUT = 10.0  # Database connection timeout (seconds)
DB_QUERY_TIMEOUT = 30.0  # Database query timeout (seconds)

# ===== LOGGING =====
LOG_LEVEL_PRODUCTION = "INFO"
LOG_LEVEL_DEVELOPMENT = "DEBUG"

# ===== CACHE TTL (in milliseconds) =====
CACHE_TTL_SLUG_LOOKUP = 300000  # 5 minutes

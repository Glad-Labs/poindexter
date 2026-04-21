# ============================================================================
# Fix sys.path for namespace packages at module import time — Poetry sometimes
# breaks namespace resolution in dev shells. Kept minimal: no paid-API SDKs
# rely on this anymore, but Ollama / Pexels / etc. still benefit on Windows.
# ============================================================================
import logging as _logging
import sys
from pathlib import Path as _PathType


def _fix_sys_path(path_cls=_PathType):
    """Fix sys.path to prioritize venv site-packages."""
    try:
        venv_site_packages = path_cls(sys.prefix) / "Lib" / "site-packages"
        if venv_site_packages.exists():
            venv_site_packages_str = str(venv_site_packages)
            # Ensure venv's site-packages is first in the path
            sys.path = [venv_site_packages_str] + [
                p for p in sys.path if p != venv_site_packages_str
            ]
            # Clear import caches
            import importlib

            importlib.invalidate_caches()
    except Exception as e:
        _logging.warning("Failed to fix sys.path: %s", e)


_fix_sys_path()
del _fix_sys_path, _PathType

import os

from services.logger_config import get_logger

# --- Define Base Directory ---
# Ensures that all file paths are relative to the project root, making the application more portable.
BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__))
)  # This gets us to src/agents/content_agent/

# Logging is configured centrally in services/logger_config.py
logger = get_logger(__name__)


class Config:
    """
    Central configuration class for the content agent.

    DB connection resolution is delegated to :mod:`brain.bootstrap`, which
    checks (in priority order):

      1. An explicit value passed by the caller
      2. ``~/.poindexter/bootstrap.toml`` (the Jellyfin/Plex-style user config
         file written by ``poindexter setup``)
      3. ``DATABASE_URL`` / ``LOCAL_DATABASE_URL`` / ``POINDEXTER_MEMORY_DSN``
         environment variables

    This replaces the legacy ``.env`` / ``.env.local`` ``dotenv`` loader.
    Every other setting below comes from the process environment only —
    Docker / systemd / shell export — so the config path for operators is
    "edit ``~/.poindexter/bootstrap.toml`` or export env vars," never a
    hidden ``.env`` file.

    Runtime settings (prompt templates, thresholds, model routing, etc.)
    live in the ``app_settings`` DB table and are read via
    ``services.site_config``. This class only holds bootstrap values that
    have to exist *before* the DB is reachable.
    """

    def __init__(self):
        from brain.bootstrap import resolve_database_url

        # --- Core Application Paths ---
        self.BASE_DIR = BASE_DIR
        self.PROMPTS_PATH = os.path.join(self.BASE_DIR, "prompts.json")

        # --- PostgreSQL Database for CMS ---
        # bootstrap.toml > DATABASE_URL env > LOCAL_DATABASE_URL > POINDEXTER_MEMORY_DSN
        self.DATABASE_URL = resolve_database_url()
        if not self.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is required. Set it in ~/.poindexter/bootstrap.toml "
                "(run `poindexter setup`) or export DATABASE_URL in the environment. "
                "This project uses PostgreSQL only — SQLite is not supported."
            )
        # Component-wise DB vars are secondary — only consumed by
        # startup_manager for diagnostic output. Defaults match the docker
        # stack; override via env if you're pointing at a different host.
        self.DATABASE_HOST = os.getenv("DATABASE_HOST", "localhost")
        self.DATABASE_PORT = os.getenv("DATABASE_PORT", "5432")
        self.DATABASE_NAME = os.getenv("DATABASE_NAME", "glad_labs")
        self.DATABASE_USER = os.getenv("DATABASE_USER", "postgres")
        self.DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "")

        # --- External Services & APIs ---
        self.PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")  # For sourcing stock photos
        self.SERPER_API_KEY = os.getenv("SERPER_API_KEY")  # For real-time web search capabilities

        # --- Local Image Generation & Storage ---
        self.IMAGE_STORAGE_PATH = os.path.join(self.BASE_DIR, "content-agent", "generated_images")
        self.DEFAULT_IMAGE_PLACEHOLDERS = 3  # Default number of images to generate for a post

        # --- Language Model Provider ---
        # Only 'ollama' / 'local' are accepted — paid-API providers (OpenAI,
        # Anthropic, Gemini) were removed in v2.8 per the no-paid-APIs policy.
        # LLMClient enforces this at runtime; this default just avoids a
        # KeyError if the env var is unset.
        self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

        # --- Local LLM (Ollama) Configuration ---
        # #198: no hardcoded localhost default. An empty string means
        # "Ollama not configured" and callers must handle that explicitly.
        self.LOCAL_LLM_API_URL = os.getenv("LOCAL_LLM_API_URL", "")
        self.LOCAL_LLM_MODEL_NAME = os.getenv("LOCAL_LLM_MODEL_NAME", "auto")

        # --- Logging Configuration ---
        self.LOG_DIR = os.path.join(self.BASE_DIR, "content-agent", "logs")
        self.APP_LOG_FILE = os.path.join(self.LOG_DIR, "app.log")
        self.PROMPTS_LOG_FILE = os.path.join(self.LOG_DIR, "prompts.log")
        self.MAX_LOG_SIZE_MB = int(os.getenv("MAX_LOG_SIZE_MB", "5"))
        self.MAX_LOG_BACKUP_COUNT = int(os.getenv("MAX_LOG_BACKUP_COUNT", "3"))


# --- Singleton Instance ---
# Create a single, immutable instance of the configuration to be imported by other modules.
config = Config()

# --- Validation Utilities ---
# Validate required environment variables. Non-strict by default to enable testing and local dev.
# Set STRICT_ENV_VALIDATION=1 to raise on missing variables.


def validate_required(strict: bool = False) -> list:
    """
    Validate required environment variables for the content agent.

    The agent needs very little to start:

    - ``DATABASE_URL`` (via ``brain.bootstrap`` — bootstrap.toml or env var)
    - Ollama running locally (``LOCAL_LLM_API_URL`` resolved via
      ``services.site_config`` at runtime, so not checked here)

    Optional: ``PEXELS_API_KEY`` for stock photos, ``SERPER_API_KEY`` for
    web search. Missing those disables those features gracefully.

    When ``strict`` is True, raises RuntimeError on missing vars so callers
    can fail fast (e.g. at startup); the default non-strict mode just logs
    a warning so tests and local dev can run without a full env.
    """
    required_vars = [
        "DATABASE_URL",
    ]

    missing_vars = [var for var in required_vars if not getattr(config, var, None)]

    if missing_vars:
        message = "Missing required environment variables: " + ", ".join(missing_vars)
        if strict:
            raise RuntimeError(message)
        logger.warning(message)
        return missing_vars

    return []


# Perform a non-strict validation at import to aid discoverability without breaking tests.
validate_required(strict=False)
logger.info("Configuration loaded successfully.")

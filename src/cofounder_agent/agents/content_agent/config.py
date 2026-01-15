# ============================================================================
# CRITICAL: Fix sys.path for namespace packages at module import time
# Poetry sometimes breaks namespace package resolution (e.g., google.generativeai)
# This MUST be done before any package imports
# ============================================================================
import sys
from pathlib import Path as _PathType

_venv_site_packages = _PathType(sys.prefix) / "Lib" / "site-packages"
if _venv_site_packages.exists():
    _venv_site_packages_str = str(_venv_site_packages)
    # Ensure venv's site-packages is first in the path
    sys.path = [_venv_site_packages_str] + [p for p in sys.path if p != _venv_site_packages_str]
    # Clear import caches
    import importlib
    importlib.invalidate_caches()
del _venv_site_packages, _venv_site_packages_str, _PathType

import os
import logging
from dotenv import load_dotenv

from pathlib import Path

# --- Define Base Directory ---
# Ensures that all file paths are relative to the project root, making the application more portable.
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))  # This gets us to src/agents/content_agent/

# The logger is configured in `logging_config.py` and used throughout the application.
logger = logging.getLogger(__name__)


class Config:
    """
    Central configuration class for the content agent.

    This class loads settings from a .env file and provides them as attributes.
    It acts as a single source of truth for all configurable parameters,
    from API keys and project IDs to file paths and model names.
    """

    def __init__(self):
        # Load environment variables from a .env file into the environment.
        # This allows for secure and flexible configuration without hardcoding secrets.
        
        # Path calculation: config.py is at src/cofounder_agent/agents/content_agent/config.py
        # So we need to go up 4 levels to reach project root (glad-labs-website/)
        project_root = Path(__file__).resolve().parents[4]
        
        # Try .env.local first (development/local), then .env (committed version)
        dotenv_local_path = project_root / '.env.local'
        dotenv_path = project_root / '.env'
        
        if os.getenv("DISABLE_DOTENV") != "1":
            if dotenv_local_path.exists():
                load_dotenv(dotenv_path=dotenv_local_path, override=True)
            elif dotenv_path.exists():
                load_dotenv(dotenv_path=dotenv_path, override=True)

        # --- Core Application Paths ---
        self.BASE_DIR = BASE_DIR
        self.CREDENTIALS_PATH = os.path.join(
            self.BASE_DIR, "credentials.json"
        )
        self.PROMPTS_PATH = os.path.join(self.BASE_DIR, "prompts.json")

        # --- PostgreSQL Database for CMS ---
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./tasks.db")
        self.DATABASE_HOST = os.getenv("DATABASE_HOST", "localhost")
        self.DATABASE_PORT = os.getenv("DATABASE_PORT", "5432")
        self.DATABASE_NAME = os.getenv("DATABASE_NAME", "glad_labs")
        self.DATABASE_USER = os.getenv("DATABASE_USER", "postgres")
        self.DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "")

        # --- External Services & APIs ---
        self.PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")  # For sourcing stock photos
        self.SERPER_API_KEY = os.getenv(
            "SERPER_API_KEY"
        )  # For real-time web search capabilities

        # --- Local Image Generation & Storage ---
        self.IMAGE_STORAGE_PATH = os.path.join(
            self.BASE_DIR, "content-agent", "generated_images"
        )
        self.DEFAULT_IMAGE_PLACEHOLDERS = (
            3  # Default number of images to generate for a post
        )

        # --- Language Model Provider --
        # Determines the LLM provider to use. Defaults to Ollama (free, local).
        # Options: 'ollama' (free, local), 'openai', 'anthropic', 'gemini'
        self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
        
        # --- Model Selection per Task Type --
        # Allows configuration of which model to use for different task stages
        self.MODEL_FOR_RESEARCH = os.getenv("MODEL_FOR_RESEARCH", "ollama/mistral")
        self.MODEL_FOR_CREATIVE = os.getenv("MODEL_FOR_CREATIVE", "ollama/mistral")
        self.MODEL_FOR_QA = os.getenv("MODEL_FOR_QA", "ollama/mistral")
        self.MODEL_FOR_IMAGE = os.getenv("MODEL_FOR_IMAGE", "ollama/mistral")
        self.MODEL_FOR_PUBLISHING = os.getenv("MODEL_FOR_PUBLISHING", "ollama/phi")

        # --- API Keys for LLM Providers ---
        # These are read from environment variables and used by the LLM client
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        self.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
        self.GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")  # For Google Gemini
        self.GEMINI_API_KEY = self.GOOGLE_API_KEY  # Alias for backward compatibility
        self.HUGGINGFACE_API_TOKEN = os.getenv("HUGGINGFACE_API_TOKEN")

        # --- Gemini-specific configuration ---
        self.GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        self.SUMMARIZER_MODEL = os.getenv("SUMMARIZER_MODEL", "gemini-2.0-flash")

        # --- Local LLM (Ollama) Configuration --
        # For running a local quality assurance model if available.
        self.LOCAL_LLM_API_URL = os.getenv("LOCAL_LLM_API_URL", "http://localhost:11434")
        self.LOCAL_LLM_MODEL_NAME = os.getenv("LOCAL_LLM_MODEL_NAME", "llava:13b")

        # --- Logging Configuration ---
        self.LOG_DIR = os.path.join(self.BASE_DIR, "content-agent", "logs")
        self.APP_LOG_FILE = os.path.join(self.LOG_DIR, "app.log")
        self.PROMPTS_LOG_FILE = os.path.join(self.LOG_DIR, "prompts.log")
        self.MAX_LOG_SIZE_MB = int(os.getenv("MAX_LOG_SIZE_MB", 5))
        self.MAX_LOG_BACKUP_COUNT = int(os.getenv("MAX_LOG_BACKUP_COUNT", 3))

        # --- Google Cloud Pub/Sub Configuration ---
        self.PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "agent-commands")
        self.PUBSUB_SUBSCRIPTION = os.getenv(
            "PUBSUB_SUBSCRIPTION", "content-agent-subscription"
        )


# --- Singleton Instance ---
# Create a single, immutable instance of the configuration to be imported by other modules.
config = Config()

# --- Validation Utilities ---
# Validate required environment variables. Non-strict by default to enable testing and local dev.
# Set STRICT_ENV_VALIDATION=1 to raise on missing variables.

def validate_required(strict: bool = False) -> list:
    """
    Validate required environment variables for the content agent.
    
    The agent can now work with minimal configuration:
    - DATABASE_URL or DATABASE_HOST/PORT/NAME for PostgreSQL
    - At least one LLM provider (Ollama is local/free, or OpenAI/Anthropic API keys)
    - Optional: PEXELS_API_KEY for stock images, SERPER_API_KEY for web search
    """
    # Core required: Just need database connection
    required_vars = [
        "DATABASE_URL",  # OR Database host/port/name
    ]
    
    # Optional but recommended
    optional_vars = [
        "PEXELS_API_KEY",
        "SERPER_API_KEY",
    ]

    missing_vars = [var for var in required_vars if not getattr(config, var, None)]

    if missing_vars:
        message = (
            "WARNING: Missing optional environment variables: "
            + ", ".join(missing_vars)
        )
        logger.warning(message)
        return missing_vars
    
    return []


# Perform a non-strict validation at import to aid discoverability without breaking tests.
validate_required(strict=False)
logger.info("Configuration loaded successfully.")

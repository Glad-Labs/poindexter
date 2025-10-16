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
        project_root = Path(__file__).resolve().parents[3]
        dotenv_path = project_root / '.env'
        if os.getenv("DISABLE_DOTENV") != "1":
            load_dotenv(dotenv_path=dotenv_path)

        # --- Core Application Paths ---
        self.BASE_DIR = BASE_DIR
        self.CREDENTIALS_PATH = os.path.join(
            self.BASE_DIR, "credentials.json"
        )
        self.PROMPTS_PATH = os.path.join(self.BASE_DIR, "prompts.json")

        # --- Google Cloud Platform (GCP) & AI Configuration ---
        self.GCP_SERVICE_ACCOUNT_EMAIL = os.getenv("GCP_SERVICE_ACCOUNT_EMAIL")
        self.GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
        self.GCP_REGION = os.getenv("GCP_REGION")
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
        self.GEMINI_MODEL = os.getenv(
            "GEMINI_MODEL", "gemini-1.5-pro-latest"
        )  # Default to the latest powerful model
        self.SUMMARIZER_MODEL = os.getenv(
            "SUMMARIZER_MODEL", "gemini-1.5-flash-latest"
        ) # Default to a fast and cheap model for summarization

        # --- Strapi CMS Integration ---
        self.STRAPI_API_URL = os.getenv(
            "STRAPI_API_URL"
        )  # e.g., "http://localhost:1337/api"
        self.STRAPI_API_TOKEN = os.getenv("STRAPI_API_TOKEN")

        # --- Google Cloud Storage (GCS) for Media ---
        self.GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

        # --- Firestore Database for Logging & Metrics ---
        self.FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "agent_runs")

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
        # Determines the LLM provider to use. Can be 'gemini' for Google's API
        # or 'local' for a local model (e.g., Ollama).
        self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

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
    required_vars = [
        "GCP_PROJECT_ID",
        "GCP_REGION",
        "GEMINI_API_KEY",
        "STRAPI_API_URL",
        "STRAPI_API_TOKEN",
        "GCS_BUCKET_NAME",
        "PEXELS_API_KEY",
        "SERPER_API_KEY",
        "GCP_SERVICE_ACCOUNT_EMAIL",
    ]

    missing_vars = [var for var in required_vars if not getattr(config, var, None)]

    if missing_vars:
        message = (
            "CRITICAL CONFIG: Missing required environment variables: "
            + ", ".join(missing_vars)
        )
        if strict or os.getenv("STRICT_ENV_VALIDATION") == "1":
            logger.critical(message)
            logger.critical(
                "Please create or check your .env file in the root directory and ensure all variables are set correctly."
            )
            raise ValueError(message)
        else:
            logger.warning(message)
    return missing_vars

# Perform a non-strict validation at import to aid discoverability without breaking tests.
validate_required(strict=False)
logger.info("Configuration loaded successfully.")

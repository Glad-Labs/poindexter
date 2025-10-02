import os
import logging
from dotenv import load_dotenv

# --- Define Base Directory ---
# This makes file paths relative to the config file's location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The logger will be configured by the main application entry point.
logger = logging.getLogger(__name__)

class Config:
    """
    Configuration class for the content agent.
    Loads environment variables and provides them as attributes.
    """
    def __init__(self):
        # Load environment variables from .env file
        load_dotenv()

        # --- Core Paths ---
        self.BASE_DIR = BASE_DIR
        self.CREDENTIALS_PATH = os.path.join(self.BASE_DIR, 'credentials.json')
        self.PROMPTS_PATH = os.path.join(self.BASE_DIR, 'prompts.json')

        # GCP & Gemini
        self.GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
        self.GCP_REGION = os.getenv("GCP_REGION")
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
        self.GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")

        # Google Sheets
        self.SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
        self.PLAN_SHEET_NAME = os.getenv("PLAN_SHEET_NAME", "Content Plan")
        self.LOG_SHEET_NAME = os.getenv("LOG_SHEET_NAME", "Generated Content Log")

        # Strapi
        self.STRAPI_API_URL = os.getenv("STRAPI_API_URL")
        self.STRAPI_API_TOKEN = os.getenv("STRAPI_API_TOKEN")

        # GCS
        self.GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

        # Firestore
        self.FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "agent_runs")

        # Image Services
        self.PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
        self.IMAGE_STORAGE_PATH = os.path.join(self.BASE_DIR, "generated_images")
        self.DEFAULT_IMAGE_PLACEHOLDERS = 3

        # Web Search
        self.SERPER_API_KEY = os.getenv("SERPER_API_KEY")
        
        # QA Model (Ollama)
        self.OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.QA_MODEL_NAME = os.getenv("QA_MODEL_NAME", "llava:13b")

        # --- Logging Configuration ---
        self.LOG_DIR = os.path.join(self.BASE_DIR, "logs")
        self.APP_LOG_FILE = os.path.join(self.LOG_DIR, "app.log")
        self.PROMPTS_LOG_FILE = os.path.join(self.LOG_DIR, "prompts.log")
        self.MAX_LOG_SIZE_MB = int(os.getenv("MAX_LOG_SIZE_MB", 5))
        self.MAX_LOG_BACKUP_COUNT = int(os.getenv("MAX_LOG_BACKUP_COUNT", 3))

# Instantiate the config
config = Config()

# --- Sanity Check for Required Variables ---
# This check is now more specific and will tell you exactly what's missing.
required_vars = [
    "GCP_PROJECT_ID", "GCP_REGION", "GEMINI_API_KEY", "SPREADSHEET_ID",
    "STRAPI_API_URL", "STRAPI_API_TOKEN", "GCS_BUCKET_NAME",
    "FIRESTORE_COLLECTION", "PEXELS_API_KEY", "SERPER_API_KEY"
]

missing_vars = [var for var in required_vars if not getattr(config, var, None)]

if missing_vars:
    error_message = f"CRITICAL: The following required environment variables are missing: {', '.join(missing_vars)}"
    logger.critical(error_message)
    logger.critical("Please check your .env file and ensure all variables are set correctly.")
    raise ValueError(error_message)

logger.info("Configuration loaded and validated successfully.")
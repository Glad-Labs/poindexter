import os
from dotenv import load_dotenv

# Load environment variables from a .env file for local development
load_dotenv()

class Config:
    """
    Centralized configuration class.
    Loads settings from environment variables for security and flexibility.
    """
    # Google Cloud Configuration
    GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")

    # Strapi CMS Configuration
    STRAPI_API_URL = os.getenv("STRAPI_API_URL")
    STRAPI_API_TOKEN = os.getenv("STRAPI_API_TOKEN")

    # Google Sheets API (if used)
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

    # --- Gemini API ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GCP_REGION = os.getenv("GCP_REGION", "us-central1")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # --- WordPress Configuration ---
    WP_URL = os.getenv("WP_URL")
    WP_USERNAME = os.getenv("WP_USERNAME")
    WP_PASSWORD = os.getenv("WP_PASSWORD")

    # --- Strapi Configuration ---
    STRAPI_API_URL = os.getenv("STRAPI_API_URL") # e.g., http://localhost:1337
    STRAPI_API_TOKEN = os.getenv("STRAPI_API_TOKEN")
    STRAPI_FRONTEND_URL = os.getenv("STRAPI_FRONTEND_URL", "http://localhost:3000") # ADD THIS

    # --- Google Cloud Storage ---
    GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

    # --- Firestore Configuration ---
    FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "agent_runs")

    # Email recipient for the 'Send as Email' feature
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

    # Pexels API for stock photos
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

    # Google Sheets configuration
    PLAN_SHEET_NAME = os.getenv("PLAN_SHEET_NAME", "Content Plan")
    LOG_SHEET_NAME = os.getenv("LOG_SHEET_NAME", "Generated Content Log")
    LOG_SHEET_HEADERS = [
        "Timestamp", "Topic", "Primary Keyword", "Target Audience", "Category",
        "Generated Title", "Status", "Rejection Reason", "Strapi URL", # FIX: Changed from "WordPress URL"
        "Sheet Row Index", "Refinement Loops", "Meta Description", "Related Keywords",
        "Social Media Posts (Twitter)", "Social Media Posts (Discord)",
        "Image 0 Source", "Image 0 Query", "Image 0 Alt Text", "Image 0 Caption", "Image 0 Description",
        "Image 1 Source", "Image 1 Query", "Image 1 Alt Text", "Image 1 Caption", "Image 1 Description",
        "Image 2 Source", "Image 2 Query", "Image 2 Alt Text", "Image 2 Caption", "Image 2 Description",
        "Image 3 Source", "Image 3 Query", "Image 3 Alt Text", "Image 3 Caption", "Image 3 Description",
        "Image 4 Source", "Image 4 Query", "Image 4 Alt Text", "Image 4 Caption", "Image 4 Description"
    ]

    # --- Local LLM (Ollama) ---
    LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "llava:13b")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # --- Paths ---
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PROMPTS_PATH = os.path.join(BASE_DIR, "prompts.json")
    TOKEN_PATH = os.path.join(BASE_DIR, "token.json") # ADD THIS
    CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json") # ADD THIS
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
    IMAGE_STORAGE_PATH = os.path.join(BASE_DIR, "generated_images") # RENAMED for clarity
    GENERATED_IMAGES_DIR = IMAGE_STORAGE_PATH # Add for backward compatibility if needed

    # --- Logging Configuration ---
    MAX_LOG_SIZE_MB = int(os.getenv("MAX_LOG_SIZE_MB", 5))
    MAX_LOG_BACKUP_COUNT = int(os.getenv("MAX_LOG_BACKUP_COUNT", 3))
    MAX_ARCHIVE_LOG_SIZE_MB = int(os.getenv("MAX_ARCHIVE_LOG_SIZE_MB", 10))
    MAX_ARCHIVE_LOG_BACKUP_COUNT = int(os.getenv("MAX_ARCHIVE_LOG_BACKUP_COUNT", 5))

    # --- Agent Configuration ---
    MAX_IMAGE_GEN_ATTEMPTS = int(os.getenv("MAX_IMAGE_GEN_ATTEMPTS", 3))
    MAX_IMAGE_METADATA_ATTEMPTS = int(os.getenv("MAX_IMAGE_METADATA_ATTEMPTS", 3))
    MAX_CONTENT_GEN_ATTEMPTS = int(os.getenv("MAX_CONTENT_GEN_ATTEMPTS", 3))
    DEFAULT_IMAGE_PLACEHOLDERS = int(os.getenv("DEFAULT_IMAGE_PLACEHOLDERS", 2))

    # --- Stable Diffusion Configuration ---
    SD_NEGATIVE_PROMPT = os.getenv("SD_NEGATIVE_PROMPT", 
        "blurry, bad anatomy, disfigured, poorly drawn face, mutation, deformed, extra limbs, ugly, "
        "text, watermark, signature, low quality, low resolution, bad art, poorly drawn, error, "
        "missing fingers, extra digit, fewer digits, cropped, jpeg artifacts, username, artist name, "
        "(worst quality, low quality:1.4), (bad anatomy), (bad hands), (bad eyes), (bad face)"
    )

# Create a single, importable instance of the configuration
config = Config()

# --- Sanity Checks ---
# Ensure that critical configuration is present at startup.
if not config.GCP_PROJECT_ID:
    raise ValueError("CRITICAL: GCP_PROJECT_ID is not set. Please define it in your .env file or environment.")
if not config.STRAPI_API_URL or not config.STRAPI_API_TOKEN:
    print("WARNING: Strapi API URL or Token is not set. The Strapi client will not function.")

print("Configuration loaded successfully.")
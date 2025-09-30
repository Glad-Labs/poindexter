import os
from dotenv import load_dotenv

# Load environment variables from a .env file for local development
load_dotenv()

class Config:
    """
    Centralized configuration class for the Glad Labs Content Agent.
    Loads all settings from environment variables for maximum security and flexibility.
    """
    # --- Core Identifiers ---
    GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
    GCP_REGION = os.getenv("GCP_REGION", "us-central1")

    # --- AI Model Configuration ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    
    # --- Service APIs & Keys ---
    STRAPI_API_URL = os.getenv("STRAPI_API_URL")
    STRAPI_API_TOKEN = os.getenv("STRAPI_API_TOKEN")
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
    
    # --- Google Cloud Services ---
    GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
    FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "agent_runs")

    # --- Input/Output Configuration (Google Sheets) ---
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
    PLAN_SHEET_NAME = os.getenv("PLAN_SHEET_NAME", "Content Plan")
    LOG_SHEET_NAME = os.getenv("LOG_SHEET_NAME", "Generated Content Log")

    # --- File Paths ---
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PROMPTS_PATH = os.path.join(BASE_DIR, "prompts.json")
    CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
    IMAGE_STORAGE_PATH = os.path.join(BASE_DIR, "generated_images")
    
    # --- Agent Behavior ---
    MAX_CONTENT_GEN_ATTEMPTS = int(os.getenv("MAX_CONTENT_GEN_ATTEMPTS", 3))
    DEFAULT_IMAGE_PLACEHOLDERS = int(os.getenv("DEFAULT_IMAGE_PLACEHOLDERS", 2))

# Create a single, importable instance of the configuration
config = Config()

# --- Sanity Checks ---
# Fail fast if critical configurations are missing.
if not all([config.GCP_PROJECT_ID, config.GEMINI_API_KEY, config.STRAPI_API_URL, 
            config.STRAPI_API_TOKEN, config.SPREADSHEET_ID, config.GCS_BUCKET_NAME,
            config.PEXELS_API_KEY]):
    raise ValueError("CRITICAL: One or more required environment variables are not set. Please check your .env file.")

print("✅ Configuration loaded and validated successfully.")
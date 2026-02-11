"""
Configuration Management for Glad Labs AI Co-Founder

This module provides centralized configuration loading and access for the entire application.
"""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# Track if environment has been loaded to avoid duplicate calls
_ENV_LOADED = False


@dataclass
class Config:
    """Application configuration dataclass."""
    # Database configuration
    database_url: str = ""
    
    # LLM API keys
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    ollama_base_url: Optional[str] = None
    
    # Environment settings
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"
    
    # Sentry configuration
    sentry_dsn: Optional[str] = None
    sentry_enabled: bool = True
    
    # Telemetry configuration
    enable_tracing: bool = False
    otlp_endpoint: str = "http://localhost:4318/v1/traces"
    
    # Application settings
    app_version: str = "3.0.1"
    secret_key: str = "your-secret-key-here"


def load_env() -> None:
    """Load environment variables from .env.local file (only once)."""
    global _ENV_LOADED
    
    # Skip if already loaded
    if _ENV_LOADED:
        return
    
    # Try to load .env.local from the project root
    # File location: src/cofounder_agent/config/__init__.py
    # Go up 3 levels: config/ → cofounder_agent/ → src/ → project_root/
    import logging
    logger = logging.getLogger(__name__)
    
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    env_local_path = os.path.join(project_root, ".env.local")
    
    if os.path.exists(env_local_path):
        load_dotenv(env_local_path, override=True)
        try:
            logger.info(f"[INFO] Loaded .env.local from: {env_local_path}")
        except UnicodeEncodeError:
            # Windows cp1252 encoding issue with emojis
            logger.info(f"[INFO] Loaded .env.local from: {env_local_path}")
    else:
        # Fallback: try current working directory
        current_dir_env = os.path.join(os.getcwd(), ".env.local")
        if os.path.exists(current_dir_env):
            load_dotenv(current_dir_env, override=True)
            try:
                logger.info(f"[INFO] Loaded .env.local from: {current_dir_env}")
            except UnicodeEncodeError:
                # Windows cp1252 encoding issue with emojis
                logger.info(f"[INFO] Loaded .env.local from: {current_dir_env}")
        else:
            logger.warning(f"[WARNING] .env.local not found at {env_local_path} or {current_dir_env}")
    
    _ENV_LOADED = True


def get_config() -> Config:
    """Get application configuration."""
    # Load environment variables if not already loaded
    load_env()
    
    return Config(
        database_url=os.getenv("DATABASE_URL", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL"),
        environment=os.getenv("ENVIRONMENT", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_format=os.getenv("LOG_FORMAT", "json" if os.getenv("ENVIRONMENT") == "production" else "text"),
        sentry_dsn=os.getenv("SENTRY_DSN"),
        sentry_enabled=os.getenv("SENTRY_ENABLED", "true").lower() in ("true", "1", "yes"),
        enable_tracing=os.getenv("ENABLE_TRACING", "false").lower() == "true",
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces"),
        app_version=os.getenv("APP_VERSION", "3.0.1"),
        secret_key=os.getenv("SECRET_KEY", "your-secret-key-here"),
    )
"""
Configuration Management for Poindexter (the AI cofounder pipeline).

This module provides centralized configuration loading and access for the entire application.
"""

import os
import secrets
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
    local_database_url: str | None = None

    # LLM configuration (Ollama-only policy)
    ollama_base_url: str | None = None

    # Environment settings
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"

    # Sentry configuration
    sentry_dsn: str | None = None
    sentry_enabled: bool = True

    # Telemetry configuration
    enable_tracing: bool = False
    otlp_endpoint: str = "http://localhost:4318/v1/traces"

    # Application settings — version is read from APP_VERSION env var or pyproject.toml
    app_version: str = "0.0.0"
    secret_key: str = "your-secret-key-here"


def load_env() -> None:
    """Load environment variables from .env.local file (only once)."""
    global _ENV_LOADED

    # Skip if already loaded
    if _ENV_LOADED:
        return

    # Load environment from the project root .env file.
    # File location: src/cofounder_agent/config/__init__.py
    # Go up 3 levels: config/ → cofounder_agent/ → src/ → project_root/
    import logging

    logger = logging.getLogger(__name__)

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))

    # Priority: .env (canonical) > .env.local (legacy/override)
    # override=False means already-set env vars win (e.g. from Docker)
    loaded = False
    for env_name in [".env", ".env.local"]:
        env_path = os.path.join(project_root, env_name)
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
            try:
                logger.info(f"[INFO] Loaded {env_name} from: {env_path}")
            except UnicodeEncodeError:
                logger.info(f"[INFO] Loaded {env_name} from: {env_path}")
            loaded = True
            break  # Use the first one found

    if not loaded:
        # Fallback: try current working directory
        for env_name in [".env", ".env.local"]:
            current_dir_env = os.path.join(os.getcwd(), env_name)
            if os.path.exists(current_dir_env):
                load_dotenv(current_dir_env, override=False)
                try:
                    logger.info(f"[INFO] Loaded {env_name} from: {current_dir_env}")
                except UnicodeEncodeError:
                    logger.info(f"[INFO] Loaded {env_name} from: {current_dir_env}")
                loaded = True
                break

    if not loaded:
        logger.warning(
            f"[WARNING] No .env file found at {project_root} or {os.getcwd()}"
            )

    _ENV_LOADED = True


_PLACEHOLDER_SECRET = "your-secret-key-here"

# Auto-generated secrets cache — stable for the lifetime of the process
_AUTO_SECRETS: dict = {}


def _auto_secret(var_name: str, default_value: str) -> str:
    """Return the env var value, or auto-generate a strong secret if missing/placeholder.

    Generated secrets are cached in-process so they stay stable across calls.
    They are also written back to os.environ so downstream code (AuthConfig,
    startup_manager) sees a real value.
    """
    value = os.getenv(var_name, "")
    if value and value != default_value:
        return value
    # Generate once per var per process
    if var_name not in _AUTO_SECRETS:
        _AUTO_SECRETS[var_name] = secrets.token_urlsafe(48)
    generated = _AUTO_SECRETS[var_name]
    os.environ[var_name] = generated
    return generated


def _read_pyproject_version() -> str:
    """Read version from pyproject.toml as fallback when APP_VERSION env var is not set."""
    try:
        # In Docker: /app/pyproject.toml, locally: src/cofounder_agent/pyproject.toml
        config_dir = os.path.dirname(os.path.abspath(__file__))
        pyproject_path = os.path.join(config_dir, "..", "pyproject.toml")
        if os.path.exists(pyproject_path):
            with open(pyproject_path) as f:
                for line in f:
                    if line.strip().startswith("version"):
                        # Parse: version = "3.0.55"
                        return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "0.0.0"


def get_config() -> Config:
    """Get application configuration."""
    # Load environment variables if not already loaded
    load_env()

    environment = os.getenv("ENVIRONMENT", "development")

    # Auto-generate secrets if missing or placeholder.
    # These are temporary — once the DB is available during lifespan startup,
    # secrets are read from app_settings (persisted) and written back to os.environ.
    # On first run, auto-generated values are saved to DB for future restarts.
    secret_key = _auto_secret("SECRET_KEY", _PLACEHOLDER_SECRET)
    _auto_secret("JWT_SECRET_KEY", "development-secret-key-change-in-production")
    _auto_secret("JWT_SECRET", "development-secret-key-change-in-production")
    _auto_secret("REVALIDATE_SECRET", "dev-secret-key")

    if _AUTO_SECRETS:
        import logging

        logging.getLogger(__name__).info(
            "[Config] Temporary secrets generated for: %s. "
            "Will load persisted values from DB once connected.",
            ", ".join(_AUTO_SECRETS.keys()),
        )

    return Config(
        database_url=os.getenv("DATABASE_URL", ""),
        local_database_url=os.getenv("LOCAL_DATABASE_URL") or None,
        ollama_base_url=os.getenv("OLLAMA_BASE_URL"),
        environment=environment,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_format=os.getenv("LOG_FORMAT", "json" if environment == "production" else "text"),
        sentry_dsn=os.getenv("SENTRY_DSN"),
        sentry_enabled=os.getenv("SENTRY_ENABLED", "true").lower() in ("true", "1", "yes"),
        enable_tracing=os.getenv("ENABLE_TRACING", "false").lower() == "true",
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces"),
        app_version=os.getenv("APP_VERSION", _read_pyproject_version()),
        secret_key=secret_key,
    )

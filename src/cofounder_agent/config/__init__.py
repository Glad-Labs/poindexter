"""
Configuration Management for Poindexter (the AI cofounder pipeline).

DB connection is resolved via :mod:`brain.bootstrap` — the same priority
chain used everywhere else in the codebase:

  1. Explicit value passed by the caller
  2. ``~/.poindexter/bootstrap.toml``
  3. ``DATABASE_URL`` / ``LOCAL_DATABASE_URL`` / ``POINDEXTER_MEMORY_DSN`` env

All other settings are read straight from the process environment — no
``.env`` / ``.env.local`` ``dotenv`` loader, no hidden config files. Docker
compose / systemd / shell ``export`` are the expected injection paths. The
runtime settings that used to live in ``.env`` (prompt templates,
thresholds, per-model routing, etc.) live in the ``app_settings`` DB table
and are read via :mod:`services.site_config` after the pool is up.
"""

import os
import secrets
from dataclasses import dataclass


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
    otlp_endpoint: str = ""  # empty = tracing disabled (#198)

    # Application settings — version is read from APP_VERSION env var or pyproject.toml
    app_version: str = "0.0.0"
    secret_key: str = "your-secret-key-here"


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
    """Get application configuration.

    DB URL is resolved via brain.bootstrap (explicit arg > bootstrap.toml >
    env vars); everything else reads straight from the process environment.
    """
    from brain.bootstrap import resolve_database_url

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
        database_url=resolve_database_url() or "",
        local_database_url=os.getenv("LOCAL_DATABASE_URL") or None,
        ollama_base_url=os.getenv("OLLAMA_BASE_URL"),
        environment=environment,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_format=os.getenv("LOG_FORMAT", "json" if environment == "production" else "text"),
        sentry_dsn=os.getenv("SENTRY_DSN"),
        sentry_enabled=os.getenv("SENTRY_ENABLED", "true").lower() in ("true", "1", "yes"),
        enable_tracing=os.getenv("ENABLE_TRACING", "false").lower() == "true",
        # #198: empty default means "tracing not configured" — the tracing
        # init code skips OTEL export in that case. No hardcoded localhost.
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        app_version=os.getenv("APP_VERSION", _read_pyproject_version()),
        secret_key=secret_key,
    )

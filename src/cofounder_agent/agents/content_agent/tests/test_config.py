import os
import importlib
import pytest


def test_config_non_strict_loads(monkeypatch):
    # Ensure STRICT_ENV_VALIDATION is not set
    monkeypatch.delenv("STRICT_ENV_VALIDATION", raising=False)

    # Remove expected required envs to simulate missing
    for key in [
        "GCP_PROJECT_ID",
        "GCP_REGION",
        "GEMINI_API_KEY",
        "STRAPI_API_URL",
        "STRAPI_API_TOKEN",
        "GCS_BUCKET_NAME",
        "PEXELS_API_KEY",
        "SERPER_API_KEY",
        "GCP_SERVICE_ACCOUNT_EMAIL",
    ]:
        monkeypatch.delenv(key, raising=False)

    # Reload module to re-run validation with current env
    import sys as _sys

    if "agents.content_agent.config" in list(_sys.modules.keys()):
        importlib.invalidate_caches()
        importlib.reload(importlib.import_module("config"))
    else:
        importlib.import_module("config")

    from config import validate_required

    missing = validate_required(strict=False)
    # Non-strict mode should not raise and returns a list (possibly empty) of missing
    assert isinstance(missing, list)


def test_config_strict_raises(monkeypatch):
    # Force strict mode and verify validate_required raises with missing values
    monkeypatch.setenv("STRICT_ENV_VALIDATION", "1")

    import sys as _sys

    if "config" in list(_sys.modules.keys()):
        del _sys.modules["config"]
    cfg_module = importlib.import_module("config")

    class Dummy:
        GCP_PROJECT_ID = None
        GCP_REGION = None
        GEMINI_API_KEY = None
        STRAPI_API_URL = None
        STRAPI_API_TOKEN = None
        GCS_BUCKET_NAME = None
        PEXELS_API_KEY = None
        SERPER_API_KEY = None
        GCP_SERVICE_ACCOUNT_EMAIL = None

    # Monkeypatch the config object to the dummy (avoids static analysis complaints)
    monkeypatch.setattr(cfg_module, "config", Dummy(), raising=False)
    with pytest.raises(ValueError):
        cfg_module.validate_required(strict=True)

    # Reset strict mode
    monkeypatch.delenv("STRICT_ENV_VALIDATION", raising=False)

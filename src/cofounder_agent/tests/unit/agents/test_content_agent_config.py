"""
Unit tests for agents/content_agent/config.py

Config resolves DATABASE_URL via brain.bootstrap.resolve_database_url()
(bootstrap.toml + env var priority chain). These tests patch the
environment directly since resolve_database_url() consults
DATABASE_URL / LOCAL_DATABASE_URL / POINDEXTER_MEMORY_DSN when
bootstrap.toml is absent, which matches the default test environment.
"""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _ignore_bootstrap_toml():
    """Stub out ~/.poindexter/bootstrap.toml so every test reads env only.

    resolve_database_url() consults bootstrap.toml BEFORE env vars. On a
    developer machine that file holds a real DSN, which would mask the
    per-test env setup and produce "Config returned my local DB, not the
    test DB" failures. Forcing an empty dict here makes resolve_database_url
    fall through to the patched env every time.
    """
    with patch("brain.bootstrap._read_bootstrap_toml", return_value={}):
        yield

# ---------------------------------------------------------------------------
# Config class tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_database_url_stored(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
        ):
            # Re-import to get a fresh Config with our env vars
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DATABASE_URL == "postgresql://user:pass@localhost/test"

    def test_raises_when_no_database_url(self):
        # Strip every env var that brain.bootstrap would fall back to, and
        # ensure there's no ~/.poindexter/bootstrap.toml leaking in from
        # a developer machine by patching bootstrap_file_exists to False.
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DATABASE_URL", "LOCAL_DATABASE_URL", "POINDEXTER_MEMORY_DSN"}
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "brain.bootstrap._read_bootstrap_toml", return_value={}
        ):
            from agents.content_agent.config import Config

            with pytest.raises(ValueError, match="DATABASE_URL"):
                Config()

    def test_default_llm_provider(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.LLM_PROVIDER == "ollama"

    def test_local_llm_provider_accepted(self):
        # 'local' is the only non-ollama value LLMClient accepts. Paid-API
        # values like 'anthropic' / 'openai' / 'gemini' were removed in v2.8.
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://user:pass@localhost/test",
                "LLM_PROVIDER": "local",
            },
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.LLM_PROVIDER == "local"

    def test_database_host_default(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DATABASE_HOST == "localhost"

    def test_database_port_default(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DATABASE_PORT == "5432"

    def test_default_image_placeholders(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DEFAULT_IMAGE_PLACEHOLDERS == 3

    def test_base_dir_is_string(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert isinstance(cfg.BASE_DIR, str)
        assert len(cfg.BASE_DIR) > 0

    def test_max_log_size_mb_int(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert isinstance(cfg.MAX_LOG_SIZE_MB, int)


# ---------------------------------------------------------------------------
# validate_required
# ---------------------------------------------------------------------------


class TestValidateRequired:
    def test_returns_empty_when_database_url_set(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            # Temporarily replace global config with our fresh one
            import agents.content_agent.config as config_module
            from agents.content_agent.config import Config, validate_required

            original = config_module.config
            config_module.config = Config()
            try:
                result = validate_required(strict=False)
            finally:
                config_module.config = original

        assert result == []

    def test_returns_list_type(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test"},
            clear=False,
        ):
            import agents.content_agent.config as config_module
            from agents.content_agent.config import Config, validate_required

            original = config_module.config
            config_module.config = Config()
            try:
                result = validate_required()
            finally:
                config_module.config = original

        assert isinstance(result, list)

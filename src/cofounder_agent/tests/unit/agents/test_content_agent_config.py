"""
Unit tests for agents/content_agent/config.py

Tests for Config class and validate_required helper.
The module creates a singleton at import time, so we mock DATABASE_URL
via environment and DISABLE_DOTENV=1 to avoid loading .env.local.
"""

import os
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Config class tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_database_url_stored(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
        ):
            # Re-import to get a fresh Config with our env vars
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DATABASE_URL == "postgresql://user:pass@localhost/test"

    def test_raises_when_no_database_url(self):
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        env["DISABLE_DOTENV"] = "1"
        with patch.dict("os.environ", env, clear=True):
            from agents.content_agent.config import Config

            with pytest.raises(ValueError, match="DATABASE_URL"):
                Config()

    def test_default_llm_provider(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.LLM_PROVIDER == "ollama"

    def test_custom_llm_provider(self):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://user:pass@localhost/test",
                "DISABLE_DOTENV": "1",
                "LLM_PROVIDER": "anthropic",
            },
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.LLM_PROVIDER == "anthropic"

    def test_database_host_default(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DATABASE_HOST == "localhost"

    def test_database_port_default(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DATABASE_PORT == "5432"

    def test_default_image_placeholders(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.DEFAULT_IMAGE_PLACEHOLDERS == 3

    def test_model_defaults_for_research(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert "ollama" in cfg.MODEL_FOR_RESEARCH

    def test_base_dir_is_string(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert isinstance(cfg.BASE_DIR, str)
        assert len(cfg.BASE_DIR) > 0

    def test_gemini_api_key_alias(self):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://user:pass@localhost/test",
                "DISABLE_DOTENV": "1",
                "GOOGLE_API_KEY": "test-google-key",
            },
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.GEMINI_API_KEY == "test-google-key"
        assert cfg.GOOGLE_API_KEY == "test-google-key"

    def test_max_log_size_mb_int(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert isinstance(cfg.MAX_LOG_SIZE_MB, int)

    def test_pubsub_topic_default(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config

            cfg = Config()
        assert cfg.PUBSUB_TOPIC == "agent-commands"


# ---------------------------------------------------------------------------
# validate_required
# ---------------------------------------------------------------------------


class TestValidateRequired:
    def test_returns_empty_when_database_url_set(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config, validate_required

            # Temporarily replace global config with our fresh one
            import agents.content_agent.config as config_module

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
            {"DATABASE_URL": "postgresql://user:pass@localhost/test", "DISABLE_DOTENV": "1"},
            clear=False,
        ):
            from agents.content_agent.config import Config, validate_required
            import agents.content_agent.config as config_module

            original = config_module.config
            config_module.config = Config()
            try:
                result = validate_required()
            finally:
                config_module.config = original

        assert isinstance(result, list)

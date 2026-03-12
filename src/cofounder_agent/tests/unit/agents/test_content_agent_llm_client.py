"""
Unit tests for agents/content_agent/services/llm_client.py — LLMClient

Tests focus on (all network calls mocked):
- _get_cache_path(): hash-based path generation
- generate_json(): cache hit path, ollama path, gemini path, error handling
- generate_text(): cache hit path, ollama path, error handling
- generate_summary(): ollama/local fallback path, cache hit
- Provider routing: "local" and "ollama" treated identically
- Initialization: unsupported provider raises ValueError
"""

import hashlib
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")


# ---------------------------------------------------------------------------
# Helpers — build LLMClient bypassing real I/O
# ---------------------------------------------------------------------------


def _make_client(provider: str = "ollama", tmp_dir: str | None = None):
    """Build an LLMClient with mocked config and no real filesystem access."""
    with (
        patch("agents.content_agent.services.llm_client.config") as mock_cfg,
        patch("agents.content_agent.services.llm_client.genai", None),
        patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
    ):
        mock_cfg.LLM_PROVIDER = provider
        mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
        mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
        mock_cfg.GEMINI_API_KEY = "test-gemini-key"
        mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
        mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"
        # Use tmp_dir or a fake path for cache
        cache_base = tmp_dir or "/tmp/test_cache"
        mock_cfg.BASE_DIR = cache_base

        from agents.content_agent.services.llm_client import LLMClient

        with patch.object(Path, "mkdir"):
            client = LLMClient()
            # Override cache_dir to a real temp dir if given, else mock it
            if tmp_dir:
                client.cache_dir = Path(tmp_dir)
            else:
                client.cache_dir = MagicMock(spec=Path)
                client.cache_dir.__truediv__ = lambda self, other: MagicMock(spec=Path, exists=MagicMock(return_value=False))

    return client


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestLLMClientInit:
    def test_ollama_provider_initializes_without_error(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            assert client.provider in ("ollama", "local")

    def test_local_provider_treated_same_as_ollama(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "local"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            assert client.provider == "local"

    def test_unsupported_provider_raises_value_error(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "unsupported_llm"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            with pytest.raises(ValueError, match="Unsupported LLM provider"):
                LLMClient()

    def test_gemini_falls_back_to_ollama_when_genai_unavailable(self, tmp_path):
        """When genai is None and provider is 'gemini', silently falls back to ollama."""
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "gemini"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = "test-key"
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            # Falls back to ollama since genai module is None
            assert client.provider == "ollama"


# ---------------------------------------------------------------------------
# _get_cache_path
# ---------------------------------------------------------------------------


class TestGetCachePath:
    def test_returns_path_based_on_prompt_hash(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            prompt = "Write a blog post about AI"
            path = client._get_cache_path(prompt, "txt")

        expected_hash = hashlib.sha256(prompt.encode()).hexdigest()
        assert expected_hash in str(path)
        assert str(path).endswith(".txt.cache")

    def test_different_prompts_produce_different_paths(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            path1 = client._get_cache_path("prompt one", "txt")
            path2 = client._get_cache_path("prompt two", "txt")

        assert path1 != path2

    def test_same_prompt_different_format_produces_different_paths(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            prompt = "same prompt"
            path_txt = client._get_cache_path(prompt, "txt")
            path_json = client._get_cache_path(prompt, "json")

        assert path_txt != path_json


# ---------------------------------------------------------------------------
# generate_json — ollama/local path (mocked HTTP)
# ---------------------------------------------------------------------------


class TestGenerateJsonOllama:
    def _build_http_response(self, response_text: str):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": response_text}
        return mock_resp

    @pytest.mark.asyncio
    async def test_returns_parsed_dict_from_ollama(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        json_payload = '{"approved": true, "score": 85}'
        http_resp = self._build_http_response(json_payload)

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=http_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.generate_json("Test JSON prompt")

        assert result == {"approved": True, "score": 85}

    @pytest.mark.asyncio
    async def test_extracts_json_from_markdown_fenced_response(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        fenced = '```json\n{"key": "value"}\n```'
        http_resp = self._build_http_response(fenced)

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=http_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.generate_json("Prompt")

        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_raises_value_error_on_non_json_response(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        http_resp = self._build_http_response("This is not JSON at all")

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=http_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(ValueError, match="not valid JSON"):
                await client.generate_json("Prompt")

    @pytest.mark.asyncio
    async def test_raises_value_error_when_response_key_missing(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        # Ollama returns JSON without 'response' key
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"wrong_key": "data"}

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(ValueError, match="No 'response' key"):
                await client.generate_json("Prompt")

    @pytest.mark.asyncio
    async def test_returns_cached_json_without_http_call(self, tmp_path):
        """If cache file exists, result is returned without hitting the network."""
        import aiofiles

        cached_data = {"cached": True, "value": 42}

        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        # Write a cache file at the expected path
        prompt = "cached prompt"
        cache_path = client._get_cache_path(prompt, "json")
        cache_path.write_text(json.dumps(cached_data), encoding="utf-8")

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            result = await client.generate_json(prompt)
            mock_cls.assert_not_called()

        assert result == cached_data


# ---------------------------------------------------------------------------
# generate_text — ollama/local path (mocked HTTP)
# ---------------------------------------------------------------------------


class TestGenerateTextOllama:
    @pytest.mark.asyncio
    async def test_returns_text_from_ollama(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "# Generated blog content\n\nHello world."}

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.generate_text("Write me something")

        assert result == "# Generated blog content\n\nHello world."

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_http_error(self, tmp_path):
        import httpx

        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=httpx.HTTPError("connection refused"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.generate_text("Some prompt")

        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_cached_text_without_http_call(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        prompt = "cached text prompt"
        cache_path = client._get_cache_path(prompt, "txt")
        cache_path.write_text("Cached blog post content", encoding="utf-8")

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            result = await client.generate_text(prompt)
            mock_cls.assert_not_called()

        assert result == "Cached blog post content"

    @pytest.mark.asyncio
    async def test_caches_result_after_successful_generation(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        prompt = "new uncached prompt"
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "Fresh content from LLM"}

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.generate_text(prompt)

        assert result == "Fresh content from LLM"
        cache_path = client._get_cache_path(prompt, "txt")
        assert cache_path.exists()
        assert cache_path.read_text(encoding="utf-8") == "Fresh content from LLM"


# ---------------------------------------------------------------------------
# generate_summary — local/ollama fallback
# ---------------------------------------------------------------------------


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_ollama_provider_falls_back_to_text_generation(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "This is the summary."}

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.generate_summary("Summarize this long text")

        assert result == "This is the summary."

    @pytest.mark.asyncio
    async def test_returns_cached_summary_without_http_call(self, tmp_path):
        with (
            patch("agents.content_agent.services.llm_client.config") as mock_cfg,
            patch("agents.content_agent.services.llm_client.genai", None),
            patch("agents.content_agent.services.llm_client._fix_sys_path_for_venv"),
        ):
            mock_cfg.LLM_PROVIDER = "ollama"
            mock_cfg.BASE_DIR = str(tmp_path)
            mock_cfg.LOCAL_LLM_API_URL = "http://localhost:11434"
            mock_cfg.LOCAL_LLM_MODEL_NAME = "test-model"
            mock_cfg.GEMINI_API_KEY = None
            mock_cfg.GEMINI_MODEL = "gemini-2.0-flash"
            mock_cfg.SUMMARIZER_MODEL = "gemini-2.0-flash"

            from agents.content_agent.services.llm_client import LLMClient

            client = LLMClient()
            client.cache_dir = tmp_path

        prompt = "my summary prompt"
        cache_path = client._get_cache_path(prompt, "summary.txt")
        cache_path.write_text("Cached summary result")

        with patch("agents.content_agent.services.llm_client.httpx.AsyncClient") as mock_cls:
            result = await client.generate_summary(prompt)
            mock_cls.assert_not_called()

        assert result == "Cached summary result"

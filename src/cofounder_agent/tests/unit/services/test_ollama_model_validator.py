"""Unit tests for StartupManager._validate_ollama_model_settings().

Covers the five scenarios called out in glad-labs-stack#1284:
  1. All models installed  -> no notification sent
  2. One model missing     -> notify_operator called, message names the model
  3. Suspect template      -> notify_operator called, message names the model
  4. Ollama unreachable    -> warning logged, notify_operator called, no hard-fail
  5. Validation disabled   -> no checks run at all

All Ollama HTTP calls are mocked via unittest.mock so the tests are
fully offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.startup_manager import StartupManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_TEMPLATE = "{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}"
_SUSPECT_TEMPLATE = "{{ .Input }}<|turn>assistant<turn|>{{ .Response }}"
_ESTABLISHED_TEMPLATE = "<start_of_turn>user\n{{ .Input }}<end_of_turn>"


def _make_site_config(overrides: dict[str, str] | None = None) -> MagicMock:
    """Build a minimal SiteConfig mock."""
    defaults = {
        "ollama_model_validation_enabled": "true",
        "ollama_base_url": "http://localhost:11434",
    }
    if overrides:
        defaults.update(overrides)

    sc = MagicMock()
    sc.get = lambda key, default="": defaults.get(key, default)
    return sc


def _make_pool_rows(rows: list[dict]):
    """Return (pool, conn) with rows as Record-like objects."""
    records = []
    for row in rows:
        rec = MagicMock()
        rec.__getitem__ = lambda self, k, _row=row: _row[k]
        records.append(rec)

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=records)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def _make_http_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()  # no-op
    return resp


def _make_manager(site_config=None) -> StartupManager:
    sc = site_config if site_config is not None else _make_site_config()
    return StartupManager(site_config=sc)


async def _run_validator(
    model_rows: list[dict],
    tags_data: dict,
    show_data: dict | None = None,
    site_config_overrides: dict | None = None,
    ollama_raises: Exception | None = None,
) -> AsyncMock:
    """Run the validator and return the notify_operator mock."""
    pool, _ = _make_pool_rows(model_rows)
    sc = _make_site_config(site_config_overrides)
    manager = _make_manager(sc)

    notify_mock = AsyncMock()

    tags_resp = _make_http_response(tags_data)
    show_resp = _make_http_response(show_data or {})

    async def fake_get(url, **kw):
        if ollama_raises is not None:
            raise ollama_raises
        return tags_resp

    async def fake_post(url, **kw):
        return show_resp

    mock_client_instance = AsyncMock()
    mock_client_instance.get = fake_get
    mock_client_instance.post = fake_post

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_httpx_cls = MagicMock(return_value=mock_cm)

    import httpx as _real_httpx
    original_async_client = _real_httpx.AsyncClient
    try:
        _real_httpx.AsyncClient = mock_httpx_cls  # type: ignore[assignment]
        with (
            patch("services.integrations.operator_notify.http_client", None),
            patch(
                "services.integrations.operator_notify.notify_operator",
                new=notify_mock,
            ),
        ):
            await manager._validate_ollama_model_settings(pool)
    finally:
        _real_httpx.AsyncClient = original_async_client  # type: ignore[assignment]

    return notify_mock


# ---------------------------------------------------------------------------
# Test 1: All installed -- no notify
# ---------------------------------------------------------------------------


class TestAllModelsOk:
    @pytest.mark.asyncio
    async def test_all_installed_no_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"},
                {"key": "cost_tier.standard.model", "value": "ollama/gemma3:27b"},
            ],
            tags_data={"models": [{"name": "gemma3:27b"}]},
            show_data={"template": _ESTABLISHED_TEMPLATE},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ollama_models_skipped(self):
        """openai/ prefixed models are skipped entirely -- no missing warning."""
        notify = await _run_validator(
            model_rows=[
                {"key": "some_model", "value": "openai/gpt-4o"},
            ],
            tags_data={"models": []},  # empty -- would fail if openai model checked
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_model_rows_no_notify(self):
        """No configured model keys means nothing to validate."""
        notify = await _run_validator(
            model_rows=[],
            tags_data={"models": [{"name": "gemma3:27b"}]},
        )
        notify.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Missing model -- notify called with model name
# ---------------------------------------------------------------------------


class TestMissingModel:
    @pytest.mark.asyncio
    async def test_missing_model_triggers_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/missing-model:latest"},
            ],
            tags_data={"models": [{"name": "other-model:latest"}]},
        )
        notify.assert_called_once()
        call_args = notify.call_args[0][0]  # positional first arg is the message
        assert "missing-model:latest" in call_args

    @pytest.mark.asyncio
    async def test_missing_model_message_contains_model_name(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma-4-31B-it-qat:latest"},
            ],
            tags_data={"models": []},
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "gemma-4-31B-it-qat:latest" in msg

    @pytest.mark.asyncio
    async def test_ollama_prefix_stripped_before_lookup(self):
        """'ollama/gemma3:27b' should match the installed model 'gemma3:27b'."""
        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"},
            ],
            tags_data={"models": [{"name": "gemma3:27b"}]},
            show_data={"template": _ESTABLISHED_TEMPLATE},
        )
        notify.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Suspect template -- notify called
# ---------------------------------------------------------------------------


class TestSuspectTemplate:
    @pytest.mark.asyncio
    async def test_suspect_template_triggers_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/bad-template:latest"},
            ],
            tags_data={"models": [{"name": "bad-template:latest"}]},
            show_data={"template": _SUSPECT_TEMPLATE},
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "bad-template" in msg

    @pytest.mark.asyncio
    async def test_good_template_no_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/good-model:latest"},
            ],
            tags_data={"models": [{"name": "good-model:latest"}]},
            show_data={"template": _ESTABLISHED_TEMPLATE},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_suspect_tokens_with_established_delimiter_ok(self):
        """A template with <|turn> AND <start_of_turn> is not flagged."""
        combined = "<start_of_turn>user\n{{ .Input }}<|turn>end"
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/combo-model:latest"},
            ],
            tags_data={"models": [{"name": "combo-model:latest"}]},
            show_data={"template": combined},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_im_start_delimiter_not_flagged(self):
        """<|im_start|> is an established delimiter; <|turn> alongside it is OK."""
        template_with_im_start = "<|im_start|>user\n{{ .Input }}<|turn>foo"
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/im-start-model:latest"},
            ],
            tags_data={"models": [{"name": "im-start-model:latest"}]},
            show_data={"template": template_with_im_start},
        )
        notify.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Ollama unreachable -- warning logged, notify called, no hard-fail
# ---------------------------------------------------------------------------


class TestOllamaUnreachable:
    @pytest.mark.asyncio
    async def test_connection_error_does_not_raise(self):
        """A ConnectionError from Ollama must NOT propagate -- only warns + notifies."""
        import httpx

        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"},
            ],
            tags_data={},  # not reached
            ollama_raises=httpx.ConnectError("Connection refused"),
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "unreachable" in msg.lower() or "cannot validate" in msg.lower()

    @pytest.mark.asyncio
    async def test_timeout_error_does_not_raise(self):
        import httpx

        notify = await _run_validator(
            model_rows=[{"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"}],
            tags_data={},
            ollama_raises=httpx.TimeoutException("timed out"),
        )
        notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_generic_exception_does_not_raise(self):
        notify = await _run_validator(
            model_rows=[{"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"}],
            tags_data={},
            ollama_raises=OSError("network failure"),
        )
        notify.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: Validation disabled -- no checks run at all
# ---------------------------------------------------------------------------


class TestValidationDisabled:
    @pytest.mark.asyncio
    async def test_disabled_setting_skips_all_checks(self):
        pool, conn = _make_pool_rows([])
        sc = _make_site_config({"ollama_model_validation_enabled": "false"})
        manager = _make_manager(sc)

        notify_mock = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            await manager._validate_ollama_model_settings(pool)

        # DB was never queried (validation exited early)
        conn.fetch.assert_not_called()
        notify_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_via_zero_value(self):
        pool, conn = _make_pool_rows([])
        sc = _make_site_config({"ollama_model_validation_enabled": "0"})
        manager = _make_manager(sc)

        notify_mock = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            await manager._validate_ollama_model_settings(pool)

        conn.fetch.assert_not_called()
        notify_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_site_config_skips_silently(self):
        """Without a SiteConfig, the validator returns early without error."""
        pool, conn = _make_pool_rows([])
        manager = StartupManager(site_config=None)

        notify_mock = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            await manager._validate_ollama_model_settings(pool)

        conn.fetch.assert_not_called()
        notify_mock.assert_not_called()

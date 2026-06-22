"""Tests for ``services.llm_providers.ollama_unload``.

Pins the VRAM-guard contract introduced for the 2026-05-19 jank-audit
finding #4 (writer LLM + SDXL Lightning hitting 98% VRAM at the
stage-5→stage-7 transition):

* ``unload_loaded_ollama_models`` enumerates ``/api/ps``, issues
  ``POST /api/generate`` with ``keep_alive: 0`` for each loaded model,
  then sleeps ``grace_seconds``.
* ``maybe_unload_writer_before_sdxl`` honours the
  ``pipeline_explicit_writer_unload_before_sdxl`` bool gate (default
  on) and the ``pipeline_writer_unload_grace_seconds`` int (default 2).
* When Ollama is unreachable, a WARNING is logged and the helper
  returns ``[]`` without raising (``feedback_no_silent_defaults`` —
  loud, but not pipeline-fatal: SDXL still runs, just on a tighter
  VRAM budget).
* When the gate is ``false``, no HTTP traffic happens at all — operators
  on 80+ GB hardware can opt out of the reload tax.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.llm_providers.ollama_unload import (
    maybe_unload_writer_before_sdxl,
    unload_loaded_ollama_models,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _site_config(
    *,
    unload_enabled: bool = True,
    grace_seconds: int = 2,
    confirm_enabled: bool = True,
    confirm_timeout: int = 15,
    poll_interval: float = 0.5,
    base_url: str = "http://host.docker.internal:11434",
) -> Any:
    """Minimal SiteConfig stand-in matching the DI seam used in stages."""
    return SimpleNamespace(
        get=lambda key, default="": {
            "ollama_base_url": base_url,
        }.get(key, default),
        get_int=lambda key, default=0: {
            "pipeline_writer_unload_grace_seconds": grace_seconds,
            "pipeline_writer_unload_confirm_timeout_seconds": confirm_timeout,
        }.get(key, default),
        get_bool=lambda key, default=False: {
            "pipeline_explicit_writer_unload_before_sdxl": unload_enabled,
            "pipeline_writer_unload_confirm_enabled": confirm_enabled,
        }.get(key, default),
        get_float=lambda key, default=0.0: {
            "pipeline_writer_unload_poll_interval_seconds": poll_interval,
        }.get(key, default),
    )


def _resp(models: list[dict[str, Any]] | None, status: int = 200) -> Any:
    """A fake ``/api/ps`` response carrying the given loaded-model list."""
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value={"models": models or []})
    return r


def _mock_http_client(
    *,
    ps_response: Any,
    generate_response: Any | None = None,
    ps_raises: Exception | None = None,
) -> AsyncMock:
    """Build an httpx.AsyncClient mock with the right context-manager shape."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if ps_raises is not None:
        client.get = AsyncMock(side_effect=ps_raises)
    else:
        client.get = AsyncMock(return_value=ps_response)
    client.post = AsyncMock(
        return_value=generate_response or MagicMock(status_code=200),
    )
    return client


# ---------------------------------------------------------------------------
# unload_loaded_ollama_models — direct contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unload_posts_keep_alive_zero_for_each_loaded_model():
    """Every model from /api/ps gets a POST /api/generate with keep_alive:0."""
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json = MagicMock(return_value={
        "models": [{"name": "gemma3:27b"}, {"name": "llama3:latest"}],
    })
    client = _mock_http_client(ps_response=ps_resp)
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(),
            grace_seconds=2.0,
        )

    assert unloaded == ["gemma3:27b", "llama3:latest"]
    assert client.post.await_count == 2
    # Verify the keep_alive:0 payload reached the API.
    posted_payloads = [call.kwargs["json"] for call in client.post.await_args_list]
    assert posted_payloads == [
        {"model": "gemma3:27b", "keep_alive": 0},
        {"model": "llama3:latest", "keep_alive": 0},
    ]
    # /api/generate URL (Ollama's contract for keep_alive=0 unload).
    posted_urls = [call.args[0] for call in client.post.await_args_list]
    for url in posted_urls:
        assert url.endswith("/api/generate")
    # Grace sleep ran exactly once after the unload sweep.
    sleep_mock.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_unload_skips_grace_sleep_when_no_models_loaded():
    """Empty /api/ps response → no posts, no sleep."""
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json = MagicMock(return_value={"models": []})
    client = _mock_http_client(ps_response=ps_resp)
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(),
            grace_seconds=2.0,
        )

    assert unloaded == []
    client.post.assert_not_called()
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_unload_logs_warning_and_returns_empty_when_ollama_unreachable(caplog):
    """Connection error → WARNING log, empty return, no raise.

    Enforces feedback_no_silent_defaults: the operator must know if
    their VRAM guard is broken. But the pipeline keeps moving (SDXL
    will still run, just on a tighter VRAM budget).
    """
    client = _mock_http_client(
        ps_response=None,
        ps_raises=httpx.ConnectError("connection refused"),
    )
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ), caplog.at_level("WARNING", logger="services.llm_providers.ollama_unload"):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(),
            grace_seconds=2.0,
        )

    assert unloaded == []
    client.post.assert_not_called()
    sleep_mock.assert_not_called()
    # Loud, not silent.
    warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("/api/ps unreachable" in msg for msg in warning_messages), (
        f"expected unreachable WARNING, got: {warning_messages}"
    )


@pytest.mark.asyncio
async def test_unload_logs_warning_on_non_200_ps_status(caplog):
    """500 from /api/ps → WARNING log, empty return, no raise."""
    ps_resp = MagicMock()
    ps_resp.status_code = 500
    client = _mock_http_client(ps_response=ps_resp)

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), caplog.at_level("WARNING", logger="services.llm_providers.ollama_unload"):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(),
            grace_seconds=2.0,
        )

    assert unloaded == []
    client.post.assert_not_called()
    warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("HTTP 500" in msg for msg in warning_messages)


@pytest.mark.asyncio
async def test_unload_continues_after_individual_model_failure(caplog):
    """One model's unload POST raises — the rest still get attempted."""
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json = MagicMock(return_value={
        "models": [
            {"name": "gemma3:27b"},
            {"name": "llama3:latest"},
        ],
    })
    client = _mock_http_client(ps_response=ps_resp)
    # First POST fails, second succeeds.
    client.post = AsyncMock(side_effect=[
        httpx.ReadTimeout("timeout"),
        MagicMock(status_code=200),
    ])

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=AsyncMock(),
    ), caplog.at_level("WARNING", logger="services.llm_providers.ollama_unload"):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(),
            grace_seconds=0.0,
        )

    # Only the successful one is in the return list.
    assert unloaded == ["llama3:latest"]
    # Both attempts ran.
    assert client.post.await_count == 2
    # The failure was logged loudly.
    warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "failed to unload gemma3:27b" in msg for msg in warning_messages
    )


@pytest.mark.asyncio
async def test_unload_uses_resolved_base_url():
    """Custom ``ollama_base_url`` from site_config flows into the requests."""
    custom_url = "http://my-ollama.lan:11434"
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json = MagicMock(return_value={"models": [{"name": "gemma3:27b"}]})
    client = _mock_http_client(ps_response=ps_resp)

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=AsyncMock(),
    ):
        await unload_loaded_ollama_models(
            site_config=_site_config(base_url=custom_url),
            grace_seconds=0.0,
        )

    # /api/ps URL used the custom host.
    client.get.assert_awaited_once()
    ps_url = client.get.await_args.args[0]
    assert ps_url == f"{custom_url}/api/ps"
    # /api/generate URL too.
    post_url = client.post.await_args_list[0].args[0]
    assert post_url == f"{custom_url}/api/generate"


# ---------------------------------------------------------------------------
# maybe_unload_writer_before_sdxl — gate + log marker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_unload_no_ops_when_gate_disabled():
    """``pipeline_explicit_writer_unload_before_sdxl=false`` → zero HTTP traffic.

    The opt-out for operators with abundant VRAM (80+ GB hardware) where
    the ~3-5 s reload tax isn't worth the safety margin.
    """
    client = _mock_http_client(ps_response=MagicMock())

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ):
        unloaded = await maybe_unload_writer_before_sdxl(
            site_config=_site_config(unload_enabled=False),
            stage_label="replace_inline_images",
        )

    assert unloaded == []
    client.get.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_unload_runs_when_gate_enabled(caplog):
    """Default-on gate → the unload sweep runs end-to-end."""
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json = MagicMock(return_value={"models": [{"name": "gemma3:27b"}]})
    client = _mock_http_client(ps_response=ps_resp)
    # Default confirm-on path: enumerate sees the model, the first confirm
    # poll sees it gone — so the sweep completes without timing out.
    client.get = AsyncMock(side_effect=[ps_resp, _resp([])])

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=AsyncMock(),
    ), caplog.at_level("INFO", logger="services.llm_providers.ollama_unload"):
        unloaded = await maybe_unload_writer_before_sdxl(
            site_config=_site_config(unload_enabled=True),
            stage_label="replace_inline_images",
        )

    assert unloaded == ["gemma3:27b"]
    # Log marker the operator greps for in worker logs to confirm the
    # fix is active.
    info_messages = [r.message for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "[REPLACE_INLINE_IMAGES] Unloaded writer model gemma3:27b" in msg
        for msg in info_messages
    ), f"expected log marker, got: {info_messages}"


@pytest.mark.asyncio
async def test_maybe_unload_threads_grace_seconds_from_settings():
    """With confirm disabled, ``pipeline_writer_unload_grace_seconds`` reaches
    the legacy blind asyncio.sleep call (the confirm-off fallback path)."""
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json = MagicMock(return_value={"models": [{"name": "gemma3:27b"}]})
    client = _mock_http_client(ps_response=ps_resp)
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ):
        await maybe_unload_writer_before_sdxl(
            site_config=_site_config(
                unload_enabled=True, grace_seconds=5, confirm_enabled=False,
            ),
            stage_label="replace_inline_images",
        )

    sleep_mock.assert_awaited_once_with(5.0)


@pytest.mark.asyncio
async def test_maybe_unload_defaults_to_on_when_site_config_missing():
    """``site_config=None`` → default-on (matches settings_defaults registry)."""
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json = MagicMock(return_value={"models": []})
    client = _mock_http_client(ps_response=ps_resp)

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ):
        unloaded = await maybe_unload_writer_before_sdxl(
            site_config=None,
            stage_label="replace_inline_images",
        )

    assert unloaded == []
    # The /api/ps probe still ran — proves we entered the unload path
    # rather than short-circuiting on a missing site_config.
    client.get.assert_awaited_once()


# ---------------------------------------------------------------------------
# unload_loaded_ollama_models — confirm-release poll (lever 3, 2026-06-21)
#
# Replaces the blind ``asyncio.sleep(grace_seconds)`` with an actual check
# that Ollama released the model BEFORE the next (SDXL/video) model loads.
# On a single 32 GB GPU shared with the Windows desktop, the old "sleep 2s
# and hope" let the 18 GB writer overlap the incoming diffusion model →
# VRAM exhaustion → WDDM desktop freeze.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_returns_as_soon_as_ps_reports_empty():
    """confirm=True re-polls /api/ps and returns the instant the model is gone
    — no blind grace sleep when release is already confirmed."""
    present = _resp([{"name": "gemma-4-31B-it-qat:latest"}])
    client = _mock_http_client(ps_response=present)
    # enumerate -> present; first confirm poll -> empty.
    client.get = AsyncMock(side_effect=[present, _resp([])])
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(), grace_seconds=2.0, confirm=True,
        )

    assert unloaded == ["gemma-4-31B-it-qat:latest"]
    # One enumerate GET + one confirm-poll GET.
    assert client.get.await_count == 2
    # Empty on the first confirm poll => no poll wait, and crucially NO blind
    # grace sleep (the whole point — we proceed only once it's actually free).
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_polls_until_model_evicted():
    """The confirm loop keeps polling /api/ps (sleeping poll_interval between
    checks) until the model disappears, then returns."""
    present = _resp([{"name": "gemma-4-31B-it-qat:latest"}])
    client = _mock_http_client(ps_response=present)
    # enumerate -> present; poll #1 -> still present; poll #2 -> gone.
    client.get = AsyncMock(side_effect=[present, present, _resp([])])
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(),
            confirm=True,
            confirm_timeout_seconds=15.0,
            poll_interval_seconds=0.5,
        )

    assert unloaded == ["gemma-4-31B-it-qat:latest"]
    assert client.get.await_count == 3  # enumerate + 2 confirm polls
    sleep_mock.assert_awaited_once_with(0.5)  # one wait between the 2 polls


@pytest.mark.asyncio
async def test_confirm_warns_and_proceeds_when_model_never_evicts(caplog):
    """If /api/ps still lists the model after the bounded confirm window, the
    helper logs a WARNING and proceeds — it must never hang the pipeline."""
    present = _resp([{"name": "gemma-4-31B-it-qat:latest"}])
    client = _mock_http_client(ps_response=present)
    client.get = AsyncMock(return_value=present)  # never frees
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ), caplog.at_level("WARNING", logger="services.llm_providers.ollama_unload"):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(),
            confirm=True,
            confirm_timeout_seconds=1.0,
            poll_interval_seconds=0.5,
        )

    assert unloaded == ["gemma-4-31B-it-qat:latest"]
    # confirm_timeout/poll_interval = 1.0/0.5 = 2 confirm polls (+1 enumerate).
    assert client.get.await_count == 3
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("still resident" in m for m in warnings), warnings


@pytest.mark.asyncio
async def test_confirm_false_uses_blind_grace_sleep():
    """confirm=False preserves the legacy blind grace-sleep behavior (no
    extra /api/ps polling) — backcompat for the opt-out path."""
    present = _resp([{"name": "gemma-4-31B-it-qat:latest"}])
    client = _mock_http_client(ps_response=present)
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ):
        unloaded = await unload_loaded_ollama_models(
            site_config=_site_config(), grace_seconds=2.0, confirm=False,
        )

    assert unloaded == ["gemma-4-31B-it-qat:latest"]
    client.get.assert_awaited_once()  # only the enumerate; no confirm poll
    sleep_mock.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_maybe_unload_threads_confirm_settings_from_site_config():
    """maybe_unload_writer_before_sdxl reads the confirm settings and runs the
    confirm-poll path (no blind grace sleep) when confirm is enabled."""
    present = _resp([{"name": "gemma-4-31B-it-qat:latest"}])
    client = _mock_http_client(ps_response=present)
    client.get = AsyncMock(side_effect=[present, _resp([])])
    sleep_mock = AsyncMock()

    with patch(
        "services.llm_providers.ollama_unload.httpx.AsyncClient",
        return_value=client,
    ), patch(
        "services.llm_providers.ollama_unload.asyncio.sleep", new=sleep_mock,
    ):
        unloaded = await maybe_unload_writer_before_sdxl(
            site_config=_site_config(
                unload_enabled=True, confirm_enabled=True, grace_seconds=9,
            ),
            stage_label="source_featured_image",
        )

    assert unloaded == ["gemma-4-31B-it-qat:latest"]
    assert client.get.await_count == 2  # enumerate + confirm poll
    # confirm path supersedes the blind grace sleep, so grace=9 is never slept.
    sleep_mock.assert_not_called()

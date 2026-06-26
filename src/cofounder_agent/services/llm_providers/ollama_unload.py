"""Ollama explicit-unload helper for VRAM-pressure pipeline transitions.

The content pipeline keeps the writer LLM (~20 GB for ``gemma3:27b``)
hot via Ollama's ``keep_alive`` default of 5 minutes. That is great for
back-to-back LLM stages but creates a VRAM cliff at the boundary
between ``quality_evaluation`` (last LLM stage) and
``replace_inline_images`` / ``source_featured_image`` (the image-gen pair).

On a 32 GB card the writer (~20 GB) plus image-gen Lightning (~12 GB) hit
~98% VRAM during the ~30 s window the GPU scheduler takes to acquire
the image-gen lock and unload the writer. On a 24 GB card the same window
OOM-crashes the worker.

``services.gpu_scheduler.GPUScheduler`` already calls
``_unload_ollama_models()`` when ``gpu.lock("image_gen", ...)`` is acquired,
but Ollama treats ``keep_alive: 0`` as fire-and-forget — the API call
returns immediately and the actual VRAM release is asynchronous. A
``/generate`` request issued seconds later (the inline-image prompt
build inside ``replace_inline_images``) can re-load a model before the
prior unload has finished, leaving both resident.

This helper provides a deterministic seam:

* Walks ``/api/ps`` to discover currently-loaded models.
* Issues ``POST /api/generate`` with ``keep_alive: 0`` for each.
* CONFIRMS the release: re-polls ``/api/ps`` until the models are gone
  (bounded by ``pipeline_writer_unload_confirm_timeout_seconds``, default
  ``15``, polled every ``pipeline_writer_unload_poll_interval_seconds``,
  default ``0.5``). ``keep_alive: 0`` is fire-and-forget — the API returns
  before the driver frees the VRAM — so on a single 32 GB GPU shared with
  the Windows desktop, an immediate image-gen/video load would overlap the
  18 GB writer, exhaust VRAM, and freeze WDDM. Waiting on the real signal
  (model gone from ``/api/ps`` ⇒ runner exited ⇒ VRAM reclaimed) prevents
  that. Set ``pipeline_writer_unload_confirm_enabled=false`` to fall back
  to the legacy blind ``pipeline_writer_unload_grace_seconds`` sleep
  (default ``2``).

The bool gate ``pipeline_writer_unload_before_image_gen`` (default
``true``) lets operators with abundant VRAM (80+ GB hardware) skip the
unload tax (~3-5 s reload when a later LLM stage needs the model back).

Per ``feedback_no_silent_defaults``: when Ollama is unreachable while
the unload call fires, this helper logs a WARNING rather than silently
no-opping. The operator should know if their VRAM guard is broken.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx

logger = logging.getLogger(__name__)


__all__ = ["maybe_unload_writer_before_image_gen", "unload_loaded_ollama_models"]


def _ollama_base_url(site_config: Any) -> str:
    """Resolve the Ollama base URL via the injected SiteConfig.

    Falls back to the same host.docker.internal default the rest of the
    code uses when site_config is unavailable (tests / bootstrap).
    """
    default = "http://host.docker.internal:11434"
    if site_config is None:
        return default
    try:
        return (
            site_config.get("ollama_base_url", "")
            or site_config.get("ollama_host", "")
            or default
        )
    except Exception:  # noqa: BLE001 — defensive against test stubs
        return default


async def _confirm_models_released(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> bool:
    """Poll ``/api/ps`` until no models are loaded, or the window expires.

    Returns ``True`` as soon as ``/api/ps`` reports an empty model list.
    Ollama drops a model from ``/api/ps`` once its runner subprocess has
    exited, at which point the driver has reclaimed the VRAM — so an empty
    list is the real "it's actually unloaded" signal that replaces the old
    blind ``asyncio.sleep``. (The Prometheus VRAM gauge is scraped every
    30 s — far too laggy for a 3-5 s eviction — and the worker container
    can't reach ``nvidia-smi`` directly on Windows Docker.)

    The poll is bounded by an attempt count derived from
    ``timeout_seconds / poll_interval_seconds`` (not wall-clock) so it can
    never hang the pipeline and stays deterministic under test. A poll
    error (Ollama briefly unreachable mid-eviction) returns ``False`` — the
    caller logs and proceeds rather than blocking forever.
    """
    interval = max(float(poll_interval_seconds), 0.05)
    attempts = max(1, math.ceil(float(timeout_seconds) / interval))
    for i in range(attempts):
        try:
            resp = await client.get(f"{base_url}/api/ps")
            if resp.status_code == 200 and not (resp.json().get("models") or []):
                return True
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug(
                "[OLLAMA_UNLOAD] confirm poll could not read /api/ps "
                "(%s: %s) — stopping confirm; caller proceeds",
                type(exc).__name__, exc,
            )
            return False
        if i < attempts - 1:
            await asyncio.sleep(interval)
    return False


async def unload_loaded_ollama_models(
    *,
    site_config: Any,
    grace_seconds: float = 2.0,
    timeout_seconds: float = 10.0,
    confirm: bool = False,
    confirm_timeout_seconds: float = 15.0,
    poll_interval_seconds: float = 0.5,
) -> list[str]:
    """Explicitly unload every currently-loaded Ollama model.

    Returns the list of model names that received the unload request.
    The list is empty on:

    * No models loaded (clean GPU).
    * Ollama unreachable (a WARNING is logged so the operator notices).
    * ``/api/ps`` returns non-200 (a WARNING is logged).

    When ``confirm`` is true (the production default via
    ``maybe_unload_writer_before_image_gen``), the helper re-polls ``/api/ps``
    after issuing the unloads and returns only once the models are gone
    (or ``confirm_timeout_seconds`` elapses, polling every
    ``poll_interval_seconds``) — the real "VRAM is free" signal. When
    ``confirm`` is false it falls back to a blind ``asyncio.sleep`` of
    ``grace_seconds`` (keep small, 1-3 s) — the legacy behavior.

    The helper never raises — callers treat a missing unload as
    advisory; the downstream image-gen phase still works (just on a tighter
    VRAM budget), so an exception here would needlessly fail the task.
    """
    base_url = _ollama_base_url(site_config).rstrip("/")
    unloaded: list[str] = []

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
        ) as client:
            try:
                ps_resp = await client.get(f"{base_url}/api/ps")
            except httpx.HTTPError as exc:
                # Per feedback_no_silent_defaults — operator should know
                # the VRAM guard couldn't query Ollama. Don't crash the
                # pipeline; the image-gen phase will still run.
                logger.warning(
                    "[OLLAMA_UNLOAD] /api/ps unreachable at %s (%s: %s) — "
                    "skipping explicit writer unload; image-gen may collide with "
                    "writer VRAM on cards <32 GB",
                    base_url, type(exc).__name__, exc,
                )
                return unloaded

            if ps_resp.status_code != 200:
                logger.warning(
                    "[OLLAMA_UNLOAD] /api/ps returned HTTP %s — skipping "
                    "explicit writer unload (operator: check Ollama logs)",
                    ps_resp.status_code,
                )
                return unloaded

            try:
                data = ps_resp.json()
            except ValueError as exc:
                logger.warning(
                    "[OLLAMA_UNLOAD] /api/ps returned non-JSON body (%s) — "
                    "skipping explicit writer unload", exc,
                )
                return unloaded

            models = data.get("models") or []
            if not models:
                # Clean GPU — nothing to unload. Not a warning.
                logger.debug(
                    "[OLLAMA_UNLOAD] no Ollama models currently loaded "
                    "at %s", base_url,
                )
                return unloaded

            for entry in models:
                name = (entry or {}).get("name")
                if not name:
                    continue
                try:
                    await client.post(
                        f"{base_url}/api/generate",
                        json={"model": name, "keep_alive": 0},
                    )
                    unloaded.append(name)
                except httpx.HTTPError as exc:
                    # Individual model failures don't abort the sweep —
                    # we still want to free everything else we can.
                    logger.warning(
                        "[OLLAMA_UNLOAD] failed to unload %s: %s: %s",
                        name, type(exc).__name__, exc,
                    )

            if unloaded and confirm:
                # Confirm the VRAM was actually released BEFORE returning —
                # the next caller (image-gen / video render) loads its model the
                # instant we return, and on a single 32 GB GPU shared with the
                # desktop, overlapping the 18 GB writer exhausts VRAM and
                # freezes WDDM. keep_alive=0 is fire-and-forget, so we poll
                # /api/ps (same open client) until the model is gone.
                released = await _confirm_models_released(
                    client,
                    base_url,
                    timeout_seconds=confirm_timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
                if not released:
                    logger.warning(
                        "[OLLAMA_UNLOAD] %d model(s) still resident after "
                        "%.1fs confirm window — proceeding anyway; the next "
                        "model load may spike VRAM / freeze the desktop",
                        len(unloaded), confirm_timeout_seconds,
                    )
    except Exception as exc:  # noqa: BLE001 — defensive umbrella
        logger.warning(
            "[OLLAMA_UNLOAD] unexpected error during unload sweep: %s: %s",
            type(exc).__name__, exc,
        )
        return unloaded

    if unloaded and not confirm and grace_seconds > 0:
        # Legacy fallback (confirm disabled): a blind grace sleep gives
        # Ollama *some* time to release VRAM before the next /generate.
        # keep_alive=0 is fire-and-forget on the server side. The
        # confirm-poll path above supersedes this when enabled — it waits
        # on the real signal instead of guessing a duration.
        await asyncio.sleep(grace_seconds)

    return unloaded


async def maybe_unload_writer_before_image_gen(
    *,
    site_config: Any,
    stage_label: str = "replace_inline_images",
) -> list[str]:
    """Conditionally unload the writer LLM before an image-gen phase.

    Gate: ``app_settings.pipeline_writer_unload_before_image_gen``
    (bool, default ``true``). When ``false``, this is a no-op — useful
    on hardware with abundant VRAM (80+ GB) where the ~3-5 s reload tax
    isn't worth the safety margin.

    Grace: ``app_settings.pipeline_writer_unload_grace_seconds`` (int,
    default ``2``) — the asyncio.sleep() after issuing the unload so
    Ollama actually releases the VRAM before the next /generate lands.

    Args:
        site_config: SiteConfig instance (DI seam — pulled from the
            pipeline stage context). Optional; missing config means we
            assume the default-on behavior.
        stage_label: Used in the success log line so operators can
            grep ``[REPLACE_INLINE_IMAGES] Unloaded writer model``
            against task logs.

    Returns:
        Names of the models that received the unload request (possibly
        empty — clean GPU, gate off, or Ollama unreachable).
    """
    # Defaults (test / bootstrap path with no config injected, or a config
    # read that raises) — confirm-on, matching settings_defaults.
    enabled = True
    grace = 2.0
    confirm = True
    confirm_timeout = 15.0
    poll_interval = 0.5
    if site_config is not None:
        try:
            enabled = site_config.get_bool(
                "pipeline_writer_unload_before_image_gen", True,
            )
            grace = float(
                site_config.get_int("pipeline_writer_unload_grace_seconds", 2),
            )
            confirm = site_config.get_bool(
                "pipeline_writer_unload_confirm_enabled", True,
            )
            confirm_timeout = float(
                site_config.get_int(
                    "pipeline_writer_unload_confirm_timeout_seconds", 15,
                ),
            )
            poll_interval = site_config.get_float(
                "pipeline_writer_unload_poll_interval_seconds", 0.5,
            )
        except Exception:  # noqa: BLE001 — defensive against test stubs
            enabled = True
            grace = 2.0
            confirm = True
            confirm_timeout = 15.0
            poll_interval = 0.5

    if not enabled:
        logger.debug(
            "[%s] pipeline_writer_unload_before_image_gen=false — "
            "skipping explicit writer unload", stage_label.upper(),
        )
        return []

    unloaded = await unload_loaded_ollama_models(
        site_config=site_config,
        grace_seconds=grace,
        confirm=confirm,
        confirm_timeout_seconds=confirm_timeout,
        poll_interval_seconds=poll_interval,
    )

    if unloaded:
        # The log marker the verification step in the PR description
        # looks for: ``[REPLACE_INLINE_IMAGES] Unloaded writer model X
        # before image-gen phase``.
        detail = (
            f"confirm-poll, timeout={confirm_timeout:.0f}s"
            if confirm else f"blind grace={grace:.1f}s"
        )
        for name in unloaded:
            logger.info(
                "[%s] Unloaded writer model %s before image-gen phase (%s)",
                stage_label.upper(), name, detail,
            )

    return unloaded

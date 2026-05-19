"""Ollama explicit-unload helper for VRAM-pressure pipeline transitions.

The content pipeline keeps the writer LLM (~20 GB for ``gemma3:27b``)
hot via Ollama's ``keep_alive`` default of 5 minutes. That is great for
back-to-back LLM stages but creates a VRAM cliff at the boundary
between ``quality_evaluation`` (last LLM stage) and
``replace_inline_images`` / ``source_featured_image`` (the SDXL pair).

On a 32 GB card the writer (~20 GB) plus SDXL Lightning (~12 GB) hit
~98% VRAM during the ~30 s window the GPU scheduler takes to acquire
the SDXL lock and unload the writer. On a 24 GB card the same window
OOM-crashes the worker.

``services.gpu_scheduler.GPUScheduler`` already calls
``_unload_ollama_models()`` when ``gpu.lock("sdxl", ...)`` is acquired,
but Ollama treats ``keep_alive: 0`` as fire-and-forget — the API call
returns immediately and the actual VRAM release is asynchronous. A
``/generate`` request issued seconds later (the inline-image prompt
build inside ``replace_inline_images``) can re-load a model before the
prior unload has finished, leaving both resident.

This helper provides a deterministic seam:

* Walks ``/api/ps`` to discover currently-loaded models.
* Issues ``POST /api/generate`` with ``keep_alive: 0`` for each.
* Sleeps ``pipeline_writer_unload_grace_seconds`` (default ``2``) so the
  kernel actually frees the VRAM before the next request lands.

The bool gate ``pipeline_explicit_writer_unload_before_sdxl`` (default
``true``) lets operators with abundant VRAM (80+ GB hardware) skip the
unload tax (~3-5 s reload when ``cross_model_qa`` later needs the LLM
back).

Per ``feedback_no_silent_defaults``: when Ollama is unreachable while
the unload call fires, this helper logs a WARNING rather than silently
no-opping. The operator should know if their VRAM guard is broken.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


__all__ = ["maybe_unload_writer_before_sdxl", "unload_loaded_ollama_models"]


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


async def unload_loaded_ollama_models(
    *,
    site_config: Any,
    grace_seconds: float = 2.0,
    timeout_seconds: float = 10.0,
) -> list[str]:
    """Explicitly unload every currently-loaded Ollama model.

    Returns the list of model names that received the unload request.
    The list is empty on:

    * No models loaded (clean GPU).
    * Ollama unreachable (a WARNING is logged so the operator notices).
    * ``/api/ps`` returns non-200 (a WARNING is logged).

    ``grace_seconds`` is the asyncio.sleep() after issuing the unload
    requests, giving Ollama time to actually release VRAM before the
    next ``/generate`` lands. Keep small (1-3 s) to avoid pipeline tax.

    The helper never raises — callers treat a missing unload as
    advisory; the downstream SDXL phase still works (just on a tighter
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
                # pipeline; the SDXL phase will still run.
                logger.warning(
                    "[OLLAMA_UNLOAD] /api/ps unreachable at %s (%s: %s) — "
                    "skipping explicit writer unload; SDXL may collide with "
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
    except Exception as exc:  # noqa: BLE001 — defensive umbrella
        logger.warning(
            "[OLLAMA_UNLOAD] unexpected error during unload sweep: %s: %s",
            type(exc).__name__, exc,
        )
        return unloaded

    if unloaded and grace_seconds > 0:
        # Give Ollama time to actually release the VRAM. keep_alive=0
        # is fire-and-forget on the server side — without this sleep
        # an immediate /generate can land before the prior model has
        # been evicted, doubling VRAM usage.
        await asyncio.sleep(grace_seconds)

    return unloaded


async def maybe_unload_writer_before_sdxl(
    *,
    site_config: Any,
    stage_label: str = "replace_inline_images",
) -> list[str]:
    """Conditionally unload the writer LLM before an SDXL phase.

    Gate: ``app_settings.pipeline_explicit_writer_unload_before_sdxl``
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
    if site_config is None:
        # No config injected (test / bootstrap path) — default to on.
        enabled = True
        grace = 2.0
    else:
        try:
            enabled = site_config.get_bool(
                "pipeline_explicit_writer_unload_before_sdxl", True,
            )
            grace = float(
                site_config.get_int(
                    "pipeline_writer_unload_grace_seconds", 2,
                ),
            )
        except Exception:  # noqa: BLE001 — defensive against test stubs
            enabled = True
            grace = 2.0

    if not enabled:
        logger.debug(
            "[%s] pipeline_explicit_writer_unload_before_sdxl=false — "
            "skipping explicit writer unload", stage_label.upper(),
        )
        return []

    unloaded = await unload_loaded_ollama_models(
        site_config=site_config, grace_seconds=grace,
    )

    if unloaded:
        # The log marker the verification step in the PR description
        # looks for: ``[REPLACE_INLINE_IMAGES] Unloaded writer model X
        # before SDXL phase``.
        for name in unloaded:
            logger.info(
                "[%s] Unloaded writer model %s before SDXL phase "
                "(grace=%.1fs)", stage_label.upper(), name, grace,
            )

    return unloaded

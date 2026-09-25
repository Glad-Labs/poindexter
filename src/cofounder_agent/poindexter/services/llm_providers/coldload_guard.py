"""Cold-load VRAM guard — clear idle media squatters before big local loads.

2026-08-25 incident (glad-labs-stack Chrome/Claude-Desktop crash trace): the
hourly topic harvest asked the primary Ollama for
``structured_extraction_model`` (``gemma-4-31B-it-qat:latest``, ~18 GB). The
model wasn't resident, so Ollama cold-loaded it onto the render GPU — where
ComfyUI idle-squatted 7.4 GB between renders — and the fit didn't:
``CUDA error: out of memory``, a llama-server segfault, a CPU-projector
retry burning four cores, and the desktop's own GPU processes (Chrome and
Claude Desktop both render on that card) crashed with it. The media
pipeline already evicts Ollama before renders
(``services.llm_providers.ollama_unload``); this guard is the same courtesy
in the other direction.

Contract:

* Fires only for local Ollama-prefixed models (``ollama/`` /
  ``ollama_chat/``) against a local ``api_base``
  (``cost_guard.is_local_base_url``) — cloud calls never trigger reclaim.
* No-ops when the model is already resident (``/api/ps``) — the common
  case costs one ~2 ms GET against a local socket.
* No-ops when the model's size (``/api/tags``, cached
  ``_TAGS_CACHE_TTL_S``) is below ``min_gb`` — small models load beside
  anything.
* No-ops when the Ollama instance at ``api_base`` provably loads onto
  cards the ladder does not touch (``gpu_scheduler.ollama_host_devices``
  disjoint from ``gpu_scheduler.render_devices``). The ladder only frees
  the render GPU, so for such a load it costs sidecar evictions and
  restarts and buys nothing. See "Placement" below.
* Otherwise runs ``gpu.reclaim_render_vram(include_ollama=False)`` — the
  shared ladder whose rungs each decline while a render is in flight and
  no-op when their sidecar is absent, so firing it is safe on every
  install (a ComfyUI mid-render declines rather than dying, per the
  poindexter#3094 posture baked into the rung).
* NEVER raises and never blocks the call: any probe or reclaim error is
  logged and the LLM call proceeds exactly as it would have before this
  guard existed.

Placement (2026-09-25). This docstring used to say the GPU-pinned keep-alive
instance (:11435, qwen3-vl) "never fires it". It did. ``KEEP_ALIVE=-1`` only
holds a model that stays loaded, and the judge reloads several times a day, so
each cold judge call (19.6 GB, over ``min_gb``) ran the ladder against the
render GPU, including the shot-QA score pass inside video renders. At 14:58Z
one ran during an image fan-out: ComfyUI's hard ``/free`` released nothing
from the 19.8 GB it held, so the squat rule queued a restart and the brain
bounced ComfyUI. The judge loads on GPU 1 and never touches that card.

The guard now decides from the INSTANCE the call resolves to
(``ollama_vision_base_url`` → the ``qa_judge`` cards in ``gpu_lock_scopes``,
``ollama_base_url`` → ``llm_primary``), never from a list of model names. It
still runs the ladder whenever it cannot prove the load misses the render
card: an unrecognised host, a role missing from the map, a malformed map, or
device scoping switched off all behave exactly as before.

DB-tunable via the ``plugin.llm_provider.litellm`` config row (same home
as ``drop_params`` / ``cloud_max_tokens``): ``coldload_reclaim_enabled``
(default true) and ``coldload_reclaim_min_gb`` (default 8). Placement reads
the settings that already describe the hardware: ``ollama_base_url``,
``ollama_vision_base_url``, ``gpu_lock_scopes``,
``gpu_lock_per_device_enabled`` and ``pipeline_gpu_index``.
"""

from __future__ import annotations

import logging
import time

import httpx

from poindexter.services.cost_guard import is_local_base_url
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

_LOCAL_PREFIXES = ("ollama/", "ollama_chat/")
_HTTP_TIMEOUT_S = 5.0
_TAGS_CACHE_TTL_S = 300.0

# base_url → (monotonic fetched_at, {model_name_with_tag: size_bytes}).
# A tag's size is immutable, so the TTL exists only to pick up newly pulled
# models. Process-local is the whole scope: one worker process, and a stale
# miss merely skips one reclaim opportunity.
_tags_cache: dict[str, tuple[float, dict[str, int]]] = {}


def _bare_model(resolved_model: str) -> str | None:
    """``ollama/gemma-4-31B-it-qat:latest`` → ``gemma-4-31B-it-qat:latest``.

    ``None`` for any non-local prefix — cloud models never guard.
    """
    for prefix in _LOCAL_PREFIXES:
        if resolved_model.startswith(prefix):
            return resolved_model[len(prefix):]
    return None


def _name_variants(bare: str) -> tuple[str, ...]:
    """Both spellings Ollama may report for a tagless pin.

    Ollama normalises ``gemma-4-31B-it-qat`` to ``…:latest`` in ``/api/ps``
    and ``/api/tags``; matching both keeps a tagless operator pin from
    firing the ladder for a model that IS resident.
    """
    return (bare,) if ":" in bare else (bare, f"{bare}:latest")


async def _model_size_bytes(
    client: httpx.AsyncClient, base: str, bare: str,
) -> int | None:
    """Size of ``bare`` per ``/api/tags`` (cached), or None when unknown."""
    now = time.monotonic()
    cached = _tags_cache.get(base)
    if cached is None or now - cached[0] > _TAGS_CACHE_TTL_S:
        resp = await client.get(f"{base}/api/tags")
        if resp.status_code != 200:
            return None
        sizes = {
            str(m.get("name") or ""): int(m.get("size") or 0)
            for m in (resp.json().get("models") or [])
        }
        _tags_cache[base] = (now, sizes)
        cached = _tags_cache[base]
    sizes = cached[1]
    for name in _name_variants(bare):
        if name in sizes:
            return sizes[name]
    return None


def _cards(cards: frozenset[int]) -> str:
    return ",".join(str(i) for i in sorted(cards)) or "none"


def _placement(
    base: str,
) -> tuple[frozenset[int] | None, frozenset[int] | None]:
    """``(cards the instance at base loads onto, cards the ladder frees)``.

    Either side is ``None`` when unknown, and the caller only skips the ladder
    when both are known and disjoint. Every unknown (unrecognised host,
    untrusted scope map, a raising resolver) therefore costs one needless
    ladder pass at worst, never a skipped pass that was needed.
    """
    try:
        from poindexter.services.gpu_scheduler import ollama_host_devices, render_devices

        return ollama_host_devices(base), render_devices()
    except Exception as exc:  # noqa: BLE001 — the guard must never break a call
        logger.warning(
            "[COLDLOAD_GUARD] could not resolve which GPU %s loads on (%s) — "
            "treating it as the render GPU, as before",
            base, describe_exception(exc),
        )
        return None, None


async def maybe_reclaim_before_coldload(
    *,
    resolved_model: str,
    api_base: str,
    enabled: bool = True,
    min_gb: float = 8.0,
) -> bool:
    """Reclaim idle media VRAM when a big local model is about to cold-load
    onto the render GPU.

    Returns True when the reclaim ladder ran (for tests and log grepping);
    False on every no-op path, including a cold load onto an instance pinned
    off the render GPU. Never raises.
    """
    if not enabled:
        return False
    bare = _bare_model(resolved_model)
    if bare is None:
        return False
    base = (api_base or "").rstrip("/")
    if not base or not is_local_base_url(base):
        return False

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            ps = await client.get(f"{base}/api/ps")
            if ps.status_code != 200:
                return False
            loaded = {
                str(m.get("name") or "")
                for m in (ps.json().get("models") or [])
            }
            if any(name in loaded for name in _name_variants(bare)):
                return False
            size = await _model_size_bytes(client, base, bare)
    except Exception as exc:  # noqa: BLE001 — the guard must never break a call
        # silent-ok: best-effort pre-flight — if Ollama is unreachable here,
        # the actual LLM call one line later fails LOUDLY through the
        # dispatcher's normal error path; a second alert from the guard would
        # be pure duplicate noise.
        logger.debug(
            "[COLDLOAD_GUARD] probe failed for %s at %s (%s) — "
            "proceeding without reclaim",
            resolved_model, base, describe_exception(exc),
        )
        return False

    if size is None or size < float(min_gb) * 1e9:
        return False

    # Resolved after the probes, not before, so the skip is logged exactly
    # when the guard would otherwise have fired. A warm model never gets here.
    instance_cards, render_cards = _placement(base)
    if instance_cards is not None and render_cards is not None:
        if instance_cards.isdisjoint(render_cards):
            logger.info(
                "[COLDLOAD_GUARD] %s (%.1f GB) is not resident at %s, which "
                "loads on GPU %s; the reclaim ladder frees GPU %s — skipping "
                "it, nothing it evicts is in this load's way",
                bare, size / 1e9, base,
                _cards(instance_cards), _cards(render_cards),
            )
            return False
        where = (
            f"loads on GPU {_cards(instance_cards)}, overlapping the render "
            f"GPU {_cards(render_cards)}"
        )
    else:
        where = "GPU placement unknown, so assuming the render GPU"

    logger.info(
        "[COLDLOAD_GUARD] %s (%.1f GB) is not resident at %s (%s) — running "
        "the media VRAM reclaim ladder before the cold load",
        bare, size / 1e9, base, where,
    )
    try:
        from poindexter.services.gpu_scheduler import gpu

        await gpu.reclaim_render_vram(include_ollama=False)
    except Exception as exc:  # noqa: BLE001 — reclaim is best-effort
        logger.warning(
            "[COLDLOAD_GUARD] reclaim ladder failed (%s) — proceeding "
            "with the load anyway",
            describe_exception(exc),
        )
    return True

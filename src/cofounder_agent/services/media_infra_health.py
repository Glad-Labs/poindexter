"""Render-infra health probe for the Stage-2 media lane.

Why this exists (2026-07-03): six published posts sat permanently wedged at
``media_pipeline_redispatch_max`` because Stage-2 dispatches fired during
wan-server / image-gen / DNS outage windows (the post-crash boot windows) and
every fast-fail burned one of the task's bounded re-dispatch attempts. The
re-dispatch cap exists to stop a *permanently-failing render* from looping
forever — an *infra outage* is a different failure class and should defer the
attempt, not consume it.

Two consumers share this probe:

- ``services/jobs/dispatch_media_pipeline.py`` — probes BEFORE dispatching
  (defers the whole cycle while unhealthy) and re-probes after a run failure
  (an unhealthy-infra fast-fail un-claims the piece instead of leaving the
  marker set for the watchdog to burn a re-dispatch on).
- ``services/jobs/media_reconciliation.py`` — requires a healthy probe before
  it resets a cap-wedged task's re-dispatch counter (the bounded self-heal).

Probes (all app_settings-tunable, per DB-first config):

- **wan-server** ``GET {wan_server_url}/health`` — URL resolution mirrors
  ``Wan21Provider`` (``wan_server_url`` → the plugin namespace → default
  ``:9840``).
- **image-gen** ``GET {image_gen_server_url}/health`` (default ``:9836``).
- **DNS canary** — resolve ``media_infra_dns_canary_host`` (default: derive
  the host from ``storage_public_url``; skip when neither is set). Catches
  the in-container-DNS-broken outage class where the render servers are up
  but uploads/research would fail anyway.
- **render-GPU VRAM** — free VRAM on ``pipeline_gpu_index`` must be ≥
  ``media_render_min_free_vram_gb`` (read via Prometheus
  ``nvidia_gpu_memory_*_mib``). Fail-closed: an unreadable reading defers, so
  a blind render can't oversubscribe the 32 GB display GPU and freeze WDDM
  (2026-07-12 desktop-lockup fix).
- **TTS engine** ``GET {engine}/health`` (2026-08-15) — the configured
  ``podcast_tts_engine`` sidecar (chatterbox, else Speaches). Narration is
  fail-soft by design, so before this probe a TTS outage did NOT defer
  dispatch — it shipped voiceless, caption-less videos: the chatterbox
  container (opt-in ``--profile tts-hq``) was stopped in a stack maintenance
  window on 08-13 and stayed down two days while three renders shipped
  silent and were all operator-rejected. A wan outage defers; a TTS outage
  must too. Skipped when ``podcast_tts_enabled`` is off — an install that
  deliberately runs without TTS keeps rendering (silent by choice).

Settings:

- ``media_infra_healthcheck_enabled`` (default ``true``) — master switch;
  ``false`` short-circuits to healthy (OSS forks without a wan sidecar).
- ``media_infra_health_timeout_seconds`` (default ``5``) — per-probe timeout.
- ``media_infra_dns_canary_host`` (default ``''`` = derive / skip).
- ``media_render_vram_gate_enabled`` (default ``true``) — render-GPU VRAM gate.
- ``media_render_min_free_vram_gb`` (default ``25``) — min free VRAM to render.
- ``media_tts_gate_enabled`` (default ``true``) — TTS-engine probe; only
  consulted when ``podcast_tts_enabled`` is on.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MediaInfraHealth:
    """Outcome of a render-infra probe pass.

    ``detail`` is operator-facing: which probe(s) failed and how, or why the
    pass was skipped. Consumers put it verbatim in logs / JobResult details.
    """

    healthy: bool
    detail: str = ""
    # True only when the render-GPU free-VRAM preflight is (a) reason the pass
    # is unhealthy. Lets dispatch_media_pipeline attempt a VRAM reclaim (evict
    # Ollama + hard-unload image-gen) before deferring, vs a wan/image-gen
    # outage where a reclaim would be pointless.
    vram_insufficient: bool = False


def _resolve_wan_health_url(site_config: Any) -> str:
    """Wan health endpoint — same URL resolution chain as ``Wan21Provider``
    (per-install ``wan_server_url`` → plugin namespace → default ``:9840``),
    so the probe watches the exact server the render will hit."""
    from services.video_providers.wan2_1 import _resolve_server_url

    return _resolve_server_url({}, site_config).rstrip("/") + "/health"


def _resolve_image_gen_health_url(site_config: Any) -> str:
    base = (
        site_config.get("image_gen_server_url", "http://image-gen-server:9836")
        or "http://image-gen-server:9836"
    )
    return str(base).rstrip("/") + "/health"


def resolve_tts_health_url(site_config: Any) -> tuple[str, str]:
    """``(engine, health_url)`` for the configured TTS engine.

    Mirrors ``podcast_service``'s engine selection (``podcast_tts_engine``:
    ``'chatterbox'`` → ``plugin.tts_provider.chatterbox.base_url``, anything
    else → Speaches via ``podcast_tts_base_url``) so the probe watches the
    exact sidecar narration will hit. Bases are OpenAI-style ``.../v1`` roots;
    both sidecars serve ``/health`` at the origin, so a trailing ``/v1`` is
    stripped. An empty base (misconfig) returns ``(engine, '')`` = skip.

    Public (no underscore): ``probe_narration_failure`` reuses it so the
    streak pager and the dispatch gate can never watch different URLs.
    """
    engine = (
        str(site_config.get("podcast_tts_engine", "") or "").strip() or "speaches"
    )
    if engine == "chatterbox":
        base = (
            site_config.get(
                "plugin.tts_provider.chatterbox.base_url",
                "http://chatterbox:8000/v1",
            )
            # prod seeds this key as '' (unset per feedback_db_first_config),
            # so the empty string must fall through to the provider default
            # exactly like ChatterboxTTSProvider does.
            or "http://chatterbox:8000/v1"
        )
    else:
        base = (
            site_config.get("podcast_tts_base_url", "http://speaches:8000/v1")
            or "http://speaches:8000/v1"
        )
    base = str(base).strip().rstrip("/")
    if not base:
        return engine, ""
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return engine, base + "/health"


def _tts_remediation_hint(engine: str) -> str:
    """Operator-facing tail for a failed TTS probe — the 08-13 outage was a
    stopped opt-in-profile container, so name the exact restart command."""
    if engine == "chatterbox":
        return (
            " — narration + captions would silently fail-soft; is the tts-hq "
            "profile up? `docker compose --profile tts-hq up -d chatterbox`"
        )
    return (
        " — narration + captions would silently fail-soft; ensure the "
        "poindexter-speaches container is running"
    )


def _resolve_dns_canary_host(site_config: Any) -> str:
    """The hostname the DNS canary resolves. Explicit setting wins; else the
    ``storage_public_url`` host (the render's delivery target, so its
    resolvability is load-bearing); else '' = skip the canary."""
    explicit = (site_config.get("media_infra_dns_canary_host", "") or "").strip()
    if explicit:
        return explicit
    base = (site_config.get("storage_public_url", "") or "").strip()
    if base:
        return urlparse(base).hostname or ""
    return ""


async def _probe_http(client: Any, name: str, url: str) -> str | None:
    """GET a health endpoint. Returns None when healthy (2xx), else a
    one-line failure description for the aggregate detail string."""
    try:
        resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001 — unreachable IS the signal here
        logger.debug("[media_infra_health] %s probe raised: %s", name, exc)
        return f"{name} {url} unreachable ({exc.__class__.__name__}: {exc})"
    if 200 <= resp.status_code < 300:
        return None
    return f"{name} {url} returned HTTP {resp.status_code}"


async def _probe_dns(host: str, timeout_s: float) -> str | None:
    """Resolve the canary host. Returns None on success, else a description."""
    try:
        await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, None),
            timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — resolution failure IS the signal
        logger.debug("[media_infra_health] DNS canary %r raised: %s", host, exc)
        return f"DNS canary {host!r} did not resolve ({exc.__class__.__name__})"
    return None


async def check_media_infra_health(
    site_config: Any,
    *,
    http_client_factory: Any = None,
) -> MediaInfraHealth:
    """Probe wan-server + image-gen ``/health`` and the DNS canary.

    Healthy only when every configured probe passes. ``site_config=None``
    (bootstrap/test paths with no DI seam) and the disabled master switch
    both read as healthy — the gate must never brick dispatch for installs
    that haven't configured render infra probing.
    """
    if site_config is None:
        return MediaInfraHealth(healthy=True, detail="no site_config — probes skipped")
    if not site_config.get_bool("media_infra_healthcheck_enabled", True):
        return MediaInfraHealth(
            healthy=True, detail="media_infra_healthcheck_enabled=false",
        )

    if http_client_factory is None:
        http_client_factory = httpx.AsyncClient
    timeout_s = site_config.get_float("media_infra_health_timeout_seconds", 5.0) or 5.0

    # TTS probe (2026-08-15): only when narration is actually expected —
    # podcast_tts_enabled off means silent renders are the operator's
    # configured choice, and the gate must not brick that install.
    tts_probe: tuple[str, str] | None = None
    if site_config.get_bool("media_tts_gate_enabled", True):
        from services.tts_service import is_tts_enabled

        if is_tts_enabled(site_config):
            engine, tts_url = resolve_tts_health_url(site_config)
            if tts_url:
                tts_probe = (engine, tts_url)

    failures: list[str] = []
    probes = (
        ("wan-server", _resolve_wan_health_url(site_config)),
        ("image-gen", _resolve_image_gen_health_url(site_config)),
    )
    async with http_client_factory(
        timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 3.0)),
    ) as client:
        for name, url in probes:
            failure = await _probe_http(client, name, url)
            if failure:
                failures.append(failure)
        if tts_probe is not None:
            engine, tts_url = tts_probe
            failure = await _probe_http(client, f"tts-{engine}", tts_url)
            if failure:
                failures.append(failure + _tts_remediation_hint(engine))

    canary = _resolve_dns_canary_host(site_config)
    if canary:
        failure = await _probe_dns(canary, timeout_s)
        if failure:
            failures.append(failure)

    # Render-GPU VRAM preflight: the Wan render loads ~24 GB onto
    # pipeline_gpu_index (the display GPU); defer unless the card has room, so
    # it can never oversubscribe the 32 GB card and spill WDDM into system RAM
    # (which freezes the desktop). Fail-closed: an unreadable reading defers.
    vram_insufficient = False
    if site_config.get_bool("media_render_vram_gate_enabled", True):
        from services.render_vram import render_gpu_free_vram_gb

        min_gb = site_config.get_float("media_render_min_free_vram_gb", 25.0) or 25.0
        free = await render_gpu_free_vram_gb(
            site_config, http_client_factory=http_client_factory,
        )
        if free is None:
            vram_insufficient = True
            failures.append(
                "render-GPU free VRAM unreadable (Prometheus) — deferring so a "
                "blind render can't oversubscribe the display GPU and freeze "
                "the desktop"
            )
        elif free < min_gb:
            vram_insufficient = True
            failures.append(
                f"render-GPU free VRAM {free:.1f} GB < {min_gb:.0f} GB required "
                f"(pipeline_gpu_index) — deferring so the render can't freeze "
                f"the desktop"
            )

    if failures:
        return MediaInfraHealth(
            healthy=False,
            detail="; ".join(failures),
            vram_insufficient=vram_insufficient,
        )
    return MediaInfraHealth(healthy=True, detail="all render-infra probes passed")


__all__ = [
    "MediaInfraHealth",
    "check_media_infra_health",
    "resolve_tts_health_url",
]

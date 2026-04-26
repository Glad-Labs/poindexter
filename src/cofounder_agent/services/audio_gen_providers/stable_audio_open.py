"""StableAudioOpenProvider — text-to-music/SFX via Stable Audio Open 1.0.

First implementation of the :class:`AudioGenProvider <plugins.audio_gen_provider.AudioGenProvider>`
Protocol (Glad-Labs/poindexter#125). Generates short ambient beds, SFX,
and intro/outro stings for the video + podcast pipelines.

License
-------

**Stability AI Community License.** Free for commercial use up to
**$1M annual revenue**. Operators that cross the threshold must either:

1. Switch ``app_settings.audio_gen_engine`` to a MIT-licensed alternative
   such as MusicGen (Meta), or
2. Purchase a Stability AI commercial license.

The provider does NOT enforce this — license compliance is an operator
concern, tracked in cost-control documentation. The metadata each
``AudioGenResult`` carries includes ``license="stability-ai-community"``
so observability can surface the licensed engine in use.

Strategy
--------

HTTP POST to a dedicated Stable Audio Open inference server. Default
port **9839** (sits next to the SDXL sidecar on 9836 and the FLUX server
on 9838). The server side is a small wrapper around the
``stable-audio-tools`` library; standing it up is a deploy step, not a
provider concern. When the server is unreachable the provider logs a
clear operator-facing error and returns ``None`` — never silent.

Config (``plugin.audio_gen_provider.stable-audio-open-1.0`` in app_settings):

- ``enabled`` (default ``true``)
- ``server_url`` (default ``http://host.docker.internal:9839``)
- ``default_duration_s`` (default 5.0; cap at 47s — the model's max).
- ``sample_rate`` (default 44100)
- ``output_format`` (default ``"wav"``)
- ``prompt_template_ambient`` / ``_sfx`` / ``_intro`` / ``_outro`` —
  per-kind prompt templates. Each accepts ``{prompt}`` placeholder and
  is wrapped around the caller-supplied prompt at dispatch time.

Kind support: ``ambient``, ``sfx``, ``intro``, ``outro`` (all four).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

import httpx

from plugins.audio_gen_provider import AudioGenResult, AudioKind

logger = logging.getLogger(__name__)


# Default inference-server URL. Sits at port 9839 next to the SDXL
# (9836) and FLUX (9838) sidecars so all media generators run as
# parallel host processes.
_DEFAULT_SERVER_URL = "http://host.docker.internal:9839"

# Stable Audio Open 1.0 produces clips up to ~47s. Default to a short
# 5s sting since most production calls (intro / outro) want exactly that.
_DEFAULT_DURATION_S = 5.0
_MAX_DURATION_S = 47.0

# Output defaults. WAV at 44.1kHz stereo matches the model's native
# output. Operators can downconvert to mp3 in the inference server if
# needed; we leave that to the server.
_DEFAULT_SAMPLE_RATE = 44100
_DEFAULT_FORMAT = "wav"

# Per-kind prompt scaffolds — defaults if no template configured. The
# scaffolds bias the model toward the right "feel" for each slot since
# Stable Audio Open is sensitive to genre/mood adjectives.
_DEFAULT_PROMPT_TEMPLATES: dict[AudioKind, str] = {
    "ambient": "{prompt}, ambient bed, no vocals, calm, looped, low energy",
    "sfx": "{prompt}, short sound effect, clean, no vocals",
    "intro": "{prompt}, energetic intro sting, rising tension, no vocals",
    "outro": "{prompt}, gentle outro, fading, no vocals",
}

# Per-call HTTP cap. Stable Audio Open generates a 5s clip in ~3-6s on
# a 5090; allow generous headroom for cold-start and longer durations.
_HTTP_TIMEOUT = httpx.Timeout(180.0, connect=5.0)


def _write_audio_bytes(path: str, content: bytes) -> None:
    """Sync helper for ``asyncio.to_thread`` — writes server response
    bytes to disk without blocking the event loop (ASYNC230). Audio
    files are typically 0.5-5 MB; a blocking ``open()`` would stall
    concurrent video / podcast jobs.
    """
    with open(path, "wb") as f:
        f.write(content)


class StableAudioOpenProvider:
    """Stable Audio Open 1.0 text-to-audio via dedicated inference server.

    Supports all four ``AudioKind`` slots. The model is text-conditioned
    only — voice / melody conditioning are out of scope (revisit when
    a use case lands).
    """

    name = "stable-audio-open-1.0"
    kinds: tuple[AudioKind, ...] = ("ambient", "sfx", "intro", "outro")

    async def generate(
        self,
        prompt: str,
        kind: AudioKind,
        config: dict[str, Any],
    ) -> AudioGenResult | None:
        prompt = (prompt or "").strip()
        if not prompt:
            return None

        if kind not in self.kinds:
            logger.warning(
                "[StableAudioOpenProvider] unsupported kind %r; "
                "supported: %s", kind, self.kinds,
            )
            return None

        site_config = config.get("_site_config")

        server_url = _resolve_server_url(config, site_config)
        duration = _resolve_duration(config, site_config)
        sample_rate = _resolve_sample_rate(config, site_config)
        output_format = _resolve_output_format(config, site_config)
        templated_prompt = _apply_prompt_template(
            prompt, kind, config, site_config,
        )

        # Resolve / create output_path. The inference server can either
        # stream bytes back or write to disk; we always end up with a
        # local file the caller can mux into video/podcast.
        output_path = str(config.get("output_path", "") or "")
        cleanup_on_failure = False
        if not output_path:
            with tempfile.NamedTemporaryFile(
                suffix=f".{output_format}", delete=False,
            ) as tmp:
                output_path = tmp.name
            cleanup_on_failure = True

        rendered_duration = await _generate_to_path(
            prompt=templated_prompt,
            output_path=output_path,
            server_url=server_url,
            duration=duration,
            sample_rate=sample_rate,
            output_format=output_format,
        )

        if rendered_duration is None or not os.path.exists(output_path):
            if cleanup_on_failure and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return None

        return AudioGenResult(
            file_path=output_path,
            duration_s=rendered_duration,
            sample_rate=sample_rate,
            kind=kind,
            format=output_format,
            prompt=templated_prompt,
            source=self.name,
            metadata={
                "model": "stable-audio-open-1.0",
                "license": "stability-ai-community",
                "license_revenue_cap_usd": 1_000_000,
                "server_url": server_url,
                "requested_duration_s": duration,
                "user_prompt": prompt,
            },
        )


# ---------------------------------------------------------------------------
# Config resolution helpers — small, individually testable
# ---------------------------------------------------------------------------


def _resolve_server_url(config: dict[str, Any], site_config: Any) -> str:
    """Pick the inference-server URL.

    Resolution order:

    1. ``config['server_url']`` (per-call override).
    2. ``site_config.stable_audio_open_server_url`` (top-level fast path).
    3. ``site_config.plugin.audio_gen_provider.stable-audio-open-1.0.server_url``
       (canonical per-install plugin namespace).
    4. Module default ``_DEFAULT_SERVER_URL``.
    """
    direct = str(config.get("server_url", "") or "")
    if direct:
        return direct

    if site_config is None:
        return _DEFAULT_SERVER_URL

    try:
        flat = site_config.get("stable_audio_open_server_url", "") or ""
    except Exception:
        flat = ""
    if flat:
        return str(flat)

    try:
        nested = site_config.get(
            "plugin.audio_gen_provider.stable-audio-open-1.0.server_url",
            "",
        ) or ""
    except Exception:
        nested = ""
    if nested:
        return str(nested)

    return _DEFAULT_SERVER_URL


def _resolve_duration(config: dict[str, Any], site_config: Any) -> float:
    """Pick the requested duration in seconds. Caps at the model max."""
    direct = config.get("duration_s")
    if direct is None and site_config is not None:
        try:
            direct = site_config.get(
                "plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s",
                None,
            )
        except Exception:
            direct = None
    if direct is None:
        direct = _DEFAULT_DURATION_S

    try:
        value = float(direct)
    except (TypeError, ValueError):
        value = _DEFAULT_DURATION_S

    if value <= 0:
        value = _DEFAULT_DURATION_S
    if value > _MAX_DURATION_S:
        logger.warning(
            "[StableAudioOpenProvider] requested duration %.1fs exceeds "
            "model max %.1fs; clamping",
            value, _MAX_DURATION_S,
        )
        value = _MAX_DURATION_S
    return value


def _resolve_sample_rate(config: dict[str, Any], site_config: Any) -> int:
    """Pick the PCM sample rate in Hz."""
    direct = config.get("sample_rate")
    if direct is None and site_config is not None:
        try:
            direct = site_config.get(
                "plugin.audio_gen_provider.stable-audio-open-1.0.sample_rate",
                None,
            )
        except Exception:
            direct = None
    if direct is None:
        direct = _DEFAULT_SAMPLE_RATE
    try:
        return int(direct)
    except (TypeError, ValueError):
        return _DEFAULT_SAMPLE_RATE


def _resolve_output_format(config: dict[str, Any], site_config: Any) -> str:
    """Pick the audio file format (wav / mp3 / ogg / flac)."""
    direct = str(config.get("output_format", "") or "")
    if direct:
        return direct.lower()
    if site_config is None:
        return _DEFAULT_FORMAT
    try:
        nested = site_config.get(
            "plugin.audio_gen_provider.stable-audio-open-1.0.output_format",
            "",
        ) or ""
    except Exception:
        nested = ""
    if nested:
        return str(nested).lower()
    return _DEFAULT_FORMAT


def _apply_prompt_template(
    prompt: str,
    kind: AudioKind,
    config: dict[str, Any],
    site_config: Any,
) -> str:
    """Wrap the caller's prompt in the kind-specific template.

    Resolution order (first non-empty wins):

    1. ``config['prompt_template']`` — explicit per-call override.
    2. ``site_config.plugin.audio_gen_provider.stable-audio-open-1.0.prompt_template_<kind>``.
    3. Module default scaffold for the kind.

    The template MUST contain ``{prompt}`` — when it doesn't we log a
    warning and return the raw caller prompt so an operator typo
    doesn't silently strip the actual content.
    """
    explicit = str(config.get("prompt_template", "") or "")
    if explicit:
        template = explicit
    else:
        template = ""
        if site_config is not None:
            try:
                template = site_config.get(
                    f"plugin.audio_gen_provider.stable-audio-open-1.0."
                    f"prompt_template_{kind}",
                    "",
                ) or ""
            except Exception:
                template = ""
        if not template:
            template = _DEFAULT_PROMPT_TEMPLATES[kind]

    if "{prompt}" not in template:
        logger.warning(
            "[StableAudioOpenProvider] prompt template for kind=%s has no "
            "{prompt} placeholder; using raw prompt instead. Template was: %r",
            kind, template,
        )
        return prompt

    return template.format(prompt=prompt)


# ---------------------------------------------------------------------------
# Generation strategy — single HTTP call, audio bytes back
# ---------------------------------------------------------------------------


async def _generate_to_path(
    *,
    prompt: str,
    output_path: str,
    server_url: str,
    duration: float,
    sample_rate: int,
    output_format: str,
) -> float | None:
    """POST the prompt to the Stable Audio Open inference server; write
    the resulting audio file to ``output_path``.

    Returns the rendered duration in seconds when the file was written
    successfully, or ``None`` on any failure (server unreachable,
    non-200, parse failure). Operators see a clear log line on failure
    so they can bring the inference server up — never silent.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{server_url}/generate",
                json={
                    "prompt": prompt,
                    "duration_s": duration,
                    "sample_rate": sample_rate,
                    "format": output_format,
                    "model": "stable-audio-open-1.0",
                },
                timeout=180,
            )
    except Exception as e:
        logger.error(
            "[StableAudioOpenProvider] inference server unreachable at %s: "
            "%s. Stand up the Stable Audio Open inference server (default "
            "port 9839) or set "
            "plugin.audio_gen_provider.stable-audio-open-1.0.server_url.",
            server_url, e,
        )
        return None

    ct = resp.headers.get("content-type", "")

    if resp.status_code == 200 and ct.startswith("audio/"):
        await asyncio.to_thread(_write_audio_bytes, output_path, resp.content)
        rendered = float(
            resp.headers.get("X-Duration-Seconds", "") or duration,
        )
        logger.info(
            "[StableAudioOpenProvider] audio generated (%.1fs, %d bytes): %s",
            rendered, len(resp.content), output_path,
        )
        return rendered

    if resp.status_code == 200 and ct.startswith("application/json"):
        rendered = await asyncio.to_thread(
            _materialize_sidecar_json, resp, output_path,
        )
        if rendered is not None:
            return rendered
        return None

    logger.error(
        "[StableAudioOpenProvider] inference server returned %s "
        "(content-type=%r): %s",
        resp.status_code, ct, (resp.text or "")[:200],
    )
    return None


def _materialize_sidecar_json(
    resp: httpx.Response,
    output_path: str,
) -> float | None:
    """Copy the audio file the sidecar wrote to its filesystem to the
    caller's ``output_path``. Returns rendered duration in seconds.

    Mirrors the SDXL/FLUX sidecar JSON contract — server returns
    ``{"audio_path": "...", "duration_s": ..., "sample_rate": ...}``
    so a single sidecar implementation can serve multiple media
    generators if desired.
    """
    import shutil

    try:
        data = resp.json()
    except Exception as e:
        logger.warning(
            "[StableAudioOpenProvider] sidecar JSON parse failed: %s", e,
        )
        return None

    src = str(data.get("audio_path", "") or "")
    if not src:
        logger.warning(
            "[StableAudioOpenProvider] sidecar JSON missing audio_path: %s",
            data,
        )
        return None

    src = src.replace("\\", "/")
    if not os.path.exists(src):
        logger.warning(
            "[StableAudioOpenProvider] sidecar file not visible from worker: "
            "src=%s", src,
        )
        return None

    try:
        shutil.copyfile(src, output_path)
    except OSError as e:
        logger.warning(
            "[StableAudioOpenProvider] sidecar copy failed: %s -> %s: %s",
            src, output_path, e,
        )
        return None

    rendered = float(data.get("duration_s", 0) or 0)
    logger.info(
        "[StableAudioOpenProvider] audio materialized from sidecar "
        "(%.1fs, %dHz): %s",
        rendered,
        int(data.get("sample_rate", 0) or 0),
        output_path,
    )
    return rendered if rendered > 0 else None

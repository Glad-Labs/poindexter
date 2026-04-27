"""TtsForVideoStage — synthesize per-scene narration audio.

Step 8 of the video pipeline (Glad-Labs/poindexter#143). Reads
``video_script`` from upstream and renders each scene's
narration_text into its own audio file via the configured
TTSProvider. Per-scene audio is what FFmpegLocalCompositor wants in
its CompositionScene.narration_path field, and per-scene durations
are what drive caption timing in the V0 (where we derive captions
from script + TTS timing rather than running whisper).

Provider selection mirrors podcast_service: read
``video_tts_engine`` (defaults to ``podcast_tts_engine`` if the
video-specific key is unset), find the registered TTSProvider with
that name, route every synthesize() call through it. Cost tracking
is the provider's job (Kokoro / EdgeTTS already wire through
CostGuard internally).

## Context reads

- ``video_script`` (dict) — output of ScriptForVideoStage
- ``site_config`` — DI seam
- ``task_id`` (str) — used for output path prefixing

## Context writes

- ``video_tts`` (dict) — see :func:`_default_tts` for shape
- ``stages["video.tts"]`` (bool)

Per-format payload::

    {
        "intro_audio_path": str | "",
        "intro_duration_s": float,
        "outro_audio_path": str | "",     # long_form only
        "outro_duration_s": float,         # long_form only
        "scenes": [
            {"scene_idx", "audio_path", "duration_s", "voice", "text"}
        ],
        "total_duration_s": float,
    }
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from plugins.stage import StageResult
from plugins.tts_provider import TTSResult

logger = logging.getLogger(__name__)


_DEFAULT_TTS_ENGINE = "edge_tts"
_TMP_PREFIX = "poindexter_tts_video_"


def _default_tts() -> dict[str, dict[str, Any]]:
    """Empty/default shape returned when nothing was rendered."""
    return {
        "long_form": {
            "intro_audio_path": "",
            "intro_duration_s": 0.0,
            "outro_audio_path": "",
            "outro_duration_s": 0.0,
            "scenes": [],
            "total_duration_s": 0.0,
        },
        "short_form": {
            "intro_audio_path": "",
            "intro_duration_s": 0.0,
            "scenes": [],
            "total_duration_s": 0.0,
        },
    }


def _resolve_tts_provider(engine_name: str) -> Any | None:
    """Pick the registered TTSProvider with the given name.

    Lazy-imported registry so test collection doesn't pay the cost.
    Returns ``None`` when no provider matches; callers fall back to
    the default engine.
    """
    try:
        from plugins.registry import get_tts_providers
    except Exception as exc:
        logger.warning("[video.tts] could not import registry: %s", exc)
        return None
    try:
        providers = get_tts_providers()
    except Exception as exc:
        logger.warning("[video.tts] get_tts_providers raised: %s", exc)
        return None
    for provider in providers:
        if getattr(provider, "name", None) == engine_name:
            return provider
    return None


def _read_engine(site_config: Any) -> str:
    """Pick the TTS engine name from app_settings.

    ``video_tts_engine`` wins; falls back to ``podcast_tts_engine``
    so operators don't need to set both. Final fallback is the
    documented default. Loud-log when the configured engine isn't
    registered so misconfiguration is visible.
    """
    if site_config is None:
        return _DEFAULT_TTS_ENGINE
    explicit = site_config.get("video_tts_engine", "")
    if explicit:
        return str(explicit)
    inherited = site_config.get("podcast_tts_engine", "")
    if inherited:
        return str(inherited)
    return _DEFAULT_TTS_ENGINE


def _provider_config(site_config: Any, engine_name: str) -> dict[str, Any]:
    """Look up ``plugin.tts_provider.<engine>`` settings for the call.

    Includes the ``_site_config`` reserved key so downstream providers
    that need a SiteConfig handle (Kokoro doesn't, EdgeTTS does) get
    one without falling back to the deleted module-level singleton.
    """
    cfg: dict[str, Any] = {}
    if site_config is None:
        return cfg
    namespace = f"plugin.tts_provider.{engine_name}"
    # Pull a small set of conventional keys; provider modules tolerate
    # missing entries.
    for key in ("default_voice", "lang_code", "speed", "rate", "pitch"):
        value = site_config.get(f"{namespace}.{key}", None)
        if value is not None:
            cfg[key] = value
    cfg["_site_config"] = site_config
    return cfg


async def _synthesize_one(
    *,
    provider: Any,
    text: str,
    output_path: Path,
    voice: str | None,
    cfg: dict[str, Any],
    label: str,
) -> tuple[str, float]:
    """Run synthesize() and return ``(audio_path, duration_s)``.

    Returns ``("", 0.0)`` on any provider failure — callers treat that
    as a missing chunk and the downstream stitch Stage drops the
    associated scene rather than failing the whole video.
    """
    if not text.strip():
        return "", 0.0
    try:
        result = await provider.synthesize(
            text, output_path, voice=voice, config=cfg,
        )
    except Exception as exc:
        logger.warning(
            "[video.tts] %s synthesize raised: %s", label, exc,
        )
        if output_path.exists():
            output_path.unlink(missing_ok=True)
        return "", 0.0
    if not isinstance(result, TTSResult):
        return "", 0.0
    audio_path = str(result.audio_path or "")
    if not audio_path or not os.path.exists(audio_path):
        return "", 0.0
    return audio_path, float(result.duration_seconds or 0.0)


class TtsForVideoStage:
    """Render every scene's narration to disk via the configured TTSProvider."""

    name = "video.tts"
    description = "Synthesize per-scene narration audio for the video pipeline"
    timeout_seconds = 600  # ~16 long-form scenes + 4 short-form
    halts_on_failure = False  # missing audio degrades, not fatal

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],  # pyright: ignore[reportUnusedParameter]
    ) -> StageResult:
        site_config = context.get("site_config")
        if site_config is None:
            return StageResult(
                ok=False,
                detail="site_config missing on context",
                context_updates={"video_tts": _default_tts()},
                metrics={"skipped": True},
            )

        script = context.get("video_script") or {}
        long_form = script.get("long_form") or {}
        short_form = script.get("short_form") or {}
        if not (long_form.get("scenes") or short_form.get("scenes")):
            return StageResult(
                ok=False,
                detail="no scenes in video_script — script_for_video upstream?",
                context_updates={"video_tts": _default_tts()},
                metrics={"skipped": True},
            )

        engine_name = _read_engine(site_config)
        provider = _resolve_tts_provider(engine_name)
        if provider is None and engine_name != _DEFAULT_TTS_ENGINE:
            logger.warning(
                "[video.tts] engine %r not registered; falling back to %r",
                engine_name, _DEFAULT_TTS_ENGINE,
            )
            engine_name = _DEFAULT_TTS_ENGINE
            provider = _resolve_tts_provider(engine_name)
        if provider is None:
            return StageResult(
                ok=False,
                detail=f"no TTSProvider registered (tried {engine_name!r})",
                context_updates={"video_tts": _default_tts()},
                metrics={"skipped": True},
            )

        cfg = _provider_config(site_config, engine_name)
        voice: str | None = cfg.get("default_voice")
        default_format = (
            getattr(provider, "default_format", "mp3") or "mp3"
        )
        suffix = f".{default_format}"

        # Per-task tempdir keeps a video's audio chunks together for
        # easy cleanup on failure.
        task_id = str(context.get("task_id") or "untagged")
        tmp_root = Path(
            tempfile.mkdtemp(prefix=f"{_TMP_PREFIX}{task_id}_"),
        )

        result_payload = _default_tts()

        # --- Long-form ---
        if long_form.get("scenes"):
            long_payload = result_payload["long_form"]
            long_total = 0.0

            intro_text = str(long_form.get("intro_hook") or "")
            if intro_text:
                path, dur = await _synthesize_one(
                    provider=provider,
                    text=intro_text,
                    output_path=tmp_root / f"long_intro_{uuid4().hex}{suffix}",
                    voice=voice,
                    cfg=cfg,
                    label="long_intro",
                )
                long_payload["intro_audio_path"] = path
                long_payload["intro_duration_s"] = dur
                long_total += dur

            for idx, scene in enumerate(long_form["scenes"]):
                text = str(scene.get("narration_text") or "")
                path, dur = await _synthesize_one(
                    provider=provider,
                    text=text,
                    output_path=tmp_root / f"long_scene_{idx:03d}_{uuid4().hex}{suffix}",
                    voice=voice,
                    cfg=cfg,
                    label=f"long_scene_{idx}",
                )
                long_payload["scenes"].append({
                    "scene_idx": idx,
                    "audio_path": path,
                    "duration_s": dur,
                    "voice": voice or "",
                    "text": text,
                })
                long_total += dur

            outro_text = str(long_form.get("outro_cta") or "")
            if outro_text:
                path, dur = await _synthesize_one(
                    provider=provider,
                    text=outro_text,
                    output_path=tmp_root / f"long_outro_{uuid4().hex}{suffix}",
                    voice=voice,
                    cfg=cfg,
                    label="long_outro",
                )
                long_payload["outro_audio_path"] = path
                long_payload["outro_duration_s"] = dur
                long_total += dur

            long_payload["total_duration_s"] = long_total

        # --- Short-form ---
        if short_form.get("scenes"):
            short_payload = result_payload["short_form"]
            short_total = 0.0

            intro_text = str(short_form.get("intro_hook") or "")
            if intro_text:
                path, dur = await _synthesize_one(
                    provider=provider,
                    text=intro_text,
                    output_path=tmp_root / f"short_intro_{uuid4().hex}{suffix}",
                    voice=voice,
                    cfg=cfg,
                    label="short_intro",
                )
                short_payload["intro_audio_path"] = path
                short_payload["intro_duration_s"] = dur
                short_total += dur

            for idx, scene in enumerate(short_form["scenes"]):
                text = str(scene.get("narration_text") or "")
                path, dur = await _synthesize_one(
                    provider=provider,
                    text=text,
                    output_path=tmp_root / f"short_scene_{idx:03d}_{uuid4().hex}{suffix}",
                    voice=voice,
                    cfg=cfg,
                    label=f"short_scene_{idx}",
                )
                short_payload["scenes"].append({
                    "scene_idx": idx,
                    "audio_path": path,
                    "duration_s": dur,
                    "voice": voice or "",
                    "text": text,
                })
                short_total += dur

            short_payload["total_duration_s"] = short_total

        long_rendered = sum(
            1 for s in result_payload["long_form"]["scenes"] if s["audio_path"]
        )
        short_rendered = sum(
            1 for s in result_payload["short_form"]["scenes"] if s["audio_path"]
        )

        stages = context.setdefault("stages", {})
        ok = (
            long_rendered == len(long_form.get("scenes") or [])
            and long_rendered > 0
        )
        stages[self.name] = ok

        return StageResult(
            ok=ok,
            detail=(
                f"engine={engine_name} "
                f"long_form={long_rendered}/{len(long_form.get('scenes') or [])} "
                f"short_form={short_rendered}/{len(short_form.get('scenes') or [])}"
            ),
            context_updates={"video_tts": result_payload, "stages": stages},
            metrics={
                "engine": engine_name,
                "long_rendered": long_rendered,
                "short_rendered": short_rendered,
                "long_total_s": result_payload["long_form"]["total_duration_s"],
                "short_total_s": result_payload["short_form"]["total_duration_s"],
            },
        )

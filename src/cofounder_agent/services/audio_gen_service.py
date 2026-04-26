"""Audio-generation dispatcher — opt-in entry point for video + podcast.

Tiny wrapper around :func:`plugins.registry.get_audio_gen_providers`
that resolves the active engine from ``app_settings.audio_gen_engine``
and forwards calls. Default engine is empty/off — no audio is
generated until an operator explicitly opts in by setting the engine
key to a registered provider name.

Adheres to the project's "no silent fallback" rule (Glad-Labs/poindexter#125
acceptance criterion #4): when ``audio_gen_engine`` is set to a name
that isn't registered, this module raises ``RuntimeError`` rather than
quietly skipping the layer. Callers in video_service / podcast_service
catch and log so a misconfiguration is loud but doesn't tank the post.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.audio_gen_provider import AudioGenProvider, AudioGenResult, AudioKind
from plugins.registry import get_audio_gen_providers, get_core_samples

logger = logging.getLogger(__name__)


def is_audio_gen_enabled(site_config: Any) -> bool:
    """Return True iff ``app_settings.audio_gen_engine`` names a non-empty
    engine. Cheap check callers run before any real work to short-circuit
    the default-off case without going through plugin discovery.
    """
    if site_config is None:
        return False
    try:
        engine = str(site_config.get("audio_gen_engine", "") or "").strip()
    except Exception:
        return False
    return bool(engine)


def resolve_audio_gen_provider(site_config: Any) -> AudioGenProvider | None:
    """Resolve the active AudioGenProvider, or ``None`` if disabled.

    Raises ``RuntimeError`` if ``audio_gen_engine`` is set but no
    registered provider matches the name. Fail-loud mirror of
    ``services.llm_providers.dispatch.dispatch_complete`` — operators
    misconfigure DB rows; a clear error beats silent dropouts.
    """
    if not is_audio_gen_enabled(site_config):
        return None

    engine = str(site_config.get("audio_gen_engine", "") or "").strip()

    # Merge entry_point providers + in-tree core samples, same as
    # image_service does for ImageProvider — keeps the dispatcher
    # working in-container before the package is pip-installed.
    providers: list[AudioGenProvider] = list(get_audio_gen_providers())
    sample_providers = get_core_samples().get("audio_gen_providers", [])
    providers.extend(sample_providers)

    for provider in providers:
        if getattr(provider, "name", "") == engine:
            return provider

    available = sorted({getattr(p, "name", "?") for p in providers})
    raise RuntimeError(
        f"audio_gen_engine={engine!r} but no provider is registered with "
        f"that name. Registered audio-gen providers: {available}. "
        "Set audio_gen_engine to one of those, or to an empty string to "
        "disable the audio-generation layer."
    )


async def generate_audio(
    prompt: str,
    kind: AudioKind,
    *,
    site_config: Any,
    output_path: str | None = None,
    duration_s: float | None = None,
) -> AudioGenResult | None:
    """Generate one audio clip via the active provider.

    Returns ``None`` when the layer is disabled. Returns ``None`` when
    the active provider declines the request (e.g. unsupported kind).
    Logs and returns ``None`` when the provider raises — callers in the
    video / podcast services should treat audio as best-effort and
    proceed without it.

    Args:
        prompt: Free-text description of the audio to generate.
        kind: Slot the audio fills (ambient/sfx/intro/outro).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
        output_path: Optional explicit output path. When ``None`` the
            provider picks a tempfile.
        duration_s: Optional duration override. When ``None`` the
            provider's ``default_duration_s`` config wins.
    """
    try:
        provider = resolve_audio_gen_provider(site_config)
    except RuntimeError as e:
        # Misconfigured engine. Log loud + bail; caller proceeds without audio.
        logger.error("[audio_gen] %s", e)
        return None

    if provider is None:
        return None

    config: dict[str, Any] = {"_site_config": site_config}
    if output_path:
        config["output_path"] = output_path
    if duration_s is not None:
        config["duration_s"] = duration_s

    try:
        return await provider.generate(prompt, kind, config)
    except Exception as e:
        logger.warning(
            "[audio_gen] provider %s raised on kind=%s: %s",
            getattr(provider, "name", "?"), kind, e,
        )
        return None

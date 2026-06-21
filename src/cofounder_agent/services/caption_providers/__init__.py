"""Core caption providers shipped with Poindexter.

Each module in this package exposes one ``CaptionProvider``-shaped
class. The media pipeline's ``media.transcribe_narration`` atom selects
one by name via :func:`get_caption_provider`, which reads
``app_settings.video_caption_engine`` (default: ``speaches``).

``speaches`` is the default because the stack already runs the Speaches
faster-whisper sidecar for narration TTS / voice STT — reusing it avoids a
second, separate whisper.cpp install (the legacy ``whisper_local`` provider
shells out to a ``whisper-cli`` binary that isn't shipped in the worker
image, so it silently produced no captions).

See :mod:`plugins.caption_provider` for the Protocol contract.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ENGINE = "speaches"

__all__ = ["get_caption_provider"]


def get_caption_provider(site_config: Any = None) -> Any:
    """Return the active :class:`~plugins.caption_provider.CaptionProvider`.

    Reads ``app_settings.video_caption_engine`` (default ``"speaches"``) and
    returns the matching provider instance, threading ``site_config`` so the
    provider can read its ``plugin.caption_provider.<name>.*`` config.

    Unknown engine names fall back to ``speaches`` with a warning rather than
    raising — captions are best-effort and must never halt the render graph.
    """
    engine = _DEFAULT_ENGINE
    if site_config is not None:
        try:
            raw = site_config.get("video_caption_engine", _DEFAULT_ENGINE)
            engine = (str(raw).strip() or _DEFAULT_ENGINE) if raw is not None else _DEFAULT_ENGINE
        except Exception:  # noqa: BLE001 — a config read failure defaults to the shipped engine
            engine = _DEFAULT_ENGINE

    if engine == "whisper_local":
        from services.caption_providers.whisper_local import WhisperLocalCaptionProvider

        return WhisperLocalCaptionProvider(site_config=site_config)

    if engine != "speaches":
        logger.warning(
            "[caption_providers] unknown video_caption_engine=%r — falling back to 'speaches'",
            engine,
        )
    from services.caption_providers.speaches import SpeachesCaptionProvider

    return SpeachesCaptionProvider(site_config=site_config)

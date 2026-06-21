"""Unit tests for the caption-provider factory.

``services.caption_providers.get_caption_provider`` finally wires the
``video_caption_engine`` app-setting that the package docstring has always
documented but nothing read — the transcribe atom hardcoded
``WhisperLocalCaptionProvider`` (a binary that was never installed).
Default is ``speaches`` (the running faster-whisper sidecar).
"""

from __future__ import annotations

from typing import Any


class _Cfg:
    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        self._m = mapping or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._m.get(key, default)


def test_default_returns_speaches():
    from services.caption_providers import get_caption_provider
    from services.caption_providers.speaches import SpeachesCaptionProvider

    assert isinstance(get_caption_provider(_Cfg()), SpeachesCaptionProvider)


def test_none_site_config_returns_speaches():
    from services.caption_providers import get_caption_provider
    from services.caption_providers.speaches import SpeachesCaptionProvider

    assert isinstance(get_caption_provider(None), SpeachesCaptionProvider)


def test_whisper_local_when_configured():
    from services.caption_providers import get_caption_provider
    from services.caption_providers.whisper_local import WhisperLocalCaptionProvider

    provider = get_caption_provider(_Cfg({"video_caption_engine": "whisper_local"}))
    assert isinstance(provider, WhisperLocalCaptionProvider)


def test_speaches_when_configured():
    from services.caption_providers import get_caption_provider
    from services.caption_providers.speaches import SpeachesCaptionProvider

    provider = get_caption_provider(_Cfg({"video_caption_engine": "speaches"}))
    assert isinstance(provider, SpeachesCaptionProvider)


def test_unknown_engine_falls_back_to_speaches():
    from services.caption_providers import get_caption_provider
    from services.caption_providers.speaches import SpeachesCaptionProvider

    provider = get_caption_provider(_Cfg({"video_caption_engine": "bogus"}))
    assert isinstance(provider, SpeachesCaptionProvider)


def test_provider_receives_site_config():
    """The factory must thread site_config into the provider so it can read
    its ``plugin.caption_provider.*`` settings."""
    from services.caption_providers import get_caption_provider

    cfg = _Cfg({"video_caption_engine": "speaches"})
    provider = get_caption_provider(cfg)
    assert provider._site_config is cfg

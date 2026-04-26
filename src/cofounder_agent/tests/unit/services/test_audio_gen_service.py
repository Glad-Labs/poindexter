"""Unit tests for the audio_gen_service dispatcher.

Exercises the opt-in gate (``audio_gen_engine`` setting), the fail-loud
behavior when the configured engine name doesn't match a registered
provider, and the wiring through to ``AudioGenProvider.generate``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.audio_gen_provider import AudioGenResult
from plugins.registry import clear_registry_cache
from services import audio_gen_service


def _stub_site_config(mapping: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = mapping or {}
    sc.get.side_effect = lambda k, d="": values.get(k, d)
    return sc


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    clear_registry_cache()
    yield
    clear_registry_cache()


# ---------------------------------------------------------------------------
# is_audio_gen_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsAudioGenEnabled:
    def test_returns_false_when_unset(self):
        assert audio_gen_service.is_audio_gen_enabled(_stub_site_config()) is False

    def test_returns_false_for_empty_string(self):
        sc = _stub_site_config({"audio_gen_engine": ""})
        assert audio_gen_service.is_audio_gen_enabled(sc) is False

    def test_returns_false_for_none_site_config(self):
        assert audio_gen_service.is_audio_gen_enabled(None) is False

    def test_returns_true_when_engine_set(self):
        sc = _stub_site_config({"audio_gen_engine": "stable-audio-open-1.0"})
        assert audio_gen_service.is_audio_gen_enabled(sc) is True

    def test_returns_false_for_whitespace_only(self):
        sc = _stub_site_config({"audio_gen_engine": "   "})
        assert audio_gen_service.is_audio_gen_enabled(sc) is False


# ---------------------------------------------------------------------------
# resolve_audio_gen_provider — fail-loud when engine name is wrong
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveAudioGenProvider:
    def test_returns_none_when_disabled(self):
        sc = _stub_site_config()
        assert audio_gen_service.resolve_audio_gen_provider(sc) is None

    def test_returns_provider_when_engine_matches_core_sample(self):
        # Core sample registers the StableAudioOpenProvider — happy path.
        sc = _stub_site_config({"audio_gen_engine": "stable-audio-open-1.0"})
        provider = audio_gen_service.resolve_audio_gen_provider(sc)
        assert provider is not None
        assert provider.name == "stable-audio-open-1.0"

    def test_unknown_engine_raises_runtime_error(self):
        # Acceptance criterion: fail loud when engine name doesn't match.
        sc = _stub_site_config({"audio_gen_engine": "made-up-engine"})
        with pytest.raises(RuntimeError) as ei:
            audio_gen_service.resolve_audio_gen_provider(sc)
        assert "made-up-engine" in str(ei.value)
        # Error includes the list of registered providers so operators
        # can fix the typo without grepping the codebase.
        assert "stable-audio-open-1.0" in str(ei.value)


# ---------------------------------------------------------------------------
# generate_audio — high-level dispatcher behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGenerateAudio:
    async def test_returns_none_when_disabled(self):
        sc = _stub_site_config()
        result = await audio_gen_service.generate_audio(
            "x", "ambient", site_config=sc,
        )
        assert result is None

    async def test_returns_none_on_unknown_engine_no_raise(self, caplog):
        """Misconfigured engine logs an error but doesn't propagate to
        the caller — video / podcast pipelines proceed without audio.
        """
        sc = _stub_site_config({"audio_gen_engine": "no-such-engine"})
        with caplog.at_level("ERROR"):
            result = await audio_gen_service.generate_audio(
                "x", "ambient", site_config=sc,
            )
        assert result is None
        assert any("no-such-engine" in rec.message for rec in caplog.records)

    async def test_forwards_to_provider_generate(self):
        sc = _stub_site_config({"audio_gen_engine": "stable-audio-open-1.0"})
        fake_result = AudioGenResult(
            file_path="/tmp/x.wav",
            duration_s=5.0,
            sample_rate=44100,
            kind="ambient",
            source="stable-audio-open-1.0",
        )

        with patch(
            "services.audio_gen_service.resolve_audio_gen_provider",
        ) as resolver:
            mock_provider = MagicMock()
            mock_provider.name = "stable-audio-open-1.0"
            mock_provider.generate = AsyncMock(return_value=fake_result)
            resolver.return_value = mock_provider

            result = await audio_gen_service.generate_audio(
                "warm pad",
                "ambient",
                site_config=sc,
                output_path="/tmp/x.wav",
                duration_s=4.0,
            )

        assert result is fake_result
        mock_provider.generate.assert_awaited_once()
        args, _ = mock_provider.generate.call_args
        assert args[0] == "warm pad"
        assert args[1] == "ambient"
        # Dispatcher seeds _site_config + output_path + duration_s in
        # the config dict so providers can reach them.
        config = args[2]
        assert config["_site_config"] is sc
        assert config["output_path"] == "/tmp/x.wav"
        assert config["duration_s"] == 4.0

    async def test_provider_exception_returns_none(self, caplog):
        """Provider crash is logged but doesn't propagate — audio is
        best-effort, video / podcast must keep shipping.
        """
        sc = _stub_site_config({"audio_gen_engine": "stable-audio-open-1.0"})
        with patch(
            "services.audio_gen_service.resolve_audio_gen_provider",
        ) as resolver:
            mock_provider = MagicMock()
            mock_provider.name = "stable-audio-open-1.0"
            mock_provider.generate = AsyncMock(
                side_effect=RuntimeError("oom"),
            )
            resolver.return_value = mock_provider

            with caplog.at_level("WARNING"):
                result = await audio_gen_service.generate_audio(
                    "x", "ambient", site_config=sc,
                )

        assert result is None
        assert any("oom" in rec.message for rec in caplog.records)

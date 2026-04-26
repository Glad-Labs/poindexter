"""Unit tests for ``services.tts_providers.kokoro.KokoroTTSProvider`` (GH-122).

The Kokoro model itself (model weights, ``KPipeline``, ``soundfile``,
``numpy``) is mocked out — these tests exercise the provider's
contract: pipeline caching, voice/lang/speed config plumbing, error
handling, ``TTSResult`` shape. The ``KPipeline`` integration is
covered by the GH-122 acceptance criteria (real-audio episode), not
unit tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from plugins.tts_provider import TTSProvider, TTSResult
from services.tts_providers import kokoro as kokoro_mod
from services.tts_providers.kokoro import KokoroTTSProvider


@pytest.fixture(autouse=True)
def _reset_pipeline_cache():
    """Wipe the module-level pipeline cache between tests."""
    kokoro_mod._PIPELINE_CACHE.clear()
    yield
    kokoro_mod._PIPELINE_CACHE.clear()


def _fake_pipeline_factory(samples_per_chunk: int = 24000):
    """Build a callable that mimics ``KPipeline`` output.

    Returns a factory that, when called like ``pipeline(text, voice=..., speed=...)``,
    yields one ``(grapheme, phoneme, audio)`` tuple. The audio payload is
    a list of length ``samples_per_chunk`` (``_synthesize_blocking``
    treats it as a numpy array via ``len()`` / shape).
    """
    captured: dict[str, Any] = {}

    class _AudioStub:
        """Minimal numpy.ndarray-shaped stub so duration math works."""

        def __init__(self, n: int) -> None:
            self.shape = (n,)
            self._n = n

        def __len__(self) -> int:
            return self._n

    def _pipeline(text, voice=None, speed=1.0):
        captured["text"] = text
        captured["voice"] = voice
        captured["speed"] = speed
        yield "graphemes", "phonemes", _AudioStub(samples_per_chunk)

    return _pipeline, captured


class TestSynthesize:
    @pytest.mark.asyncio
    async def test_writes_audio_and_returns_tts_result(self, tmp_path, monkeypatch):
        out = tmp_path / "ep.wav"
        pipeline, captured = _fake_pipeline_factory(samples_per_chunk=48000)

        # Capture _write_wav output without needing soundfile.
        def fake_write(path: Path, audio: Any, sample_rate: int):
            Path(path).write_bytes(b"FAKE_WAV_PAYLOAD")

        monkeypatch.setattr(kokoro_mod, "_get_pipeline", lambda lang: pipeline)
        monkeypatch.setattr(kokoro_mod, "_write_wav", fake_write)

        # The blocking helper imports numpy lazily — provide a stub so
        # the test environment doesn't have to install it.
        np_stub = MagicMock()
        np_stub.concatenate = lambda chunks: chunks[0]
        with patch.dict("sys.modules", {"numpy": np_stub}):
            result = await KokoroTTSProvider().synthesize(
                "Hello, world!",
                out,
                voice="af_heart",
                config={"lang_code": "a", "speed": 1.0},
            )

        assert isinstance(result, TTSResult)
        assert result.audio_path == out
        assert out.exists()
        assert result.voice == "af_heart"
        assert result.sample_rate == 24000
        assert result.audio_format == "wav"
        assert result.file_size_bytes == len(b"FAKE_WAV_PAYLOAD")
        # 48000 samples / 24000 Hz = 2s
        assert result.duration_seconds == 2
        assert result.metadata["engine"] == "kokoro-82M"
        assert result.metadata["lang_code"] == "a"

        # Pipeline was invoked with the expected args
        assert captured["voice"] == "af_heart"
        assert captured["speed"] == 1.0

    @pytest.mark.asyncio
    async def test_default_voice_falls_back_to_config_then_constant(
        self, tmp_path, monkeypatch,
    ):
        out = tmp_path / "ep.wav"
        pipeline, captured = _fake_pipeline_factory()

        monkeypatch.setattr(kokoro_mod, "_get_pipeline", lambda lang: pipeline)
        monkeypatch.setattr(kokoro_mod, "_write_wav", lambda p, a, sr: Path(p).write_bytes(b"x"))

        np_stub = MagicMock()
        np_stub.concatenate = lambda chunks: chunks[0]

        # No voice param, no config voice → constant default ("af_heart")
        with patch.dict("sys.modules", {"numpy": np_stub}):
            r = await KokoroTTSProvider().synthesize("Test", out)
        assert r.voice == "af_heart"

        # No voice param + config voice → config wins
        with patch.dict("sys.modules", {"numpy": np_stub}):
            r = await KokoroTTSProvider().synthesize(
                "Test", tmp_path / "ep2.wav",
                config={"default_voice": "am_michael"},
            )
        assert r.voice == "am_michael"

        # Explicit voice overrides everything
        with patch.dict("sys.modules", {"numpy": np_stub}):
            r = await KokoroTTSProvider().synthesize(
                "Test", tmp_path / "ep3.wav",
                voice="bf_emma",
                config={"default_voice": "am_michael"},
            )
        assert r.voice == "bf_emma"

    @pytest.mark.asyncio
    async def test_speed_config_plumbed_through(self, tmp_path, monkeypatch):
        pipeline, captured = _fake_pipeline_factory()

        monkeypatch.setattr(kokoro_mod, "_get_pipeline", lambda lang: pipeline)
        monkeypatch.setattr(kokoro_mod, "_write_wav", lambda p, a, sr: Path(p).write_bytes(b"x"))

        np_stub = MagicMock()
        np_stub.concatenate = lambda chunks: chunks[0]
        with patch.dict("sys.modules", {"numpy": np_stub}):
            await KokoroTTSProvider().synthesize(
                "Test",
                tmp_path / "ep.wav",
                config={"speed": 1.25},
            )
        assert captured["speed"] == 1.25

    @pytest.mark.asyncio
    async def test_invalid_speed_falls_back_to_one(self, tmp_path, monkeypatch):
        pipeline, captured = _fake_pipeline_factory()

        monkeypatch.setattr(kokoro_mod, "_get_pipeline", lambda lang: pipeline)
        monkeypatch.setattr(kokoro_mod, "_write_wav", lambda p, a, sr: Path(p).write_bytes(b"x"))

        np_stub = MagicMock()
        np_stub.concatenate = lambda chunks: chunks[0]
        with patch.dict("sys.modules", {"numpy": np_stub}):
            await KokoroTTSProvider().synthesize(
                "Test",
                tmp_path / "ep.wav",
                config={"speed": "fast-as-possible"},
            )
        assert captured["speed"] == 1.0

    @pytest.mark.asyncio
    async def test_empty_text_raises(self, tmp_path):
        with pytest.raises(ValueError):
            await KokoroTTSProvider().synthesize("", tmp_path / "x.wav")
        with pytest.raises(ValueError):
            await KokoroTTSProvider().synthesize("   \n", tmp_path / "x.wav")

    @pytest.mark.asyncio
    async def test_pipeline_yields_no_chunks_raises(self, tmp_path, monkeypatch):
        def empty_pipeline(text, voice=None, speed=1.0):
            return
            yield  # noqa: B901 — explicit empty generator

        monkeypatch.setattr(kokoro_mod, "_get_pipeline", lambda lang: empty_pipeline)

        np_stub = MagicMock()
        with patch.dict("sys.modules", {"numpy": np_stub}):
            with pytest.raises(RuntimeError, match="synthesis failed"):
                await KokoroTTSProvider().synthesize(
                    "Hello", tmp_path / "ep.wav", voice="af_heart",
                )

    @pytest.mark.asyncio
    async def test_pipeline_exception_cleans_up_partial(self, tmp_path, monkeypatch):
        out = tmp_path / "ep.wav"
        out.write_bytes(b"partial")  # simulate partial write

        def boom_pipeline(text, voice=None, speed=1.0):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover

        monkeypatch.setattr(kokoro_mod, "_get_pipeline", lambda lang: boom_pipeline)

        np_stub = MagicMock()
        with patch.dict("sys.modules", {"numpy": np_stub}):
            with pytest.raises(RuntimeError, match="synthesis failed"):
                await KokoroTTSProvider().synthesize("hi", out, voice="af_heart")

        assert not out.exists(), "partial file should be cleaned up"

    @pytest.mark.asyncio
    async def test_empty_output_file_raises(self, tmp_path, monkeypatch):
        out = tmp_path / "ep.wav"
        pipeline, _ = _fake_pipeline_factory()

        def fake_write_empty(path: Path, audio: Any, sample_rate: int):
            Path(path).write_bytes(b"")  # zero-byte file

        monkeypatch.setattr(kokoro_mod, "_get_pipeline", lambda lang: pipeline)
        monkeypatch.setattr(kokoro_mod, "_write_wav", fake_write_empty)

        np_stub = MagicMock()
        np_stub.concatenate = lambda chunks: chunks[0]
        with patch.dict("sys.modules", {"numpy": np_stub}):
            with pytest.raises(RuntimeError, match="empty file"):
                await KokoroTTSProvider().synthesize(
                    "hello", out, voice="af_heart",
                )


class TestPipelineCache:
    def test_pipeline_cached_across_calls(self, monkeypatch):
        """``_get_pipeline`` should hand back the same instance for one lang."""
        constructed = {"count": 0}

        class _StubPipeline:
            def __init__(self, lang_code):
                constructed["count"] += 1
                self.lang_code = lang_code

            def __call__(self, *a, **k):  # pragma: no cover
                return iter([])

        # Stub the kokoro import in the module so the real package isn't needed.
        fake_kokoro = MagicMock()
        fake_kokoro.KPipeline = _StubPipeline
        with patch.dict("sys.modules", {"kokoro": fake_kokoro}):
            kokoro_mod._get_pipeline("a")
            kokoro_mod._get_pipeline("a")
            kokoro_mod._get_pipeline("a")
        assert constructed["count"] == 1

    def test_pipeline_separate_per_lang(self):
        constructed: list[str] = []

        class _StubPipeline:
            def __init__(self, lang_code):
                constructed.append(lang_code)

        fake_kokoro = MagicMock()
        fake_kokoro.KPipeline = _StubPipeline
        with patch.dict("sys.modules", {"kokoro": fake_kokoro}):
            kokoro_mod._get_pipeline("a")
            kokoro_mod._get_pipeline("b")
        assert constructed == ["a", "b"]

    def test_pipeline_missing_kokoro_raises(self, monkeypatch):
        kokoro_mod._PIPELINE_CACHE.clear()

        # Simulate ``import kokoro`` failing.
        import builtins

        real_import = builtins.__import__

        def blocked_import(name, *a, **k):
            if name == "kokoro":
                raise ImportError("No module named 'kokoro'")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        with pytest.raises(RuntimeError, match="not installed"):
            kokoro_mod._get_pipeline("a")


class TestProviderShape:
    def test_class_attributes(self):
        p = KokoroTTSProvider()
        assert p.name == "kokoro"
        assert p.sample_rate_hz == 24000
        assert p.default_format == "wav"

    def test_satisfies_protocol(self):
        assert isinstance(KokoroTTSProvider(), TTSProvider)

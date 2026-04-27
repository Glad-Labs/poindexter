"""Unit tests for ``services.caption_providers.whisper_local``.

Mocks ``_run_whisper_blocking`` and the filesystem-side outputs so the
tests never spawn whisper.cpp. Covers Protocol conformance, the four
bail-loudly gates (disabled / missing audio / missing binary / missing
model), helper-level argv plumbing, JSON-shape edge cases, and the
success + failure subprocess paths.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from plugins.caption_provider import CaptionProvider, CaptionResult, CaptionSegment
from services.caption_providers import whisper_local as whisper_mod
from services.caption_providers.whisper_local import (
    WhisperLocalCaptionProvider,
    _build_command,
    _parse_segments,
    _read_text_if_exists,
    _resolve_binary,
)


class _StubSiteConfig:
    """Minimal site_config double — supports .get() with a mapping.

    Keys are the full ``plugin.caption_provider.whisper_local.<key>``
    namespace. Tests pass a dict like ``{"enabled": False, ...}`` and
    this stub prefixes the lookup automatically so test data stays
    short.
    """

    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        self._mapping = {
            f"plugin.caption_provider.whisper_local.{k}": v
            for k, v in (mapping or {}).items()
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)


def _make_provider(mapping: dict[str, Any] | None = None) -> WhisperLocalCaptionProvider:
    return WhisperLocalCaptionProvider(site_config=_StubSiteConfig(mapping or {}))


# ---------------------------------------------------------------------------
# Protocol conformance + class attributes
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_caption_provider_protocol(self):
        assert isinstance(WhisperLocalCaptionProvider(), CaptionProvider)

    def test_class_attributes(self):
        p = WhisperLocalCaptionProvider()
        assert p.name == "whisper_local"
        # Whisper is auto-detect — tuple is empty by design
        assert p.supported_languages == ()
        assert p.supports_diarization is False


# ---------------------------------------------------------------------------
# Bail-loudly gates
# ---------------------------------------------------------------------------


class TestDisabledGate:
    @pytest.mark.asyncio
    async def test_disabled_returns_failure(self):
        provider = _make_provider({"enabled": False})
        result = await provider.transcribe(audio_path="/nonexistent.wav")
        assert isinstance(result, CaptionResult)
        assert result.success is False
        assert result.error is not None
        assert "disabled" in result.error.lower()

    @pytest.mark.asyncio
    async def test_disabled_does_not_spawn_subprocess(self):
        """Disabled gate should run BEFORE we touch _run_whisper_blocking."""
        provider = _make_provider({"enabled": False})
        with patch.object(whisper_mod, "_run_whisper_blocking") as mock_run:
            result = await provider.transcribe(audio_path="/whatever.wav")
        mock_run.assert_not_called()
        assert result.success is False


class TestMissingAudio:
    @pytest.mark.asyncio
    async def test_missing_audio_path_fails(self, tmp_path):
        provider = _make_provider({"model_path": "/x"})
        result = await provider.transcribe(
            audio_path=str(tmp_path / "missing.wav"),
        )
        assert result.success is False
        assert "audio_path" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_audio_path_fails(self):
        provider = _make_provider({})
        result = await provider.transcribe(audio_path="")
        assert result.success is False
        assert result.error is not None


class TestMissingBinary:
    @pytest.mark.asyncio
    async def test_resolve_binary_returns_none_when_nothing_on_path(self, tmp_path):
        # Both audio and model exist; only the binary is missing.
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00" * 16)
        model = tmp_path / "ggml-base.bin"
        model.write_bytes(b"\x00" * 16)

        provider = _make_provider({
            "binary_path": "definitely-not-on-path-xyz123",
            "model_path": str(model),
        })

        with patch("services.caption_providers.whisper_local.shutil.which", return_value=None):
            result = await provider.transcribe(audio_path=str(audio))

        assert result.success is False
        assert "binary not found" in (result.error or "").lower()


class TestMissingModel:
    @pytest.mark.asyncio
    async def test_missing_model_returns_failure(self, tmp_path):
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00" * 16)
        provider = _make_provider({
            # Non-absolute name — the resolver hits shutil.which, which
            # we patch to return a fake hit. (Absolute paths get rejected
            # immediately when os.path.exists is False, regardless of which.)
            "binary_path": "whisper-cli",
            "model_path": str(tmp_path / "missing-model.bin"),
        })

        # Patch shutil.which so the binary check passes.
        with patch(
            "services.caption_providers.whisper_local.shutil.which",
            return_value="/fake/whisper-cli",
        ):
            result = await provider.transcribe(audio_path=str(audio))
        assert result.success is False
        assert "model not found" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_empty_model_path_returns_failure(self, tmp_path):
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00")
        provider = _make_provider({
            "binary_path": "whisper-cli",
            # model_path absent
        })
        with patch(
            "services.caption_providers.whisper_local.shutil.which",
            return_value="/fake/whisper-cli",
        ):
            result = await provider.transcribe(audio_path=str(audio))
        assert result.success is False
        assert "model not found" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# _resolve_binary
# ---------------------------------------------------------------------------


class TestResolveBinary:
    def test_absolute_existing_path_passes_through(self, tmp_path):
        binary = tmp_path / "whisper-cli.exe"
        binary.write_bytes(b"")
        result = _resolve_binary(str(binary))
        assert result == str(binary)

    def test_absolute_missing_path_returns_none(self, tmp_path):
        assert _resolve_binary(str(tmp_path / "does-not-exist")) is None

    def test_path_name_lookup_falls_back_through_legacy_main(self):
        """When configured name isn't on PATH, fall back via whisper-cli → main."""
        calls: list[str] = []

        def fake_which(name: str):
            calls.append(name)
            # Configured + whisper-cli miss; legacy "main" hits.
            if name == "main":
                return "/usr/local/bin/main"
            return None

        with patch("services.caption_providers.whisper_local.shutil.which", side_effect=fake_which):
            result = _resolve_binary("custom-name")
        assert result == "/usr/local/bin/main"
        # Should have tried the configured name AND the historical fallbacks
        assert calls[0] == "custom-name"
        assert "whisper-cli" in calls
        assert "main" in calls

    def test_returns_none_when_no_candidate_resolves(self):
        with patch("services.caption_providers.whisper_local.shutil.which", return_value=None):
            assert _resolve_binary("nope") is None

    def test_empty_configured_string_still_tries_defaults(self):
        with patch(
            "services.caption_providers.whisper_local.shutil.which",
            side_effect=lambda n: "/x/whisper-cli" if n == "whisper-cli" else None,
        ):
            assert _resolve_binary("") == "/x/whisper-cli"


# ---------------------------------------------------------------------------
# _build_command
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def _kwargs(self, **overrides):
        base = dict(
            binary="/usr/bin/whisper-cli",
            model_path="/m/ggml-base.bin",
            audio_path="/a/in.wav",
            output_dir="/tmp/out",
            output_stem="transcript",
            threads=8,
            beam_size=5,
            use_gpu=True,
            language="",
            granularity="segment",
        )
        base.update(overrides)
        return base

    def test_includes_required_flags(self):
        cmd = _build_command(**self._kwargs())
        # The executable must be first
        assert cmd[0] == "/usr/bin/whisper-cli"
        # Core flags expected by whisper.cpp
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "/m/ggml-base.bin"
        assert "--file" in cmd
        assert cmd[cmd.index("--file") + 1] == "/a/in.wav"
        assert "--output-json" in cmd
        assert "--output-srt" in cmd
        assert "--output-vtt" in cmd
        # threads/beam plumbed correctly
        assert "--threads" in cmd
        assert cmd[cmd.index("--threads") + 1] == "8"
        assert "--beam-size" in cmd
        assert cmd[cmd.index("--beam-size") + 1] == "5"

    def test_output_file_uses_stem_under_output_dir(self):
        cmd = _build_command(**self._kwargs())
        assert "--output-file" in cmd
        out_arg = cmd[cmd.index("--output-file") + 1]
        # os.path.join — check both pieces are present
        assert "transcript" in out_arg
        assert os.path.basename(out_arg) == "transcript"

    def test_no_gpu_flag_when_use_gpu_false(self):
        cmd = _build_command(**self._kwargs(use_gpu=False))
        assert "--no-gpu" in cmd

    def test_no_gpu_flag_absent_when_use_gpu_true(self):
        cmd = _build_command(**self._kwargs(use_gpu=True))
        assert "--no-gpu" not in cmd

    def test_language_added_when_supplied(self):
        cmd = _build_command(**self._kwargs(language="en"))
        assert "--language" in cmd
        assert cmd[cmd.index("--language") + 1] == "en"

    def test_language_absent_when_empty(self):
        cmd = _build_command(**self._kwargs(language=""))
        assert "--language" not in cmd

    def test_word_granularity_adds_max_len_and_split_on_word(self):
        cmd = _build_command(**self._kwargs(granularity="word"))
        assert "--max-len" in cmd
        assert cmd[cmd.index("--max-len") + 1] == "1"
        assert "--split-on-word" in cmd

    def test_segment_granularity_omits_word_flags(self):
        cmd = _build_command(**self._kwargs(granularity="segment"))
        assert "--max-len" not in cmd
        assert "--split-on-word" not in cmd


# ---------------------------------------------------------------------------
# _parse_segments
# ---------------------------------------------------------------------------


class TestParseSegments:
    def _write_payload(self, tmp_path, payload: dict) -> str:
        p = tmp_path / "transcript.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return str(p)

    def test_parses_basic_segments(self, tmp_path):
        path = self._write_payload(tmp_path, {
            "result": {"language": "en"},
            "transcription": [
                {
                    "offsets": {"from": 0, "to": 1500},
                    "text": "Hello world",
                },
                {
                    "offsets": {"from": 1500, "to": 3000},
                    "text": " How are you ",
                },
            ],
        })
        segs, lang = _parse_segments(path)
        assert lang == "en"
        assert len(segs) == 2
        assert isinstance(segs[0], CaptionSegment)
        assert segs[0].start_s == 0.0
        assert segs[0].end_s == 1.5
        assert segs[0].text == "Hello world"
        # whitespace stripped
        assert segs[1].text == "How are you"
        # Provider doesn't diarize / score
        assert segs[0].speaker is None
        assert segs[0].confidence is None

    def test_drops_zero_duration_segments(self, tmp_path):
        path = self._write_payload(tmp_path, {
            "transcription": [
                {"offsets": {"from": 1000, "to": 1000}, "text": "instant"},
                {"offsets": {"from": 1000, "to": 999}, "text": "negative"},
                {"offsets": {"from": 1000, "to": 2000}, "text": "valid"},
            ],
        })
        segs, _ = _parse_segments(path)
        assert len(segs) == 1
        assert segs[0].text == "valid"

    def test_skips_empty_text_entries(self, tmp_path):
        path = self._write_payload(tmp_path, {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": ""},
                {"offsets": {"from": 1000, "to": 2000}, "text": "   "},
                {"offsets": {"from": 2000, "to": 3000}, "text": "real"},
            ],
        })
        segs, _ = _parse_segments(path)
        assert len(segs) == 1
        assert segs[0].text == "real"

    def test_skips_missing_offsets(self, tmp_path):
        path = self._write_payload(tmp_path, {
            "transcription": [
                {"offsets": {}, "text": "no times"},
                {"offsets": {"from": None, "to": None}, "text": "still none"},
                {"offsets": {"from": 0, "to": 500}, "text": "good"},
            ],
        })
        segs, _ = _parse_segments(path)
        assert len(segs) == 1
        assert segs[0].text == "good"

    def test_language_defaults_to_empty_string_when_missing(self, tmp_path):
        path = self._write_payload(tmp_path, {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": "hi"},
            ],
        })
        segs, lang = _parse_segments(path)
        assert lang == ""
        assert len(segs) == 1

    def test_handles_empty_transcription_list(self, tmp_path):
        path = self._write_payload(tmp_path, {"transcription": []})
        segs, lang = _parse_segments(path)
        assert segs == []
        assert lang == ""


# ---------------------------------------------------------------------------
# _read_text_if_exists
# ---------------------------------------------------------------------------


class TestReadTextIfExists:
    def test_returns_empty_string_for_missing_file(self, tmp_path):
        assert _read_text_if_exists(str(tmp_path / "nope.srt")) == ""

    def test_returns_contents_for_existing_file(self, tmp_path):
        p = tmp_path / "x.vtt"
        p.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nhi\n", encoding="utf-8")
        assert "WEBVTT" in _read_text_if_exists(str(p))


# ---------------------------------------------------------------------------
# transcribe — happy + failure paths
# ---------------------------------------------------------------------------


def _seed_whisper_outputs(tmpdir: str, *, with_segments: bool = True) -> None:
    """Populate a temp dir with the artifacts whisper.cpp would write."""
    payload = {
        "result": {"language": "en"},
        "transcription": (
            [{"offsets": {"from": 0, "to": 1000}, "text": "hello"}]
            if with_segments else []
        ),
    }
    with open(os.path.join(tmpdir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)
    with open(os.path.join(tmpdir, "transcript.srt"), "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    with open(os.path.join(tmpdir, "transcript.vtt"), "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n")


class TestTranscribeHappyPath:
    @pytest.mark.asyncio
    async def test_success_returns_segments_and_sidecars(self, tmp_path):
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00" * 16)
        model = tmp_path / "ggml-base.bin"
        model.write_bytes(b"\x00" * 16)

        provider = _make_provider({
            "binary_path": "whisper-cli",
            "model_path": str(model),
        })

        # Patch the tempfile factory so we can pre-seed the outputs ffmpeg
        # would normally produce. Use a manageable side-effect that wraps
        # tempfile.TemporaryDirectory and seeds files inside.
        seeded: dict[str, str] = {}

        original_tmpdir = whisper_mod.tempfile.TemporaryDirectory

        class _SeededTmpDir:
            def __init__(self, *a, **kw):
                self._inner = original_tmpdir(*a, **kw)

            def __enter__(self):
                path = self._inner.__enter__()
                _seed_whisper_outputs(path)
                seeded["path"] = path
                return path

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

        with patch("services.caption_providers.whisper_local.shutil.which", return_value="/fake/whisper-cli"):
            with patch.object(whisper_mod.tempfile, "TemporaryDirectory", _SeededTmpDir):
                with patch.object(
                    whisper_mod, "_run_whisper_blocking",
                    return_value=(0, "", ""),
                ):
                    result = await provider.transcribe(
                        audio_path=str(audio),
                    )

        assert result.success is True
        assert result.error is None
        assert len(result.segments) == 1
        assert result.segments[0].text == "hello"
        assert result.language == "en"
        assert "WEBVTT" in result.vtt_text
        assert "00:00:00,000" in result.srt_text
        # Local provider — never charges dollars.
        assert result.cost_usd == 0.0
        # Metadata reflects config.
        assert result.metadata["binary"] == "/fake/whisper-cli"
        assert result.metadata["audio_basename"] == "in.wav"

    @pytest.mark.asyncio
    async def test_no_segments_after_clean_run_marks_failure(self, tmp_path):
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00")
        model = tmp_path / "ggml.bin"
        model.write_bytes(b"\x00")

        provider = _make_provider({
            "binary_path": "whisper-cli",
            "model_path": str(model),
        })

        original_tmpdir = whisper_mod.tempfile.TemporaryDirectory

        class _SeededTmpDir:
            def __init__(self, *a, **kw):
                self._inner = original_tmpdir(*a, **kw)

            def __enter__(self):
                path = self._inner.__enter__()
                # Empty transcription list — JSON exists but yields no segments
                _seed_whisper_outputs(path, with_segments=False)
                return path

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

        with patch("services.caption_providers.whisper_local.shutil.which", return_value="/fake/whisper-cli"):
            with patch.object(whisper_mod.tempfile, "TemporaryDirectory", _SeededTmpDir):
                with patch.object(
                    whisper_mod, "_run_whisper_blocking",
                    return_value=(0, "", ""),
                ):
                    result = await provider.transcribe(audio_path=str(audio))

        assert result.success is False
        assert "no usable segments" in (result.error or "").lower()


class TestTranscribeFailurePath:
    @pytest.mark.asyncio
    async def test_nonzero_returncode_marks_failure(self, tmp_path):
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00")
        model = tmp_path / "ggml.bin"
        model.write_bytes(b"\x00")

        provider = _make_provider({
            "binary_path": "whisper-cli",
            "model_path": str(model),
        })

        long_stderr = "boom\n" * 200  # > 500 chars → must be truncated
        with patch("services.caption_providers.whisper_local.shutil.which", return_value="/fake/whisper-cli"):
            with patch.object(
                whisper_mod, "_run_whisper_blocking",
                return_value=(2, "", long_stderr),
            ):
                result = await provider.transcribe(audio_path=str(audio))

        assert result.success is False
        assert "exited 2" in (result.error or "")
        # stderr should be truncated to last 500 chars
        assert "boom" in (result.error or "")

    @pytest.mark.asyncio
    async def test_clean_exit_but_no_json_marks_failure(self, tmp_path):
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00")
        model = tmp_path / "ggml.bin"
        model.write_bytes(b"\x00")

        provider = _make_provider({
            "binary_path": "whisper-cli",
            "model_path": str(model),
        })

        # Don't pre-seed JSON — whisper.cpp claims success but produced
        # nothing.
        with patch("services.caption_providers.whisper_local.shutil.which", return_value="/fake/whisper-cli"):
            with patch.object(
                whisper_mod, "_run_whisper_blocking",
                return_value=(0, "", ""),
            ):
                result = await provider.transcribe(audio_path=str(audio))

        assert result.success is False
        assert "no JSON output" in (result.error or "")

    @pytest.mark.asyncio
    async def test_subprocess_wrapper_exception_caught(self, tmp_path):
        """to_thread surfaces — exception inside _run_whisper_blocking."""
        audio = tmp_path / "in.wav"
        audio.write_bytes(b"\x00")
        model = tmp_path / "ggml.bin"
        model.write_bytes(b"\x00")

        provider = _make_provider({
            "binary_path": "whisper-cli",
            "model_path": str(model),
        })

        with patch("services.caption_providers.whisper_local.shutil.which", return_value="/fake/whisper-cli"):
            with patch.object(
                whisper_mod, "_run_whisper_blocking",
                side_effect=OSError("permission denied"),
            ):
                result = await provider.transcribe(audio_path=str(audio))

        assert result.success is False
        assert "OSError" in (result.error or "")

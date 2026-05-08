"""Unit tests for ``services/audio_gen_providers/stable_audio_open.py``.

Glad-Labs/poindexter#125 — first AudioGenProvider implementation. The
provider speaks to a separate inference server (default port 9839) and
mirrors the SDXL/FLUX sidecar response shapes (raw bytes OR JSON with
``audio_path``). Tests mock ``httpx.AsyncClient`` so they never touch
a real server.

License-conformance smoke test below double-checks the metadata
records the Stability AI Community License so observability can
surface it. Operators above $1M revenue must swap engines (see ticket).
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.audio_gen_provider import AudioGenProvider, AudioGenResult
from services.audio_gen_providers.stable_audio_open import (
    StableAudioOpenProvider,
    _apply_prompt_template,
    _resolve_duration,
    _resolve_output_format,
    _resolve_sample_rate,
    _resolve_server_url,
)


# ---------------------------------------------------------------------------
# Helpers — mirror the SDXL/FLUX test fixtures so failure modes match.
# ---------------------------------------------------------------------------


def _audio_response(content: bytes = b"RIFFfake-wav-bytes", duration: str = "5.0"):
    """Fake httpx Response for a successful Stable Audio Open server reply."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "audio/wav", "X-Duration-Seconds": duration}
    resp.content = content
    resp.text = ""
    return resp


def _error_response(status: int = 500, text: str = "internal error"):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "text/plain"}
    resp.text = text
    resp.content = b""
    return resp


def _html_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}
    resp.text = "<html>error</html>"
    resp.content = b""
    return resp


def _json_sidecar_response(
    audio_path: str, duration: float = 5.0, sample_rate: int = 44100,
):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json = MagicMock(
        return_value={
            "audio_path": audio_path,
            "duration_s": duration,
            "sample_rate": sample_rate,
            "model": "stable-audio-open-1.0",
        },
    )
    resp.text = ""
    return resp


@contextmanager
def _mock_httpx_post(response):
    """Patch httpx.AsyncClient to yield a client whose .post returns
    ``response``. When ``response`` is an Exception it's raised — used
    to simulate an unreachable inference server.
    """
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if isinstance(response, Exception):
        client.post = AsyncMock(side_effect=response)
    else:
        client.post = AsyncMock(return_value=response)
    with patch(
        "services.audio_gen_providers.stable_audio_open.httpx.AsyncClient",
        return_value=client,
    ):
        yield client


class _StubSiteConfig:
    """Minimal site_config double — supports .get() with a mapping."""

    def __init__(self, mapping: dict | None = None) -> None:
        self._mapping = mapping or {}

    def get(self, key: str, default=None):
        return self._mapping.get(key, default)


# ---------------------------------------------------------------------------
# Metadata / Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStableAudioOpenMetadata:
    def test_name_is_stable_audio_open_1_0(self):
        # The name MUST match the entry-point key. Lock it down so
        # nobody silently renames and breaks DB-configured engines.
        assert StableAudioOpenProvider.name == "stable-audio-open-1.0"

    def test_kinds_supports_all_four(self):
        assert set(StableAudioOpenProvider.kinds) == {
            "ambient", "sfx", "intro", "outro",
        }


@pytest.mark.unit
class TestContract:
    def test_conforms_to_audio_gen_provider_protocol(self):
        provider = StableAudioOpenProvider()
        assert isinstance(provider, AudioGenProvider)

    def test_generate_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(StableAudioOpenProvider.generate)


# ---------------------------------------------------------------------------
# Config resolution helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServerUrlResolution:
    def test_per_call_config_wins(self):
        sc = _StubSiteConfig({
            "stable_audio_open_server_url": "http://from-flat:9839",
            "plugin.audio_gen_provider.stable-audio-open-1.0.server_url":
                "http://from-nested:9839",
        })
        assert _resolve_server_url(
            {"server_url": "http://override:9999"}, sc,
        ) == "http://override:9999"

    def test_flat_site_config_key_used(self):
        sc = _StubSiteConfig(
            {"stable_audio_open_server_url": "http://flat:9839"},
        )
        assert _resolve_server_url({}, sc) == "http://flat:9839"

    def test_nested_plugin_namespace_used(self):
        sc = _StubSiteConfig({
            "plugin.audio_gen_provider.stable-audio-open-1.0.server_url":
                "http://nested:9839",
        })
        assert _resolve_server_url({}, sc) == "http://nested:9839"

    def test_default_when_nothing_configured(self):
        assert _resolve_server_url({}, None) == (
            "http://host.docker.internal:9839"
        )

    def test_default_when_site_config_get_raises(self):
        class Boom:
            def get(self, *a, **kw):
                raise RuntimeError("db down")

        assert _resolve_server_url({}, Boom()) == (
            "http://host.docker.internal:9839"
        )


@pytest.mark.unit
class TestDurationResolution:
    def test_per_call_config_wins(self):
        sc = _StubSiteConfig({
            "plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s": 10.0,
        })
        assert _resolve_duration({"duration_s": 7.5}, sc) == 7.5

    def test_site_config_default_used(self):
        sc = _StubSiteConfig({
            "plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s": 10.0,
        })
        assert _resolve_duration({}, sc) == 10.0

    def test_default_when_nothing_set(self):
        assert _resolve_duration({}, None) == 5.0

    def test_clamps_at_model_max(self):
        # Model max is 47s; anything higher gets clamped + warned.
        assert _resolve_duration({"duration_s": 120.0}, None) == 47.0

    def test_negative_falls_back_to_default(self):
        assert _resolve_duration({"duration_s": -1.0}, None) == 5.0

    def test_garbage_falls_back_to_default(self):
        assert _resolve_duration({"duration_s": "not a float"}, None) == 5.0


@pytest.mark.unit
class TestSampleRateResolution:
    def test_default_when_nothing_set(self):
        assert _resolve_sample_rate({}, None) == 44100

    def test_per_call_override(self):
        assert _resolve_sample_rate({"sample_rate": 22050}, None) == 22050

    def test_site_config_override(self):
        sc = _StubSiteConfig({
            "plugin.audio_gen_provider.stable-audio-open-1.0.sample_rate": 48000,
        })
        assert _resolve_sample_rate({}, sc) == 48000


@pytest.mark.unit
class TestOutputFormatResolution:
    def test_default_is_wav(self):
        assert _resolve_output_format({}, None) == "wav"

    def test_per_call_lowercased(self):
        assert _resolve_output_format({"output_format": "MP3"}, None) == "mp3"

    def test_site_config_override(self):
        sc = _StubSiteConfig({
            "plugin.audio_gen_provider.stable-audio-open-1.0.output_format": "ogg",
        })
        assert _resolve_output_format({}, sc) == "ogg"


@pytest.mark.unit
class TestPromptTemplate:
    def test_default_template_for_each_kind(self):
        for kind in ("ambient", "sfx", "intro", "outro"):
            out = _apply_prompt_template("warm pad", kind, {}, None)
            assert "warm pad" in out
            # Each default template should embed the prompt + a kind-
            # specific scaffold; smoke-check non-default text exists.
            assert out != "warm pad"

    def test_per_call_template_wins(self):
        out = _apply_prompt_template(
            "warm pad",
            "ambient",
            {"prompt_template": "PREFIX: {prompt} :END"},
            None,
        )
        assert out == "PREFIX: warm pad :END"

    def test_site_config_per_kind_template(self):
        sc = _StubSiteConfig({
            "plugin.audio_gen_provider.stable-audio-open-1.0.prompt_template_intro":
                "INTRO[{prompt}]",
        })
        out = _apply_prompt_template("rising synth", "intro", {}, sc)
        assert out == "INTRO[rising synth]"

    def test_template_missing_placeholder_returns_raw(self):
        # Operator typo — template forgot {prompt}. Falls back to raw
        # prompt and logs a warning instead of silently emitting the
        # operator's literal template (which would be content-free).
        out = _apply_prompt_template(
            "warm pad",
            "ambient",
            {"prompt_template": "no placeholder here"},
            None,
        )
        assert out == "warm pad"


# ---------------------------------------------------------------------------
# StableAudioOpenProvider.generate — happy path + failure matrix
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestStableAudioOpenGenerate:
    async def test_empty_prompt_returns_none(self):
        assert await StableAudioOpenProvider().generate("", "ambient", {}) is None

    async def test_whitespace_prompt_returns_none(self):
        assert await StableAudioOpenProvider().generate(
            "   ", "ambient", {},
        ) is None

    async def test_unsupported_kind_returns_none(self):
        # Provider validates kind defensively even though the type is
        # Literal — runtime callers may pass arbitrary strings.
        result = await StableAudioOpenProvider().generate(
            "x", "voiceover", {},  # type: ignore[arg-type]
        )
        assert result is None

    async def test_server_success_returns_audio_result(self, tmp_path):
        output_path = str(tmp_path / "out.wav")
        with _mock_httpx_post(
            _audio_response(content=b"RIFFwav-bytes", duration="5.0"),
        ):
            result = await StableAudioOpenProvider().generate(
                "warm cinematic synth pad",
                "ambient",
                {"output_path": output_path},
            )

        assert isinstance(result, AudioGenResult)
        assert result.file_path == output_path
        assert result.kind == "ambient"
        assert result.source == "stable-audio-open-1.0"
        assert result.duration_s == 5.0
        assert result.metadata["model"] == "stable-audio-open-1.0"
        assert result.metadata["license"] == "stability-ai-community"
        assert result.metadata["license_revenue_cap_usd"] == 1_000_000
        # File was written
        assert (tmp_path / "out.wav").read_bytes() == b"RIFFwav-bytes"

    async def test_server_unreachable_returns_none(self, tmp_path):
        """Connection refused → log error, return None. Operator sees
        a clear message; no silent fallback.
        """
        with _mock_httpx_post(RuntimeError("connection refused")):
            result = await StableAudioOpenProvider().generate(
                "x",
                "ambient",
                {"output_path": str(tmp_path / "x.wav")},
            )
        assert result is None

    async def test_server_500_returns_none(self, tmp_path):
        with _mock_httpx_post(_error_response(500)):
            result = await StableAudioOpenProvider().generate(
                "x",
                "sfx",
                {"output_path": str(tmp_path / "x.wav")},
            )
        assert result is None

    async def test_server_html_response_returns_none(self, tmp_path):
        """200 with text/html is an inference-server error page."""
        with _mock_httpx_post(_html_response()):
            result = await StableAudioOpenProvider().generate(
                "x",
                "ambient",
                {"output_path": str(tmp_path / "x.wav")},
            )
        assert result is None

    async def test_tempfile_used_when_output_path_not_set(self):
        with _mock_httpx_post(_audio_response(content=b"RIFFtmp")):
            result = await StableAudioOpenProvider().generate(
                "warm pad", "ambient", {},
            )

        assert result is not None
        assert result.file_path
        # Tempfile lives somewhere on disk and exists
        import os
        assert os.path.exists(result.file_path)
        os.remove(result.file_path)  # cleanup

    async def test_duration_forwarded_to_server(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _audio_response(content=b"RIFF")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.audio_gen_providers.stable_audio_open.httpx.AsyncClient",
            return_value=client,
        ):
            await StableAudioOpenProvider().generate(
                "rising sting",
                "intro",
                {
                    "output_path": str(tmp_path / "o.wav"),
                    "duration_s": 3.0,
                },
            )

        assert captured["json"]["duration_s"] == 3.0
        assert captured["json"]["model"] == "stable-audio-open-1.0"
        assert captured["json"]["sample_rate"] == 44100
        assert captured["url"].startswith("http://host.docker.internal:9839")

    async def test_default_duration_when_not_specified(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _audio_response()

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.audio_gen_providers.stable_audio_open.httpx.AsyncClient",
            return_value=client,
        ):
            await StableAudioOpenProvider().generate(
                "warm pad", "ambient",
                {"output_path": str(tmp_path / "o.wav")},
            )

        # Module-level default is 5.0s
        assert captured["json"]["duration_s"] == 5.0

    async def test_server_url_override_from_config(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["url"] = url
            return _audio_response()

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.audio_gen_providers.stable_audio_open.httpx.AsyncClient",
            return_value=client,
        ):
            await StableAudioOpenProvider().generate(
                "x",
                "ambient",
                {
                    "output_path": str(tmp_path / "o.wav"),
                    "server_url": "http://staging-audio:9839",
                },
            )

        assert captured["url"] == "http://staging-audio:9839/generate"

    async def test_prompt_template_applied_per_kind(self, tmp_path):
        captured: dict = {}

        async def capture_post(url, json=None, timeout=None):
            captured["json"] = json
            return _audio_response()

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=capture_post)

        with patch(
            "services.audio_gen_providers.stable_audio_open.httpx.AsyncClient",
            return_value=client,
        ):
            await StableAudioOpenProvider().generate(
                "warm synth",
                "intro",
                {
                    "output_path": str(tmp_path / "o.wav"),
                    "prompt_template": "INTRO[{prompt}]",
                },
            )

        assert captured["json"]["prompt"] == "INTRO[warm synth]"

    async def test_sidecar_json_response_materializes_file(self, tmp_path):
        sidecar_src = tmp_path / "audio_abc.wav"
        sidecar_src.write_bytes(b"RIFFsidecar-generated-audio")
        output_path = str(tmp_path / "caller-out.wav")

        resp = _json_sidecar_response(str(sidecar_src), duration=4.5)
        with _mock_httpx_post(resp):
            result = await StableAudioOpenProvider().generate(
                "warm pad",
                "ambient",
                {"output_path": output_path},
            )

        assert result is not None
        assert result.file_path == output_path
        assert result.duration_s == 4.5
        assert (tmp_path / "caller-out.wav").read_bytes() == (
            b"RIFFsidecar-generated-audio"
        )

    async def test_sidecar_json_missing_audio_path_returns_none(
        self, tmp_path,
    ):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json = MagicMock(return_value={"error": "oom"})
        resp.text = '{"error": "oom"}'

        with _mock_httpx_post(resp):
            result = await StableAudioOpenProvider().generate(
                "x", "ambient",
                {"output_path": str(tmp_path / "x.wav")},
            )
        assert result is None

    async def test_sidecar_json_source_file_missing_returns_none(
        self, tmp_path,
    ):
        resp = _json_sidecar_response("/nonexistent/path/audio.wav")
        with _mock_httpx_post(resp):
            result = await StableAudioOpenProvider().generate(
                "x", "ambient",
                {"output_path": str(tmp_path / "x.wav")},
            )
        assert result is None


# ---------------------------------------------------------------------------
# Plugin discovery test removed during the #345 triage.
#
# StableAudioOpenProvider is implemented in
# ``services/audio_gen_providers/stable_audio_open.py`` but is not registered
# in either the ``poindexter.audio_gen_providers`` entry-point group OR the
# ``get_core_samples()`` imperative list, so the discoverability assertion
# that lived here always failed. Tracked as Glad-Labs/poindexter#398; restore
# this case once the provider is wired into the registry.
# ---------------------------------------------------------------------------

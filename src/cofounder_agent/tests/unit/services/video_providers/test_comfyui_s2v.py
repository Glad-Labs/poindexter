"""ComfyUIProvider speech-to-video path — chunk math, S2V graph wiring, the
two-upload transport ladder and the ``__AUDIO__`` override, all against a
faked ``httpx.AsyncClient`` so no test reaches a live sidecar (the CI runners
sit on the operator box where :8188 can be REAL).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.video_providers import comfyui
from poindexter.services.video_providers.comfyui import (
    ComfyUIProvider,
    build_s2v_graph,
    s2v_chunks_for,
)


class _FakeSiteConfig:
    def __init__(self, values: dict | None = None) -> None:
        self._values = values or {}

    def get(self, key, default=None):
        val = self._values.get(key)
        return default if val in (None, "") else val


class _Resp:
    def __init__(self, status_code, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _resp(status, payload=None, content=b""):
    return _Resp(status, payload, content)


class _FakeClient:
    """Routes GET/POST by URL suffix, echoes uploaded filenames back the way
    the real ``/upload/image`` does, and records submitted graphs."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.submitted_graphs: list[dict] = []
        self.uploads: list[tuple[str, str]] = []  # (filename, content_type)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _route(self, url: str):
        for suffix, response in self.routes.items():
            if suffix in url:
                return response
        raise AssertionError(f"unrouted URL in test: {url}")

    async def get(self, url, **kwargs):
        return self._route(url)

    async def post(self, url, **kwargs):
        if "/upload/image" in url:
            name, _raw, ctype = kwargs["files"]["image"]
            self.uploads.append((name, ctype))
            return _resp(200, {"name": name})
        if "/prompt" in url:
            self.submitted_graphs.append(kwargs.get("json", {}).get("prompt", {}))
        return self._route(url)


def _happy_routes(video_name="poindexter_talking_head_00001_.mp4"):
    return {
        "/system_stats": _resp(200, {"system": {}}),
        "/prompt": _resp(200, {"prompt_id": "pid-1"}),
        "/history/pid-1": _resp(200, {"pid-1": {
            "status": {"status_str": "success"},
            "outputs": {"91": {"images": [{"filename": video_name}]}},
        }}),
        "/view": _resp(200, content=b"MP4BYTES"),
    }


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr(comfyui, "_POLL_INTERVAL_S", 0)
    monkeypatch.setattr(comfyui.asyncio, "sleep", AsyncMock())


def _patched_client(fake):
    return patch(
        "poindexter.services.video_providers.comfyui.httpx.AsyncClient",
        MagicMock(return_value=fake),
    )


def _config(tmp_path, *, settings=None, audio=True, **extra):
    still = tmp_path / "still.png"
    still.write_bytes(b"PNG")
    cfg = {
        "output_path": str(tmp_path / "out.mp4"),
        "image_path": str(still),
        "_site_config": _FakeSiteConfig(settings or {}),
        "width": 832,
        "height": 480,
    }
    if audio:
        speech = tmp_path / "speech.wav"
        speech.write_bytes(b"RIFF....WAVE")
        cfg["audio_path"] = str(speech)
        cfg["audio_duration_s"] = 4.8
    cfg.update(extra)
    return cfg


def _by_class(graph, class_type):
    return {k: v for k, v in graph.items() if v["class_type"] == class_type}


# ---------------------------------------------------------------------------
# chunk math
# ---------------------------------------------------------------------------

class TestChunkMath:
    def test_one_chunk_covers_its_own_length(self):
        assert s2v_chunks_for(4.8, 77, 16, 6) == 1

    def test_just_over_one_chunk_needs_two(self):
        assert s2v_chunks_for(4.9, 77, 16, 6) == 2
        assert s2v_chunks_for(8.0, 77, 16, 6) == 2

    def test_cap_is_honoured(self):
        assert s2v_chunks_for(60.0, 77, 16, 6) == 6
        assert s2v_chunks_for(60.0, 77, 16, 0) == 1

    def test_degenerate_inputs_render_one_chunk(self):
        assert s2v_chunks_for(0.0, 77, 16, 6) == 1
        assert s2v_chunks_for(5.0, 0, 16, 6) == 1
        assert s2v_chunks_for(5.0, 77, 0, 6) == 1


# ---------------------------------------------------------------------------
# graph wiring
# ---------------------------------------------------------------------------

def _graph(**overrides):
    base = dict(
        prompt="a presenter speaks", negative="neg",
        ref_image_name="ref.png", audio_name="speech.wav",
        width=832, height=480, length=77, fps=16, seed=7,
        steps=20, cfg=6.0, shift=8.0, sampler="uni_pc", chunks=1,
    )
    base.update(overrides)
    return build_s2v_graph(**base)


class TestBuildS2VGraph:
    def test_single_chunk_wiring(self):
        g = _graph()
        assert _by_class(g, "WanSoundImageToVideoExtend") == {}
        (enc_id, enc), = _by_class(g, "AudioEncoderEncode").items()
        (aud_id, aud), = _by_class(g, "LoadAudio").items()
        assert aud["inputs"]["audio"] == "speech.wav"
        assert enc["inputs"]["audio"] == [aud_id, 0]
        (s2v_id, s2v), = _by_class(g, "WanSoundImageToVideo").items()
        (img_id, img), = _by_class(g, "LoadImage").items()
        assert img["inputs"]["image"] == "ref.png"
        assert s2v["inputs"]["ref_image"] == [img_id, 0]
        assert s2v["inputs"]["audio_encoder_output"] == [enc_id, 0]
        assert (s2v["inputs"]["width"], s2v["inputs"]["height"], s2v["inputs"]["length"]) == (832, 480, 77)
        (ks_id, ks), = _by_class(g, "KSampler").items()
        assert ks["inputs"]["sampler_name"] == "uni_pc"
        assert (ks["inputs"]["steps"], ks["inputs"]["cfg"], ks["inputs"]["seed"]) == (20, 6.0, 7)
        assert ks["inputs"]["latent_image"] == [s2v_id, 2]
        (ms_id, ms), = _by_class(g, "ModelSamplingSD3").items()
        assert ms["inputs"]["shift"] == 8.0
        assert ks["inputs"]["model"] == [ms_id, 0]
        (cv_id, cv), = _by_class(g, "CreateVideo").items()
        assert cv["inputs"]["audio"] == [aud_id, 0]
        assert cv["inputs"]["fps"] == 16.0
        (dec_id, _), = _by_class(g, "VAEDecode").items()
        assert cv["inputs"]["images"] == [dec_id, 0]
        (sv,), = [list(_by_class(g, "SaveVideo").values())]
        assert sv["inputs"]["video"] == [cv_id, 0]
        assert sv["inputs"]["filename_prefix"] == "poindexter_talking_head"

    def test_multi_chunk_chains_extend_nodes_off_the_previous_latent(self):
        g = _graph(chunks=3)
        exts = _by_class(g, "WanSoundImageToVideoExtend")
        samplers = _by_class(g, "KSampler")
        assert len(exts) == 2 and len(samplers) == 3
        assert len(_by_class(g, "VAEDecode")) == 3
        batches = _by_class(g, "ImageBatch")
        assert len(batches) == 2
        # every Extend consumes a sampler's latent, never the raw S2V latent
        sampler_ids = set(samplers)
        for ext in exts.values():
            assert ext["inputs"]["video_latent"][0] in sampler_ids
            assert ext["inputs"]["ref_image"] == ["8", 0]
            assert ext["inputs"]["length"] == 77
        # chunk seeds differ so the chunks don't repeat the same noise
        assert len({s["inputs"]["seed"] for s in samplers.values()}) == 3
        # CreateVideo reads the LAST ImageBatch and still muxes the full audio
        (cv,), = [list(_by_class(g, "CreateVideo").values())]
        last_batch = max(batches, key=int)
        assert cv["inputs"]["images"] == [last_batch, 0]
        assert cv["inputs"]["audio"] == ["6", 0]

    def test_weight_filenames_come_from_arguments(self):
        g = _graph(model="s2v.safetensors", audio_encoder="w2v.safetensors",
                   text_encoder="te.safetensors", vae="vae.safetensors")
        assert _by_class(g, "UNETLoader")["1"]["inputs"]["unet_name"] == "s2v.safetensors"
        assert _by_class(g, "AudioEncoderLoader")["5"]["inputs"]["audio_encoder_name"] == "w2v.safetensors"
        assert _by_class(g, "CLIPLoader")["3"]["inputs"]["clip_name"] == "te.safetensors"
        assert _by_class(g, "VAELoader")["4"]["inputs"]["vae_name"] == "vae.safetensors"


# ---------------------------------------------------------------------------
# fetch() speech path
# ---------------------------------------------------------------------------

class TestFetchSpeech:
    @pytest.mark.asyncio
    async def test_happy_path_uploads_image_and_audio_and_reports_s2v(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path)
        with _patched_client(fake):
            results = await provider.fetch("a presenter speaks", cfg)
        assert provider.last_error == ""
        assert [n for n, _ in fake.uploads] == ["still.png", "speech.wav"]
        assert fake.uploads[1][1] == "audio/wav"
        graph = fake.submitted_graphs[0]
        assert _by_class(graph, "WanSoundImageToVideo")
        assert _by_class(graph, "LoadAudio")["6"]["inputs"]["audio"] == "speech.wav"
        assert _by_class(graph, "LoadImage")["8"]["inputs"]["image"] == "still.png"
        (out,) = results
        assert out.metadata["s2v"] is True and out.metadata["i2v"] is False
        assert out.metadata["model"] == "wan2.2-s2v-14b-fp8"
        assert out.metadata["chunks"] == 1
        assert out.metadata["audio_truncated"] is False
        assert out.duration_s == 4  # 77 frames @ 16 fps, floored
        with open(cfg["output_path"], "rb") as fh:
            assert fh.read() == b"MP4BYTES"

    @pytest.mark.asyncio
    async def test_long_speech_chains_chunks_and_scales_the_timeout(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path, audio_duration_s=8.0)
        seen: dict = {}
        real_poll = provider._poll

        async def spy(client, server_url, prompt_id, timeout_s, heartbeat_cb=None):
            seen["timeout_s"] = timeout_s
            return await real_poll(client, server_url, prompt_id, timeout_s, heartbeat_cb=heartbeat_cb)

        provider._poll = spy  # type: ignore[method-assign]
        with _patched_client(fake):
            (out,) = await provider.fetch("a presenter speaks", cfg)
        assert out.metadata["chunks"] == 2
        assert len(_by_class(fake.submitted_graphs[0], "WanSoundImageToVideoExtend")) == 1
        assert seen["timeout_s"] == 2 * 900.0

    @pytest.mark.asyncio
    async def test_cap_truncates_and_flags_it(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path, settings={"video_comfyui_s2v_max_chunks": "1"}, audio_duration_s=20.0)
        with _patched_client(fake):
            (out,) = await provider.fetch("a presenter speaks", cfg)
        assert out.metadata["chunks"] == 1
        assert out.metadata["audio_truncated"] is True

    @pytest.mark.asyncio
    async def test_missing_audio_file_fails_with_reason(self, tmp_path):
        provider = ComfyUIProvider()
        cfg = _config(tmp_path)
        cfg["audio_path"] = str(tmp_path / "nope.wav")
        assert await provider.fetch("p", cfg) == []
        assert "speech audio missing" in provider.last_error

    @pytest.mark.asyncio
    async def test_unknown_duration_fails_loud(self, tmp_path, monkeypatch):
        provider = ComfyUIProvider()
        cfg = _config(tmp_path)
        cfg.pop("audio_duration_s")
        monkeypatch.setattr(comfyui, "_probe_audio_seconds", AsyncMock(return_value=None))
        assert await provider.fetch("p", cfg) == []
        assert "speech duration" in provider.last_error

    @pytest.mark.asyncio
    async def test_probe_supplies_the_duration_when_not_given(self, tmp_path, fast_poll, monkeypatch):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path)
        cfg.pop("audio_duration_s")
        probe = AsyncMock(return_value=9.0)
        monkeypatch.setattr(comfyui, "_probe_audio_seconds", probe)
        with _patched_client(fake):
            (out,) = await provider.fetch("p", cfg)
        probe.assert_awaited_once_with(cfg["audio_path"])
        assert out.metadata["chunks"] == 2
        assert out.metadata["audio_seconds"] == 9.0

    @pytest.mark.asyncio
    async def test_override_substitutes_the_audio_token(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        template = {
            "1": {"class_type": "LoadAudio", "inputs": {"audio": "__AUDIO__"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "__INIT_IMAGE__"}},
            "3": {"class_type": "SaveVideo", "inputs": {
                "filename_prefix": "__FILENAME_PREFIX__", "video": ["2", 0]}},
        }
        cfg = _config(tmp_path, settings={
            "video_comfyui_s2v_workflow_override_json": json.dumps(template)})
        with _patched_client(fake):
            await provider.fetch("p", cfg)
        graph = fake.submitted_graphs[0]
        assert graph["1"]["inputs"]["audio"] == "speech.wav"
        assert graph["2"]["inputs"]["image"] == "still.png"
        assert graph["3"]["inputs"]["filename_prefix"] == "poindexter_talking_head"

    @pytest.mark.asyncio
    async def test_bad_override_json_fails_with_reason(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path, settings={"video_comfyui_s2v_workflow_override_json": "{nope"})
        with _patched_client(fake):
            assert await provider.fetch("p", cfg) == []
        assert "video_comfyui_s2v_workflow_override_json" in provider.last_error

    @pytest.mark.asyncio
    async def test_i2v_path_is_untouched_without_audio(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        fake = _FakeClient({**_happy_routes(), "/history/pid-1": _resp(200, {"pid-1": {
            "status": {"status_str": "success"},
            "outputs": {"15": {"video": [{"filename": "poindexter_hero_00001_.mp4"}]}},
        }})})
        cfg = _config(tmp_path, audio=False)
        with _patched_client(fake):
            (out,) = await provider.fetch("a console", cfg)
        assert [n for n, _ in fake.uploads] == ["still.png"]
        graph = fake.submitted_graphs[0]
        assert _by_class(graph, "WanImageToVideo")
        assert not _by_class(graph, "LoadAudio")
        assert out.metadata["i2v"] is True and "s2v" not in out.metadata

"""ComfyUIProvider — graph construction, override substitution, and the
HTTP transport ladder (upload → submit → poll → download), all against a
faked ``httpx.AsyncClient`` so no test ever reaches a live sidecar (the CI
runners are containers on the operator box, where :8188 can be REAL — the
known silent-test-network-hazard shape).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.video_providers import comfyui
from services.video_providers.comfyui import (
    ComfyUIProvider,
    build_graph,
    substitute_override,
)


class _FakeSiteConfig:
    """Minimal site_config: ``get`` reads a dict, mirroring the real
    ``SiteConfig.get(key, default)`` unset-returns-default contract."""

    def __init__(self, values: dict | None = None) -> None:
        self._values = values or {}

    def get(self, key, default=None):
        val = self._values.get(key)
        return default if val in (None, "") else val


def _graph_kwargs(**overrides):
    base = dict(
        prompt="a console",
        negative="neg",
        init_image_name="still.png",
        width=832, height=480, length=81, fps=16,
        seed=7, steps=4, cfg=1.0, shift=5.0,
        use_lora=True,
    )
    base.update(overrides)
    return base


class TestBuildGraph:
    def test_lora_variant_wires_distill_loras_onto_both_experts(self):
        g = build_graph(**_graph_kwargs())
        assert g["20"]["class_type"] == "LoraLoaderModelOnly"
        assert g["21"]["class_type"] == "LoraLoaderModelOnly"
        # ModelSamplingSD3 must read the LoRA-wrapped models, not the bare
        # UNETs — wiring them to ["1",0]/["2",0] silently renders LoRA-less.
        assert g["5"]["inputs"]["model"] == ["20", 0]
        assert g["6"]["inputs"]["model"] == ["21", 0]
        assert g["20"]["inputs"]["model"] == ["1", 0]
        assert g["21"]["inputs"]["model"] == ["2", 0]

    def test_no_lora_variant_omits_lora_nodes(self):
        g = build_graph(**_graph_kwargs(use_lora=False, steps=20, cfg=3.5))
        assert "20" not in g and "21" not in g
        assert g["5"]["inputs"]["model"] == ["1", 0]
        assert g["6"]["inputs"]["model"] == ["2", 0]

    def test_two_expert_step_split(self):
        g = build_graph(**_graph_kwargs(steps=20))
        high, low = g["11"]["inputs"], g["12"]["inputs"]
        assert high["start_at_step"] == 0
        assert high["end_at_step"] == 10
        assert high["return_with_leftover_noise"] == "enable"
        assert low["start_at_step"] == 10
        assert low["end_at_step"] == 10000
        assert low["add_noise"] == "disable"
        # The low expert continues the HIGH expert's latent.
        assert low["latent_image"] == ["11", 0]

    def test_geometry_length_and_save_settings_land(self):
        g = build_graph(**_graph_kwargs(width=480, height=832, length=49, fps=24))
        wan = g["10"]["inputs"]
        assert (wan["width"], wan["height"], wan["length"]) == (480, 832, 49)
        assert g["14"]["inputs"]["fps"] == 24
        assert g["15"]["inputs"]["format"] == "mp4"

    def test_model_filenames_are_parameters(self):
        g = build_graph(**_graph_kwargs(
            high_model="h.safetensors", low_model="l.safetensors",
            text_encoder="te.safetensors", vae="v.safetensors",
        ))
        assert g["1"]["inputs"]["unet_name"] == "h.safetensors"
        assert g["2"]["inputs"]["unet_name"] == "l.safetensors"
        assert g["3"]["inputs"]["clip_name"] == "te.safetensors"
        assert g["4"]["inputs"]["vae_name"] == "v.safetensors"


class TestSubstituteOverride:
    def test_typed_substitution(self):
        template = {
            "9": {"inputs": {"image": "__INIT_IMAGE__"}},
            "10": {"inputs": {"width": "__WIDTH__", "cfg": "__CFG__"}},
            "7": {"inputs": {"text": "__PROMPT__"}},
        }
        out = substitute_override(template, {
            "__INIT_IMAGE__": "x.png", "__WIDTH__": 832,
            "__CFG__": 3.5, "__PROMPT__": "hello",
        })
        assert out["9"]["inputs"]["image"] == "x.png"
        assert out["10"]["inputs"]["width"] == 832
        assert out["10"]["inputs"]["cfg"] == 3.5
        assert out["7"]["inputs"]["text"] == "hello"

    def test_unknown_placeholder_left_verbatim(self):
        # It must fail loudly at ComfyUI validation, not silently render
        # with a half-substituted graph.
        out = substitute_override({"a": "__NOT_A_TOKEN__"}, {"__PROMPT__": "p"})
        assert out["a"] == "__NOT_A_TOKEN__"


# ---------------------------------------------------------------------------
# Transport — a routing fake for httpx.AsyncClient
# ---------------------------------------------------------------------------


def _resp(status=200, json_data=None, content=b"", text=""):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.text = text or (json.dumps(json_data) if json_data is not None else "")
    if json_data is not None:
        r.json = MagicMock(return_value=json_data)
    else:
        r.json = MagicMock(side_effect=ValueError("no json"))
    return r


class _FakeClient:
    """Routes GET/POST by URL suffix; records submitted graphs."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.submitted_graphs: list[dict] = []
        self.uploads: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _route(self, url: str):
        for suffix, responses in self.routes.items():
            if suffix in url:
                if isinstance(responses, list):
                    return responses.pop(0) if len(responses) > 1 else responses[0]
                return responses
        raise AssertionError(f"unrouted URL in test: {url}")

    async def get(self, url, **kwargs):
        out = self._route(url)
        if isinstance(out, Exception):
            raise out
        return out

    async def post(self, url, **kwargs):
        if "/prompt" in url:
            self.submitted_graphs.append(kwargs.get("json", {}).get("prompt", {}))
        if "/upload" in url:
            self.uploads.append(url)
        out = self._route(url)
        if isinstance(out, Exception):
            raise out
        return out


def _happy_routes(video_name="poindexter_hero_00001_.mp4"):
    return {
        "/system_stats": _resp(200, {"system": {}}),
        "/upload/image": _resp(200, {"name": "still.png"}),
        "/prompt": _resp(200, {"prompt_id": "pid-1"}),
        "/history/pid-1": _resp(200, {"pid-1": {
            "status": {"status_str": "success"},
            "outputs": {"15": {"video": [{"filename": video_name}]}},
        }}),
        "/view": _resp(200, content=b"MP4BYTES"),
    }


@pytest.fixture
def fast_poll(monkeypatch):
    """No real sleeping in the submit/poll loop."""
    monkeypatch.setattr(comfyui, "_POLL_INTERVAL_S", 0)
    monkeypatch.setattr(
        comfyui.asyncio, "sleep", AsyncMock(),
    )


def _patched_client(fake):
    return patch(
        "services.video_providers.comfyui.httpx.AsyncClient",
        MagicMock(return_value=fake),
    )


def _config(tmp_path, **extra):
    init = tmp_path / "still.png"
    init.write_bytes(b"PNG")
    cfg = {
        "output_path": str(tmp_path / "out.mp4"),
        "image_path": str(init),
        "_site_config": _FakeSiteConfig(extra.pop("settings", {})),
        "width": 832,
        "height": 480,
    }
    cfg.update(extra)
    return cfg


class TestFetch:
    @pytest.mark.asyncio
    async def test_happy_path_writes_output_and_reports_metadata(
        self, tmp_path, fast_poll,
    ):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path)
        with _patched_client(fake):
            results = await provider.fetch("a console", cfg)

        assert len(results) == 1
        out = results[0]
        assert out.file_path == cfg["output_path"]
        with open(cfg["output_path"], "rb") as fh:
            assert fh.read() == b"MP4BYTES"
        assert out.source == "comfyui-wan22-i2v"
        assert out.metadata["i2v"] is True
        assert out.metadata["lightning_lora"] is True
        assert out.fps == 16  # model-native, not the caller's lane fps
        assert provider.last_error == ""

    @pytest.mark.asyncio
    async def test_empty_prompt_fails_with_reason(self, tmp_path):
        provider = ComfyUIProvider()
        assert await provider.fetch("", _config(tmp_path)) == []
        assert "empty prompt" in provider.last_error

    @pytest.mark.asyncio
    async def test_missing_init_image_is_i2v_only_failure(self, tmp_path):
        provider = ComfyUIProvider()
        cfg = _config(tmp_path)
        cfg["image_path"] = str(tmp_path / "nope.png")
        assert await provider.fetch("prompt", cfg) == []
        assert "i2v-only" in provider.last_error

    @pytest.mark.asyncio
    async def test_missing_output_path_fails(self, tmp_path):
        provider = ComfyUIProvider()
        cfg = _config(tmp_path)
        cfg["output_path"] = ""
        assert await provider.fetch("prompt", cfg) == []
        assert "output_path" in provider.last_error

    @pytest.mark.asyncio
    async def test_settings_flip_to_quality_regime(self, tmp_path, fast_poll):
        """steps 20 / cfg 3.5 / LoRA off — the quality tier is two settings,
        and the submitted graph must actually reflect them."""
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path, settings={
            "video_comfyui_steps": "20",
            "video_comfyui_cfg": "3.5",
            "video_comfyui_use_lightning_lora": "false",
        })
        with _patched_client(fake):
            results = await provider.fetch("a console", cfg)

        assert results
        graph = fake.submitted_graphs[0]
        assert "20" not in graph  # no LoRA nodes
        assert graph["11"]["inputs"]["steps"] == 20
        assert graph["11"]["inputs"]["cfg"] == 3.5
        assert graph["11"]["inputs"]["end_at_step"] == 10

    @pytest.mark.asyncio
    async def test_submit_rejection_surfaces_node_error(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        routes = _happy_routes()
        routes["/prompt"] = _resp(400, {
            "error": {"message": "invalid prompt"},
            "node_errors": {"10": {"errors": [
                {"message": "Value 83 not divisible by 16 for width"},
            ]}},
        })
        fake = _FakeClient(routes)
        with _patched_client(fake):
            assert await provider.fetch("p", _config(tmp_path)) == []
        assert "not divisible" in provider.last_error

    @pytest.mark.asyncio
    async def test_execution_error_surfaces_exception_message(
        self, tmp_path, fast_poll,
    ):
        provider = ComfyUIProvider()
        routes = _happy_routes()
        routes["/history/pid-1"] = _resp(200, {"pid-1": {
            "status": {"status_str": "error", "messages": [
                ["execution_start", {}],
                ["execution_error", {
                    "exception_message": "Allocation on device (OOM)",
                }],
            ]},
            "outputs": {},
        }})
        fake = _FakeClient(routes)
        with _patched_client(fake):
            assert await provider.fetch("p", _config(tmp_path)) == []
        assert "Allocation on device" in provider.last_error

    @pytest.mark.asyncio
    async def test_ready_wait_timeout_names_the_sidecar(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        routes = {"/system_stats": ConnectionError("refused")}
        fake = _FakeClient(routes)
        cfg = _config(tmp_path, settings={"video_comfyui_ready_wait_s": "0"})
        with _patched_client(fake):
            assert await provider.fetch("p", cfg) == []
        assert "not ready" in provider.last_error
        assert "--profile comfyui" in provider.last_error

    @pytest.mark.asyncio
    async def test_workflow_override_is_submitted_substituted(
        self, tmp_path, fast_poll,
    ):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        override = json.dumps({
            "7": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "__PROMPT__"}},
            "10": {"class_type": "X", "inputs": {"width": "__WIDTH__"}},
        })
        cfg = _config(tmp_path, settings={
            "video_comfyui_workflow_override_json": override,
        })
        with _patched_client(fake):
            results = await provider.fetch("my prompt", cfg)

        assert results
        graph = fake.submitted_graphs[0]
        assert graph["7"]["inputs"]["text"] == "my prompt"
        assert graph["10"]["inputs"]["width"] == 832

    @pytest.mark.asyncio
    async def test_invalid_override_json_fails_loud(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        fake = _FakeClient(_happy_routes())
        cfg = _config(tmp_path, settings={
            "video_comfyui_workflow_override_json": "{not json",
        })
        with _patched_client(fake):
            assert await provider.fetch("p", cfg) == []
        assert "not valid" in provider.last_error
        # A broken override must not silently fall back to the code-built
        # graph — the operator asked for THEIR workflow.
        assert fake.submitted_graphs == []

    @pytest.mark.asyncio
    async def test_finished_without_video_output_fails(self, tmp_path, fast_poll):
        provider = ComfyUIProvider()
        routes = _happy_routes()
        routes["/history/pid-1"] = _resp(200, {"pid-1": {
            "status": {"status_str": "success"},
            "outputs": {"15": {"images": [{"filename": "frame.png"}]}},
        }})
        fake = _FakeClient(routes)
        with _patched_client(fake):
            assert await provider.fetch("p", _config(tmp_path)) == []
        assert "no video output" in provider.last_error

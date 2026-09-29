"""Step / guidance pinning tests for scripts/image-gen-server.py.

A distilled model's step count and guidance scale are part of the model, not
choices a caller gets to make. The Lightning LoRA was trained for exactly 4
steps at CFG 0; Z-Image-Turbo was distilled (Decoupled-DMD) to finish in 8
function evaluations, which its scheduler spells as 9 steps, at CFG 0. The
server alone knows which model is live, so it pins both numbers to the
registry and ignores whatever the request carried.

Z-Image-Turbo's steps were not pinned until 2026-09-28, and a stale caller
showed why they have to be. ``POST /api/tasks/{id}/generate-image`` (since
retired to a 410) was still sending Stable Diffusion XL base's 50 steps /
CFG 7.5 from before the model
moved. The server zeroed the guidance but rendered all 50 steps: about 5.5x
the denoise time per render (median 2.79 it/s on the live card, so ~18 s
instead of ~3 s), paid again on every OCR-gate re-roll.
``scripts/backfill_awaiting_images.py`` sent Lightning's 4.

A model with real classifier-free guidance (Stable Diffusion XL base) is the
negative control: a caller's numbers are legitimate there and must survive.

The server script imports torch at module top, so it loads under the same
scoped torch stub as the other image-gen tests (see
test_image_gen_self_heal.py for why the stub must not leak).
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "image-gen-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/image-gen-server.py from " + str(start))


def _load_image_gen_server():
    stub_installed = False
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        torch_stub.__spec__ = importlib.util.spec_from_loader("torch", loader=None)
        torch_stub.float16 = "float16"
        torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
        sys.modules["torch"] = torch_stub
        stub_installed = True

    server_path = _find_repo_root(Path(__file__)) / "scripts" / "image-gen-server.py"
    spec = importlib.util.spec_from_file_location(
        "img_gen_server_distilled_params_under_test", server_path,
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


srv = _load_image_gen_server()


class _FakeGenerator:
    def __init__(self, device: str | None = None) -> None:
        self.device = device

    def manual_seed(self, seed: int) -> "_FakeGenerator":
        return self


def _fake_torch() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            OutOfMemoryError=type("OutOfMemoryError", (RuntimeError,), {}),
            empty_cache=lambda: None,
            is_available=lambda: False,
        ),
        Generator=_FakeGenerator,
    )


class _FakeImage:
    def save(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG not really")


class RecordingPipeline:
    """Diffusion-pipeline stand-in that keeps the kwargs of every call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(images=[_FakeImage()])


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Fresh state with a recording pipeline already loaded, torch faked,
    renders written under tmp_path and the OCR gate off, so each request is
    exactly one render."""
    monkeypatch.setattr(srv, "state", srv.ServerState())
    monkeypatch.setattr(srv, "torch", _fake_torch())
    monkeypatch.setattr(srv, "OUTPUT_DIR", tmp_path)
    srv.state.ocr_gate = srv.OcrGateConfig(enabled=False)
    srv.state.pipeline = RecordingPipeline()
    return srv


async def _render(server, model: str, **request):
    """Render one request on ``model``; return the pipeline call's kwargs."""
    server.state.config = server.REGISTRY[model]
    await server.generate(server.GenerateRequest(prompt="a lighthouse", seed=7, **request))
    assert len(server.state.pipeline.calls) == 1
    return server.state.pipeline.calls[0]


@pytest.mark.parametrize("model", ["z_image_turbo", "sdxl_lightning"])
@pytest.mark.parametrize(
    "sent",
    [
        # What POST /api/tasks/{id}/generate-image sent until 2026-09-28.
        pytest.param({"steps": 50, "guidance_scale": 7.5}, id="50-steps-cfg-7.5"),
        # What scripts/backfill_awaiting_images.py sent until 2026-09-28.
        pytest.param({"steps": 4, "guidance_scale": 1.0}, id="4-steps-cfg-1"),
        # What every pipeline render path sends.
        pytest.param({}, id="neither"),
    ],
)
async def test_a_distilled_model_renders_at_its_registry_steps_and_guidance(server, model, sent):
    config = server.REGISTRY[model]

    kwargs = await _render(server, model, **sent)

    assert kwargs["num_inference_steps"] == config.default_steps, (
        f"{model} is step-distilled: it must render at its registry's "
        f"{config.default_steps} steps, not the {sent.get('steps')} the caller sent"
    )
    assert kwargs["guidance_scale"] == config.default_guidance_scale == 0.0


async def test_a_model_with_real_guidance_keeps_the_callers_numbers(server):
    """Pinning is for distilled models only. Stable Diffusion XL base runs real
    classifier-free guidance at whatever step count it is given, so a caller's
    numbers there are a legitimate choice and must reach the pipeline."""
    kwargs = await _render(server, "sdxl_base", steps=20, guidance_scale=5.0)

    assert kwargs["num_inference_steps"] == 20
    assert kwargs["guidance_scale"] == 5.0

"""Self-heal watchdog tests for scripts/image-gen-server.py.

Regression cover for the 21-hour silent outage on 2026-06-04: on a host
reboot, image-gen-server raced postgres-local (compose `depends_on` is not honored
by restart-policy restarts), read app_settings while Postgres was still in
startup (57P03 "the database system is starting up"), latched `degraded`, and
never retried — so every /generate returned 503 and ALL image + video
generation was down until a manual POST /reload.

These tests pin the recovery contract: reload_config() recovers from a
transient DB failure, and degraded_watchdog() drives that recovery on its own.

The server script imports torch at module top (used only in the GPU paths),
so we stub torch to import the module without a CUDA stack. diffusers is
imported lazily inside load_pipeline(), so it needs no stub here.
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "image-gen-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/image-gen-server.py from " + str(start))


def _load_image_gen_server():
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        # __spec__=None causes ValueError in importlib.util.find_spec() (Python 3.12+),
        # which breaks diffusers' optional-dep check in later tests.
        torch_stub.__spec__ = importlib.util.spec_from_loader("torch", loader=None)
        torch_stub.float16 = "float16"
        torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
        sys.modules["torch"] = torch_stub

    server_path = _find_repo_root(Path(__file__)) / "scripts" / "image-gen-server.py"
    spec = importlib.util.spec_from_file_location("img_gen_server_server_under_test", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


img_gen_server = _load_image_gen_server()


def test_next_retry_delay_contract():
    """CONTRIBUTION POINT contract — see next_retry_delay() in the server.

    Any reasonable cadence/backoff policy must: return a positive float, and
    poll at least as fast while degraded as it does when healthy (so a boot
    race heals promptly rather than waiting a full idle cycle)."""
    assert img_gen_server.next_retry_delay(0) > 0
    assert img_gen_server.next_retry_delay(1) > 0
    assert img_gen_server.next_retry_delay(1) <= img_gen_server.next_retry_delay(0)


def test_z_image_turbo_registry_contract():
    """The Z-Image-Turbo entry must carry its guidance-distilled config so the
    model swap renders correctly: a distinct pipeline kind, bf16, 9 steps,
    guidance 0, no fp16 variant, no negative prompt. #image-zimage-and-variety.
    """
    cfg = img_gen_server.REGISTRY.get("z_image_turbo")
    assert cfg is not None, "z_image_turbo missing from image-gen server REGISTRY"
    assert cfg.model_id == "Tongyi-MAI/Z-Image-Turbo"
    assert cfg.pipeline_kind == "zimage"
    assert cfg.torch_dtype == "bfloat16"
    assert cfg.use_fp16_variant is False
    assert cfg.supports_negative_prompt is False
    assert cfg.default_steps == 9
    assert cfg.default_guidance_scale == 0.0


def test_image_models_keep_img_gen_server_pipeline_defaults():
    """The sdxl_lightning entry must stay on the sdxl pipeline kind with an fp16
    variant + negative prompt — the new ModelConfig fields default correctly
    so existing models are unaffected by the Z-Image addition.
    """
    light = img_gen_server.REGISTRY["sdxl_lightning"]
    assert light.pipeline_kind == "sdxl"
    assert light.use_fp16_variant is True
    assert light.supports_negative_prompt is True
    assert light.torch_dtype == "float16"


def test_reload_config_preserves_pipeline_on_transient_db_failure():
    """Postgres-restart cascade regression (stack#1152).

    When the DB is temporarily unreachable, reload_config() MUST NOT clear
    state.config or unload the pipeline. The model hasn't changed — only the
    DB went away. Unloading VRAM on a transient failure caused a 2-minute
    model reload delay after Postgres recovered (the 2026-06-04 cascade).
    """
    async def body():
        unloaded = {"n": 0}

        def tracking_unload():
            unloaded["n"] += 1

        img_gen_server.unload_pipeline = tracking_unload

        async def failing_read():
            raise RuntimeError("connection refused")

        img_gen_server.read_model_setting = failing_read
        img_gen_server.state.degraded = False
        # Simulate a previously-loaded config.
        img_gen_server.state.config = img_gen_server.REGISTRY.get("sdxl_lightning")

        await img_gen_server.reload_config()

        assert img_gen_server.state.degraded is True, "should enter degraded on DB failure"
        assert unloaded["n"] == 0, (
            "pipeline must NOT be unloaded on a transient DB failure — "
            "stack#1152: this caused a 2-min reload delay after Postgres restart"
        )
        assert img_gen_server.state.config is not None, (
            "state.config must be preserved — we don't know the model changed"
        )

    asyncio.run(body())


def test_reload_config_recovers_from_transient_db_failure():
    async def body():
        calls = {"n": 0}

        async def fake_read():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("the database system is starting up")
            return "sdxl_lightning"

        img_gen_server.read_model_setting = fake_read
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.degraded = False
        img_gen_server.state.config = None

        # First read raises -> degraded latches (the 2026-06-04 failure)
        await img_gen_server.reload_config()
        assert img_gen_server.state.degraded is True
        assert "starting up" in img_gen_server.state.degraded_reason

        # Second read succeeds -> recovered (what the watchdog drives)
        await img_gen_server.reload_config()
        assert img_gen_server.state.degraded is False
        assert img_gen_server.state.config is not None
        assert img_gen_server.state.config.friendly_name == "sdxl_lightning"

    asyncio.run(body())


def test_degraded_watchdog_self_heals():
    """The watchdog must clear a latched degraded state on its own once the DB
    recovers. next_retry_delay is stubbed tiny so this isolates the loop from
    whatever cadence policy is chosen."""
    async def body():
        img_gen_server.next_retry_delay = lambda attempt: 0.01
        img_gen_server.unload_pipeline = lambda: None

        async def fake_read():
            return "sdxl_lightning"

        img_gen_server.read_model_setting = fake_read
        img_gen_server.state.degraded = True
        img_gen_server.state.degraded_reason = (
            "DB read failed for 'image_generation_model': "
            "the database system is starting up"
        )
        img_gen_server.state.config = None

        task = asyncio.create_task(img_gen_server.degraded_watchdog())
        try:
            for _ in range(50):
                await asyncio.sleep(0.01)
                if not img_gen_server.state.degraded:
                    break
            assert img_gen_server.state.degraded is False
            assert img_gen_server.state.config is not None
            assert img_gen_server.state.config.friendly_name == "sdxl_lightning"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(body())

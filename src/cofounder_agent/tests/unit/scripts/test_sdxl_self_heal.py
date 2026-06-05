"""Self-heal watchdog tests for scripts/sdxl-server.py.

Regression cover for the 21-hour silent outage on 2026-06-04: on a host
reboot, sdxl-server raced postgres-local (compose `depends_on` is not honored
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
        if (parent / "scripts" / "sdxl-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/sdxl-server.py from " + str(start))


def _load_sdxl_module():
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        # __spec__=None causes ValueError in importlib.util.find_spec() (Python 3.12+),
        # which breaks diffusers' optional-dep check in later tests.
        torch_stub.__spec__ = importlib.util.spec_from_loader("torch", loader=None)
        torch_stub.float16 = "float16"
        torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
        sys.modules["torch"] = torch_stub

    server_path = _find_repo_root(Path(__file__)) / "scripts" / "sdxl-server.py"
    spec = importlib.util.spec_from_file_location("sdxl_server_under_test", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sdxl = _load_sdxl_module()


def test_next_retry_delay_contract():
    """CONTRIBUTION POINT contract — see next_retry_delay() in the server.

    Any reasonable cadence/backoff policy must: return a positive float, and
    poll at least as fast while degraded as it does when healthy (so a boot
    race heals promptly rather than waiting a full idle cycle)."""
    assert sdxl.next_retry_delay(0) > 0
    assert sdxl.next_retry_delay(1) > 0
    assert sdxl.next_retry_delay(1) <= sdxl.next_retry_delay(0)


def test_reload_config_recovers_from_transient_db_failure():
    async def body():
        calls = {"n": 0}

        async def fake_read():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("the database system is starting up")
            return "sdxl_lightning"

        sdxl.read_model_setting = fake_read
        sdxl.unload_pipeline = lambda: None
        sdxl.state.degraded = False
        sdxl.state.config = None

        # First read raises -> degraded latches (the 2026-06-04 failure)
        await sdxl.reload_config()
        assert sdxl.state.degraded is True
        assert "starting up" in sdxl.state.degraded_reason

        # Second read succeeds -> recovered (what the watchdog drives)
        await sdxl.reload_config()
        assert sdxl.state.degraded is False
        assert sdxl.state.config is not None
        assert sdxl.state.config.friendly_name == "sdxl_lightning"

    asyncio.run(body())


def test_degraded_watchdog_self_heals():
    """The watchdog must clear a latched degraded state on its own once the DB
    recovers. next_retry_delay is stubbed tiny so this isolates the loop from
    whatever cadence policy is chosen."""
    async def body():
        sdxl.next_retry_delay = lambda attempt: 0.01
        sdxl.unload_pipeline = lambda: None

        async def fake_read():
            return "sdxl_lightning"

        sdxl.read_model_setting = fake_read
        sdxl.state.degraded = True
        sdxl.state.degraded_reason = (
            "DB read failed for 'image_generation_model': "
            "the database system is starting up"
        )
        sdxl.state.config = None

        task = asyncio.create_task(sdxl.degraded_watchdog())
        try:
            for _ in range(50):
                await asyncio.sleep(0.01)
                if not sdxl.state.degraded:
                    break
            assert sdxl.state.degraded is False
            assert sdxl.state.config is not None
            assert sdxl.state.config.friendly_name == "sdxl_lightning"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(body())

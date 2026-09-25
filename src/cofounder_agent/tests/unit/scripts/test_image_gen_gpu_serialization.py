"""GPU serialization + event-loop tests for scripts/image-gen-server.py.

2026-09-25: image-gen ran diffusers inference and model loads directly on its
asyncio event loop, so ``GET /health`` could not be served while it worked. The
Docker healthcheck failed through long renders and cold loads
(``container_health_state`` read not-healthy 13 times in the 15 days before,
for 8-22 minutes each), the brain's container health watch needed a 30-minute
override so ordinary work would not page, and every other ``/health`` probe
(``media_infra_health``'s dispatch gate among them) saw a dead server
mid-render.

Moving the GPU work into worker threads frees the loop, and it also removes
the only thing that kept two renders apart: a blocked loop cannot start a
second one. ``state.gpu_lock`` takes over that job. These tests pin both
halves:

* the cheap endpoints answer while a render or a model load runs;
* two renders never share the card, whether requests queue, arrive during a
  cold start, or are cancelled while their render thread (which cannot be
  interrupted) is still running;
* nothing drops the pipeline out from under a render: the idle unloader,
  ``/unload`` and a model switch all wait their turn.

``FakeRender`` stands in for the diffusion pipeline. It blocks a worker thread
until the test releases it and records how many renders overlap. Under the old
in-loop code a held render blocks the test's own event loop, so these tests
fail after the render's timeout instead of hanging.
"""
import asyncio
import importlib.util
import os
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

#: Upper bound on any simulated render or load. A test that forgets to release
#: one fails after this long instead of hanging the suite.
_HOLD_TIMEOUT_S = 5.0


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "image-gen-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/image-gen-server.py from " + str(start))


def _load_image_gen_server():
    # Same scoped torch stub as test_image_gen_self_heal.py (see there for why
    # it must not leak into sys.modules). The fixture below swaps in a fuller
    # fake per test; the stub only has to survive the module import.
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
        "img_gen_server_gpu_serialization_under_test", server_path,
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


srv = _load_image_gen_server()


class _FakeCuda:
    class OutOfMemoryError(RuntimeError):
        pass

    def __init__(self) -> None:
        self.reserved_mb = 0

    def is_available(self) -> bool:
        return False

    def empty_cache(self) -> None:
        pass

    def memory_allocated(self, idx: int = 0) -> int:
        return 0

    def memory_reserved(self, idx: int = 0) -> int:
        return self.reserved_mb * 1024 * 1024


class _FakeGenerator:
    def __init__(self, device: str | None = None) -> None:
        self.device = device

    def manual_seed(self, seed: int) -> "_FakeGenerator":
        self.seed = seed
        return self


def _fake_torch() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        cuda=_FakeCuda(),
        Generator=_FakeGenerator,
        randint=lambda low, high, size: types.SimpleNamespace(item=lambda: 1234),
        float16="float16",
        bfloat16="bfloat16",
    )


class _FakeImage:
    def save(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG not really")


class FakeRender:
    """Diffusion-pipeline stand-in that records overlapping renders.

    ``held=True`` blocks each call until ``release`` is set, so a test can act
    while a render is provably still running. ``hold_s`` adds a fixed render
    time on top.
    """

    def __init__(self, *, held: bool = False, hold_s: float = 0.0,
                 oom: bool = False) -> None:
        self.release = threading.Event()
        if not held:
            self.release.set()
        self.hold_s = hold_s
        self.oom = oom
        self.started = threading.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._mu = threading.Lock()

    def __call__(self, **kwargs):
        with self._mu:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            time.sleep(self.hold_s)
            self.release.wait(_HOLD_TIMEOUT_S)
            if self.oom:
                raise srv.torch.cuda.OutOfMemoryError("CUDA out of memory")
        finally:
            with self._mu:
                self.active -= 1
        return types.SimpleNamespace(images=[_FakeImage()])


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Fresh state per test (its own GPU lock, counter and config), torch
    faked, renders written under tmp_path."""
    monkeypatch.setattr(srv, "state", srv.ServerState())
    monkeypatch.setattr(srv, "torch", _fake_torch())
    monkeypatch.setattr(srv, "OUTPUT_DIR", tmp_path)
    srv.state.config = srv.REGISTRY["z_image_turbo"]
    # The OCR gate has its own tests. Off here, so a render is only a render.
    srv.state.ocr_gate = srv.OcrGateConfig(enabled=False)
    return srv


def _req(prompt: str = "a lighthouse at dusk"):
    return srv.GenerateRequest(prompt=prompt)


async def _wait_for(event: threading.Event) -> None:
    """Wait for a worker-thread signal without blocking the event loop."""
    assert await asyncio.to_thread(event.wait, _HOLD_TIMEOUT_S), "timed out waiting"


async def _let_tasks_park() -> None:
    """Give freshly created tasks a few loop turns to reach their first await."""
    for _ in range(5):
        await asyncio.sleep(0)


async def _noop() -> None:
    return None


# ---------------------------------------------------------------------------
# The loop stays free
# ---------------------------------------------------------------------------


async def test_health_answers_over_http_while_a_render_holds_the_gpu(server):
    """The bug: the healthcheck's GET /health could not be served until the
    render finished, so Docker marked a working server unhealthy."""
    render = FakeRender(held=True)
    server.state.pipeline = render
    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://image-gen") as client:
        resp = await asyncio.wait_for(client.get("/health"), timeout=2)

    assert resp.status_code == 200
    body = resp.json()
    assert render.active == 1, "/health must have been served DURING the render"
    assert body["inflight"] == 1
    assert body["gpu_busy"] is True

    render.release.set()
    result = await asyncio.wait_for(gen, _HOLD_TIMEOUT_S)
    assert result.filename.startswith("img_")


async def test_health_answers_while_the_model_loads(server, monkeypatch):
    """A cold load takes minutes (~157 s for Z-Image from disk). It is the
    longest stretch the old loop was blocked for."""
    loading = threading.Event()
    finish = threading.Event()

    def slow_load(config):
        loading.set()
        finish.wait(_HOLD_TIMEOUT_S)
        return FakeRender()

    monkeypatch.setattr(server, "load_pipeline", slow_load)
    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(loading)

    body = await asyncio.wait_for(server.health(), timeout=1)
    assert not finish.is_set(), "/health must have been served DURING the load"
    assert body["inflight"] == 1 and body["gpu_busy"] is True

    finish.set()
    await asyncio.wait_for(gen, _HOLD_TIMEOUT_S)
    assert body["status"] == "idle"
    assert (await server.health())["status"] == "ready"


async def test_health_reports_an_idle_gpu_at_rest(server):
    body = await server.health()
    assert body["inflight"] == 0
    assert body["gpu_busy"] is False


# ---------------------------------------------------------------------------
# One render on the card at a time
# ---------------------------------------------------------------------------


async def test_queued_requests_take_turns_on_the_gpu(server):
    """The blocked loop used to serialize renders for free. With inference in
    threads only the lock does, and without it these four would share the
    card, which on a real GPU is a CUDA OOM for all of them."""
    render = FakeRender(hold_s=0.05)
    server.state.pipeline = render

    results = await asyncio.wait_for(
        asyncio.gather(*(server.generate(_req(f"shot {i}")) for i in range(4))),
        timeout=10,
    )

    assert render.calls == 4
    assert render.max_active == 1, "two renders ran on the GPU at once"
    assert len({r.filename for r in results}) == 4
    assert server.state.inflight == 0
    assert not server.state.gpu_lock.locked()


async def test_requests_behind_a_cold_start_share_one_model_load(server, monkeypatch):
    """A second model copy next to the first is ~13 GB of VRAM the card does
    not have. Requests that arrive during a load must wait for it and reuse
    the result."""
    loads = []

    def slow_load(config):
        loads.append(config.friendly_name)
        time.sleep(0.1)
        return FakeRender()

    monkeypatch.setattr(server, "load_pipeline", slow_load)

    await asyncio.wait_for(
        asyncio.gather(*(server.generate(_req(f"shot {i}")) for i in range(3))),
        timeout=10,
    )

    assert loads == ["z_image_turbo"]


async def test_a_cancelled_request_keeps_the_gpu_until_its_render_ends(server):
    """A worker thread cannot be interrupted. If cancelling the request
    released the lock at once, the next request would start a second render
    beside the one still running. A client that times out and retries would
    produce exactly that."""
    render = FakeRender(held=True)
    server.state.pipeline = render
    first = asyncio.create_task(server.generate(_req("first")))
    await _wait_for(render.started)

    first.cancel()
    await _let_tasks_park()
    second = asyncio.create_task(server.generate(_req("second")))
    await asyncio.sleep(0.05)

    assert server.state.gpu_lock.locked(), "lock released while the render thread still runs"
    assert not first.done()
    assert render.calls == 1, "second render started beside the orphaned first"

    render.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, _HOLD_TIMEOUT_S)
    await asyncio.wait_for(second, _HOLD_TIMEOUT_S)
    assert render.max_active == 1
    assert server.state.inflight == 0


async def test_the_ocr_retry_loop_runs_inside_one_gpu_turn(server, monkeypatch):
    """The OCR gate's re-roll happens under the same lock hold as the first
    render. The gate's own tests cover its pure logic but never drive
    /generate, so this is the end-to-end check that it still works with the
    renders in threads: a leaking first attempt, a clean re-roll, and only
    the clean file kept."""
    render = FakeRender()
    server.state.pipeline = render
    server.state.ocr_gate = server.OcrGateConfig(enabled=True, max_chars=6, max_attempts=3)
    scans = iter([[([], "LEAKED HEADLINE", 0.9)], []])

    class ScriptedReader:
        def readtext(self, _path, detail=1):
            return next(scans)

    async def reader():
        return ScriptedReader()

    audit = []

    async def record_audit(**row):
        audit.append(row)

    monkeypatch.setattr(server, "ensure_ocr_reader", reader)
    monkeypatch.setattr(server, "write_ocr_gate_audit_log", record_audit)

    result = await asyncio.wait_for(server.generate(_req()), _HOLD_TIMEOUT_S)

    assert render.calls == 2 and render.max_active == 1
    assert result.ocr_gate_attempts == 2
    assert result.ocr_gate_status == server.OCR_STATUS_PASS
    assert result.ocr_text_chars == 0
    assert [p.name for p in server.OUTPUT_DIR.iterdir()] == [result.filename]
    assert audit and audit[0]["status"] == server.OCR_STATUS_PASS
    assert not server.state.gpu_lock.locked()


async def test_a_failed_load_reports_503_and_frees_the_gpu(server, monkeypatch):
    def failing_load(config):
        raise RuntimeError("CUDA error: out of memory")

    monkeypatch.setattr(server, "load_pipeline", failing_load)

    with pytest.raises(HTTPException) as exc:
        await server.generate(_req())

    assert exc.value.status_code == 503
    assert server.state.degraded
    assert not server.state.gpu_lock.locked()
    assert server.state.inflight == 0


async def test_an_oom_on_the_first_attempt_reports_503_and_frees_the_gpu(server):
    server.state.pipeline = FakeRender(oom=True)

    with pytest.raises(HTTPException) as exc:
        await server.generate(_req())

    assert exc.value.status_code == 503
    assert exc.value.detail == "GPU OOM"
    assert not server.state.gpu_lock.locked()
    assert server.state.inflight == 0


# ---------------------------------------------------------------------------
# Nothing drops the pipeline out from under a render
# ---------------------------------------------------------------------------


async def test_idle_unloader_leaves_a_running_render_alone(server, monkeypatch):
    """A render can outlast IDLE_TIMEOUT (a cold load plus three OCR retries
    does). The in-loop tick could not run mid-render. Off the loop it can, so
    it must check the in-flight counter, as wan's does."""
    render = FakeRender(held=True)
    server.state.pipeline = render
    dropped = []
    monkeypatch.setattr(server, "unload_pipeline", lambda: dropped.append(render.active))
    monkeypatch.setattr(server, "reload_ocr_gate_config", _noop)

    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)
    server.state.last_used = 0.0  # as if the render had run past IDLE_TIMEOUT
    # Promptly, too: the tick must skip a busy server, not queue behind it.
    await asyncio.wait_for(server._idle_unloader_tick(), timeout=1)
    assert dropped == []

    render.release.set()
    await asyncio.wait_for(gen, _HOLD_TIMEOUT_S)
    server.state.last_used = 0.0
    await server._idle_unloader_tick()
    assert dropped == [0], "an idle pipeline must still unload once the render is done"


async def test_idle_unloader_rechecks_after_waiting_for_the_gpu(server, monkeypatch):
    """A request that arrives while the tick waits for the lock is about to
    use the pipeline. The tick must notice it and keep the pipeline loaded."""
    server.state.pipeline = object()
    server.state.last_used = 0.0
    dropped = []
    monkeypatch.setattr(server, "unload_pipeline", lambda: dropped.append(True))
    monkeypatch.setattr(server, "reload_ocr_gate_config", _noop)

    await server.state.gpu_lock.acquire()  # e.g. /unload mid-unload
    tick = asyncio.create_task(server._idle_unloader_tick())
    await _let_tasks_park()
    server.state.inflight = 1  # a /generate arrives and queues on the lock
    server.state.gpu_lock.release()
    await asyncio.wait_for(tick, _HOLD_TIMEOUT_S)

    assert dropped == []


async def test_a_model_switch_waits_for_the_render_in_flight(server, monkeypatch):
    """Swapping config under a render pairs the old weights with the new
    model's call convention (a negative_prompt handed to Z-Image, say). The
    switch must happen between renders."""
    render = FakeRender(held=True)
    server.state.pipeline = render
    dropped = []
    monkeypatch.setattr(server, "unload_pipeline", lambda: dropped.append(render.active))

    async def read_setting():
        return "sdxl_lightning"

    monkeypatch.setattr(server, "read_model_setting", read_setting)

    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)
    reload = asyncio.create_task(server.reload_config())
    await asyncio.sleep(0.05)

    assert server.state.config.friendly_name == "z_image_turbo", "config swapped mid-render"
    assert dropped == []

    render.release.set()
    await asyncio.wait_for(gen, _HOLD_TIMEOUT_S)
    await asyncio.wait_for(reload, _HOLD_TIMEOUT_S)
    assert dropped == [0]
    assert server.state.config.friendly_name == "sdxl_lightning"


async def test_soft_unload_rechecks_after_waiting_for_the_gpu(server, monkeypatch):
    server.state.pipeline = object()
    dropped = []
    monkeypatch.setattr(server, "unload_pipeline", lambda: dropped.append(True))

    await server.state.gpu_lock.acquire()  # e.g. the idle tick mid-unload
    unload = asyncio.create_task(server.unload(None))
    await _let_tasks_park()
    server.state.inflight = 1
    server.state.gpu_lock.release()
    result = await asyncio.wait_for(unload, _HOLD_TIMEOUT_S)

    assert result["status"] == "busy_generation_in_flight"
    assert dropped == []


async def test_hard_unload_does_not_exit_under_a_request_that_arrived_mid_unload(
    server, monkeypatch,
):
    """The exit is irreversible. A /generate that arrives while the unload
    reads its floor from the DB is queued on the lock the unload holds, and
    os._exit would reset its connection. Decline instead, as the up-front
    in-flight check does."""
    server.state.pipeline = None
    server.torch.cuda.reserved_mb = 20_000
    monkeypatch.setattr(server, "unload_pipeline", lambda: None)

    async def floor_read_while_a_request_arrives():
        server.state.inflight += 1
        return 512

    monkeypatch.setattr(
        server, "read_hard_unload_min_reserved_mb", floor_read_while_a_request_arrives,
    )

    with patch.object(os, "_exit") as exit_mock:
        result = await server.unload(server.UnloadRequest(hard=True))

    exit_mock.assert_not_called()
    assert result["status"] == "busy_generation_in_flight"
    assert result["inflight"] == 1


async def test_unload_still_declines_at_once_while_a_render_holds_the_gpu(server):
    """The scheduler calls /unload with a 10 s timeout, and a timed-out hard
    unload reads as "freed nothing", which can end in a container restart
    mid-render. The decline must not wait for the lock."""
    render = FakeRender(held=True)
    server.state.pipeline = render
    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)

    result = await asyncio.wait_for(
        server.unload(server.UnloadRequest(hard=True)), timeout=1,
    )

    assert result["status"] == "busy_generation_in_flight"
    render.release.set()
    await asyncio.wait_for(gen, _HOLD_TIMEOUT_S)


async def _drop_via_idle_tick(server, monkeypatch):
    server.state.pipeline = object()
    server.state.last_used = 0.0
    monkeypatch.setattr(server, "reload_ocr_gate_config", _noop)
    await server._idle_unloader_tick()


async def _drop_via_soft_unload(server, monkeypatch):
    server.state.pipeline = object()
    await server.unload(None)


async def _drop_via_hard_unload(server, monkeypatch):
    server.state.pipeline = object()
    server.torch.cuda.reserved_mb = 20_000

    async def floor():
        return 512

    monkeypatch.setattr(server, "read_hard_unload_min_reserved_mb", floor)
    with patch.object(os, "_exit"):
        await server.unload(server.UnloadRequest(hard=True))


def _drop_via_reload(setting):
    async def _drop(server, monkeypatch):
        server.state.pipeline = object()

        async def read_setting():
            return setting

        monkeypatch.setattr(server, "read_model_setting", read_setting)
        await server.reload_config()

    return _drop


@pytest.mark.parametrize(
    "drop",
    [
        _drop_via_idle_tick,
        _drop_via_soft_unload,
        _drop_via_hard_unload,
        _drop_via_reload("sdxl_lightning"),  # model switch
        _drop_via_reload(None),  # setting cleared
        _drop_via_reload("no_such_model"),  # unknown model
    ],
    ids=["idle-tick", "soft-unload", "hard-unload", "model-switch",
         "setting-cleared", "unknown-model"],
)
async def test_every_pipeline_drop_holds_the_gpu_lock(server, monkeypatch, drop):
    """Structural pin: a path that drops the pipeline without the lock can do
    it mid-render, whichever path it is."""
    held = []
    monkeypatch.setattr(
        server, "unload_pipeline", lambda: held.append(server.state.gpu_lock.locked()),
    )

    await drop(server, monkeypatch)

    assert held == [True]

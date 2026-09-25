"""GPU serialization + event-loop tests for scripts/stable-audio-server.py.

2026-09-25: stable-audio had the defect image-gen had before
glad-labs-stack#4021. Its cold model load (~20 s warm, ~125 s under VRAM
contention) ran directly on the asyncio event loop, so ``GET /health`` could
not be served for the whole load and Docker's healthcheck (30 s x 3) failed
through it. Its renders already ran in a thread, with no lock: two concurrent
``/generate`` requests diffused on the card at the same time. Nothing else
keeps them apart either, since the pipeline's audio calls take no
``gpu.lock``.

``_state.gpu_lock`` now serializes the card, and every load, render and unload
runs in a worker thread (``_run_on_gpu``). These tests pin both halves:

* the cheap endpoints answer while a render or a model load runs;
* two renders never share the card, whether requests queue, arrive during a
  cold start, or are cancelled while their render thread (which cannot be
  interrupted) is still running;
* nothing drops the model out from under a render: the idle watchdog,
  ``/unload`` and switching the engine off all wait their turn, and neither
  exit path fires under a request that arrived while it unloaded;
* a request stays in flight until its file has been sent, and the file is
  deleted once it has.

``FakeRender`` stands in for ``_generate_sync``: it blocks a worker thread
until the test releases it, and records how many renders overlap. Loads run
the real ``_load_model`` against a fake ``stable_audio_tools`` (there is no
torch here). Under the old in-loop load a held load blocks the test's own
event loop, so those tests fail after the hold timeout instead of hanging.
"""
import asyncio
import gc
import importlib.util
import json
import logging
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

#: What a render writes: big enough that the response goes out in several
#: 64 KiB chunks, so a test can act while the body is mid-send.
_WAV_BYTES = b"RIFF" + bytes(200_000)


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "stable-audio-server.py").exists():
            return parent
    raise RuntimeError(
        "could not locate scripts/stable-audio-server.py from " + str(start)
    )


def _load_server():
    # Same scoped torch stub as test_stable_audio_server_unload.py: popped
    # after exec, so a bare ModuleType can't poison a later ``import torch``.
    # The fixture below swaps in a fuller fake per test.
    stub_installed = False
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        torch_stub.__spec__ = importlib.util.spec_from_loader("torch", loader=None)
        torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
        sys.modules["torch"] = torch_stub
        stub_installed = True

    path = _find_repo_root(Path(__file__)) / "scripts" / "stable-audio-server.py"
    spec = importlib.util.spec_from_file_location(
        "stable_audio_server_gpu_serialization_under_test", path,
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


sa = _load_server()


class _FakeCuda:
    def __init__(self) -> None:
        self.available = False
        self.reserved_mb = 0

    def is_available(self) -> bool:
        return self.available

    def empty_cache(self) -> None:
        pass

    def memory_allocated(self, idx: int = 0) -> int:
        return 0

    def memory_reserved(self, idx: int = 0) -> int:
        return self.reserved_mb * 1024 * 1024


class _FakeModel:
    def eval(self) -> "_FakeModel":
        return self

    def cuda(self) -> "_FakeModel":
        return self


def _never_called(*args, **kwargs):
    raise AssertionError("_generate_sync is faked in these tests")


class FakeLoader:
    """``stable_audio_tools.get_pretrained_model`` stand-in for the real
    ``_load_model``. ``held=True`` blocks the load until ``release`` is set."""

    def __init__(self, *, held: bool = False, hold_s: float = 0.0,
                 fail: bool = False, config: dict | None = None) -> None:
        self.release = threading.Event()
        if not held:
            self.release.set()
        self.hold_s = hold_s
        self.fail = fail
        self.config = config if config is not None else {
            "sample_rate": 44100, "sample_size": 2_097_152,
        }
        self.started = threading.Event()
        self.calls = 0
        self._mu = threading.Lock()

    def __call__(self, name: str):
        with self._mu:
            self.calls += 1
        self.started.set()
        time.sleep(self.hold_s)
        self.release.wait(_HOLD_TIMEOUT_S)
        if self.fail:
            raise RuntimeError("CUDA error: out of memory")
        return _FakeModel(), dict(self.config)


def _install_loader(monkeypatch, loader: FakeLoader) -> None:
    """Point the real ``_load_model`` at a fake ``stable_audio_tools``. Only
    these three keys are injected, and monkeypatch restores exactly them."""
    package = types.ModuleType("stable_audio_tools")
    inference = types.ModuleType("stable_audio_tools.inference")
    generation = types.ModuleType("stable_audio_tools.inference.generation")
    package.get_pretrained_model = loader
    generation.generate_diffusion_cond = _never_called
    package.inference = inference
    inference.generation = generation
    monkeypatch.setitem(sys.modules, "stable_audio_tools", package)
    monkeypatch.setitem(sys.modules, "stable_audio_tools.inference", inference)
    monkeypatch.setitem(
        sys.modules, "stable_audio_tools.inference.generation", generation,
    )


class FakeRender:
    """``_generate_sync`` stand-in that records overlapping renders.

    ``held=True`` blocks each call until ``release`` is set, so a test can act
    while a render is provably still running. ``hold_s`` adds a fixed render
    time on top. ``fails=True`` reports failed inference, as the real one does.
    """

    def __init__(self, *, held: bool = False, hold_s: float = 0.0,
                 fails: bool = False) -> None:
        self.release = threading.Event()
        if not held:
            self.release.set()
        self.hold_s = hold_s
        self.fails = fails
        self.started = threading.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._mu = threading.Lock()

    def __call__(self, prompt: str, duration_s: float, output_path: str,
                 output_format: str) -> float | None:
        with self._mu:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            time.sleep(self.hold_s)
            self.release.wait(_HOLD_TIMEOUT_S)
            if self.fails:
                return None
            Path(output_path).write_bytes(_WAV_BYTES)
            return duration_s
        finally:
            with self._mu:
                self.active -= 1


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Fresh state per test (its own GPU lock and counter), torch faked,
    renders written under tmp_path."""
    monkeypatch.setattr(sa, "_state", sa._State())
    monkeypatch.setattr(sa, "torch", types.SimpleNamespace(cuda=_FakeCuda()))
    monkeypatch.setattr(sa, "OUTPUT_DIR", tmp_path)
    return sa


def _req(prompt: str = "warm analog pad, slow swell"):
    return sa.GenerateRequest(prompt=prompt, duration_s=1.0)


def _warm(server, monkeypatch, render: FakeRender) -> FakeRender:
    """A loaded model and ``render`` as its inference."""
    server._state.model = _FakeModel()
    monkeypatch.setattr(server, "_generate_sync", render)
    return render


def _idle(server, monkeypatch) -> None:
    """As if the last request had ended longer than IDLE_TIMEOUT ago."""
    monkeypatch.setattr(server, "IDLE_TIMEOUT", 0)
    server._state.last_used = time.monotonic() - 1.0


def _big_reserved_pool(server) -> None:
    server.torch.cuda.available = True
    server.torch.cuda.reserved_mb = 20_000


async def _wait_for(event: threading.Event) -> None:
    """Wait for a worker-thread signal without blocking the event loop."""
    assert await asyncio.to_thread(event.wait, _HOLD_TIMEOUT_S), "timed out waiting"


async def _let_tasks_park() -> None:
    """Give freshly created tasks a few loop turns to reach their first await."""
    for _ in range(5):
        await asyncio.sleep(0)


async def _send(response) -> bytes:
    """Send ``response`` the way Starlette does once the endpoint has returned."""
    body = bytearray()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await response({"type": "http", "method": "POST", "headers": []}, receive, send)
    return bytes(body)


async def _asgi_post(server, payload: dict, *, mid_body=None) -> tuple[int, bytes]:
    """POST /generate through the whole ASGI app, as uvicorn drives it: the
    endpoint returns, then the body is sent. ``mid_body`` runs after each
    chunk that has more coming, i.e. while the response is mid-send."""
    raw = json.dumps(payload).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/generate",
        "raw_path": b"/generate", "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode())],
        "client": ("127.0.0.1", 40000), "server": ("stable-audio", 9839),
    }
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(_HOLD_TIMEOUT_S)  # an open connection, saying nothing
        return {"type": "http.disconnect"}

    status = None
    body = bytearray()

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))
            if mid_body is not None and message.get("more_body", False):
                await mid_body()

    await asyncio.wait_for(server.app(scope, receive, send), _HOLD_TIMEOUT_S)
    return status, bytes(body)


def _client(server) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://stable-audio",
    )


# ---------------------------------------------------------------------------
# The loop stays free
# ---------------------------------------------------------------------------


async def test_health_answers_while_the_model_loads(server, monkeypatch):
    """The bug: the cold load ran on the event loop, so the healthcheck's
    GET /health went unanswered for all of it (~125 s under VRAM contention)
    and Docker marked a working server unhealthy."""
    loader = FakeLoader(held=True)
    _install_loader(monkeypatch, loader)
    monkeypatch.setattr(server, "_generate_sync", FakeRender())
    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(loader.started)

    async with _client(server) as client:
        resp = await asyncio.wait_for(client.get("/health"), timeout=2)

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_loaded"] is False, "/health must have been served DURING the load"
    assert body["inflight"] == 1
    assert body["gpu_busy"] is True

    loader.release.set()
    audio = await _send(await asyncio.wait_for(gen, _HOLD_TIMEOUT_S))
    assert audio == _WAV_BYTES
    assert (await server.health())["model_loaded"] is True


async def test_health_answers_over_http_while_a_render_holds_the_gpu(server, monkeypatch):
    render = _warm(server, monkeypatch, FakeRender(held=True))
    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)

    async with _client(server) as client:
        resp = await asyncio.wait_for(client.get("/health"), timeout=2)

    assert resp.status_code == 200
    body = resp.json()
    assert render.active == 1, "/health must have been served DURING the render"
    assert body["inflight"] == 1
    assert body["gpu_busy"] is True

    render.release.set()
    assert await _send(await asyncio.wait_for(gen, _HOLD_TIMEOUT_S)) == _WAV_BYTES


async def test_health_reports_an_idle_gpu_at_rest(server):
    body = await server.health()
    assert body["inflight"] == 0
    assert body["gpu_busy"] is False


async def test_health_answers_mid_unload_and_never_finds_the_model_missing(
    server, monkeypatch,
):
    """/health reads ``_state.model`` from the loop while an unload runs in a
    thread. ``del`` then re-assign leaves a moment where the attribute does
    not exist, and a /health read landing there raises AttributeError."""

    class _WatchedState(sa._State):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []

        def __delattr__(self, name: str) -> None:
            self.deleted.append(name)
            super().__delattr__(name)

    state = _WatchedState()
    state.model = _FakeModel()
    monkeypatch.setattr(server, "_state", state)
    collecting = threading.Event()
    finish = threading.Event()

    def slow_collect():
        collecting.set()
        finish.wait(_HOLD_TIMEOUT_S)

    monkeypatch.setattr(server, "gc", types.SimpleNamespace(collect=slow_collect))

    unload = asyncio.create_task(server.unload(None))
    await _wait_for(collecting)
    body = await asyncio.wait_for(server.health(), timeout=1)
    finish.set()

    assert (await asyncio.wait_for(unload, _HOLD_TIMEOUT_S))["status"] == "unloaded"
    assert body["model_loaded"] is False
    assert body["gpu_busy"] is True, "/health must have been served DURING the unload"
    assert state.deleted == [], "an unload deleted an attribute /health reads"


# ---------------------------------------------------------------------------
# One render on the card at a time
# ---------------------------------------------------------------------------


async def test_queued_requests_take_turns_on_the_gpu(server, monkeypatch):
    """Renders ran in threads with nothing between them, so these four were
    four diffusion passes on one card at once."""
    render = _warm(server, monkeypatch, FakeRender(hold_s=0.05))

    responses = await asyncio.wait_for(
        asyncio.gather(*(server.generate(_req(f"cue {i}")) for i in range(4))),
        timeout=10,
    )
    bodies = [await _send(r) for r in responses]

    assert render.calls == 4
    assert render.max_active == 1, "two renders ran on the GPU at once"
    assert bodies == [_WAV_BYTES] * 4
    assert server._state.inflight == 0
    assert not server._state.gpu_lock.locked()


async def test_a_queued_request_counts_as_in_flight(server, monkeypatch):
    """A request waiting for the lock is about to use the model: /unload must
    decline for it as for the render ahead of it."""
    render = _warm(server, monkeypatch, FakeRender(held=True))
    first = asyncio.create_task(server.generate(_req("first")))
    await _wait_for(render.started)
    second = asyncio.create_task(server.generate(_req("second")))
    await _let_tasks_park()

    body = await server.health()
    declined = await asyncio.wait_for(server.unload(None), timeout=1)

    assert body["inflight"] == 2
    assert render.calls == 1
    assert declined == {"status": "busy_generation_in_flight", "inflight": 2}

    render.release.set()
    await _send(await asyncio.wait_for(first, _HOLD_TIMEOUT_S))
    await _send(await asyncio.wait_for(second, _HOLD_TIMEOUT_S))
    assert server._state.inflight == 0


async def test_requests_behind_a_cold_start_share_one_model_load(server, monkeypatch):
    """A second copy of the model beside the first is VRAM the card may not
    have. Requests that arrive during a load wait for it and reuse it."""
    loader = FakeLoader(hold_s=0.1)
    _install_loader(monkeypatch, loader)
    monkeypatch.setattr(server, "_generate_sync", FakeRender())

    responses = await asyncio.wait_for(
        asyncio.gather(*(server.generate(_req(f"cue {i}")) for i in range(3))),
        timeout=10,
    )
    bodies = [await _send(r) for r in responses]

    assert loader.calls == 1
    assert bodies == [_WAV_BYTES] * 3


async def test_a_cancelled_request_keeps_the_gpu_until_its_render_ends(server, monkeypatch):
    """A worker thread cannot be interrupted. If cancelling the request
    released the lock at once, the next request would start a second render
    beside the one still running. A client that times out and retries would
    produce exactly that."""
    render = _warm(server, monkeypatch, FakeRender(held=True))
    first = asyncio.create_task(server.generate(_req("first")))
    await _wait_for(render.started)

    first.cancel()
    await _let_tasks_park()
    second = asyncio.create_task(server.generate(_req("second")))
    await asyncio.sleep(0.05)

    assert server._state.gpu_lock.locked(), "lock released while the render thread still runs"
    assert not first.done()
    assert render.calls == 1, "second render started beside the orphaned first"

    render.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, _HOLD_TIMEOUT_S)
    assert await _send(await asyncio.wait_for(second, _HOLD_TIMEOUT_S)) == _WAV_BYTES
    assert render.max_active == 1
    assert server._state.inflight == 0
    assert list(server.OUTPUT_DIR.iterdir()) == [], "the cancelled render's file stayed behind"


async def test_a_thread_failing_after_its_request_is_cancelled_is_collected(server, caplog):
    """``asyncio.shield`` stops watching the thread once its caller is
    cancelled. A failure after that is retrieved by nobody, and asyncio
    reports it as "Task exception was never retrieved" when the task is
    collected. ``_run_on_gpu`` collects it and says what happened."""
    caplog.set_level(logging.WARNING, logger=server.logger.name)
    loop = asyncio.get_running_loop()
    reports: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reports.append(context))
    started = threading.Event()
    fail = threading.Event()

    def render_that_fails_late():
        started.set()
        fail.wait(_HOLD_TIMEOUT_S)
        raise RuntimeError("CUDA error: an illegal memory access was encountered")

    try:
        call = asyncio.create_task(server._run_on_gpu(render_that_fails_late))
        await _wait_for(started)
        call.cancel()
        await _let_tasks_park()
        fail.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, _HOLD_TIMEOUT_S)
        del call
        gc.collect()
    finally:
        loop.set_exception_handler(previous_handler)

    assert [r.get("message") for r in reports] == []
    assert (
        "render_that_fails_late finished after its request was cancelled, "
        "raising RuntimeError"
    ) in caplog.text


async def test_a_failed_load_reports_503_and_frees_the_gpu(server, monkeypatch):
    _install_loader(monkeypatch, FakeLoader(fail=True))

    with pytest.raises(HTTPException) as exc:
        await server.generate(_req())

    assert exc.value.status_code == 503
    assert server._state.degraded
    assert server._state.model is None
    assert not server._state.gpu_lock.locked()
    assert server._state.inflight == 0


async def test_requests_queued_behind_a_failed_load_do_not_retry_it(server, monkeypatch):
    """With the load on the loop, a request that arrived mid-load was read
    only after the load had failed, and saw the degraded flag. Queued on the
    lock it has already passed that check, so it must look again, or every
    queued request runs its own failing load."""
    loader = FakeLoader(held=True, fail=True)
    _install_loader(monkeypatch, loader)
    first = asyncio.create_task(server.generate(_req("first")))
    await _wait_for(loader.started)
    second = asyncio.create_task(server.generate(_req("second")))
    await _let_tasks_park()

    loader.release.set()
    results = await asyncio.wait_for(
        asyncio.gather(first, second, return_exceptions=True), _HOLD_TIMEOUT_S,
    )

    assert [getattr(r, "status_code", r) for r in results] == [503, 503]
    assert loader.calls == 1, "a queued request retried the load that had just failed"
    assert server._state.inflight == 0


async def test_a_load_that_fails_partway_leaves_no_model_behind(server, monkeypatch):
    """The model is published last. Published first, a load whose config was
    missing a field left ``model`` set without the rest, and every later
    request skipped the load and failed inside the render instead."""
    _install_loader(monkeypatch, FakeLoader(config={"sample_rate": 44100}))

    with pytest.raises(HTTPException) as exc:
        await server.generate(_req())

    assert exc.value.status_code == 503
    assert server._state.model is None
    assert (await server.health())["model_loaded"] is False


async def test_a_failed_render_reports_500_and_deletes_its_file(server, monkeypatch):
    _warm(server, monkeypatch, FakeRender(fails=True))

    with pytest.raises(HTTPException) as exc:
        await server.generate(_req())

    assert exc.value.status_code == 500
    assert list(server.OUTPUT_DIR.iterdir()) == []
    assert server._state.inflight == 0
    assert not server._state.gpu_lock.locked()


# ---------------------------------------------------------------------------
# A request lasts until its file is sent
# ---------------------------------------------------------------------------


async def test_a_request_stays_in_flight_until_its_file_is_sent(server, monkeypatch):
    """FastAPI returns from the endpoint before Starlette sends the body. The
    request used to end there, so a hard /unload during the send saw nothing
    in flight, exited, and cut the caller's file off mid-body."""
    _warm(server, monkeypatch, FakeRender())
    _big_reserved_pool(server)
    mid_send: list[tuple[int, float, dict]] = []

    async def hard_unload_mid_body():
        if not mid_send:
            result = await server.unload(server.UnloadRequest(hard=True))
            mid_send.append((server._state.inflight, server._state.last_used, result))

    with patch.object(os, "_exit") as exit_mock:
        status, body = await _asgi_post(
            server, {"prompt": "warm analog pad", "duration_s": 1.0},
            mid_body=hard_unload_mid_body,
        )

    exit_mock.assert_not_called()
    assert status == 200
    assert body == _WAV_BYTES
    ((inflight, last_used, result),) = mid_send
    assert inflight == 1
    assert result["status"] == "busy_generation_in_flight"
    assert server._state.inflight == 0
    assert server._state.last_used > last_used, "the idle window must start once the file is out"
    assert list(server.OUTPUT_DIR.iterdir()) == [], "a sent file stayed behind"


async def test_a_request_whose_send_fails_still_ends(server, monkeypatch):
    """A caller that drops the connection mid-body must not stay counted:
    every unload would decline for it from then on."""
    _warm(server, monkeypatch, FakeRender())

    async def connection_lost():
        raise OSError("connection reset by peer")

    with pytest.raises(OSError):
        await _asgi_post(
            server, {"prompt": "warm analog pad", "duration_s": 1.0},
            mid_body=connection_lost,
        )

    assert server._state.inflight == 0
    assert list(server.OUTPUT_DIR.iterdir()) == []


# ---------------------------------------------------------------------------
# Nothing drops the model out from under a render
# ---------------------------------------------------------------------------


async def test_idle_watchdog_leaves_a_running_render_alone(server, monkeypatch):
    """``last_used`` is stamped when a request ends, so mid-render it can be
    any age. The tick must check the in-flight count, and skip a busy server
    at once instead of queueing behind its render."""
    render = _warm(server, monkeypatch, FakeRender(held=True))
    dropped: list[int] = []
    exits: list[dict] = []
    monkeypatch.setattr(server, "_unload_model", lambda: dropped.append(render.active))
    monkeypatch.setattr(server, "_hard_exit_if_reserved_pool", lambda **kw: exits.append(kw))

    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)
    _idle(server, monkeypatch)
    await asyncio.wait_for(server._idle_unload_tick(), timeout=1)
    assert dropped == [] and exits == []

    render.release.set()
    await _send(await asyncio.wait_for(gen, _HOLD_TIMEOUT_S))
    _idle(server, monkeypatch)
    await server._idle_unload_tick()
    assert dropped == [0], "an idle model must still unload once the render is done"
    assert exits == [{"quiet_skip": True}]


async def test_idle_watchdog_rechecks_after_waiting_for_the_gpu(server, monkeypatch):
    """A request that arrives while the tick waits for the lock is about to
    use the model. The tick must notice it and leave the model loaded."""
    server._state.model = _FakeModel()
    _idle(server, monkeypatch)
    dropped: list[bool] = []
    exits: list[dict] = []
    monkeypatch.setattr(server, "_unload_model", lambda: dropped.append(True))
    monkeypatch.setattr(server, "_hard_exit_if_reserved_pool", lambda **kw: exits.append(kw))

    await server._state.gpu_lock.acquire()  # e.g. /unload mid-unload
    tick = asyncio.create_task(server._idle_unload_tick())
    await _let_tasks_park()
    server._state.inflight = 1  # a /generate arrives and queues on the lock
    server._state.gpu_lock.release()
    await asyncio.wait_for(tick, _HOLD_TIMEOUT_S)

    assert dropped == [] and exits == []


async def _unload_while_a_request_arrives(server, monkeypatch, unload_call):
    """Run ``unload_call`` with a request arriving while its unload thread
    runs. Returns what the unload returned and the audio the request got."""
    server._state.model = _FakeModel()
    _big_reserved_pool(server)
    _install_loader(monkeypatch, FakeLoader())
    monkeypatch.setattr(server, "_generate_sync", FakeRender())
    unloading = threading.Event()
    finish_unload = threading.Event()

    def slow_unload():
        server._state.model = None
        unloading.set()
        finish_unload.wait(_HOLD_TIMEOUT_S)

    monkeypatch.setattr(server, "_unload_model", slow_unload)
    unload = asyncio.create_task(unload_call())
    await _wait_for(unloading)
    gen = asyncio.create_task(server.generate(_req()))
    await _let_tasks_park()
    finish_unload.set()
    result = await asyncio.wait_for(unload, _HOLD_TIMEOUT_S)
    audio = await _send(await asyncio.wait_for(gen, _HOLD_TIMEOUT_S))
    return result, audio


async def test_hard_unload_does_not_exit_under_a_request_that_arrived_mid_unload(
    server, monkeypatch,
):
    """The exit is irreversible. A /generate that arrives during the unload
    is queued on the lock the unload holds, and os._exit would reset its
    connection. Decline instead, as the up-front in-flight check does."""
    with patch.object(os, "_exit") as exit_mock:
        result, audio = await _unload_while_a_request_arrives(
            server, monkeypatch,
            lambda: server.unload(server.UnloadRequest(hard=True)),
        )

    exit_mock.assert_not_called()
    assert result["status"] == "busy_generation_in_flight"
    assert result["inflight"] == 1
    assert audio == _WAV_BYTES, "the request cold-loads and still gets its audio"


async def test_idle_watchdog_does_not_exit_under_a_request_that_arrived_mid_unload(
    server, monkeypatch,
):
    """Same exit, same rule, from the idle side: the tick holds the lock
    while it unloads, so a request can queue behind it."""
    _idle(server, monkeypatch)
    with patch.object(os, "_exit") as exit_mock:
        _, audio = await _unload_while_a_request_arrives(
            server, monkeypatch, server._idle_unload_tick,
        )

    exit_mock.assert_not_called()
    assert audio == _WAV_BYTES


async def test_unload_still_declines_at_once_while_a_render_holds_the_gpu(server, monkeypatch):
    """The scheduler calls /unload with a 10 s timeout, and a timed-out hard
    unload reads as "freed nothing", which can end in a container restart
    mid-render. The decline must not wait for the lock."""
    render = _warm(server, monkeypatch, FakeRender(held=True))
    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)

    result = await asyncio.wait_for(
        server.unload(server.UnloadRequest(hard=True)), timeout=1,
    )

    assert result["status"] == "busy_generation_in_flight"
    render.release.set()
    await _send(await asyncio.wait_for(gen, _HOLD_TIMEOUT_S))


async def test_soft_unload_rechecks_after_waiting_for_the_gpu(server, monkeypatch):
    server._state.model = _FakeModel()
    dropped: list[bool] = []
    monkeypatch.setattr(server, "_unload_model", lambda: dropped.append(True))

    await server._state.gpu_lock.acquire()  # e.g. the idle tick mid-unload
    unload = asyncio.create_task(server.unload(None))
    await _let_tasks_park()
    server._state.inflight = 1
    server._state.gpu_lock.release()
    result = await asyncio.wait_for(unload, _HOLD_TIMEOUT_S)

    assert result["status"] == "busy_generation_in_flight"
    assert dropped == []


async def _engine_off(key: str, default: str = "") -> str:
    return ""


async def test_switching_the_engine_off_waits_for_the_render_in_flight(server, monkeypatch):
    """New requests are refused at once, but the model is dropped between
    renders: mid-render the drop frees nothing, and turning the engine back
    on would then load a second copy beside the first."""
    render = _warm(server, monkeypatch, FakeRender(held=True))
    dropped: list[int] = []
    monkeypatch.setattr(server, "_unload_model", lambda: dropped.append(render.active))
    monkeypatch.setattr(server, "_read_setting", _engine_off)

    gen = asyncio.create_task(server.generate(_req()))
    await _wait_for(render.started)
    reload = asyncio.create_task(server.reload_config())
    await asyncio.sleep(0.05)

    assert server._state.degraded, "new requests must be refused at once"
    assert dropped == [], "model dropped mid-render"
    assert not reload.done()

    render.release.set()
    await _send(await asyncio.wait_for(gen, _HOLD_TIMEOUT_S))
    await asyncio.wait_for(reload, _HOLD_TIMEOUT_S)
    assert dropped == [0]


async def _drop_via_idle_tick(server, monkeypatch):
    _idle(server, monkeypatch)
    await server._idle_unload_tick()


async def _drop_via_soft_unload(server, monkeypatch):
    await server.unload(None)


async def _drop_via_hard_unload(server, monkeypatch):
    _big_reserved_pool(server)
    with patch.object(os, "_exit"):
        await server.unload(server.UnloadRequest(hard=True))


async def _drop_via_engine_off(server, monkeypatch):
    monkeypatch.setattr(server, "_read_setting", _engine_off)
    await server.reload_config()


@pytest.mark.parametrize(
    "drop",
    [_drop_via_idle_tick, _drop_via_soft_unload, _drop_via_hard_unload,
     _drop_via_engine_off],
    ids=["idle-tick", "soft-unload", "hard-unload", "engine-off"],
)
async def test_every_model_drop_holds_the_gpu_lock(server, monkeypatch, drop):
    """Structural pin: a path that drops the model without the lock can do it
    mid-render, whichever path it is."""
    server._state.model = _FakeModel()
    held: list[bool] = []
    monkeypatch.setattr(
        server, "_unload_model", lambda: held.append(server._state.gpu_lock.locked()),
    )

    await drop(server, monkeypatch)

    assert held == [True]

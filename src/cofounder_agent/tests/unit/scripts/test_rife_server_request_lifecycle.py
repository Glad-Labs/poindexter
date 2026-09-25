"""Request lifecycle + GPU serialization tests for scripts/rife-server.py.

2026-09-25: the RIFE sidecar had the request-lifecycle defects stable-audio had
before glad-labs-stack#4034:

* every /interpolate left its work dir (the uploaded clip and the interpolated
  one) behind; the live container held 67 ``rife_*`` dirs, 126 MB, three days
  after it was created;
* ``busy`` was checked on arrival but set only after ``await file.read()``,
  which yields for any upload Starlette has spooled to disk (over 1 MB), so two
  requests could both pass the check and interpolate on the card at once;
* ``busy`` was cleared in the endpoint, which FastAPI leaves BEFORE Starlette
  sends the clip, so a hard /unload during the send could exit mid-body;
* the model load ran on the event loop;
* the idle unload and /unload dropped the model without any lock.

Now a request is admitted and marked busy in one step, and one arriving during
it gets 409, which the renderer answers with its ffmpeg fallback. It stays busy
until its clip is sent, and its work dir goes with it. ``_state.gpu_lock``
keeps loads, interpolations and unloads apart, and that work runs in worker
threads (``_run_on_gpu``).

``FakeInterpolate`` stands in for ``_interpolate_sync``: it blocks a worker
thread until the test releases it, and records how many interpolations overlap.
Loads run the real ``_load_model`` against a fake ``interpolation_model`` (there
is no torch here). Uploads are real Starlette ``UploadFile``s spooled past
1 MB, so reading one yields to the loop exactly as it does in the server.
"""
import asyncio
import gc
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np
import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

#: Upper bound on any simulated load, interpolation or unload. A test that
#: forgets to release one fails after this long instead of hanging the suite.
_HOLD_TIMEOUT_S = 5.0

#: An uploaded clip past Starlette's 1 MB spool, so it is read from disk in a
#: worker thread (the live container's largest upload was 1.77 MB).
_UPLOAD = b"clip@16fps" + bytes(1_536_000)

#: What an interpolation writes: big enough that the response goes out in
#: several 64 KiB chunks, so a test can act while the clip is mid-send.
_CLIP = b"clip@30fps" + bytes(300_000)


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "rife-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/rife-server.py from " + str(start))


def _load_server():
    """Import the server with torch and safetensors stubbed. Only the keys
    installed here are removed after exec, so a bare stub can't poison a later
    ``import torch`` (the scoped-stub rule in
    test_stable_audio_server_unload.py). The fixture swaps in a fuller fake."""
    installed: list[str] = []

    def stub(name: str, **attrs) -> None:
        if name in sys.modules:
            return
        module = types.ModuleType(name)
        module.__spec__ = importlib.util.spec_from_loader(name, loader=None)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        installed.append(name)

    stub(
        "torch",
        cuda=types.SimpleNamespace(is_available=lambda: False),
        # _midpoint is decorated at import time.
        no_grad=lambda: (lambda fn: fn),
    )
    stub("safetensors")
    stub("safetensors.torch", load_file=lambda path: {})
    path = _find_repo_root(Path(__file__)) / "scripts" / "rife-server.py"
    spec = importlib.util.spec_from_file_location(
        "rife_server_request_lifecycle_under_test", path,
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        for name in installed:
            sys.modules.pop(name, None)
    return module


rife = _load_server()


class _FakeCuda:
    def __init__(self) -> None:
        self.available = False
        self.reserved_mb = 0

    def is_available(self) -> bool:
        return self.available

    def empty_cache(self) -> None:
        pass

    def memory_reserved(self, idx: int = 0) -> int:
        return self.reserved_mb * 1024 * 1024


class _FakeModel:
    pass


class FakeLoader:
    """What the real ``_load_model`` imports: ``interpolation_model.IFNet``.
    ``held=True`` blocks the load (in ``load_state_dict``) until ``release`` is
    set. ``fail=True`` raises there, as weights that do not fit the net do."""

    def __init__(self, *, held: bool = False, fail: bool = False) -> None:
        self.release = threading.Event()
        if not held:
            self.release.set()
        self.fail = fail
        self.started = threading.Event()
        self.calls = 0
        loader = self

        class IFNet:
            def load_state_dict(self, state: dict) -> None:
                loader.calls += 1
                loader.started.set()
                loader.release.wait(_HOLD_TIMEOUT_S)
                if loader.fail:
                    raise RuntimeError(
                        "Error(s) in loading state_dict for IFNet: size mismatch",
                    )

            def to(self, device: str) -> "IFNet":
                return self

            def eval(self) -> "IFNet":
                return self

        self.IFNet = IFNet


def _install_loader(server, monkeypatch, loader: FakeLoader) -> None:
    module = types.ModuleType("interpolation_model")
    module.IFNet = loader.IFNet
    monkeypatch.setitem(sys.modules, "interpolation_model", module)
    monkeypatch.setattr(server, "load_file", lambda path: {})


class FakeInterpolate:
    """``_interpolate_sync`` stand-in that records overlapping interpolations.

    ``held=True`` blocks each call until ``release`` is set, so a test can act
    while an interpolation is provably still running. ``hold_s`` adds a fixed
    run time on top. ``raises`` is raised instead of writing a clip.
    """

    def __init__(self, *, held: bool = False, hold_s: float = 0.0,
                 raises: Exception | None = None) -> None:
        self.release = threading.Event()
        if not held:
            self.release.set()
        self.hold_s = hold_s
        self.raises = raises
        self.started = threading.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.uploads: list[bytes] = []
        self.models: list[object] = []  # _state.model as each call found it
        self._mu = threading.Lock()

    def __call__(self, src: str, dest: str, target_fps: float) -> dict:
        with self._mu:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.uploads.append(Path(src).read_bytes())
        self.models.append(rife._state.model)
        self.started.set()
        try:
            time.sleep(self.hold_s)
            self.release.wait(_HOLD_TIMEOUT_S)
            if self.raises is not None:
                raise self.raises
            Path(dest).write_bytes(_CLIP)
            return {"source_fps": 16.0, "target_fps": target_fps, "model_calls": 240}
        finally:
            with self._mu:
                self.active -= 1


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Fresh state per test (its own GPU lock), torch faked, and work dirs
    created under tmp_path."""
    monkeypatch.setattr(rife, "_state", rife._State())
    monkeypatch.setattr(rife, "torch", types.SimpleNamespace(cuda=_FakeCuda()))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    # A real load puts MODEL_CODE_DIR on sys.path.
    monkeypatch.setattr(sys, "path", list(sys.path))
    return rife


def _work_dirs(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.glob("rife_*"))


def _upload() -> UploadFile:
    """An upload as Starlette hands it over: spooled, and past 1 MB rolled to
    disk, so ``read()`` runs in a worker thread and yields to the loop."""
    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    spool.write(_UPLOAD)
    spool.seek(0)
    return UploadFile(file=spool, filename="presenter_0.mp4", size=len(_UPLOAD))


def _interpolate(server):
    return server.interpolate(file=_upload(), target_fps=30.0)


def _warm(server, monkeypatch, work: FakeInterpolate) -> FakeInterpolate:
    """A loaded model and ``work`` as its interpolation."""
    server._state.model = _FakeModel()
    monkeypatch.setattr(server, "_interpolate_sync", work)
    return work


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


async def _asgi_post(server, *, mid_body=None) -> tuple[int, bytes, dict[str, str]]:
    """POST /interpolate through the whole ASGI app, as uvicorn drives it: the
    multipart upload is parsed, the endpoint returns, then the clip is sent.
    ``mid_body`` runs after each chunk that has more coming, i.e. while the
    clip is mid-send."""
    request = httpx.Request(
        "POST", "http://rife-server/interpolate",
        files={"file": ("presenter_0.mp4", _UPLOAD, "video/mp4")},
        data={"target_fps": "30"},
    )
    raw = request.read()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/interpolate",
        "raw_path": b"/interpolate", "query_string": b"", "root_path": "",
        "headers": [(b"content-type", request.headers["content-type"].encode()),
                    (b"content-length", str(len(raw)).encode())],
        "client": ("127.0.0.1", 40000), "server": ("rife-server", 9842),
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
    headers: dict[str, str] = {}
    body = bytearray()

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            headers.update((k.decode(), v.decode()) for k, v in message["headers"])
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))
            if mid_body is not None and message.get("more_body", False):
                await mid_body()

    await asyncio.wait_for(server.app(scope, receive, send), _HOLD_TIMEOUT_S)
    return status, bytes(body), headers


def _client(server) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://rife-server",
    )


# ---------------------------------------------------------------------------
# A request's files leave with it
# ---------------------------------------------------------------------------


async def test_a_sent_clip_leaves_no_work_dir_behind(server, monkeypatch, tmp_path):
    """Each request's uploaded and interpolated clip stayed in /tmp for good:
    67 ``rife_*`` dirs, 126 MB, three days after the container was created."""
    work = _warm(server, monkeypatch, FakeInterpolate())

    for _ in range(3):
        status, body, headers = await _asgi_post(server)
        assert status == 200
        assert body == _CLIP
        assert '"model_calls": 240' in headers["x-rife-stats"]

    assert work.uploads == [_UPLOAD] * 3
    assert _work_dirs(tmp_path) == [], "a sent clip's work dir stayed behind"
    assert server._state.busy is False


async def test_a_request_stays_busy_until_its_clip_is_sent(server, monkeypatch, tmp_path):
    """FastAPI returns from the endpoint before Starlette sends the body.
    ``busy`` was cleared there, so a hard /unload during the send saw nothing
    in flight, exited, and cut the caller's clip off mid-body."""
    _warm(server, monkeypatch, FakeInterpolate())
    _big_reserved_pool(server)
    mid_send: list[tuple] = []

    async def hard_unload_mid_body():
        if not mid_send:
            result = await server.unload(server.UnloadRequest(hard=True))
            mid_send.append(
                (server._state.busy, server._state.last_used, _work_dirs(tmp_path), result),
            )

    with patch.object(os, "_exit") as exit_mock:
        status, body, _ = await _asgi_post(server, mid_body=hard_unload_mid_body)

    exit_mock.assert_not_called()
    assert status == 200
    assert body == _CLIP
    ((busy, last_used, dirs, result),) = mid_send
    assert busy is True
    assert result["status"] == "busy"
    assert len(dirs) == 1, "the clip being sent must still be on disk"
    assert server._state.busy is False
    assert server._state.last_used > last_used, "the idle window must start once the clip is out"
    assert _work_dirs(tmp_path) == []


async def test_a_request_whose_send_fails_still_ends(server, monkeypatch, tmp_path):
    """A caller that drops the connection mid-body must not stay busy: every
    later clip would be refused, and every unload declined, from then on."""
    _warm(server, monkeypatch, FakeInterpolate())

    async def connection_lost():
        raise OSError("connection reset by peer")

    with pytest.raises(OSError):
        await _asgi_post(server, mid_body=connection_lost)

    assert server._state.busy is False
    assert _work_dirs(tmp_path) == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ValueError("source is already 30 fps; target 30 is not an increase"), 400),
        (RuntimeError("encode failed (ffmpeg rc=1)"), 500),
    ],
    ids=["bad-clip", "failed-interpolation"],
)
async def test_a_failed_interpolation_ends_its_request(
    server, monkeypatch, tmp_path, error, status,
):
    _warm(server, monkeypatch, FakeInterpolate(raises=error))

    with pytest.raises(HTTPException) as exc:
        await _interpolate(server)

    assert exc.value.status_code == status
    assert _work_dirs(tmp_path) == []
    assert server._state.busy is False
    assert not server._state.gpu_lock.locked()


async def test_a_failed_load_reports_503_and_ends_its_request(server, monkeypatch, tmp_path):
    _install_loader(server, monkeypatch, FakeLoader(fail=True))
    work = FakeInterpolate()
    monkeypatch.setattr(server, "_interpolate_sync", work)

    with pytest.raises(HTTPException) as exc:
        await _interpolate(server)

    assert exc.value.status_code == 503
    assert "size mismatch" in exc.value.detail
    assert work.calls == 0
    assert _work_dirs(tmp_path) == []
    assert server._state.busy is False
    assert not server._state.gpu_lock.locked()


def test_a_load_that_fails_partway_publishes_no_model(server, monkeypatch):
    """The net is published only once its weights are in. Published earlier,
    a load whose weights did not fit would leave a randomly initialised IFNet
    behind, and every later request would skip the load and interpolate with
    it."""
    _install_loader(server, monkeypatch, FakeLoader(fail=True))

    assert server._load_model() is False
    assert server._state.model is None
    assert "size mismatch" in server._state.load_error


def test_repeated_loads_put_the_model_dir_on_sys_path_once(server, monkeypatch):
    """A load follows every idle unload, and each one inserted MODEL_CODE_DIR
    at the front of sys.path again."""
    _install_loader(server, monkeypatch, FakeLoader())

    for _ in range(3):
        assert server._load_model() is True
        server._unload_model()

    assert sys.path.count(server.MODEL_CODE_DIR) == 1


# ---------------------------------------------------------------------------
# The loop stays free
# ---------------------------------------------------------------------------


async def test_health_answers_while_the_model_loads(server, monkeypatch):
    """The load ran on the event loop, so /health, and every other request,
    waited for it. It is quick on a free card, but a load is where every
    request after an idle unload starts, and nothing bounds it on a contended
    one."""
    loader = FakeLoader(held=True)
    _install_loader(server, monkeypatch, loader)
    monkeypatch.setattr(server, "_interpolate_sync", FakeInterpolate())
    request = asyncio.create_task(_interpolate(server))
    await _wait_for(loader.started)

    async with _client(server) as client:
        resp = await asyncio.wait_for(client.get("/health"), timeout=2)

    body = resp.json()
    assert body["model_loaded"] is False, "/health must have been served DURING the load"
    assert body["busy"] is True
    assert body["gpu_busy"] is True

    loader.release.set()
    assert await _send(await asyncio.wait_for(request, _HOLD_TIMEOUT_S)) == _CLIP
    assert (await server.health())["model_loaded"] is True


async def test_health_answers_over_http_while_an_interpolation_runs(server, monkeypatch):
    work = _warm(server, monkeypatch, FakeInterpolate(held=True))
    request = asyncio.create_task(_interpolate(server))
    await _wait_for(work.started)

    async with _client(server) as client:
        resp = await asyncio.wait_for(client.get("/health"), timeout=2)

    body = resp.json()
    assert work.active == 1, "/health must have been served DURING the interpolation"
    assert body["busy"] is True
    assert body["gpu_busy"] is True

    work.release.set()
    assert await _send(await asyncio.wait_for(request, _HOLD_TIMEOUT_S)) == _CLIP


async def test_health_reports_an_idle_card_at_rest(server):
    body = await server.health()
    assert body["busy"] is False
    assert body["gpu_busy"] is False


async def test_health_answers_mid_unload_and_never_finds_the_model_missing(
    server, monkeypatch,
):
    """/health reads ``_state.model`` from the loop while an unload runs in a
    thread. ``del`` then re-assign would leave a moment where the attribute
    does not exist, and a /health read landing there raises AttributeError."""

    class _WatchedState(rife._State):
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
# One interpolation at a time
# ---------------------------------------------------------------------------


async def test_requests_that_arrive_together_are_admitted_one_at_a_time(server, monkeypatch):
    """The admission race. ``busy`` was checked on arrival and set only after
    ``await file.read()``, which yields for an upload spooled to disk. Both
    requests passed the check in that gap, and both interpolated on the card."""
    work = _warm(server, monkeypatch, FakeInterpolate(hold_s=0.2))

    results = await asyncio.wait_for(
        asyncio.gather(_interpolate(server), _interpolate(server), return_exceptions=True),
        timeout=10,
    )

    assert work.max_active == 1, "two interpolations ran on the card at once"
    refused = [r for r in results if isinstance(r, HTTPException)]
    admitted = [r for r in results if not isinstance(r, BaseException)]
    assert [r.status_code for r in refused] == [409]
    assert len(admitted) == 1
    assert await _send(admitted[0]) == _CLIP
    assert work.calls == 1
    assert server._state.busy is False


async def test_a_request_during_an_interpolation_is_refused_with_409(
    server, monkeypatch, tmp_path,
):
    """The contract the renderer relies on: any non-200 sends that clip to
    its ffmpeg fallback at once (``shot_list_renderer._rife_interpolate``)."""
    work = _warm(server, monkeypatch, FakeInterpolate(held=True))
    first = asyncio.create_task(_asgi_post(server))
    await _wait_for(work.started)

    async with _client(server) as client:
        resp = await asyncio.wait_for(
            client.post(
                "/interpolate",
                files={"file": ("hero_3.mp4", _UPLOAD, "video/mp4")},
                data={"target_fps": "30"},
            ),
            timeout=2,
        )

    assert resp.status_code == 409
    assert resp.json()["detail"] == "another interpolation is in flight"
    assert len(_work_dirs(tmp_path)) == 1, "a refused request must not leave a work dir"

    work.release.set()
    status, body, _ = await asyncio.wait_for(first, _HOLD_TIMEOUT_S)
    assert status == 200
    assert body == _CLIP
    assert work.calls == 1
    assert _work_dirs(tmp_path) == []


async def test_a_request_that_arrives_during_an_unload_waits_for_it(server, monkeypatch):
    """409 is for another interpolation, not for an unload. A request that
    arrives while the idle unload holds the card queues behind it (it is
    brief), loads the model again, and gets its clip."""
    server._state.model = _FakeModel()
    _install_loader(server, monkeypatch, FakeLoader())
    work = FakeInterpolate()
    monkeypatch.setattr(server, "_interpolate_sync", work)
    unloading = threading.Event()
    finish = threading.Event()

    def slow_unload():
        server._state.model = None
        unloading.set()
        finish.wait(_HOLD_TIMEOUT_S)

    monkeypatch.setattr(server, "_unload_model", slow_unload)
    _idle(server, monkeypatch)
    tick = asyncio.create_task(server._idle_unload_tick())
    await _wait_for(unloading)
    request = asyncio.create_task(_interpolate(server))
    await asyncio.sleep(0.05)

    assert not request.done(), "a request behind an unload must wait, not be refused"
    assert work.calls == 0

    finish.set()
    await asyncio.wait_for(tick, _HOLD_TIMEOUT_S)
    assert await _send(await asyncio.wait_for(request, _HOLD_TIMEOUT_S)) == _CLIP
    assert work.models[0] is not None, "the clip must be interpolated with a loaded model"


async def test_a_cancelled_request_keeps_the_card_until_its_thread_ends(
    server, monkeypatch, tmp_path,
):
    """A worker thread cannot be interrupted. If cancelling the request ended
    it at once, the next one would be admitted and start a second
    interpolation beside the one still running."""
    work = _warm(server, monkeypatch, FakeInterpolate(held=True))
    first = asyncio.create_task(_interpolate(server))
    await _wait_for(work.started)

    first.cancel()
    await _let_tasks_park()

    assert server._state.gpu_lock.locked(), "lock released while the thread still runs"
    with pytest.raises(HTTPException) as refused:
        await _interpolate(server)
    assert refused.value.status_code == 409, "admitted beside the orphaned interpolation"
    assert not first.done()

    work.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, _HOLD_TIMEOUT_S)
    assert server._state.busy is False
    assert _work_dirs(tmp_path) == [], "the cancelled request's work dir stayed behind"
    assert await _send(await _interpolate(server)) == _CLIP
    assert work.max_active == 1


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

    def interpolation_that_fails_late():
        started.set()
        fail.wait(_HOLD_TIMEOUT_S)
        raise RuntimeError("CUDA error: an illegal memory access was encountered")

    try:
        call = asyncio.create_task(server._run_on_gpu(interpolation_that_fails_late))
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
        "interpolation_that_fails_late finished after its request was cancelled, "
        "raising RuntimeError"
    ) in caplog.text


# ---------------------------------------------------------------------------
# Nothing drops the model out from under a request
# ---------------------------------------------------------------------------


async def test_idle_unload_leaves_a_running_interpolation_alone(server, monkeypatch):
    """``last_used`` is stamped when a request ends, so mid-request it can be
    any age. The tick must check ``busy``, and skip a busy server at once
    instead of queueing behind it."""
    work = _warm(server, monkeypatch, FakeInterpolate(held=True))
    dropped: list[int] = []
    monkeypatch.setattr(server, "_unload_model", lambda: dropped.append(work.active))

    request = asyncio.create_task(_interpolate(server))
    await _wait_for(work.started)
    _idle(server, monkeypatch)
    await asyncio.wait_for(server._idle_unload_tick(), timeout=1)
    assert dropped == []

    work.release.set()
    await _send(await asyncio.wait_for(request, _HOLD_TIMEOUT_S))
    _idle(server, monkeypatch)
    await server._idle_unload_tick()
    assert dropped == [0], "an idle model must still unload once the request is done"


async def test_idle_unload_rechecks_after_waiting_for_the_gpu(server, monkeypatch):
    """A request admitted while the tick waits for the lock is about to use
    the model. The tick must notice it and leave the model loaded."""
    server._state.model = _FakeModel()
    _idle(server, monkeypatch)
    dropped: list[bool] = []
    monkeypatch.setattr(server, "_unload_model", lambda: dropped.append(True))

    await server._state.gpu_lock.acquire()  # e.g. /unload mid-unload
    tick = asyncio.create_task(server._idle_unload_tick())
    await _let_tasks_park()
    server._state.busy = True  # an /interpolate is admitted and queues on the lock
    server._state.gpu_lock.release()
    await asyncio.wait_for(tick, _HOLD_TIMEOUT_S)

    assert dropped == []


def test_the_idle_loop_survives_a_failed_pass(server):
    """Nothing else unloads an idle model, so an exception in one pass (a
    CUDA error mid-unload, say) must cost that pass, not the loop. An escaped
    one ended the task, silently, for good."""

    class _StopLoop(BaseException):
        pass

    passes: list[bool] = []
    sleeps = {"n": 0}

    async def failing_tick():
        passes.append(True)
        raise RuntimeError("CUDA error: unspecified launch failure")

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 2:
            raise _StopLoop

    async def body():
        with patch.object(server.asyncio, "sleep", fake_sleep), \
             patch.object(server, "_idle_unload_tick", failing_tick):
            with pytest.raises(_StopLoop):
                await server._idle_loop()

    asyncio.run(body())
    assert passes == [True, True]


async def test_unload_still_declines_at_once_while_an_interpolation_holds_the_gpu(
    server, monkeypatch,
):
    """The scheduler gives /unload 15 s, and an interpolation holds the lock
    for its whole run. The decline must not wait for the lock."""
    work = _warm(server, monkeypatch, FakeInterpolate(held=True))
    request = asyncio.create_task(_interpolate(server))
    await _wait_for(work.started)

    result = await asyncio.wait_for(
        server.unload(server.UnloadRequest(hard=True)), timeout=1,
    )

    assert result == {"status": "busy", "detail": "interpolation in flight; not unloading"}
    work.release.set()
    await _send(await asyncio.wait_for(request, _HOLD_TIMEOUT_S))


async def test_soft_unload_rechecks_after_waiting_for_the_gpu(server, monkeypatch):
    server._state.model = _FakeModel()
    dropped: list[bool] = []
    monkeypatch.setattr(server, "_unload_model", lambda: dropped.append(True))

    await server._state.gpu_lock.acquire()  # e.g. the idle unload mid-unload
    unload = asyncio.create_task(server.unload(None))
    await _let_tasks_park()
    server._state.busy = True
    server._state.gpu_lock.release()
    result = await asyncio.wait_for(unload, _HOLD_TIMEOUT_S)

    assert result["status"] == "busy"
    assert dropped == []


async def test_hard_unload_does_not_exit_under_a_request_that_arrived_mid_unload(
    server, monkeypatch,
):
    """The exit is irreversible. An /interpolate admitted during the unload
    is queued on the lock the unload holds, and os._exit would reset its
    connection. Decline instead; the request loads the model again."""
    server._state.model = _FakeModel()
    _big_reserved_pool(server)
    _install_loader(server, monkeypatch, FakeLoader())
    work = FakeInterpolate()
    monkeypatch.setattr(server, "_interpolate_sync", work)
    unloading = threading.Event()
    finish_unload = threading.Event()

    def slow_unload():
        server._state.model = None
        unloading.set()
        finish_unload.wait(_HOLD_TIMEOUT_S)

    monkeypatch.setattr(server, "_unload_model", slow_unload)
    with patch.object(os, "_exit") as exit_mock:
        unload = asyncio.create_task(server.unload(server.UnloadRequest(hard=True)))
        await _wait_for(unloading)
        request = asyncio.create_task(_interpolate(server))
        await _let_tasks_park()
        finish_unload.set()
        result = await asyncio.wait_for(unload, _HOLD_TIMEOUT_S)
        clip = await _send(await asyncio.wait_for(request, _HOLD_TIMEOUT_S))

    exit_mock.assert_not_called()
    assert result["status"] == "busy"
    assert clip == _CLIP, "the request must load the model again and get its clip"
    assert work.models[0] is not None


async def _drop_via_idle_tick(server, monkeypatch):
    _idle(server, monkeypatch)
    await server._idle_unload_tick()


async def _drop_via_soft_unload(server, monkeypatch):
    await server.unload(None)


async def _drop_via_hard_unload(server, monkeypatch):
    _big_reserved_pool(server)
    with patch.object(os, "_exit"):
        await server.unload(server.UnloadRequest(hard=True))


@pytest.mark.parametrize(
    "drop",
    [_drop_via_idle_tick, _drop_via_soft_unload, _drop_via_hard_unload],
    ids=["idle-tick", "soft-unload", "hard-unload"],
)
async def test_every_model_drop_holds_the_gpu_lock(server, monkeypatch, drop):
    """Structural pin: a path that drops the model without the lock can do it
    while a request loads or uses it, whichever path it is."""
    server._state.model = _FakeModel()
    held: list[bool] = []
    monkeypatch.setattr(
        server, "_unload_model", lambda: held.append(server._state.gpu_lock.locked()),
    )

    await drop(server, monkeypatch)

    assert held == [True]


# ---------------------------------------------------------------------------
# The unload contract itself (unchanged, pinned across the restructure)
# ---------------------------------------------------------------------------


async def test_soft_unload_drops_the_model_and_stays_up(server):
    server._state.model = _FakeModel()
    _big_reserved_pool(server)

    with patch.object(os, "_exit") as exit_mock:
        result = await server.unload(None)

    exit_mock.assert_not_called()
    assert result == {
        "status": "unloaded", "vram_reserved_mb": 20_000,
        "min_reserved_mb": server.HARD_UNLOAD_MIN_RESERVED_MB,
    }
    assert server._state.model is None


async def test_hard_unload_exits_when_the_reserved_pool_clears_the_floor(server):
    server._state.model = _FakeModel()
    _big_reserved_pool(server)

    with patch.object(os, "_exit") as exit_mock:
        await server.unload(server.UnloadRequest(hard=True))

    exit_mock.assert_called_once_with(0)


async def test_hard_unload_below_the_floor_stays_up(server):
    """Exiting would reclaim nothing and cost a restart (the image-gen
    lesson, ~24 consecutive no-op exits before its gate)."""
    server._state.model = _FakeModel()
    server.torch.cuda.available = True
    server.torch.cuda.reserved_mb = 100

    with patch.object(os, "_exit") as exit_mock:
        result = await server.unload(server.UnloadRequest(hard=True))

    exit_mock.assert_not_called()
    assert result["status"] == "nothing_to_reclaim"


# ---------------------------------------------------------------------------
# The encoder never outlives a failed clip
# ---------------------------------------------------------------------------


class FakeEncoder:
    """``subprocess.Popen`` stand-in for the ffmpeg encoder. Like ffmpeg reading
    raw frames from a pipe, it runs until its stdin is closed (and then writes
    the output file) or until it is killed."""

    def __init__(self, args, **kwargs) -> None:
        self.args = args
        self.frames = 0
        self.killed = False
        self.hung = False
        self.returncode = None
        self._done = threading.Event()
        self.stdin = self._Pipe(self)

    class _Pipe:
        def __init__(self, encoder: "FakeEncoder") -> None:
            self.encoder = encoder
            self.closed = False

        def write(self, data: bytes) -> None:
            self.encoder.frames += 1

        def close(self) -> None:
            if not self.closed and not self.encoder.killed:
                Path(self.encoder.args[-1]).write_bytes(b"encoded")
            self.closed = True
            self.encoder._done.set()

    def kill(self) -> None:
        self.killed = True
        self._done.set()

    def wait(self, timeout=None) -> int:
        # Bounded: the real wait is 30 minutes.
        if not self._done.wait(min(timeout or _HOLD_TIMEOUT_S, _HOLD_TIMEOUT_S)):
            self.hung = True
            raise subprocess.TimeoutExpired(self.args, timeout)
        self.returncode = -9 if self.killed else 0
        return self.returncode


def _fake_pipeline(server, monkeypatch, *, fail_on_call: int | None = None) -> list[FakeEncoder]:
    """Three 64x32 source frames at 16 fps, and a model that raises on call
    ``fail_on_call`` if given. Returns the encoders ``_interpolate_sync``
    starts."""
    encoders: list[FakeEncoder] = []

    def popen(args, **kwargs):
        encoders.append(FakeEncoder(args, **kwargs))
        return encoders[-1]

    calls = {"n": 0}

    def midpoint(model, a, b):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB")
        return a

    monkeypatch.setattr(
        server, "subprocess", types.SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE),
    )
    monkeypatch.setattr(server, "_probe", lambda path: (64, 32, 16.0, 3))
    monkeypatch.setattr(
        server, "_read_frames", lambda path, w, h: np.zeros((3, h, w, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(server, "_to_tensor", lambda frame, dev: frame)
    monkeypatch.setattr(server, "_to_bytes", lambda t: b"frame")
    monkeypatch.setattr(server, "_midpoint", midpoint)
    server._state.model = _FakeModel()
    return encoders


def test_a_model_error_mid_clip_fails_the_request_at_once(server, monkeypatch, tmp_path):
    """ffmpeg reads frames from its stdin until the pipe closes. When the model
    raised mid-clip (a CUDA OOM, say), the pipe stayed open, and the wait for
    the encoder sat out its whole 30-minute timeout, with the request holding
    the card and refusing every other clip, before the error surfaced as a
    TimeoutExpired in place of the real one."""
    encoders = _fake_pipeline(server, monkeypatch, fail_on_call=2)
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        server._interpolate_sync(str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"), 30.0)

    (encoder,) = encoders
    assert not encoder.hung, "the wait for the encoder sat out its timeout"
    assert encoder.killed
    assert encoder.stdin.closed
    assert time.monotonic() - started < _HOLD_TIMEOUT_S


def test_a_clean_clip_closes_the_encoder_and_leaves_it_to_finish(server, monkeypatch, tmp_path):
    encoders = _fake_pipeline(server, monkeypatch)

    stats = server._interpolate_sync(
        str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"), 30.0,
    )

    (encoder,) = encoders
    assert not encoder.killed
    assert encoder.stdin.closed
    # 16 fps bisected to 64 (two rounds, three model calls per source pair):
    # frame 0, then four frames per pair.
    assert encoder.frames == 1 + 2 * 4
    assert stats["model_calls"] == 6
    assert stats["dense_fps"] == 64.0
    assert (tmp_path / "out.mp4").read_bytes() == b"encoded"

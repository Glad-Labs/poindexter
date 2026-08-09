"""ImageService renders under the GPU lock and says why it failed (#1005).

Two contracts, one root cause. The operator single-image endpoints
(``poindexter tasks regen-image`` / ``add-image``, ``POST
/api/tasks/{id}/generate-image``) reach the renderer only through
``ImageService``, and that path used to POST straight at the image-gen server:
no lock, so no Ollama eviction, so a regen issued while the ~19 GB writer was
warm raced it and lost to a CUDA OOM on a 31 GB card. It then reported that
OOM as "image generation produced no output" — a symptom, describing an output
step the render never reached.

So: (1) the render happens inside ``gpu.lock("image_gen")``, whose acquire
evicts + confirms Ollama on every host, at operator priority with a bounded
wait; (2) failures carry the reason, and specifically the image-gen server's
own diagnosis, all the way to the caller.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services import gpu_scheduler as gpu_scheduler_mod
from services.gpu_admission import GpuBusyError
from services.gpu_scheduler import GpuLockTimeoutError
from services.image_service import ImageGenOutcome, ImageService, _server_error_detail
from services.site_config import SiteConfig

# No pytestmark: asyncio_mode="auto" already runs the async tests, and marking
# the sync ones here only produces PytestWarnings.


def _svc(**config) -> ImageService:
    """Pool-less ImageService — live_activity self-disables without a pool."""
    return ImageService(SiteConfig(initial_config=config))


@asynccontextmanager
async def _noop_lock(*_a, **_kw):
    yield


def _recording_lock(calls: list):
    """Stand-in for ``gpu.lock`` that records how it was called."""

    @asynccontextmanager
    async def _lock(owner, **kwargs):
        calls.append({"owner": owner, **kwargs})
        yield

    return _lock


def _raising_lock(exc: BaseException):
    @asynccontextmanager
    async def _lock(*_a, **_kw):
        raise exc
        yield  # pragma: no cover — unreachable, keeps this a generator

    return _lock


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


async def test_render_runs_inside_the_image_gen_lock():
    """``image_gen`` is the owner that triggers the Ollama eviction on acquire.

    This is the whole fix: the scheduler unloads every resident Ollama model
    and *confirms* the VRAM is released before yielding, so the renderer no
    longer loads alongside a warm 19 GB writer.
    """
    svc = _svc()
    calls: list = []
    order: list[str] = []

    async def _impl(*_a, **_kw):
        order.append("render")
        return ImageGenOutcome(True)

    with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_recording_lock(calls))), \
            patch.object(ImageService, "_generate_image_impl", side_effect=_impl):
        outcome = await svc.generate_image_result("a cat", "/tmp/x.png", task_id="t9")

    assert outcome.ok
    assert order == ["render"], "the render must happen inside the lock, not outside it"
    assert len(calls) == 1
    assert calls[0]["owner"] == "image_gen"
    assert calls[0]["task_id"] == "t9", "task_id threads through for cost attribution"
    assert calls[0]["phase"] == "operator_image"


async def test_lock_is_operator_priority_with_a_bounded_wait():
    """poindexter#914 P2 group 3 — outranks background work, never the pipeline.

    The budget matters because a human is holding an open HTTP request: the
    client gives up at post_edit_regen_image_timeout_s (300s), so an unbounded
    wait behind a ~380s render can only ever end in a bare client timeout.
    """
    svc = _svc()
    calls: list = []
    with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_recording_lock(calls))), \
            patch.object(
                gpu_scheduler_mod, "operator_image_wait_budget_s", lambda: 150.0,
            ), \
            patch.object(
                ImageService, "_generate_image_impl",
                AsyncMock(return_value=ImageGenOutcome(True)),
            ):
        await svc.generate_image_result("a cat", "/tmp/x.png")

    assert calls[0]["priority"] == "operator"
    assert calls[0]["max_wait_s"] == 150.0


async def test_zero_budget_restores_the_unbounded_legacy_wait():
    """``gpu_sched_operator_image_max_wait_s=0`` is the documented escape hatch:
    the helper returns None, which is gpu.lock's legacy no-admission contract."""
    svc = _svc()
    calls: list = []
    with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_recording_lock(calls))), \
            patch.object(
                gpu_scheduler_mod, "operator_image_wait_budget_s", lambda: None,
            ), \
            patch.object(
                ImageService, "_generate_image_impl",
                AsyncMock(return_value=ImageGenOutcome(True)),
            ):
        await svc.generate_image_result("a cat", "/tmp/x.png")

    assert calls[0]["max_wait_s"] is None


@pytest.mark.parametrize(
    "exc",
    [
        GpuBusyError("eta_exceeds_budget", 412.0),
        GpuLockTimeoutError("gpu.lock('image_gen') timed out after 900s"),
    ],
    ids=["admission_reject", "acquire_timeout"],
)
async def test_gpu_unavailable_is_reported_not_raised(exc):
    """Capacity is an outcome, not a crash — and it must be NAMED.

    Returning rather than raising keeps generate_image()'s bool contract for
    every existing caller, while the reason token lets the operator surface
    say "the GPU is busy, retry" instead of blaming the renderer for a queue
    it never entered.
    """
    svc = _svc()
    impl = AsyncMock(return_value=ImageGenOutcome(True))
    with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_raising_lock(exc))), \
            patch.object(ImageService, "_generate_image_impl", impl):
        outcome = await svc.generate_image_result("a cat", "/tmp/x.png")

    assert outcome.ok is False
    assert outcome.reason == "gpu_busy"
    impl.assert_not_awaited(), "a refused wait must not reach the renderer"


async def test_admission_reject_message_carries_the_holder_eta():
    """The ETA is the actionable part — it tells the operator when to retry."""
    svc = _svc()
    with patch.object(
        gpu_scheduler_mod, "gpu",
        SimpleNamespace(lock=_raising_lock(GpuBusyError("eta_exceeds_budget", 412.0))),
    ), patch.object(
        ImageService, "_generate_image_impl",
        AsyncMock(return_value=ImageGenOutcome(True)),
    ):
        outcome = await svc.generate_image_result("a cat", "/tmp/x.png")

    assert "412" in outcome.message
    assert "gpu_busy" in outcome.message


# ---------------------------------------------------------------------------
# Backward compatibility of the bool API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,expected",
    [(ImageGenOutcome(True), True), (ImageGenOutcome(False, "server_error", "x"), False)],
    ids=["success", "failure"],
)
async def test_generate_image_still_returns_a_plain_bool(outcome, expected):
    """Existing callers (ImageGenProvider, startup warmup) keep their contract."""
    svc = _svc()
    with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_noop_lock)), \
            patch.object(ImageService, "_generate_image_impl", AsyncMock(return_value=outcome)):
        result = await svc.generate_image("a cat", "/tmp/x.png")

    assert result is expected
    assert isinstance(result, bool)


def test_outcome_has_no_bool_override():
    """A failed outcome must stay TRUTHY as an object, or `err or fallback`
    silently drops it — which is exactly how the image-gen diagnosis travels
    across the diffusers fallback. Callers test ``.ok``."""
    assert bool(ImageGenOutcome(False, "server_error", "boom")) is True
    assert (ImageGenOutcome(False, "server_error", "boom") or "fallback") != "fallback"


def test_outcome_message_names_the_reason_even_without_detail():
    assert "server_error" in ImageGenOutcome(False, "server_error").message


# ---------------------------------------------------------------------------
# Reason propagation out of the image-gen server
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=503, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


_OOM = (
    "CUDA out of memory. Tried to allocate 76.00 MiB. GPU 0 has a total "
    "capacity of 31.34 GiB of which 30.94 MiB is free."
)


def test_server_error_detail_prefers_the_fastapi_detail_field():
    """Where the image-gen server actually writes the cause (its own 503 body)."""
    resp = _FakeResponse(payload={"detail": f"pipeline load failed: {_OOM}"})
    assert "CUDA out of memory" in _server_error_detail(resp)


def test_server_error_detail_falls_back_to_the_raw_body():
    resp = _FakeResponse(payload=None, text="  upstream exploded  ")
    assert _server_error_detail(resp) == "upstream exploded"


def test_server_error_detail_truncates_the_allocator_dump():
    """A torch OOM carries a multi-line allocator dump — useless in an HTTP body."""
    resp = _FakeResponse(payload={"detail": "x" * 5000})
    assert len(_server_error_detail(resp)) <= 400


def test_server_error_detail_never_raises_on_an_unreadable_body():
    class _Hostile:
        status_code = 503

        def json(self):
            raise RuntimeError("no")

        @property
        def text(self):
            raise RuntimeError("no")

    assert _server_error_detail(_Hostile()) == "unreadable response body"


async def test_oom_from_the_server_reaches_the_caller():
    """End-to-end for the reported bug: the OOM the server logged is the OOM
    the operator is told about, instead of "produced no output"."""
    svc = _svc()
    resp = _FakeResponse(status_code=503, payload={"detail": f"pipeline load failed: {_OOM}"})

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *_a, **_kw):
            return resp

    with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_noop_lock)), \
            patch("httpx.AsyncClient", lambda **_kw: _FakeClient()):
        # gen_available False → no local diffusers fallback, which is the live
        # shape since torch left the worker image.
        svc.gen_initialized = True
        svc.gen_available = False
        outcome = await svc.generate_image_result("a cat", "/tmp/x.png")

    assert outcome.ok is False
    assert outcome.reason == "server_error", (
        "a diagnosed server failure must not be flattened into the generic "
        "'no backend available' reason by the diffusers fallback"
    )
    assert "CUDA out of memory" in outcome.message
    assert "503" in outcome.message


async def test_unreachable_server_reports_the_type_but_never_the_address():
    """`detail` ends up in an HTTP response body, and an httpx ConnectError
    carries the resolved address of the host it failed to reach — the
    disclosure scripts/ci/lint_http_detail_leak.py exists to stop. The
    exception TYPE is the actionable half and is safe."""

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *_a, **_kw):
            raise OSError("connection refused to 172.18.0.7:9836")

    svc = _svc()
    with patch.object(gpu_scheduler_mod, "gpu", SimpleNamespace(lock=_noop_lock)), \
            patch("httpx.AsyncClient", lambda **_kw: _Boom()):
        svc.gen_initialized = True
        svc.gen_available = False
        outcome = await svc.generate_image_result("a cat", "/tmp/x.png")

    assert outcome.reason == "server_error"
    assert "unreachable" in outcome.message
    assert "OSError" in outcome.message
    assert "172.18.0.7" not in outcome.message, "no resolved address in a client-bound string"

"""RIFE frame-interpolation sidecar.

One endpoint: POST a clip, get it back at a higher frame rate. The renderer
calls this instead of ffmpeg's ``minterpolate`` when it is reachable, and falls
back to ffmpeg when it is not — an un-interpolated or ffmpeg-interpolated clip
is a quality regression, a missing clip is a lost shot.

**Why not midpoint-only in one hop.** The pinned model is RIFE v4's IFNet: it
takes two frames and returns the frame HALFWAY between them. Nothing else. So
an arbitrary rate change (16 -> 30 fps here) is done by recursive bisection to
a power-of-two multiple that clears the target — 16 -> 32 -> 64 — and then
resampling 64 -> 30 by nearest frame, which lands every output frame within
1/128 s of its true time. Three model calls per source pair.

**Transport is HTTP, deliberately.** No shared mounts, the same boundary the
ComfyUI provider keeps: the caller uploads the clip and receives the result.
Clips are a few MB.

**GPU posture.** The model is 12 MB and loads in well under a second, so this
holds nothing between calls by default: it unloads after
``RIFE_IDLE_TIMEOUT`` seconds idle and honours the reclaim ladder's ``/unload``
(soft and hard) like every other sidecar. It must never be the reason a render
cannot find VRAM.

**Dropping the model is not enough (2026-09-25).** The first interpolation
creates a CUDA context, and only a process exit returns it: measured in a
throwaway container of this image, per PID from the host's nvidia-smi, the
server holds 720 MiB with the model loaded and 622 MiB after the model is
dropped, with ``memory_reserved`` at 0. The hard unload's old floor measured
``memory_reserved``, which cannot see a context, so every unload answered
``nothing_to_reclaim`` and the idle process kept 622 MiB of the render GPU for
good. So the exit is now gated on what the driver counts for this process
(NVML, read without a context; see ``_process_vram_mb``), and the idle pass
exits once the model is gone and a context is left: Docker's restart policy
brings the server back in ~1.4 s, and the next interpolation pays ~1.2 s for
CUDA init and the model load (measured: 2.0 s cold, 0.76 s warm, for a 3 s
clip). The reclaim ladder's rung stays soft, so a render that interpolates
every few minutes keeps a warm process; the idle pass returns the context
``RIFE_IDLE_TIMEOUT`` after the last clip.

**Request lifecycle (2026-09-25), the stable-audio pattern (#4034).** One
interpolation at a time: a request is admitted and counted as busy in one step,
and any other that arrives meanwhile is refused with 409 (see ``interpolate``
for why that beats queueing). It stays busy until its clip has been sent, and
its work dir goes with it. ``_state.gpu_lock`` keeps its load and interpolation
apart from the idle unload and ``/unload``, and that blocking work runs in
worker threads (``_run_on_gpu``) so the loop keeps answering ``/health``.
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from safetensors.torch import load_file
from starlette.types import Receive, Scope, Send

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rife")

PORT = int(os.getenv("RIFE_PORT", "9842"))
IDLE_TIMEOUT = float(os.getenv("RIFE_IDLE_TIMEOUT", "300"))
WEIGHTS = os.getenv("RIFE_WEIGHTS", "/app/flownet.safetensors")
# Where the image puts interpolation_model.py (Dockerfile.rife).
MODEL_CODE_DIR = "/app"
# Exit floors: below them an exit would return nothing worth a restart, so the
# process stays up (the image-gen lesson: ~24 consecutive no-op exits before its
# gate). The process floor applies to the driver's count for this process
# (NVML), which includes the CUDA context; every context measured on the render
# GPU was 498 MiB or more, so any context clears it. The reserved-pool floor is
# the fallback when NVML cannot answer: torch's view, blind to the context.
HARD_UNLOAD_MIN_PROCESS_MB = int(os.getenv("RIFE_HARD_UNLOAD_MIN_PROCESS_MB", "128"))
HARD_UNLOAD_MIN_RESERVED_MB = int(os.getenv("RIFE_HARD_UNLOAD_MIN_RESERVED_MB", "512"))
# RIFE's pyramid downsamples by 32; frames are padded up and cropped back.
ALIGN = 32
MAX_SOURCE_FRAMES = int(os.getenv("RIFE_MAX_SOURCE_FRAMES", "4000"))
# How far past the target the dense grid must reach before resampling; see
# _interpolate_sync. 2.0 = one extra bisection round for a halved timing error.
DENSE_MULTIPLE = float(os.getenv("RIFE_DENSE_MULTIPLE", "2.0"))

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Resolve NVML now, so a missing binding shows up in the boot log rather
    # than as an idle process that never gives its context back.
    held = _process_vram_mb()
    logger.info(
        "exit gate measures %s",
        f"this process through NVML ({held} MiB held now, no CUDA context)"
        if held is not None
        else "torch's reserved pool (NVML unusable, see the warning above); it "
        "cannot see a CUDA context",
    )
    task = asyncio.create_task(_idle_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="RIFE interpolation server", version="1.0", lifespan=_lifespan)


class _State:
    def __init__(self) -> None:
        self.model: Any = None
        self.last_used: float = 0.0
        # True from the moment /interpolate admits a request until its clip
        # has been sent, or sending it has failed. Only one request is
        # admitted at a time; /unload and the idle unload decline while one is.
        self.busy: bool = False
        self.load_error: str = ""
        # One load, interpolation or unload on the card at a time. That work
        # runs in worker threads (_run_on_gpu) so the loop keeps answering
        # /health, which also means nothing but this lock stops an unload
        # from dropping the model while a request loads or uses it.
        self.gpu_lock = asyncio.Lock()
        # The NVML binding once initialised, and why NVML is unusable once it
        # has failed (see _process_vram_mb).
        self.nvml: Any = None
        self.nvml_error: str | None = None


_state = _State()


def _device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


async def _run_on_gpu(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run blocking GPU work (a load, an interpolation, an unload) in a worker
    thread. The caller holds ``_state.gpu_lock``.

    A thread cannot be interrupted, though. If the awaiting request is
    cancelled, this still waits for the thread to finish before letting the
    cancellation through. Otherwise the caller's ``async with _state.gpu_lock``
    would release the lock, and the request would end, while the thread still
    ran on the card.
    """
    work = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            # A repeated cancel changes nothing: the card is busy until the
            # thread ends.
            with suppress(asyncio.CancelledError):
                await asyncio.wait({work})
        # shield() stops watching the thread once its caller is cancelled, so
        # a failure after that point is ours to collect, or asyncio logs it
        # as "Task exception was never retrieved".
        if not work.cancelled() and (exc := work.exception()) is not None:
            logger.warning(
                "%s finished after its request was cancelled, raising %s: %s",
                getattr(fn, "__name__", "gpu call"), type(exc).__name__, exc,
            )
        raise


def _load_model() -> bool:
    """Load IFNet if it is not loaded. Blocking: call it via :func:`_run_on_gpu`
    under the GPU lock."""
    if _state.model is not None:
        return True
    try:
        # Once: this runs on every load, and a load follows every idle unload.
        if MODEL_CODE_DIR not in sys.path:
            sys.path.insert(0, MODEL_CODE_DIR)
        from interpolation_model import IFNet  # type: ignore[import-not-found]

        model = IFNet()
        model.load_state_dict(load_file(WEIGHTS))
        model.to(_device()).eval()
        # Published last, in one assignment: "model is not None" is what the
        # next request and /health read as loaded, so a load that fails
        # partway (weights that do not fit the net, say) publishes nothing.
        _state.load_error = ""
        _state.model = model
        logger.info("RIFE loaded on %s", _device())
        return True
    except Exception as exc:  # noqa: BLE001 — reported through /health
        _state.load_error = f"{type(exc).__name__}: {exc}"
        logger.error("RIFE load failed: %s", _state.load_error)
        return False


def _unload_model() -> None:
    """Drop the model and return its VRAM to the caching allocator. Blocking:
    call it via :func:`_run_on_gpu` under the GPU lock.

    One assignment, never ``del`` then re-assign: /health reads
    ``_state.model`` from the loop while this runs in a thread."""
    _state.model = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _reserved_mb() -> int:
    if not torch.cuda.is_available():
        return 0
    return int(torch.cuda.memory_reserved(0) // 1024 // 1024)


def _process_vram_mb() -> int | None:
    """VRAM this process holds (MiB), as the driver counts it, or ``None``
    when NVML cannot say.

    The driver's count includes the CUDA context, and ``memory_reserved``
    does not. That difference is the whole defect this reads around: after
    the model was dropped the server held 622 MiB with 0 MB reserved.

    NVML answers without creating a context. Inside a container it lists only
    that container's processes, under their in-container PIDs (measured on
    driver 595.84: a throwaway container's server saw itself as PID 1 with
    612 MiB, the figure the host's nvidia-smi gave for its host PID), so
    ``os.getpid()`` finds this server. Summed over every GPU NVML can see,
    because an exit returns all of it. The first failure disables NVML for
    this process and logs once; the exit gate then falls back to
    ``memory_reserved``.
    """
    if _state.nvml_error is not None:
        return None
    try:
        nvml = _state.nvml
        if nvml is None:
            # nvidia-ml-py. Images built before 2026-09-25 do not have it, and
            # libnvidia-ml.so.1 is only in the container when the NVIDIA
            # runtime grants the `utility` driver capability.
            import pynvml

            pynvml.nvmlInit()
            nvml = _state.nvml = pynvml
        pid = os.getpid()
        used = 0
        for index in range(nvml.nvmlDeviceGetCount()):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            for proc in nvml.nvmlDeviceGetComputeRunningProcesses(handle):
                if proc.pid != pid:
                    continue
                if proc.usedGpuMemory is None:
                    raise RuntimeError(
                        "NVML lists this process without its memory "
                        "(per-process accounting unavailable here)",
                    )
                used += int(proc.usedGpuMemory)
        return used // 1024 // 1024
    except Exception as exc:  # noqa: BLE001 — any NVML failure means "use torch's view"
        _state.nvml = None
        _state.nvml_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "NVML unusable (%s). The exit gate falls back to torch's reserved "
            "pool, which cannot see a CUDA context, so an idle process keeps "
            "its context (~0.6 GB on the render GPU).",
            _state.nvml_error,
        )
        return None


def _exit_gate() -> dict[str, Any]:
    """What an exit would return to the card, and whether it clears the floor.

    ``reclaimable`` decides both the hard ``/unload`` and the idle exit. With
    NVML it is the driver's count for this process against
    ``HARD_UNLOAD_MIN_PROCESS_MB``; without it, torch's reserved pool against
    ``HARD_UNLOAD_MIN_RESERVED_MB`` (the pre-2026-09-25 measure, which cannot
    see a context). ``vram_process_source`` is ``"nvml"`` only when the
    driver answered: the GPU scheduler trusts a decline that says so.
    """
    process_mb = _process_vram_mb()
    reserved_mb = _reserved_mb()
    if process_mb is not None:
        reclaimable = process_mb >= HARD_UNLOAD_MIN_PROCESS_MB
    else:
        reclaimable = reserved_mb >= HARD_UNLOAD_MIN_RESERVED_MB
    return {
        "reclaimable": reclaimable,
        "vram_process_mb": process_mb,
        "vram_process_source": "nvml" if process_mb is not None else None,
        "min_process_mb": HARD_UNLOAD_MIN_PROCESS_MB,
        "vram_reserved_mb": reserved_mb,
        "min_reserved_mb": HARD_UNLOAD_MIN_RESERVED_MB,
    }


def _gate_fields(gate: dict[str, Any]) -> dict[str, Any]:
    """The gate's measurements, as a response reports them."""
    return {k: v for k, v in gate.items() if k != "reclaimable"}


def _describe_gate(gate: dict[str, Any]) -> str:
    if gate["vram_process_source"] == "nvml":
        return (
            f"{gate['vram_process_mb']} MiB held per the driver, "
            f"floor {gate['min_process_mb']} MiB"
        )
    return (
        f"{gate['vram_reserved_mb']} MB reserved (NVML unusable), "
        f"floor {gate['min_reserved_mb']} MB"
    )


def _exit_now(reason: str) -> None:
    """Exit so the CUDA context goes back to the card. Docker's restart policy
    brings the server back; the next /interpolate loads the model again."""
    logger.warning("%s — exiting to return the CUDA context", reason)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def _probe(path: str) -> tuple[int, int, float, int]:
    """(width, height, fps, nb_frames) — fps from the container, never assumed."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,nb_frames", "-of", "json", path],
        capture_output=True, text=True, timeout=60,
    ).stdout
    s = json.loads(out)["streams"][0]
    num, _, den = str(s["r_frame_rate"]).partition("/")
    fps = float(num) / float(den or 1)
    try:
        n = int(s.get("nb_frames") or 0)
    except (TypeError, ValueError):
        n = 0
    return int(s["width"]), int(s["height"]), fps, n


def _read_frames(path: str, w: int, h: int) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=1800,
    ).stdout
    n = len(raw) // (w * h * 3)
    if n < 2:
        raise ValueError(f"decoded {n} frame(s) from {path!r}; need at least 2")
    if n > MAX_SOURCE_FRAMES:
        raise ValueError(f"{n} source frames exceeds RIFE_MAX_SOURCE_FRAMES={MAX_SOURCE_FRAMES}")
    return np.frombuffer(raw[: n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)


def _to_tensor(frame: np.ndarray, dev: str) -> torch.Tensor:
    # A copy: frames are views into ffmpeg's read-only output buffer, and
    # torch.from_numpy warns about "undefined behavior" on a read-only array.
    t = torch.from_numpy(np.array(frame)).to(dev).permute(2, 0, 1).float() / 255.0
    return t.unsqueeze(0)


def _pad(t: torch.Tensor) -> tuple[torch.Tensor, int, int]:
    _, _, h, w = t.shape
    ph = (ALIGN - h % ALIGN) % ALIGN
    pw = (ALIGN - w % ALIGN) % ALIGN
    if ph or pw:
        t = torch.nn.functional.pad(t, (0, pw, 0, ph), mode="replicate")
    return t, h, w


@torch.no_grad()
def _midpoint(model: Any, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    x, h, w = _pad(torch.cat([a, b], dim=1))
    out = model(x)
    if isinstance(out, (tuple, list)):
        out = out[0]
    return out[:, :, :h, :w].clamp(0.0, 1.0)


def _to_bytes(t: torch.Tensor) -> bytes:
    arr = (t[0].permute(1, 2, 0) * 255.0).round().clamp(0, 255).to(torch.uint8)
    return arr.cpu().numpy().tobytes()


def _interpolate_sync(src: str, dest: str, target_fps: float) -> dict[str, Any]:
    """Raise the clip to ``target_fps``; returns a small stats dict. Blocking:
    call it via :func:`_run_on_gpu` under the GPU lock."""
    w, h, src_fps, _ = _probe(src)
    if target_fps <= src_fps + 1e-6:
        raise ValueError(f"source is already {src_fps:g} fps; target {target_fps:g} is not an increase")
    frames = _read_frames(src, w, h)
    dev = _device()
    model = _state.model

    # Bisect to the first power-of-two multiple at or above the target, so
    # every emitted frame sits on an exact 1/2^k boundary and the downsample
    # to target_fps is a pure nearest-frame pick.
    # Oversample the dense grid past the target before resampling. At exactly
    # the target (16 -> 32 for a 30 fps ask) the nearest-frame pick is up to
    # half a dense frame off — 15.6 ms, close to half an output frame, which
    # reads as judder. One more bisection round (64 fps) cuts that to 7.8 ms
    # for 3 model calls per pair instead of 1.
    need = target_fps * DENSE_MULTIPLE
    mult = 2
    while src_fps * mult < need - 1e-6:
        mult *= 2
    dense_fps = src_fps * mult

    started = time.monotonic()
    calls = 0
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", f"{dense_fps:.6f}", "-i", "-",
         "-vf", f"fps={target_fps:.6f}",
         "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", dest],
        stdin=subprocess.PIPE,
    )
    if enc.stdin is None:
        raise RuntimeError("could not open a pipe to the encoder")
    try:
        prev = _to_tensor(frames[0], dev)
        enc.stdin.write(_to_bytes(prev))
        for i in range(1, len(frames)):
            cur = _to_tensor(frames[i], dev)
            # Recursive bisection. One round halves every segment, so after
            # log2(mult) rounds the pair is split into `mult` equal steps:
            # [a, b] -> [a, .5, b] -> [a, .25, .5, .75, b] -> ...
            pts = [prev, cur]
            while len(pts) - 1 < mult:
                nxt = [pts[0]]
                for k in range(len(pts) - 1):
                    nxt.append(_midpoint(model, pts[k], pts[k + 1]))
                    calls += 1
                    nxt.append(pts[k + 1])
                pts = nxt
            # `prev` was already written (as the previous pair's endpoint, or
            # as frame 0), so emit the interpolants and this pair's endpoint.
            for t in pts[1:]:
                enc.stdin.write(_to_bytes(t))
            prev = cur
        enc.stdin.close()
    except BaseException:
        # The encoder is still reading its stdin, so the wait below would sit
        # out its whole timeout (30 min) before this failure could surface,
        # and the request would hold the card, refusing every other clip,
        # until then.
        enc.kill()
        with suppress(OSError):
            enc.stdin.close()
        raise
    finally:
        enc.wait(timeout=1800)
    if enc.returncode != 0 or not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise RuntimeError(f"encode failed (ffmpeg rc={enc.returncode})")
    return {
        "source_fps": round(src_fps, 4), "target_fps": round(target_fps, 4),
        "dense_fps": round(dense_fps, 4), "source_frames": int(len(frames)),
        "model_calls": calls, "seconds": round(time.monotonic() - started, 2),
    }


def _end_request(workdir: str | None) -> None:
    """End one /interpolate: remove its work dir (the uploaded clip and the
    interpolated one), stamp ``last_used`` and free the slot. Stamped on the
    way OUT, so the idle timer measures time since the caller had its clip."""
    if workdir is not None:
        try:
            shutil.rmtree(workdir)
        except OSError as exc:
            logger.warning("could not remove work dir %s: %s", workdir, exc)
    _state.last_used = time.monotonic()
    _state.busy = False


class _ClipFileResponse(FileResponse):
    """The interpolated clip on its way to the caller. ``on_sent`` runs once
    the body is out, or once sending it has failed.

    That is where a request ends. FastAPI returns from the endpoint BEFORE
    Starlette sends the body, so a request ended in the endpoint was no longer
    busy while its clip streamed, and a hard ``/unload`` could exit mid-body.
    ``on_sent`` also removes the work dir: nothing did, and the live container
    held 67 of them, 126 MB of clips, three days after it was created
    (measured 2026-09-25).
    """

    def __init__(self, path: str, *, on_sent: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._on_sent = on_sent

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._on_sent()


class UnloadRequest(BaseModel):
    hard: bool = False


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model_loaded": _state.model is not None,
        # A request is in the server (loading, interpolating or sending its
        # clip back), and whether a load, interpolation or unload holds the
        # card right now.
        "busy": _state.busy,
        "gpu_busy": _state.gpu_lock.locked(),
        "device": _device(),
        "last_used": _state.last_used,
        "vram_reserved_mb": _reserved_mb(),
        # What this process holds on the card as the driver counts it, CUDA
        # context included (null when NVML cannot answer). With the model
        # dropped, torch's reserved pool reads 0 while this reads ~622.
        "vram_process_mb": _process_vram_mb(),
        "load_error": _state.load_error,
    }


@app.post("/interpolate")
async def interpolate(
    file: Annotated[UploadFile, File()],
    target_fps: Annotated[float, Form()] = 30.0,
) -> _ClipFileResponse:
    """One interpolation at a time; a request that arrives during another is
    refused with 409 rather than queued.

    409 is what the one caller relies on. The renderer
    (``shot_list_renderer._rife_interpolate``) treats any non-200 as "use
    ffmpeg's minterpolate for this clip" and moves on, and it calls from inside
    the render's exclusive ``gpu.lock('video')``, so its clips never overlap
    here. A second request therefore means the first one's caller gave up (at
    ``video_clip_interpolation_timeout_s``, 600 s, against 2-7 s interpolations
    in prod) or someone else is calling. Queued, a clip would wait on work
    already known to be stuck or slow, for as long as that takes, with the
    render holding the video lock, and fall back to ffmpeg anyway once its own
    budget ran out. Refused, it costs one ffmpeg-interpolated clip.

    The check and the claim happen with no await between them, so two
    requests cannot both pass. The claim used to come after ``await
    file.read()``, which yields for any upload Starlette has spooled to disk
    (over 1 MB; the live container's largest was 1.77 MB), so a second
    request could pass the check in that gap and interpolate beside the first.
    From the claim on, the request ends in ``_ClipFileResponse`` once its clip
    is sent, or below if no response gets that far.
    """
    if _state.busy:
        raise HTTPException(status_code=409, detail="another interpolation is in flight")
    _state.busy = True
    workdir: str | None = None
    try:
        workdir = tempfile.mkdtemp(prefix="rife_")
        return await _interpolate_inner(workdir, file, float(target_fps))
    except BaseException:
        _end_request(workdir)
        raise


async def _interpolate_inner(workdir: str, file: UploadFile, target_fps: float) -> _ClipFileResponse:
    """The body of /interpolate, run in the request's own work dir."""
    src = os.path.join(workdir, "in" + (Path(file.filename or "in.mp4").suffix or ".mp4"))
    dest = os.path.join(workdir, "out.mp4")
    with open(src, "wb") as fh:
        fh.write(await file.read())

    # The load and the interpolation hold the card, their CUDA work in worker
    # threads. Only an unload can hold the lock ahead of this request (another
    # interpolation was refused above), and it is brief. Sending the clip
    # needs no GPU, so it happens after the lock is released.
    async with _state.gpu_lock:
        if not await _run_on_gpu(_load_model):
            raise HTTPException(status_code=503, detail=f"model unavailable: {_state.load_error}")
        try:
            stats = await _run_on_gpu(_interpolate_sync, src, dest, target_fps)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — the caller falls back to ffmpeg
            logger.warning("interpolation failed: %s: %s", type(exc).__name__, exc)
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    logger.info("interpolated %s", stats)
    return _ClipFileResponse(
        dest, media_type="video/mp4", filename="interpolated.mp4",
        headers={"X-Rife-Stats": json.dumps(stats)},
        on_sent=lambda: _end_request(workdir),
    )


@app.post("/unload")
async def unload(req: UnloadRequest | None = None) -> dict[str, Any]:
    """Manual VRAM release for the worker's reclaim ladder.

    Never unloads out from under a live interpolation: the ladder calls this
    whenever the render GPU looks short, and work in flight is part of that
    picture — obeying would destroy the clip the reclaim exists to make room
    for (the wan-server lesson, poindexter#962).

    The decline does not wait for the GPU lock, which an interpolation holds
    for its whole run: the scheduler gives this call 15 s. Past the lock, the
    request is checked again, and once more right before a hard exit.

    A hard unload exits when the process still holds VRAM once the model is
    dropped (``_exit_gate``): what is left is the CUDA context, and only an
    exit returns it. Below the floor it answers ``nothing_to_reclaim`` and
    reports what it measured, so a decline carries its own evidence.
    """
    if _state.busy:
        return _decline_unload()
    hard = bool(req.hard) if req else False
    async with _state.gpu_lock:
        # Re-check: a request admitted while we waited for the lock is about
        # to load the model and use it.
        if _state.busy:
            return _decline_unload()
        await _run_on_gpu(_unload_model)
        gate = _exit_gate()
        if hard and gate["reclaimable"]:
            if _state.busy:
                # Admitted during the unload and queued on this lock: the exit
                # is irreversible and would reset its connection. The model is
                # already dropped, so it loads again; the process stays up.
                logger.warning(
                    "[HARD UNLOAD] not exiting: an interpolation arrived during "
                    "the unload; model dropped, process kept up",
                )
                return _decline_unload(
                    "interpolation arrived during the unload; model dropped, process kept up",
                )
            _exit_now(f"[HARD UNLOAD] {_describe_gate(gate)}")
            # Reachable only with os._exit mocked (tests): a real exit never
            # returns, so falling through to the generic return below would
            # report "nothing_to_reclaim" for an exit that actually happened.
            return {"status": "exiting", **_gate_fields(gate)}
    return {
        "status": "unloaded" if not hard else "nothing_to_reclaim",
        **_gate_fields(gate),
    }


def _decline_unload(detail: str = "interpolation in flight; not unloading") -> dict[str, Any]:
    return {"status": "busy", "detail": detail}


def _idle_unload_due() -> bool:
    """Whether the idle pass should act now. ``busy`` counts because
    ``last_used`` is stamped when a request ends, so mid-request it can be any
    age. A process that has served nothing (``last_used`` 0) has no context
    to give back either."""
    return (
        not _state.busy
        and _state.last_used > 0
        and time.monotonic() - _state.last_used > IDLE_TIMEOUT
    )


async def _idle_unload_tick() -> None:
    """One idle pass: drop an idle model, then exit if the process still
    holds VRAM. That is the CUDA context the first interpolation created, and
    only an exit returns it (the module docstring has the measurements).

    A busy server is skipped at once, not queued behind; past the lock the
    condition is checked again, since a request may have been admitted while
    the tick waited for it, and once more right before the exit. A model
    dropped by the ladder's soft ``/unload`` leaves the same context, so the
    pass exits for it too once the timeout has run.
    """
    if not _idle_unload_due():
        return
    if _state.model is None and not _exit_gate()["reclaimable"]:
        # Nothing loaded and nothing held (or NVML cannot tell): no reason to
        # take the lock every 30 s.
        return
    async with _state.gpu_lock:
        if not _idle_unload_due():
            return
        if _state.model is not None:
            logger.info("idle %.0fs — unloading", IDLE_TIMEOUT)
            await _run_on_gpu(_unload_model)
        gate = _exit_gate()
        if gate["reclaimable"] and not _state.busy:
            _exit_now(
                f"[IDLE EXIT] idle {IDLE_TIMEOUT:.0f}s with no model loaded, "
                f"{_describe_gate(gate)}",
            )


async def _idle_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            await _idle_unload_tick()
        except Exception:  # noqa: BLE001 — logged; the next tick retries
            # One failed pass (a CUDA error mid-unload, say) must not end the
            # loop: nothing else unloads an idle model.
            logger.exception("idle unload failed; retrying next tick")


if __name__ == "__main__":
    # Container-internal bind; compose publishes the port to localhost only,
    # same as every other sidecar.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104 - container-internal; compose publishes to localhost only  # noqa: S104
        port=PORT,
        log_level="warning",
    )

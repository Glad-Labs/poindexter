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
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from safetensors.torch import load_file

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rife")

PORT = int(os.getenv("RIFE_PORT", "9842"))
IDLE_TIMEOUT = float(os.getenv("RIFE_IDLE_TIMEOUT", "300"))
WEIGHTS = os.getenv("RIFE_WEIGHTS", "/app/flownet.safetensors")
# Below this reserved pool a hard unload would reclaim nothing, so the process
# stays up — the image-gen lesson (~24 consecutive no-op exits before its gate).
HARD_UNLOAD_MIN_RESERVED_MB = int(os.getenv("RIFE_HARD_UNLOAD_MIN_RESERVED_MB", "512"))
# RIFE's pyramid downsamples by 32; frames are padded up and cropped back.
ALIGN = 32
MAX_SOURCE_FRAMES = int(os.getenv("RIFE_MAX_SOURCE_FRAMES", "4000"))
# How far past the target the dense grid must reach before resampling; see
# _interpolate_sync. 2.0 = one extra bisection round for a halved timing error.
DENSE_MULTIPLE = float(os.getenv("RIFE_DENSE_MULTIPLE", "2.0"))

@asynccontextmanager
async def _lifespan(_app: FastAPI):
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
        self.busy: bool = False
        self.load_error: str = ""


_state = _State()


def _device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_model() -> bool:
    if _state.model is not None:
        return True
    try:
        import sys

        sys.path.insert(0, "/app")
        from interpolation_model import IFNet  # type: ignore[import-not-found]

        model = IFNet()
        model.load_state_dict(load_file(WEIGHTS))
        model.to(_device()).eval()
        _state.model = model
        _state.load_error = ""
        logger.info("RIFE loaded on %s", _device())
        return True
    except Exception as exc:  # noqa: BLE001 — reported through /health
        _state.load_error = f"{type(exc).__name__}: {exc}"
        logger.error("RIFE load failed: %s", _state.load_error)
        return False


def _unload_model() -> None:
    _state.model = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _reserved_mb() -> int:
    if not torch.cuda.is_available():
        return 0
    return int(torch.cuda.memory_reserved(0) // 1024 // 1024)


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
    t = torch.from_numpy(np.ascontiguousarray(frame)).to(dev).permute(2, 0, 1).float() / 255.0
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
    """Raise the clip to ``target_fps``; returns a small stats dict."""
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
    finally:
        enc.wait(timeout=1800)
    if enc.returncode != 0 or not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise RuntimeError(f"encode failed (ffmpeg rc={enc.returncode})")
    return {
        "source_fps": round(src_fps, 4), "target_fps": round(target_fps, 4),
        "dense_fps": round(dense_fps, 4), "source_frames": int(len(frames)),
        "model_calls": calls, "seconds": round(time.monotonic() - started, 2),
    }


class UnloadRequest(BaseModel):
    hard: bool = False


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model_loaded": _state.model is not None,
        "busy": _state.busy,
        "device": _device(),
        "last_used": _state.last_used,
        "vram_reserved_mb": _reserved_mb(),
        "load_error": _state.load_error,
    }


@app.post("/interpolate")
async def interpolate(
    file: UploadFile = File(...),
    target_fps: float = Form(30.0),
) -> FileResponse:
    if _state.busy:
        raise HTTPException(status_code=409, detail="another interpolation is in flight")
    if not _load_model():
        raise HTTPException(status_code=503, detail=f"model unavailable: {_state.load_error}")
    tmp = tempfile.mkdtemp(prefix="rife_")
    src = os.path.join(tmp, "in" + (Path(file.filename or "in.mp4").suffix or ".mp4"))
    dest = os.path.join(tmp, "out.mp4")
    with open(src, "wb") as fh:
        fh.write(await file.read())
    _state.busy = True
    try:
        stats = await asyncio.to_thread(_interpolate_sync, src, dest, float(target_fps))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — the caller falls back to ffmpeg
        logger.warning("interpolation failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    finally:
        _state.busy = False
        # Stamped on the way OUT so the idle timer measures time since the
        # work finished, not since it started.
        _state.last_used = time.monotonic()
    logger.info("interpolated %s", stats)
    return FileResponse(
        dest, media_type="video/mp4", filename="interpolated.mp4",
        headers={"X-Rife-Stats": json.dumps(stats)},
    )


@app.post("/unload")
async def unload(req: UnloadRequest | None = None) -> dict[str, Any]:
    """Manual VRAM release for the worker's reclaim ladder.

    Never unloads out from under a live interpolation: the ladder calls this
    whenever the render GPU looks short, and work in flight is part of that
    picture — obeying would destroy the clip the reclaim exists to make room
    for (the wan-server lesson, poindexter#962).
    """
    if _state.busy:
        return {"status": "busy", "detail": "interpolation in flight; not unloading"}
    hard = bool(req.hard) if req else False
    _unload_model()
    reserved = _reserved_mb()
    if hard and reserved >= HARD_UNLOAD_MIN_RESERVED_MB:
        logger.warning("[HARD UNLOAD] exiting to return the CUDA context (%d MB reserved)", reserved)
        os._exit(0)
    return {
        "status": "unloaded" if not hard else "nothing_to_reclaim",
        "vram_reserved_mb": reserved,
        "min_reserved_mb": HARD_UNLOAD_MIN_RESERVED_MB,
    }


async def _idle_loop() -> None:
    while True:
        await asyncio.sleep(30)
        if (
            _state.model is not None
            and not _state.busy
            and _state.last_used > 0
            and time.monotonic() - _state.last_used > IDLE_TIMEOUT
        ):
            logger.info("idle %.0fs — unloading", IDLE_TIMEOUT)
            _unload_model()


if __name__ == "__main__":
    # Container-internal bind; compose publishes the port to localhost only,
    # same as every other sidecar.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104 - container-internal; compose publishes to localhost only  # noqa: S104
        port=PORT,
        log_level="warning",
    )

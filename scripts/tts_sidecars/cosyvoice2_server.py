"""OpenAI-compatible /v1/audio/speech shim for CosyVoice2-0.5B (Apache-2.0).

`instruct` drives emotion/style. Uses a bundled 16 kHz reference clip as the
stock speaker (CosyVoice2's native zero-shot voice mechanism). Encodes to the
requested format with ffmpeg so the client's loudnorm pass applies.

Build/verify is hardware-gated (needs a GPU, a pinned upstream commit, and the
reference clip at scripts/tts_sidecars/assets/stock_speaker_16k.wav — see the
assets/README.md):
    docker compose --profile tts-hq up -d cosyvoice2
    curl -fsS http://localhost:8012/health
"""

from __future__ import annotations

import io
import os
import subprocess

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

app = FastAPI()
_engine = None

_MODEL_DIR = os.environ.get("COSYVOICE_MODEL_DIR", "/app/pretrained_models/CosyVoice2-0.5B")
_PROMPT_WAV = os.environ.get("COSYVOICE_PROMPT_WAV", "/app/assets/stock_speaker_16k.wav")


def _get_engine():
    global _engine
    if _engine is None:
        from cosyvoice.cli.cosyvoice import CosyVoice2  # heavy import
        _engine = CosyVoice2(_MODEL_DIR, load_jit=False, load_trt=False, fp16=False)
    return _engine


def _encode(samples, sample_rate: int, fmt: str) -> bytes:
    wav_buf = io.BytesIO()
    sf.write(wav_buf, samples, sample_rate, format="WAV")
    if fmt == "wav":
        return wav_buf.getvalue()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", "pipe:0", "-f", fmt, "pipe:1"],
        input=wav_buf.getvalue(), capture_output=True,
    )
    if proc.returncode != 0:
        raise HTTPException(500, f"ffmpeg encode failed: {proc.stderr[:300]!r}")
    return proc.stdout


class SpeechRequest(BaseModel):
    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    instruct: str = ""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(400, "empty input")
    engine = _get_engine()
    instruct = req.instruct or "Speak in a clear, natural, neutral narration voice."
    sample_rate = int(engine.sample_rate)
    # Pass the full text once and let CosyVoice2 do its OWN sentence segmentation:
    # inference_instruct2 already splits internally and yields one segment per
    # sentence, so external sentence-chunking (re-priming the instruct on every
    # chunk) double-segments and bleeds instruction-like artifacts into the audio
    # at chunk seams. We just concatenate the engine's own yields, no gaps.
    # inference_instruct2(tts_text, instruct_text, prompt_wav_path, ...): this
    # pinned commit re-loads the prompt clip from a PATH internally (frontend
    # _extract_speech_feat -> load_wav), so pass the path, not a loaded tensor.
    # Yields dicts with a 'tts_speech' torch tensor at engine.sample_rate.
    segments: list[np.ndarray] = []
    for out in engine.inference_instruct2(req.input, instruct, _PROMPT_WAV, stream=False):
        segments.append(out["tts_speech"].squeeze(0).detach().cpu().numpy().astype(np.float32))
    samples = np.concatenate(segments) if segments else np.zeros(1, dtype=np.float32)
    audio = _encode(samples, sample_rate, req.response_format.lower())
    media = {"mp3": "audio/mpeg", "wav": "audio/wav",
             "opus": "audio/opus", "aac": "audio/aac"}.get(
        req.response_format.lower(), "application/octet-stream")
    return Response(content=audio, media_type=media)

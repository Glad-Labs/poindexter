"""ComfyUIProvider — image-to-video hero clips via a ComfyUI sidecar.

Second concrete :class:`VideoProvider <plugins.video_provider.VideoProvider>`
implementation, alongside :class:`Wan21Provider`. Renders Wan 2.2 **14B**
i2v through a headless ComfyUI server instead of the bespoke diffusers
wan-server, because the 2026-08-15 spike showed the quality gap lives in
the model + sampler config, not the prompts:

- the 5B sidecar (50 steps, diffusers repo-default flow-shift) inverted the
  palette and morphed away from the init still within 2.5s — the "hero slop";
- the same 5B through ComfyUI's tuned config (shift 8, uni_pc, 20 steps)
  fixed the palette but still hallucinated content (grew a human hand into an
  "empty unpopulated scene");
- 14B fp8 held composition/palette/style at every geometry tried, and with
  the lightx2v 4-step distill LoRAs renders in ~123s — *faster* than the 5B
  sidecar renders slop (~147s). That 4-step configuration is the default
  here; the full 20-step regime is two settings away (see below).

**License boundary (deliberate):** ComfyUI is GPL-3. It runs as an HTTP
sidecar and is never vendored or imported — this module speaks its REST API
only, which keeps the repo's own licensing untouched (the same conclusion
the 2026-06-19 video-quality design recorded when it shelved ComfyUI as an
optional render-layer consolidation).

**Transport is API-only, no shared mounts.** The init still is uploaded via
``POST /upload/image``; the finished MP4 is fetched back via ``GET /view``
and written to the caller's ``output_path``. The ComfyUI container therefore
needs no bind-mount into the worker's filesystem (unlike wan-server's
shared ``generated-videos`` dir).

Selection: ``app_settings.video_generative_provider`` — ``"wan21"``
(default, the deployed 5B sidecar) or ``"comfyui"``. The shot-list renderer
reads it per clip, so flipping providers is a settings change, no deploy.

Config (all in app_settings; per-call ``config`` overrides win where noted):

- ``video_comfyui_server_url`` (default ``http://comfyui:8188`` — compose
  service DNS; the sidecar's host publish is loopback-only, so the
  host-gateway route the other sidecars use cannot reach it)
- ``video_comfyui_steps`` / ``video_comfyui_cfg`` — sampler regime. Defaults
  ``4`` / ``1.0`` = the lightx2v 4-step distill configuration (requires the
  LoRAs, below). The full-quality regime is ``20`` / ``3.5`` with
  ``video_comfyui_use_lightning_lora=false``.
- ``video_comfyui_shift`` (default 5.0 — ModelSamplingSD3 for the 14B i2v
  pair, per the official ComfyUI template; the 5B TI2V template uses 8.0)
- ``video_comfyui_use_lightning_lora`` (default true) — wires the lightx2v
  4-step LoRAs onto both experts.
- ``video_comfyui_length_frames`` / ``video_comfyui_fps`` (defaults 81 / 16
  — the 14B-native ~5s profile. The caller's ``fps`` config key is
  deliberately ignored: the model has one native framerate, and the
  compositor's normalize pass conforms + loops clips to the shot length
  regardless.)
- ``video_comfyui_negative_prompt`` — defaults to the canonical Wan negative
  from the official templates (Chinese; the model was trained with it).
- ``video_comfyui_high_model`` / ``video_comfyui_low_model`` /
  ``video_comfyui_text_encoder`` / ``video_comfyui_vae`` /
  ``video_comfyui_lora_high`` / ``video_comfyui_lora_low`` — model filenames
  as ComfyUI sees them in its models dirs; weight swaps are settings-only.
- ``video_comfyui_timeout_s`` (default 900) — end-to-end render budget.
- ``video_comfyui_ready_wait_s`` (default 90) — how long to wait for
  ``/system_stats`` to answer before failing, mirroring the wan-server
  ready-wait (#3102: a cold-booting sidecar must delay the render, not
  silently degrade it).
- ``video_comfyui_workflow_override_json`` (default empty) — full workflow
  swap without code: an API-format graph JSON whose placeholder leaf values
  (``__PROMPT__``, ``__NEGATIVE__``, ``__INIT_IMAGE__``, ``__WIDTH__``,
  ``__HEIGHT__``, ``__LENGTH__``, ``__FPS__``, ``__SEED__``, ``__STEPS__``,
  ``__CFG__``, ``__SHIFT__``, ``__FILENAME_PREFIX__``) are substituted
  (typed) before submission. Empty → the code-built 14B two-expert graph.

Speech-to-video (talking heads, 2026-09-14 spike): when the caller passes
``config["audio_path"]`` the same provider renders **Wan 2.2 S2V 14B** instead.
The init still becomes the presenter reference, the speech file is uploaded
through the same ``/upload/image`` endpoint (ComfyUI's input store is
type-agnostic) and encoded by wav2vec2, and the clip runs in chunks of
``video_comfyui_s2v_length_frames`` (77 @ 16 fps = 4.8 s) chained with
``WanSoundImageToVideoExtend`` up to ``video_comfyui_s2v_max_chunks`` — the
chunk count follows the audio (``config["audio_duration_s"]`` or an ffprobe).
Settings: ``video_comfyui_s2v_{model,audio_encoder,steps,cfg,shift,sampler,
length_frames,max_chunks,timeout_per_chunk_s,workflow_override_json}``; the
override contract adds ``__AUDIO__`` (server-side name of the uploaded speech).
Measured on an idle 5090: ~420 s and 31.9 GB peak per chunk — the render owns
the card, so admit it through the GPU scheduler, never beside Ollama.

Kind: ``"generate"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
from typing import Any

import httpx

from poindexter.plugins.video_provider import VideoResult
from poindexter.services.podcast_sting_mixer import probe_duration_s

logger = logging.getLogger(__name__)


# Compose-network service DNS. The sidecar binds its host publish to
# 127.0.0.1 only, which host.docker.internal (the host-gateway IP) cannot
# reach — a worker-side probe caught exactly that before first flip.
_DEFAULT_SERVER_URL = "http://comfyui:8188"

# 14B-native output profile (~5s @ 16fps). The lightx2v 4-step regime is the
# default because the spike measured it at ~123s/clip on a 5090 — inside the
# budget the 5B sidecar was already spending — while the 20-step regime takes
# ~8 min/clip. Operators flip two settings for the quality tier.
_DEFAULT_STEPS = 4
_DEFAULT_CFG = 1.0
_DEFAULT_SHIFT = 5.0
_DEFAULT_LENGTH = 81
_DEFAULT_FPS = 16
_DEFAULT_TIMEOUT_S = 900.0
_DEFAULT_READY_WAIT_S = 90.0
_POLL_INTERVAL_S = 5.0
# How often a still-executing prompt reports progress upstream (see _poll).
_HEARTBEAT_EVERY_S = 30.0

_DEFAULT_HIGH_MODEL = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
_DEFAULT_LOW_MODEL = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
_DEFAULT_TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
_DEFAULT_VAE = "wan_2.1_vae.safetensors"
_DEFAULT_LORA_HIGH = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
_DEFAULT_LORA_LOW = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"

# Canonical Wan negative prompt from the official ComfyUI templates. The
# model is trained against this Chinese negative; an English negative is
# measurably weaker. Operators can override via
# ``video_comfyui_negative_prompt``.
_DEFAULT_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，"
    "低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，"
    "毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)

# Same generous cap as Wan21Provider (poindexter#996) — the reason lands in
# a hero_render_fallback finding and the diagnostic tail of a CUDA OOM is
# the half that matters.
_MAX_REASON_CHARS = 400

# Speech-to-video (Wan 2.2 S2V 14B fp8, same repackaged repo). 20 / 6.0 / 8.0 /
# uni_pc is the regime the 2026-09-14 spike validated (identity + lip shapes
# held for the whole chunk); 77 frames is one S2V chunk at the model's 16 fps.
_DEFAULT_S2V_MODEL = "wan2.2_s2v_14B_fp8_scaled.safetensors"
_DEFAULT_S2V_AUDIO_ENCODER = "wav2vec2_large_english_fp16.safetensors"
_DEFAULT_S2V_STEPS = 20
_DEFAULT_S2V_CFG = 6.0
_DEFAULT_S2V_SHIFT = 8.0
_DEFAULT_S2V_SAMPLER = "uni_pc"
_DEFAULT_S2V_LENGTH = 77
_DEFAULT_S2V_MAX_CHUNKS = 6
_DEFAULT_S2V_TIMEOUT_PER_CHUNK_S = 900.0
_S2V_FILENAME_PREFIX = "poindexter_talking_head"
_AUDIO_CONTENT_TYPES = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
}

# Placeholder tokens the workflow-override substitution recognises, mapped to
# whether the substituted value is numeric (replaces the string leaf with an
# int/float) or textual (stays a string).
_OVERRIDE_NUMERIC = {
    "__WIDTH__", "__HEIGHT__", "__LENGTH__", "__FPS__", "__SEED__",
    "__STEPS__", "__CFG__", "__SHIFT__",
}
_OVERRIDE_TEXTUAL = {
    "__PROMPT__", "__NEGATIVE__", "__INIT_IMAGE__", "__FILENAME_PREFIX__",
    "__AUDIO__",  # speech path only: server-side name of the uploaded audio
}


def _read_bytes(path: str) -> bytes:
    """Sync helper for ``asyncio.to_thread`` (ASYNC230)."""
    with open(path, "rb") as f:
        return f.read()


def _write_bytes(path: str, content: bytes) -> None:
    """Sync helper for ``asyncio.to_thread`` (ASYNC230)."""
    with open(path, "wb") as f:
        f.write(content)


def _sc_get(site_config: Any, key: str, default: Any) -> Any:
    """Read a site_config key without letting a settings failure decide a
    render's fate (same posture as the renderer's own reads)."""
    if site_config is None:
        return default
    try:
        val = site_config.get(key, default)
    except Exception:  # noqa: BLE001  # silent-ok: config read must not
        # break a render; the code default is the documented fallback.
        return default
    return default if val in (None, "") else val


def _sc_num(site_config: Any, key: str, default: float) -> float:
    try:
        return float(_sc_get(site_config, key, default))
    except (TypeError, ValueError):
        return default


def _sc_bool(site_config: Any, key: str, default: bool) -> bool:
    val = _sc_get(site_config, key, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _resolve_server_url(config: dict[str, Any], site_config: Any) -> str:
    """Server URL resolution — per-call override, then app_settings, then
    module default (mirrors ``wan2_1._resolve_server_url``)."""
    direct = str(config.get("server_url", "") or "")
    if direct:
        return direct
    return str(_sc_get(site_config, "video_comfyui_server_url", _DEFAULT_SERVER_URL))


def build_graph(
    *,
    prompt: str,
    negative: str,
    init_image_name: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
    shift: float,
    use_lora: bool,
    high_model: str = _DEFAULT_HIGH_MODEL,
    low_model: str = _DEFAULT_LOW_MODEL,
    text_encoder: str = _DEFAULT_TEXT_ENCODER,
    vae: str = _DEFAULT_VAE,
    lora_high: str = _DEFAULT_LORA_HIGH,
    lora_low: str = _DEFAULT_LORA_LOW,
    filename_prefix: str = "poindexter_hero",
) -> dict[str, Any]:
    """Build the Wan 2.2 14B two-expert i2v graph in ComfyUI API format.

    Mirrors the official ``video_wan2_2_14B_i2v`` template: high-noise expert
    denoises steps ``[0, steps/2)``, low-noise expert ``[steps/2, end)``,
    both behind ``ModelSamplingSD3(shift)``. With ``use_lora`` the lightx2v
    distill LoRAs wrap each expert (the template's shipped fast variant).
    Module-level and pure so tests can assert the wiring without HTTP.
    """
    half = max(1, steps // 2)
    graph: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": high_model, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {
            "unet_name": low_model, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": text_encoder, "type": "wan", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
    }
    high_src, low_src = ["1", 0], ["2", 0]
    if use_lora:
        graph["20"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": lora_high, "strength_model": 1.0}}
        graph["21"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["2", 0], "lora_name": lora_low, "strength_model": 1.0}}
        high_src, low_src = ["20", 0], ["21", 0]
    graph.update({
        "5": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": high_src, "shift": shift}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": low_src, "shift": shift}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["3", 0], "text": prompt}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["3", 0], "text": negative}},
        "9": {"class_type": "LoadImage", "inputs": {"image": init_image_name}},
        "10": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["7", 0], "negative": ["8", 0], "vae": ["4", 0],
            "start_image": ["9", 0],
            "width": width, "height": height,
            "length": length, "batch_size": 1}},
        "11": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["5", 0], "add_noise": "enable", "noise_seed": seed,
            "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["10", 0], "negative": ["10", 1],
            "latent_image": ["10", 2],
            "start_at_step": 0, "end_at_step": half,
            "return_with_leftover_noise": "enable"}},
        "12": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["6", 0], "add_noise": "disable", "noise_seed": 0,
            "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["10", 0], "negative": ["10", 1],
            "latent_image": ["11", 0],
            "start_at_step": half, "end_at_step": 10000,
            "return_with_leftover_noise": "disable"}},
        "13": {"class_type": "VAEDecode", "inputs": {
            "samples": ["12", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {
            "images": ["13", 0], "fps": fps}},
        "15": {"class_type": "SaveVideo", "inputs": {
            "video": ["14", 0], "filename_prefix": filename_prefix,
            "format": "mp4", "codec": "h264"}},
    })
    return graph


def substitute_override(template: Any, values: dict[str, Any]) -> Any:
    """Walk a parsed workflow-override structure replacing placeholder leaf
    strings with typed values (``__WIDTH__`` → int, ``__PROMPT__`` → str).

    Unknown placeholders are left verbatim — the graph will fail loudly at
    ComfyUI validation rather than silently rendering with a literal token.
    """
    if isinstance(template, dict):
        return {k: substitute_override(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [substitute_override(v, values) for v in template]
    if isinstance(template, str) and template in values:
        return values[template]
    return template


def s2v_chunks_for(audio_s: float, length: int, fps: int, max_chunks: int) -> int:
    """How many S2V chunks cover ``audio_s`` seconds of speech.

    One chunk is ``length`` frames at ``fps`` (77 @ 16 = 4.8125 s). Always at
    least one, never more than ``max_chunks`` — the caller reads the cap back
    as ``audio_truncated`` in the result metadata rather than rendering a
    clip that silently outlives its render budget.
    """
    if length <= 0 or fps <= 0 or audio_s <= 0:
        return 1
    per_chunk_s = length / fps
    wanted = math.ceil(audio_s / per_chunk_s - 1e-9)
    return max(1, min(wanted, max(1, int(max_chunks))))


def _audio_content_type(path: str) -> str:
    return _AUDIO_CONTENT_TYPES.get(
        os.path.splitext(path)[1].lower(), "application/octet-stream",
    )


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


async def _probe_audio_seconds(path: str) -> float | None:
    """Duration of the speech file via the shared ffprobe helper (``None``
    when ffprobe cannot read it — the caller then needs ``audio_duration_s``)."""
    return await probe_duration_s(path)


def build_s2v_graph(
    *,
    prompt: str,
    negative: str,
    ref_image_name: str,
    audio_name: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
    shift: float,
    sampler: str,
    chunks: int,
    model: str = _DEFAULT_S2V_MODEL,
    audio_encoder: str = _DEFAULT_S2V_AUDIO_ENCODER,
    text_encoder: str = _DEFAULT_TEXT_ENCODER,
    vae: str = _DEFAULT_VAE,
    filename_prefix: str = _S2V_FILENAME_PREFIX,
) -> dict[str, Any]:
    """Build the Wan 2.2 S2V talking-head graph in ComfyUI API format.

    Chunk 1 is ``WanSoundImageToVideo`` (reference still + audio embedding);
    every further chunk is ``WanSoundImageToVideoExtend`` fed the WHOLE video
    so far — every previous chunk's sampled latent, concatenated along time
    with ``LatentConcat(dim="t")`` — as its ``video_latent``. Each chunk is
    sampled and decoded on its own and the frames are concatenated with
    ``ImageBatch`` before ``CreateVideo`` muxes the full speech track back
    in. Module-level and pure so the wiring is testable without HTTP.

    **Why the whole video, not just the previous chunk.** ComfyUI's Extend
    node derives the chunk's position in the speech from the latent it is
    handed: ``frame_offset = video_latent.shape[-3] * 4`` (comfy_extras/
    nodes_wan.py), and ``wan_sound_to_video`` slices the audio embedding at
    that offset. It only uses the latent's last 19 frames as motion
    reference, so a longer latent costs nothing. Feeding it the previous
    chunk alone made every chunk after the second start its audio window at
    4.8 s again — the closing talking head of render c1c43a8b (17.6 s, four
    chunks) mouthed the words from 4.8-9.6 s twice over (measured 2026-09-17
    per chunk: chunks 1-2 aligned, chunks 3-4 off by 1-3 s and incoherent).
    Two-chunk clips were never affected, which is why the 8-10 s opening
    shots always looked right.
    """
    chunks = max(1, int(chunks))
    graph: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": model, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": text_encoder, "type": "wan", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "AudioEncoderLoader", "inputs": {
            "audio_encoder_name": audio_encoder}},
        "6": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
        "7": {"class_type": "AudioEncoderEncode", "inputs": {
            "audio_encoder": ["5", 0], "audio": ["6", 0]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": ref_image_name}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["3", 0], "text": prompt}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["3", 0], "text": negative}},
        "11": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": ["1", 0], "shift": shift}},
        "12": {"class_type": "WanSoundImageToVideo", "inputs": {
            "positive": ["9", 0], "negative": ["10", 0], "vae": ["4", 0],
            "width": width, "height": height, "length": length,
            "batch_size": 1,
            "audio_encoder_output": ["7", 0], "ref_image": ["8", 0]}},
        "13": {"class_type": "KSampler", "inputs": {
            "model": ["11", 0], "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": sampler, "scheduler": "simple",
            "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["12", 2], "denoise": 1.0}},
        "14": {"class_type": "VAEDecode", "inputs": {
            "samples": ["13", 0], "vae": ["4", 0]}},
    }
    images_src: list[Any] = ["14", 0]
    # The video so far, as ONE latent. Extend reads its own audio offset off
    # this latent's length, so it must grow with every chunk (see docstring).
    video_so_far: list[Any] = ["13", 0]
    nid = 20
    for k in range(1, chunks):
        ext, samp, dec, batch, cat = (
            str(nid), str(nid + 1), str(nid + 2), str(nid + 3), str(nid + 4),
        )
        nid += 5
        graph[ext] = {"class_type": "WanSoundImageToVideoExtend", "inputs": {
            "positive": ["9", 0], "negative": ["10", 0], "vae": ["4", 0],
            "length": length, "video_latent": video_so_far,
            "audio_encoder_output": ["7", 0], "ref_image": ["8", 0]}}
        graph[samp] = {"class_type": "KSampler", "inputs": {
            "model": ["11", 0], "seed": seed + k, "steps": steps, "cfg": cfg,
            "sampler_name": sampler, "scheduler": "simple",
            "positive": [ext, 0], "negative": [ext, 1],
            "latent_image": [ext, 2], "denoise": 1.0}}
        graph[dec] = {"class_type": "VAEDecode", "inputs": {
            "samples": [samp, 0], "vae": ["4", 0]}}
        graph[batch] = {"class_type": "ImageBatch", "inputs": {
            "image1": images_src, "image2": [dec, 0]}}
        graph[cat] = {"class_type": "LatentConcat", "inputs": {
            "samples1": video_so_far, "samples2": [samp, 0], "dim": "t"}}
        images_src, video_so_far = [batch, 0], [cat, 0]
    graph["90"] = {"class_type": "CreateVideo", "inputs": {
        "images": images_src, "fps": float(fps), "audio": ["6", 0]}}
    graph["91"] = {"class_type": "SaveVideo", "inputs": {
        "video": ["90", 0], "filename_prefix": filename_prefix,
        "format": "mp4", "codec": "h264"}}
    return graph


class ComfyUIProvider:
    """Wan 2.2 14B image-to-video via a headless ComfyUI sidecar.

    ``last_error`` mirrors the ``Wan21Provider`` contract (poindexter#996):
    the empty-list return is the VideoProvider protocol, and ``last_error``
    carries WHY, so the renderer's ``hero_render_fallback`` finding is
    diagnosable after the fact.
    """

    name = "comfyui-wan22-i2v"
    kind = "generate"

    def __init__(self) -> None:
        self.last_error: str = ""

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[VideoResult]:
        self.last_error = ""
        prompt = (query_or_prompt or "").strip()
        if not prompt:
            self.last_error = "empty prompt — nothing was sent to ComfyUI"
            return []

        site_config = config.get("_site_config")
        if site_config is None:
            logger.warning(
                "[ComfyUIProvider] config missing '_site_config' key; "
                "dispatcher hasn't seeded it (GH#95)",
            )

        image_path = str(config.get("image_path", "") or "")
        if not image_path or not os.path.exists(image_path):
            # i2v-only on purpose: every hero shot animates its stylized
            # still (spec §3.3), and a t2v fallback here would silently
            # abandon the brand look the still fixes.
            self.last_error = (
                f"init image missing at {image_path!r} — ComfyUIProvider is "
                "i2v-only (hero shots animate their stylized still)"
            )
            return []

        output_path = str(config.get("output_path", "") or "")
        if not output_path:
            self.last_error = "config missing output_path"
            return []

        server_url = _resolve_server_url(config, site_config).rstrip("/")
        audio_path = str(config.get("audio_path", "") or "")
        if audio_path:
            # Speech-driven (talking head): same sidecar, S2V graph, clip
            # length follows the audio. See _fetch_speech.
            return await self._fetch_speech(
                prompt=prompt, config=config, site_config=site_config,
                image_path=image_path, output_path=output_path,
                server_url=server_url, audio_path=audio_path,
            )
        width = int(config.get("width") or 0) or 832
        height = int(config.get("height") or 0) or 480
        # Deliberately NOT honouring config["fps"]: the 14B pair has one
        # native framerate and the compositor conforms + loops clips to the
        # shot length regardless (see module docstring).
        length = int(_sc_num(site_config, "video_comfyui_length_frames", _DEFAULT_LENGTH))
        fps = int(_sc_num(site_config, "video_comfyui_fps", _DEFAULT_FPS))
        use_lora = _sc_bool(site_config, "video_comfyui_use_lightning_lora", True)
        steps = int(_sc_num(site_config, "video_comfyui_steps", _DEFAULT_STEPS))
        cfg = _sc_num(site_config, "video_comfyui_cfg", _DEFAULT_CFG)
        shift = _sc_num(site_config, "video_comfyui_shift", _DEFAULT_SHIFT)
        timeout_s = _sc_num(site_config, "video_comfyui_timeout_s", _DEFAULT_TIMEOUT_S)
        ready_wait_s = _sc_num(
            site_config, "video_comfyui_ready_wait_s", _DEFAULT_READY_WAIT_S,
        )
        negative = str(config.get("negative_prompt", "") or "") or str(
            _sc_get(site_config, "video_comfyui_negative_prompt", _DEFAULT_NEGATIVE),
        )
        seed = random.randrange(2**62)

        client_timeout = httpx.Timeout(30.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                ok, reason = await self._wait_ready(
                    client, server_url, ready_wait_s,
                )
                if not ok:
                    self.last_error = reason
                    return []

                init_name, reason = await self._upload_init(
                    client, server_url, image_path,
                )
                if not init_name:
                    self.last_error = reason
                    return []

                graph, reason = self._resolve_graph(
                    site_config=site_config,
                    prompt=prompt, negative=negative,
                    init_image_name=init_name,
                    width=width, height=height, length=length, fps=fps,
                    seed=seed, steps=steps, cfg=cfg, shift=shift,
                    use_lora=use_lora,
                )
                if graph is None:
                    self.last_error = reason
                    return []

                prompt_id, reason = await self._submit(client, server_url, graph)
                if not prompt_id:
                    self.last_error = reason
                    return []

                filename, reason = await self._poll(
                    client, server_url, prompt_id, timeout_s,
                    heartbeat_cb=config.get("_heartbeat_cb"),
                )
                if not filename:
                    self.last_error = reason
                    return []

                ok, reason = await self._download(
                    client, server_url, filename, output_path,
                )
                if not ok:
                    self.last_error = reason
                    return []
        except Exception as e:
            # Transport-level surprise outside the per-step handlers (DNS,
            # TLS, protocol error mid-stream). Same posture as wan2_1: the
            # reason travels; the renderer's fallback ladder takes over.
            logger.error(
                "[ComfyUIProvider] render failed against %s: %s: %s. "
                "Stand up the ComfyUI sidecar (docker compose --profile "
                "comfyui up -d) or set video_comfyui_server_url. The "
                "shot-list renderer falls back for this hero "
                "(hero_render_fallback finding).",
                server_url, type(e).__name__, e,
            )
            self.last_error = (
                f"comfyui unreachable/failed at {server_url}: "
                f"{type(e).__name__}: {e}"
            )[:_MAX_REASON_CHARS]
            return []

        file_size = 0
        try:
            file_size = os.path.getsize(output_path)
        except OSError:  # silent-ok: size is cosmetic metadata — the file's
            # existence was just verified by the download step.
            pass

        duration = length / fps if fps else 0.0
        return [
            VideoResult(
                file_url=f"file://{output_path}",
                file_path=output_path,
                duration_s=int(duration),
                width=width,
                height=height,
                fps=fps,
                codec="h264",
                format="mp4",
                source=self.name,
                prompt=prompt,
                metadata={
                    "local_path": output_path,
                    "file_size_bytes": file_size,
                    "negative_prompt": negative,
                    "steps": steps,
                    "guidance_scale": cfg,
                    "shift": shift,
                    "seed": seed,
                    "lightning_lora": use_lora,
                    "model": "wan2.2-i2v-14b-fp8",
                    "model_repo": "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                    "license": "apache-2.0",
                    "i2v": True,
                    "server_url": server_url,
                },
            ),
        ]

    # ------------------------------------------------------------------
    # Steps — each returns (value, reason) so fetch() stays a flat ladder
    # ------------------------------------------------------------------

    async def _wait_ready(
        self, client: httpx.AsyncClient, server_url: str, ready_wait_s: float,
    ) -> tuple[bool, str]:
        """Poll ``/system_stats`` until the sidecar answers (#3102 shape:
        a cold-booting sidecar must delay the render, not degrade it)."""
        deadline = time.monotonic() + ready_wait_s
        last: str = "no attempt made"
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{server_url}/system_stats")
                if resp.status_code == 200:
                    return True, ""
                last = f"HTTP {resp.status_code}"
            except Exception as e:  # noqa: BLE001 — retried until deadline
                last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(2.0)
        return False, (
            f"comfyui not ready at {server_url} after {ready_wait_s:.0f}s "
            f"(last: {last}) — is the sidecar up? "
            "(docker compose --profile comfyui up -d)"
        )[:_MAX_REASON_CHARS]

    async def _upload_init(
        self, client: httpx.AsyncClient, server_url: str, image_path: str,
    ) -> tuple[str, str]:
        """Upload the init still; returns the server-side filename."""
        return await self._upload_file(
            client, server_url, image_path,
            content_type="image/png", label="init-image",
        )

    async def _upload_file(
        self,
        client: httpx.AsyncClient,
        server_url: str,
        path: str,
        *,
        content_type: str,
        label: str,
    ) -> tuple[str, str]:
        """Put a local file into the sidecar's input store via
        ``/upload/image`` — ComfyUI's one upload endpoint, type-agnostic (the
        stock frontend sends LoadAudio files through it too). Returns the
        server-side filename, or ``("", reason)``."""
        raw = await asyncio.to_thread(_read_bytes, path)
        basename = os.path.basename(path) or f"{label}.bin"
        resp = await client.post(
            f"{server_url}/upload/image",
            files={"image": (basename, raw, content_type)},
            data={"overwrite": "true"},
        )
        if resp.status_code != 200:
            return "", (
                f"{label} upload failed: HTTP {resp.status_code}: "
                f"{(resp.text or '')[:150]}"
            )
        try:
            name = str(resp.json().get("name", "") or "")
        except Exception:  # noqa: BLE001
            name = ""
        if not name:
            return "", f"{label} upload returned no filename"
        return name, ""

    def _resolve_graph(
        self, *, site_config: Any, **values: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        """Code-built graph, or the operator's override template with
        placeholders substituted."""
        override = str(
            _sc_get(site_config, "video_comfyui_workflow_override_json", ""),
        ).strip()
        if not override:
            graph = build_graph(
                high_model=str(_sc_get(
                    site_config, "video_comfyui_high_model", _DEFAULT_HIGH_MODEL)),
                low_model=str(_sc_get(
                    site_config, "video_comfyui_low_model", _DEFAULT_LOW_MODEL)),
                text_encoder=str(_sc_get(
                    site_config, "video_comfyui_text_encoder", _DEFAULT_TEXT_ENCODER)),
                vae=str(_sc_get(site_config, "video_comfyui_vae", _DEFAULT_VAE)),
                lora_high=str(_sc_get(
                    site_config, "video_comfyui_lora_high", _DEFAULT_LORA_HIGH)),
                lora_low=str(_sc_get(
                    site_config, "video_comfyui_lora_low", _DEFAULT_LORA_LOW)),
                **values,
            )
            return graph, ""
        try:
            template = json.loads(override)
        except ValueError as e:
            return None, (
                "video_comfyui_workflow_override_json is set but is not valid "
                f"JSON ({e}) — fix or clear the setting"
            )
        sub = {
            "__PROMPT__": values["prompt"],
            "__NEGATIVE__": values["negative"],
            "__INIT_IMAGE__": values["init_image_name"],
            "__FILENAME_PREFIX__": "poindexter_hero",
            "__WIDTH__": int(values["width"]),
            "__HEIGHT__": int(values["height"]),
            "__LENGTH__": int(values["length"]),
            "__FPS__": int(values["fps"]),
            "__SEED__": int(values["seed"]),
            "__STEPS__": int(values["steps"]),
            "__CFG__": float(values["cfg"]),
            "__SHIFT__": float(values["shift"]),
        }
        graph = substitute_override(template, sub)
        if not isinstance(graph, dict):
            return None, (
                "video_comfyui_workflow_override_json must be a JSON object "
                "(ComfyUI API-format graph)"
            )
        return graph, ""

    def _resolve_s2v_graph(
        self, *, site_config: Any, **values: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        """Code-built S2V graph, or the operator's S2V override template with
        placeholders (incl. ``__AUDIO__``) substituted. An override renders
        whatever the template wires — chunking is the template's business."""
        override = str(
            _sc_get(site_config, "video_comfyui_s2v_workflow_override_json", ""),
        ).strip()
        if not override:
            graph = build_s2v_graph(
                model=str(_sc_get(
                    site_config, "video_comfyui_s2v_model", _DEFAULT_S2V_MODEL)),
                audio_encoder=str(_sc_get(
                    site_config, "video_comfyui_s2v_audio_encoder",
                    _DEFAULT_S2V_AUDIO_ENCODER)),
                text_encoder=str(_sc_get(
                    site_config, "video_comfyui_text_encoder", _DEFAULT_TEXT_ENCODER)),
                vae=str(_sc_get(site_config, "video_comfyui_vae", _DEFAULT_VAE)),
                **values,
            )
            return graph, ""
        try:
            template = json.loads(override)
        except ValueError as e:
            return None, (
                "video_comfyui_s2v_workflow_override_json is set but is not "
                f"valid JSON ({e}) — fix or clear the setting"
            )
        sub = {
            "__PROMPT__": values["prompt"],
            "__NEGATIVE__": values["negative"],
            "__INIT_IMAGE__": values["ref_image_name"],
            "__AUDIO__": values["audio_name"],
            "__FILENAME_PREFIX__": _S2V_FILENAME_PREFIX,
            "__WIDTH__": int(values["width"]),
            "__HEIGHT__": int(values["height"]),
            "__LENGTH__": int(values["length"]),
            "__FPS__": int(values["fps"]),
            "__SEED__": int(values["seed"]),
            "__STEPS__": int(values["steps"]),
            "__CFG__": float(values["cfg"]),
            "__SHIFT__": float(values["shift"]),
        }
        graph = substitute_override(template, sub)
        if not isinstance(graph, dict):
            return None, (
                "video_comfyui_s2v_workflow_override_json must be a JSON "
                "object (ComfyUI API-format graph)"
            )
        return graph, ""

    async def _fetch_speech(
        self,
        *,
        prompt: str,
        config: dict[str, Any],
        site_config: Any,
        image_path: str,
        output_path: str,
        server_url: str,
        audio_path: str,
    ) -> list[VideoResult]:
        """Speech-driven render (Wan 2.2 S2V): the init still is the
        presenter reference, ``audio_path`` drives lips and motion, and the
        clip length follows the audio in whole chunks."""
        if not os.path.exists(audio_path):
            self.last_error = (
                f"speech audio missing at {audio_path!r} — config['audio_path'] "
                "must point at a rendered narration file"
            )
            return []
        width = int(config.get("width") or 0) or 832
        height = int(config.get("height") or 0) or 480
        fps = int(_sc_num(site_config, "video_comfyui_fps", _DEFAULT_FPS))
        length = int(_sc_num(
            site_config, "video_comfyui_s2v_length_frames", _DEFAULT_S2V_LENGTH))
        max_chunks = int(_sc_num(
            site_config, "video_comfyui_s2v_max_chunks", _DEFAULT_S2V_MAX_CHUNKS))
        steps = int(_sc_num(site_config, "video_comfyui_s2v_steps", _DEFAULT_S2V_STEPS))
        cfg = _sc_num(site_config, "video_comfyui_s2v_cfg", _DEFAULT_S2V_CFG)
        shift = _sc_num(site_config, "video_comfyui_s2v_shift", _DEFAULT_S2V_SHIFT)
        sampler = str(_sc_get(
            site_config, "video_comfyui_s2v_sampler", _DEFAULT_S2V_SAMPLER))
        per_chunk_s = _sc_num(
            site_config, "video_comfyui_s2v_timeout_per_chunk_s",
            _DEFAULT_S2V_TIMEOUT_PER_CHUNK_S)
        ready_wait_s = _sc_num(
            site_config, "video_comfyui_ready_wait_s", _DEFAULT_READY_WAIT_S)
        negative = str(config.get("negative_prompt", "") or "") or str(
            _sc_get(site_config, "video_comfyui_negative_prompt", _DEFAULT_NEGATIVE),
        )

        audio_s = _as_float(config.get("audio_duration_s"))
        if audio_s is None:
            audio_s = await _probe_audio_seconds(audio_path)
        if not audio_s or audio_s <= 0:
            self.last_error = (
                f"could not determine the speech duration of {audio_path!r} "
                "(ffprobe failed and config['audio_duration_s'] was not given)"
            )
            return []
        chunks = s2v_chunks_for(audio_s, length, fps, max_chunks)
        covered_s = chunks * length / fps if fps else 0.0
        truncated = audio_s > covered_s + 1e-6
        if truncated:
            logger.warning(
                "[ComfyUIProvider] speech is %.1fs but video_comfyui_s2v_max_chunks"
                "=%d covers only %.1fs — the clip stops early; raise the cap or "
                "split the narration.",
                audio_s, max_chunks, covered_s,
            )
        seed = random.randrange(2**62)
        timeout_s = per_chunk_s * chunks

        client_timeout = httpx.Timeout(30.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                ok, reason = await self._wait_ready(client, server_url, ready_wait_s)
                if not ok:
                    self.last_error = reason
                    return []
                ref_name, reason = await self._upload_init(
                    client, server_url, image_path,
                )
                if not ref_name:
                    self.last_error = reason
                    return []
                audio_name, reason = await self._upload_file(
                    client, server_url, audio_path,
                    content_type=_audio_content_type(audio_path),
                    label="speech-audio",
                )
                if not audio_name:
                    self.last_error = reason
                    return []
                graph, reason = self._resolve_s2v_graph(
                    site_config=site_config,
                    prompt=prompt, negative=negative,
                    ref_image_name=ref_name, audio_name=audio_name,
                    width=width, height=height, length=length, fps=fps,
                    seed=seed, steps=steps, cfg=cfg, shift=shift,
                    sampler=sampler, chunks=chunks,
                )
                if graph is None:
                    self.last_error = reason
                    return []
                prompt_id, reason = await self._submit(client, server_url, graph)
                if not prompt_id:
                    self.last_error = reason
                    return []
                filename, reason = await self._poll(
                    client, server_url, prompt_id, timeout_s,
                    heartbeat_cb=config.get("_heartbeat_cb"),
                )
                if not filename:
                    self.last_error = reason
                    return []
                ok, reason = await self._download(
                    client, server_url, filename, output_path,
                )
                if not ok:
                    self.last_error = reason
                    return []
        except Exception as e:
            logger.error(
                "[ComfyUIProvider] speech render failed against %s: %s: %s. "
                "Stand up the ComfyUI sidecar (docker compose --profile comfyui "
                "up -d) with the S2V weights mounted, or set "
                "video_comfyui_server_url.",
                server_url, type(e).__name__, e,
            )
            self.last_error = (
                f"comfyui unreachable/failed at {server_url}: "
                f"{type(e).__name__}: {e}"
            )[:_MAX_REASON_CHARS]
            return []

        file_size = 0
        try:
            file_size = os.path.getsize(output_path)
        except OSError:  # silent-ok: size is cosmetic metadata — the file's
            # existence was just verified by the download step.
            pass

        return [
            VideoResult(
                file_url=f"file://{output_path}",
                file_path=output_path,
                duration_s=int(covered_s),
                width=width,
                height=height,
                fps=fps,
                codec="h264",
                format="mp4",
                source=self.name,
                prompt=prompt,
                metadata={
                    "local_path": output_path,
                    "file_size_bytes": file_size,
                    "negative_prompt": negative,
                    "steps": steps,
                    "guidance_scale": cfg,
                    "shift": shift,
                    "sampler": sampler,
                    "seed": seed,
                    "model": "wan2.2-s2v-14b-fp8",
                    "model_repo": "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                    "license": "apache-2.0",
                    "i2v": False,
                    "s2v": True,
                    "audio_path": audio_path,
                    "audio_seconds": round(float(audio_s), 3),
                    "chunks": chunks,
                    "chunk_frames": length,
                    "audio_truncated": truncated,
                    "server_url": server_url,
                },
            ),
        ]

    async def _submit(
        self, client: httpx.AsyncClient, server_url: str, graph: dict[str, Any],
    ) -> tuple[str, str]:
        resp = await client.post(
            f"{server_url}/prompt",
            json={"prompt": graph, "client_id": "poindexter"},
        )
        if resp.status_code != 200:
            # ComfyUI validation errors arrive as JSON with node_errors —
            # surface the first message, it names the broken node/input.
            detail = (resp.text or "")[:_MAX_REASON_CHARS]
            try:
                parsed = resp.json()
                node_errors = parsed.get("node_errors") or {}
                if node_errors:
                    first = next(iter(node_errors.values()))
                    errs = first.get("errors") or []
                    if errs:
                        detail = str(errs[0].get("message", detail))
                elif parsed.get("error"):
                    detail = str(
                        parsed["error"].get("message", detail)
                        if isinstance(parsed["error"], dict) else parsed["error"],
                    )
            except Exception:  # noqa: BLE001  # silent-ok: detail already
                # holds the raw body — a non-JSON error stays verbatim.
                pass
            return "", f"comfyui rejected the workflow: {detail}"
        try:
            prompt_id = str(resp.json().get("prompt_id", "") or "")
        except Exception:  # noqa: BLE001
            prompt_id = ""
        if not prompt_id:
            return "", "comfyui /prompt returned no prompt_id"
        return prompt_id, ""

    async def _poll(
        self,
        client: httpx.AsyncClient,
        server_url: str,
        prompt_id: str,
        timeout_s: float,
        heartbeat_cb: Any = None,
    ) -> tuple[str, str]:
        """Poll ``/history/{id}`` until the render finishes; returns the
        output video filename.

        ``heartbeat_cb`` (optional async callable, no args) is awaited at most
        every ``_HEARTBEAT_EVERY_S`` while the prompt is still executing. A
        long S2V clip is 20+ minutes of legitimate work inside ONE graph node;
        without a heartbeat the stuck-flow probe reads that as "no progress"
        and cancels the render mid-clip (2026-09-16 22:31Z, third presenter
        render: "No graph-node progress for 21m (stall threshold 20m)").
        Best-effort — a failing heartbeat never disturbs the poll.
        """
        deadline = time.monotonic() + timeout_s
        last_beat = time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)
            if heartbeat_cb is not None and time.monotonic() - last_beat >= _HEARTBEAT_EVERY_S:
                last_beat = time.monotonic()
                try:
                    await heartbeat_cb()
                except Exception as exc:  # noqa: BLE001
                    # silent-ok: the heartbeat is observability for the probe,
                    # not part of the render; the poll must never fail on it.
                    logger.debug("[ComfyUIProvider] heartbeat failed: %s", exc)
            try:
                resp = await client.get(f"{server_url}/history/{prompt_id}")
            except Exception as e:  # noqa: BLE001  # silent-ok: transient
                # poll failure while the render runs — retried every interval,
                # and the deadline path below reports loudly if it never
                # recovers. A warning per 5s tick would spam a 10-min render.
                logger.debug(
                    "[ComfyUIProvider] history poll error (retrying): %s", e,
                )
                continue
            if resp.status_code != 200:
                continue
            try:
                hist = resp.json()
            except Exception:  # noqa: BLE001  # silent-ok: a half-written
                # history response mid-render parses on the next tick; the
                # timeout path reports if it never does.
                continue
            entry = hist.get(prompt_id)
            if not entry:
                continue
            status = entry.get("status", {}) or {}
            if status.get("status_str") == "error":
                msg = "execution error"
                for m in status.get("messages", []) or []:
                    if isinstance(m, (list, tuple)) and len(m) > 1 \
                            and m[0] == "execution_error":
                        msg = str(
                            (m[1] or {}).get("exception_message", msg),
                        )
                        break
                return "", f"comfyui execution error: {msg}"[:_MAX_REASON_CHARS]
            for node_out in (entry.get("outputs", {}) or {}).values():
                for key in ("video", "videos", "images", "gifs"):
                    for f in node_out.get(key, []) or []:
                        name = str(f.get("filename", "") or "")
                        if name.endswith(".mp4") or name.endswith(".webm"):
                            return name, ""
            # Entry present but no video output and no error — treat as a
            # failure rather than spinning until timeout.
            if entry.get("outputs"):
                return "", (
                    "comfyui finished but produced no video output "
                    "(check the SaveVideo node in the workflow)"
                )
        return "", (
            f"comfyui render timed out after {timeout_s:.0f}s "
            f"(prompt_id={prompt_id}) — the queue may be wedged or the "
            "render is slower than video_comfyui_timeout_s"
        )

    async def _download(
        self,
        client: httpx.AsyncClient,
        server_url: str,
        filename: str,
        output_path: str,
    ) -> tuple[bool, str]:
        resp = await client.get(
            f"{server_url}/view",
            params={"filename": filename, "type": "output"},
        )
        if resp.status_code != 200 or not resp.content:
            return False, (
                f"comfyui /view failed for {filename!r}: "
                f"HTTP {resp.status_code}, {len(resp.content or b'')} bytes"
            )
        await asyncio.to_thread(_write_bytes, output_path, resp.content)
        logger.info(
            "[ComfyUIProvider] video generated: %s (%d bytes)",
            output_path, len(resp.content),
        )
        return True, ""

"""Reference-conditioned stills — every frame drawn from the cast's portraits.

Creative studio phase 7 (poindexter#1141; epic poindexter#1134). The first
cartoon renders switched art style whenever a character spoke: stills (and the
frames animated shots start from) came from the production image model, drawn
from text, while talking shots lip-synced a fixed portrait made by a different
model with its own style and a baked-in background.

This draws a still with an image-EDIT model (Qwen-Image-Edit-2511 in ComfyUI)
that takes up to three reference images. Given the portraits of the characters
in a shot, it keeps their identity AND the art style the portraits share. A
shot with no character passes the cast portraits as style-only references, so
the backgrounds match too. The renderer uses it for every prompted still of a
screenplay video and for each cast shot's per-scene talking frame (the frame
the lip-sync animates instead of the fixed portrait).

The wording that tells the model which picture is which character lives in
settings (``video_reference_*_template``), like every other render prompt.
Every failure returns ``(False, reason)``: the caller falls back to the text
path, because a still drawn without references is a weaker shot, and no shot
at all sinks the whole video.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Qwen-Image-Edit's multi-reference node takes at most three images.
MAX_REFERENCES = 3


def _cfg(site_config: Any, key: str) -> str:
    """A setting, falling back to its registered default (``DEFAULTS``).

    Never a second literal in code: on 2026-10-13 a verification render ran
    against a database that did not have these keys yet, the code fallback
    for the Lightning LoRA was ``""``, and four steps without it drew mush.
    A row that exists is honoured even when empty (``""`` = no LoRA).
    """
    from poindexter.services.settings_defaults import DEFAULTS

    if site_config is None:
        return str(DEFAULTS[key])
    value = site_config.get(key, DEFAULTS[key])
    return str(DEFAULTS[key] if value is None else value)


def _cfg_num(site_config: Any, key: str) -> float:
    from poindexter.services.settings_defaults import DEFAULTS

    try:
        return float(_cfg(site_config, key))
    except ValueError:
        return float(DEFAULTS[key])


def enabled(site_config: Any) -> bool:
    if site_config is None:
        return False
    return _cfg(site_config, "video_reference_frames_enabled").strip().lower() in ("true", "1", "yes", "on")


def compose_reference_prompt(
    prompt: str, names: list[str], *, style_only: bool, site_config: Any,
) -> str:
    """Tell the edit model what each reference picture is, then the shot.

    ``names`` are the display names of the reference pictures, in order.
    ``style_only``: the pictures set the look, but nobody in them is in this
    shot.
    """
    if style_only:
        return _cfg(site_config, "video_reference_style_only_template").replace("{prompt}", prompt)
    line = _cfg(site_config, "video_reference_name_template")
    refs = ". ".join(
        line.replace("{n}", str(i + 1)).replace("{name}", name) for i, name in enumerate(names)
    )
    return (
        _cfg(site_config, "video_reference_prompt_template")
        .replace("{references}", refs).replace("{prompt}", prompt)
    )


def build_reference_graph(
    *,
    prompt: str,
    references: list[str],
    width: int,
    height: int,
    seed: int,
    site_config: Any,
    prefix: str = "reference_frame",
) -> dict[str, Any]:
    """ComfyUI API graph for one reference-conditioned still. Pure."""
    if not references:
        raise ValueError("a reference frame needs at least one reference image")
    refs = references[:MAX_REFERENCES]
    model = _cfg(site_config, "video_reference_model")
    lora = _cfg(site_config, "video_reference_lora").strip()
    steps = int(_cfg_num(site_config, "video_reference_steps"))
    cfg = _cfg_num(site_config, "video_reference_cfg")
    shift = _cfg_num(site_config, "video_reference_shift")
    graph: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "5": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": _cfg(site_config, "video_reference_text_encoder"),
            "type": "qwen_image", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {
            "vae_name": _cfg(site_config, "video_reference_vae")}},
    }
    model_src: list[Any] = ["1", 0]
    if lora:
        graph["2"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model_src, "lora_name": lora, "strength_model": 1.0}}
        model_src = ["2", 0]
    graph["3"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": model_src, "shift": shift}}
    graph["4"] = {"class_type": "CFGNorm", "inputs": {"model": ["3", 0], "strength": 1.0}}
    images: dict[str, Any] = {}
    for i, name in enumerate(refs):
        load, scale = str(10 + i), str(20 + i)
        graph[load] = {"class_type": "LoadImage", "inputs": {"image": name}}
        graph[scale] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [load, 0], "upscale_method": "lanczos", "megapixels": 1.0,
            "resolution_steps": 1}}
        images[f"image{i + 1}"] = [scale, 0]
    graph["30"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
        "clip": ["5", 0], "prompt": prompt, "vae": ["6", 0], **images}}
    graph["31"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
        "clip": ["5", 0], "prompt": "", "vae": ["6", 0], **images}}
    positive, negative = ["30", 0], ["31", 0]
    method = _cfg(site_config, "video_reference_latent_method").strip()
    if method:
        graph["32"] = {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": positive, "reference_latents_method": method}}
        graph["33"] = {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": negative, "reference_latents_method": method}}
        positive, negative = ["32", 0], ["33", 0]
    graph["40"] = {"class_type": "EmptySD3LatentImage", "inputs": {
        "width": int(width), "height": int(height), "batch_size": 1}}
    graph["50"] = {"class_type": "KSampler", "inputs": {
        "model": ["4", 0], "seed": int(seed), "steps": steps, "cfg": cfg,
        "sampler_name": "euler", "scheduler": "simple", "positive": positive,
        "negative": negative, "latent_image": ["40", 0], "denoise": 1.0}}
    graph["60"] = {"class_type": "VAEDecode", "inputs": {"samples": ["50", 0], "vae": ["6", 0]}}
    graph["70"] = {"class_type": "SaveImage", "inputs": {"images": ["60", 0], "filename_prefix": prefix}}
    return graph


async def _upload(client: httpx.AsyncClient, server: str, path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    resp = await client.post(
        f"{server}/upload/image",
        files={"image": (os.path.basename(path), raw, "image/png")},
        data={"overwrite": "true"},
    )
    resp.raise_for_status()
    name = str(resp.json().get("name") or "")
    if not name:
        raise RuntimeError(f"upload of {path} returned no filename")
    return name


async def render_reference_frame(
    *,
    prompt: str,
    reference_paths: list[str],
    output_path: str,
    width: int,
    height: int,
    site_config: Any,
    seed: int | None = None,
) -> tuple[bool, str]:
    """Draw one still with ``reference_paths`` as references. ``(ok, reason)``."""
    refs = [p for p in reference_paths if p and os.path.exists(p)][:MAX_REFERENCES]
    if not refs:
        return False, "no reference image on disk"
    server = _cfg(site_config, "video_comfyui_server_url").rstrip("/")
    timeout_s = _cfg_num(site_config, "video_reference_timeout_s")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            names = [await _upload(client, server, p) for p in refs]
            graph = build_reference_graph(
                prompt=prompt, references=names, width=width, height=height,
                seed=seed if seed is not None else random.randint(1, 2**31 - 1),
                site_config=site_config,
            )
            resp = await client.post(f"{server}/prompt", json={"prompt": graph, "client_id": "poindexter"})
            if resp.status_code != 200:
                return False, f"comfyui rejected the reference graph: {(resp.text or '')[:300]}"
            prompt_id = str(resp.json().get("prompt_id") or "")
            if not prompt_id:
                return False, "comfyui returned no prompt_id"
            while time.monotonic() - started < timeout_s:
                await asyncio.sleep(2.0)
                hist = (await client.get(f"{server}/history/{prompt_id}")).json().get(prompt_id)
                if not hist:
                    continue
                status = hist.get("status") or {}
                if status.get("status_str") == "error":
                    msgs = [m[1].get("exception_message", "?") for m in status.get("messages", [])
                            if isinstance(m, (list, tuple)) and m and m[0] == "execution_error"]
                    return False, f"comfyui execution error: {msgs[:1]}"[:300]
                for out in (hist.get("outputs") or {}).values():
                    for img in out.get("images") or []:
                        view = await client.get(f"{server}/view", params={
                            "filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": "output"})
                        if view.status_code == 200 and view.content:
                            with open(output_path, "wb") as fh:
                                fh.write(view.content)
                            logger.info(
                                "[reference_frames] drew %s from %d reference(s) in %.0fs",
                                os.path.basename(output_path), len(refs), time.monotonic() - started,
                            )
                            return True, ""
                if hist.get("outputs"):
                    return False, "comfyui finished without an image output"
            return False, f"reference frame timed out after {timeout_s:.0f}s"
    except Exception as exc:  # noqa: BLE001 — the caller falls back to the text path
        return False, f"{type(exc).__name__}: {exc}"[:300]


__all__ = [
    "MAX_REFERENCES",
    "build_reference_graph",
    "compose_reference_prompt",
    "enabled",
    "render_reference_frame",
]

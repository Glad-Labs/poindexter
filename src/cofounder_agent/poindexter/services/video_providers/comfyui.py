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

Kind: ``"generate"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any

import httpx

from plugins.video_provider import VideoResult

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

# Placeholder tokens the workflow-override substitution recognises, mapped to
# whether the substituted value is numeric (replaces the string leaf with an
# int/float) or textual (stays a string).
_OVERRIDE_NUMERIC = {
    "__WIDTH__", "__HEIGHT__", "__LENGTH__", "__FPS__", "__SEED__",
    "__STEPS__", "__CFG__", "__SHIFT__",
}
_OVERRIDE_TEXTUAL = {
    "__PROMPT__", "__NEGATIVE__", "__INIT_IMAGE__", "__FILENAME_PREFIX__",
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
        raw = await asyncio.to_thread(_read_bytes, image_path)
        basename = os.path.basename(image_path) or "init.png"
        resp = await client.post(
            f"{server_url}/upload/image",
            files={"image": (basename, raw, "image/png")},
            data={"overwrite": "true"},
        )
        if resp.status_code != 200:
            return "", (
                f"init-image upload failed: HTTP {resp.status_code}: "
                f"{(resp.text or '')[:150]}"
            )
        try:
            name = str(resp.json().get("name", "") or "")
        except Exception:  # noqa: BLE001
            name = ""
        if not name:
            return "", "init-image upload returned no filename"
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
    ) -> tuple[str, str]:
        """Poll ``/history/{id}`` until the render finishes; returns the
        output video filename."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)
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

"""Featured-image fan-out — render N model candidates, judge, ship the best.

Phase 1 of the multi-provider image plan (2026-08-15 bake-off follow-up):
for the FEATURED image only, the same brief is rendered by up to four
models and a vision judge picks the winner —

- ``zimage`` — the production image-gen server render (OCR-gated). The
  *stage* renders this one through its existing ``_render_image_gen`` path
  and passes the file in; this service never imports stage code (services
  must not depend on ``modules/content`` — the engine never imports content).
- ``schnell`` — FLUX.1-schnell fp8 via the ComfyUI sidecar (~3s warm).
- ``klein`` — FLUX.2-klein-4B (distilled) via the ComfyUI sidecar. BFL's
  flux1-schnell successor: Apache-2.0 and ungated, 4 steps at cfg 1 like
  schnell, ~16GB of weights. (FLUX.2-dev and klein-9B are
  ``flux-non-commercial-license`` — disqualifying for a commercial
  publishing pipeline. Only the 4B is license-clean; do not swap the
  ``image_fanout_klein_model`` setting onto a 9B file.)
- ``qwen`` — Qwen-Image fp8 via the ComfyUI sidecar (~24s warm; first load
  of the ~28GB pair takes minutes — budget accordingly).

The schnell and qwen graphs are the byte-for-byte shape proven in the
2026-08-15 bake-off runner; the klein graph is the byte-for-byte shape of
ComfyUI's own ``image_flux2_klein_text_to_image`` template (distilled
subgraph). They live in-service rather than as registered ImageProvider
plugins on purpose: Phase 1 is featured-only, and a registered provider
would also surface in the inline-image dispatch, which is Phase-2 scope.

**The judged fan-out is also the router's training data.** Every judged
render writes an ``image_fanout_judged`` row to ``audit_log`` (winner +
per-candidate scores + the brief) — the Phase-2 class→provider routing map
is seeded from these rows' win rates, so running Phase 1 IS collecting the
dataset.

Judging mirrors ``shot_vision_qa.score_shot_frame``: one image per call to
``qa_vision_model`` through ``dispatch_complete`` (cost_logs + Langfuse for
free), ``max_tokens`` ≥ 1024 for the qwen3-vl think-trace budget, fail-soft
``None`` scores. All-``None`` (judge down / disabled) falls open to
``image_fanout_priority`` order — first present candidate wins, which with
the default priority reproduces today's single-model behaviour exactly.

VRAM choreography: the stage renders zimage FIRST (image-gen is warm from
the inline batch); this service then hard-unloads image-gen via the gpu
scheduler rung before the ComfyUI candidates load (schnell 17GB / klein
~16GB / qwen ~28GB cannot coexist with a 13-25GB image-gen resident on a
32GB card). The default candidate order renders the ComfyUI models
ascending by footprint so the heaviest load lands last.
ComfyUI swaps its own models internally and the dispatch reclaim ladder's
``_unload_comfyui`` rung frees it when the video render later needs the
card. The next post's inline batch pays one image-gen cold reload (~60s) —
the documented cost of the fan-out window.

Master switch: ``image_fanout_enabled`` (default ``false`` — dark launch).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_COMFY_URL = "http://comfyui:8188"
_DEFAULT_CANDIDATES = "zimage,schnell,klein,qwen"
_DEFAULT_PRIORITY = "zimage,schnell,klein,qwen"
_DEFAULT_RENDER_TIMEOUT_S = 600.0
_DEFAULT_W = 1024
_DEFAULT_H = 1024
_POLL_INTERVAL_S = 3.0

# Which candidate names this service renders itself (via the ComfyUI
# sidecar) rather than receiving pre-rendered from the stage. ONE list:
# the wanted-filter and the graph dispatch must agree, so a new candidate
# is added here and in ``_build_candidate_graph`` — never by inlining a
# name tuple at the filter site, which is how a graph builder can exist
# for a candidate that the orchestrator silently never asks for.
_COMFY_CANDIDATES = ("schnell", "klein", "qwen")

_DEFAULT_SCHNELL_CKPT = "flux1-schnell-fp8.safetensors"
_DEFAULT_QWEN_MODEL = "qwen_image_2512_fp8_e4m3fn.safetensors"
_DEFAULT_QWEN_TE = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
_DEFAULT_QWEN_VAE = "qwen_image_vae.safetensors"
# FLUX.2-klein-4B, Comfy-Org repackage. The DISTILLED file (guidance-baked,
# 4 steps at cfg 1); `flux-2-klein-base-4b.safetensors` is the base variant
# and wants ~20 steps at cfg 5 — swapping the file alone renders noise, the
# steps/cfg settings must move with it.
_DEFAULT_KLEIN_MODEL = "flux-2-klein-4b.safetensors"
# Qwen3-4B — FLUX.2's text embedder is part of the architecture, not an
# interchangeable CLIP. Loaded with CLIPLoader type "flux2".
_DEFAULT_KLEIN_TE = "qwen_3_4b.safetensors"
_DEFAULT_KLEIN_VAE = "flux2-vae.safetensors"

# Appended to every ComfyUI candidate's positive prompt. The production
# zimage path has a server-side OCR gate; the ComfyUI candidates have no
# equivalent yet (Phase-2 note in the doc), so text discipline rides the
# prompt AND the judge's no-legible-text criterion.
_NO_TEXT_CLAUSE = (
    "no text, no words, no letters, no captions, no labels, textless "
    "composition"
)


@dataclass
class FanoutCandidate:
    """One rendered candidate awaiting judgment."""

    name: str
    path: str
    meta: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    reason: str = ""


def _sc_get(site_config: Any, key: str, default: Any) -> Any:
    if site_config is None:
        return default
    try:
        val = site_config.get(key, default)
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not
        # decide a render's fate; code default is the documented fallback.
        return default
    return default if val in (None, "") else val


def _sc_num(site_config: Any, key: str, default: float) -> float:
    try:
        return float(_sc_get(site_config, key, default))
    except (TypeError, ValueError):
        return default


def _csv(value: Any) -> list[str]:
    return [t.strip().lower() for t in str(value or "").split(",") if t.strip()]


def fanout_enabled(site_config: Any) -> bool:
    val = _sc_get(site_config, "image_fanout_enabled", False)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# ComfyUI candidate graphs — byte-for-byte the bake-off runner's proven shapes
# ---------------------------------------------------------------------------


def schnell_graph(
    *, prompt: str, seed: int, width: int, height: int,
    ckpt: str = _DEFAULT_SCHNELL_CKPT, steps: int = 4, cfg: float = 1.0,
) -> dict[str, Any]:
    """FLUX.1-schnell fp8 txt2img. Distilled: 4 steps, cfg 1, empty negative
    (schnell ignores negative guidance at cfg 1 — the empty encode is just
    the KSampler's required input)."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": f"{prompt}, {_NO_TEXT_CLAUSE}"}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": ""}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["2", 0], "negative": ["3", 0],
            "latent_image": ["4", 0], "denoise": 1.0}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "fanout_schnell"}},
    }


def qwen_graph(
    *, prompt: str, negative: str, seed: int, width: int, height: int,
    model: str = _DEFAULT_QWEN_MODEL, text_encoder: str = _DEFAULT_QWEN_TE,
    vae: str = _DEFAULT_QWEN_VAE, steps: int = 20, cfg: float = 2.5,
    shift: float = 3.1,
) -> dict[str, Any]:
    """Qwen-Image fp8 txt2img (AuraFlow sampling, shift 3.1 per the official
    template)."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": text_encoder, "type": "qwen_image",
            "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": shift}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": f"{prompt}, {_NO_TEXT_CLAUSE}"}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": negative}},
        "7": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["5", 0], "negative": ["6", 0],
            "latent_image": ["7", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "fanout_qwen"}},
    }


def klein_graph(
    *, prompt: str, seed: int, width: int, height: int,
    model: str = _DEFAULT_KLEIN_MODEL, text_encoder: str = _DEFAULT_KLEIN_TE,
    vae: str = _DEFAULT_KLEIN_VAE, steps: int = 4, cfg: float = 1.0,
) -> dict[str, Any]:
    """FLUX.2-klein-4B (distilled) txt2img.

    Shape is ComfyUI's own ``image_flux2_klein_text_to_image`` template,
    distilled subgraph. Three things differ from the schnell/qwen graphs
    and all three are load-bearing:

    - **Sampling runs through ``SamplerCustomAdvanced``, not ``KSampler``.**
      FLUX.2 needs ``Flux2Scheduler``, which emits SIGMAS from
      ``(steps, width, height)`` — resolution-aware shift is computed there
      rather than passed as a shift knob, so there is no ``KSampler`` port
      to hand it to.
    - **The negative is ``ConditioningZeroOut`` of the positive**, not an
      empty text encode. At cfg 1 the guider ignores it either way, but
      zeroing costs no second text-encoder pass — and the encoder here is
      an 8GB Qwen3-4B, so the saving is real.
    - **The text encoder is a separate 8GB file** loaded with ``CLIPLoader``
      type ``flux2``. It is part of the architecture, not swappable.
    """
    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": text_encoder, "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": f"{prompt}, {_NO_TEXT_CLAUSE}"}},
        "5": {"class_type": "ConditioningZeroOut",
              "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "CFGGuider", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
            "cfg": cfg}},
        "7": {"class_type": "KSamplerSelect",
              "inputs": {"sampler_name": "euler"}},
        "8": {"class_type": "Flux2Scheduler", "inputs": {
            "steps": steps, "width": width, "height": height}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["9", 0], "guider": ["6", 0], "sampler": ["7", 0],
            "sigmas": ["8", 0], "latent_image": ["10", 0]}},
        # SamplerCustomAdvanced returns (output, denoised_output) — slot 0
        # is the sampled latent the template decodes.
        "12": {"class_type": "VAEDecode",
               "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage",
               "inputs": {"images": ["12", 0], "filename_prefix": "fanout_klein"}},
    }


def _candidate_dimensions(
    name: str, site_config: Any,
) -> tuple[int, int]:
    """Resolve one candidate's render size, per-candidate first.

    These models do not share a best resolution: Qwen-Image is trained at
    1328x1328, while FLUX.1-schnell degrades above roughly 1.2 MP and klein
    is native 1024. A single global size therefore has to hurt someone, and
    the fan-out's whole premise is each model getting to show its best work.

    ``image_fanout_<name>_width`` / ``_height`` override
    ``image_fanout_width`` / ``_height``. Unset ('' per the app_settings
    convention, or absent) inherits the global, so a candidate with no
    override renders exactly as it did before.
    """
    width = int(_sc_num(site_config, "image_fanout_width", _DEFAULT_W))
    height = int(_sc_num(site_config, "image_fanout_height", _DEFAULT_H))
    return (
        int(_sc_num(site_config, f"image_fanout_{name}_width", width)),
        int(_sc_num(site_config, f"image_fanout_{name}_height", height)),
    )


def _build_candidate_graph(
    name: str, *, prompt: str, negative: str, seed: int,
    site_config: Any,
) -> dict[str, Any] | None:
    width, height = _candidate_dimensions(name, site_config)
    if name == "schnell":
        return schnell_graph(
            prompt=prompt, seed=seed, width=width, height=height,
            ckpt=str(_sc_get(
                site_config, "image_fanout_schnell_checkpoint",
                _DEFAULT_SCHNELL_CKPT)),
            steps=int(_sc_num(site_config, "image_fanout_schnell_steps", 4)),
            cfg=_sc_num(site_config, "image_fanout_schnell_cfg", 1.0),
        )
    if name == "qwen":
        return qwen_graph(
            prompt=prompt, negative=negative, seed=seed,
            width=width, height=height,
            model=str(_sc_get(
                site_config, "image_fanout_qwen_model", _DEFAULT_QWEN_MODEL)),
            text_encoder=str(_sc_get(
                site_config, "image_fanout_qwen_text_encoder", _DEFAULT_QWEN_TE)),
            vae=str(_sc_get(
                site_config, "image_fanout_qwen_vae", _DEFAULT_QWEN_VAE)),
            steps=int(_sc_num(site_config, "image_fanout_qwen_steps", 20)),
            cfg=_sc_num(site_config, "image_fanout_qwen_cfg", 2.5),
            shift=_sc_num(site_config, "image_fanout_qwen_shift", 3.1),
        )
    if name == "klein":
        return klein_graph(
            prompt=prompt, seed=seed, width=width, height=height,
            model=str(_sc_get(
                site_config, "image_fanout_klein_model", _DEFAULT_KLEIN_MODEL)),
            text_encoder=str(_sc_get(
                site_config, "image_fanout_klein_text_encoder",
                _DEFAULT_KLEIN_TE)),
            vae=str(_sc_get(
                site_config, "image_fanout_klein_vae", _DEFAULT_KLEIN_VAE)),
            steps=int(_sc_num(site_config, "image_fanout_klein_steps", 4)),
            cfg=_sc_num(site_config, "image_fanout_klein_cfg", 1.0),
        )
    return None


# ---------------------------------------------------------------------------
# ComfyUI transport — submit / poll / download (image flavour)
# ---------------------------------------------------------------------------


async def _render_via_comfy(
    name: str, graph: dict[str, Any], *, server_url: str, timeout_s: float,
) -> tuple[str | None, dict[str, Any]]:
    """Submit a graph, wait, download the PNG to a temp file.

    Returns ``(path, meta)`` — ``path`` None on any failure with the reason
    in ``meta['failure']`` (mirrors the featured stage's window-vs-verdict
    metadata convention loosely; a fan-out candidate miss is never fatal,
    the judge simply sees fewer candidates).
    """
    t0 = time.monotonic()
    client_timeout = httpx.Timeout(30.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            resp = await client.post(
                f"{server_url}/prompt",
                json={"prompt": graph, "client_id": "poindexter-fanout"},
            )
            if resp.status_code != 200:
                return None, {"failure": (
                    f"comfyui rejected {name} graph: HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:200]}"
                )}
            prompt_id = str(resp.json().get("prompt_id", "") or "")
            if not prompt_id:
                return None, {"failure": f"{name}: no prompt_id returned"}

            deadline = time.monotonic() + timeout_s
            filename = ""
            while time.monotonic() < deadline:
                await asyncio.sleep(_POLL_INTERVAL_S)
                try:
                    hist = await client.get(f"{server_url}/history/{prompt_id}")
                except Exception:  # noqa: BLE001  # silent-ok: transient poll
                    # miss mid-render — retried every tick; the deadline path
                    # reports if it never recovers.
                    continue
                if hist.status_code != 200:
                    continue
                entry = hist.json().get(prompt_id)
                if not entry:
                    continue
                status = entry.get("status", {}) or {}
                if status.get("status_str") == "error":
                    msg = "execution error"
                    for m in status.get("messages", []) or []:
                        if isinstance(m, (list, tuple)) and len(m) > 1 \
                                and m[0] == "execution_error":
                            msg = str((m[1] or {}).get("exception_message", msg))
                            break
                    return None, {"failure": f"{name}: {msg[:300]}"}
                for node_out in (entry.get("outputs", {}) or {}).values():
                    for f in node_out.get("images", []) or []:
                        filename = str(f.get("filename", "") or "")
                        if filename:
                            break
                    if filename:
                        break
                if filename:
                    break
            if not filename:
                return None, {"failure": (
                    f"{name}: render timed out after {timeout_s:.0f}s "
                    "(first load of a large model can exceed the budget — "
                    "raise image_fanout_render_timeout_s if this recurs warm)"
                )}

            view = await client.get(
                f"{server_url}/view",
                params={"filename": filename, "type": "output"},
            )
            if view.status_code != 200 or not view.content:
                return None, {"failure": f"{name}: /view failed for {filename!r}"}
            fd, out_path = tempfile.mkstemp(
                prefix=f"fanout_{name}_", suffix=".png")
            with os.fdopen(fd, "wb") as fh:
                fh.write(view.content)
            return out_path, {
                "model": name,
                "elapsed_s": round(time.monotonic() - t0, 1),
                "bytes": len(view.content),
            }
    except Exception as e:  # noqa: BLE001 — a candidate miss must not kill
        # the fan-out; the failure is carried in meta and logged by the caller.
        return None, {"failure": f"{name}: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Judge — one score call per candidate, mirroring shot_vision_qa
# ---------------------------------------------------------------------------


def _parse_score(text: str) -> tuple[float | None, str]:
    json_text = text
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            json_text = m.group(1)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\"score\".*?\}", text, re.DOTALL)
        if not m:
            return None, "unparseable vision response"
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None, "unparseable vision response"
    raw = parsed.get("score")
    if not isinstance(raw, (int, float)):
        return None, "vision response missing numeric score"
    return float(raw), str(parsed.get("reason", ""))[:200]


async def _score_candidate(
    candidate: FanoutCandidate, *, brief: str, site_config: Any, pool: Any,
) -> None:
    """Score one candidate in place. Fail-soft: score stays ``None``."""
    model = str(_sc_get(site_config, "qa_vision_model", "") or "").strip()
    if not model or pool is None:
        candidate.reason = "judge unavailable (no model/pool)"
        return
    try:
        with open(candidate.path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        candidate.reason = f"unreadable candidate file: {exc}"
        return

    from services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.featured_image_fanout", brief=brief,
    )

    from services.llm_providers.dispatcher import dispatch_complete

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }]
    # qwen3-vl's <think> trace shares this budget with the JSON answer; at
    # 1024 the answer was starved out of ~30% of live judge calls in the
    # first 8 days ("unparseable vision response" on 19/72 candidate scores).
    max_tokens = int(_sc_num(
        site_config, "image_fanout_judge_max_tokens", 2048))
    try:
        completion = await dispatch_complete(
            pool, messages, model,  # type: ignore[arg-type]  # multimodal
            # content list — same shape shot_vision_qa ships; the dispatcher
            # signature types the simple text case only.
            tier="standard", phase="image_fanout_judge",
            temperature=0.2, max_tokens=max_tokens, timeout_s=150.0,
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[IMAGE_FANOUT] judge call failed for %s (fail-soft): %s",
            candidate.name, exc,
        )
        candidate.reason = "judge call failed"
        return
    candidate.score, candidate.reason = _parse_score(text)


def _pick_winner(
    candidates: list[FanoutCandidate], priority: list[str],
) -> FanoutCandidate:
    """Highest score wins; ties and the all-``None`` case resolve by
    ``priority`` order (default = today's production model first, so a downed
    judge reproduces current behaviour exactly)."""

    def prio(c: FanoutCandidate) -> int:
        try:
            return priority.index(c.name)
        except ValueError:
            return len(priority)

    scored = [c for c in candidates if c.score is not None]
    if not scored:
        return min(candidates, key=prio)
    best = max(c.score for c in scored)  # type: ignore[type-var]
    top = [c for c in scored if c.score == best]
    return min(top, key=prio)


async def _record_outcome(
    pool: Any, *, task_id: str | None, brief: str,
    candidates: list[FanoutCandidate], winner: FanoutCandidate,
    judge_ran: bool, zimage_absent_reason: str = "",
) -> None:
    """Write the ``image_fanout_judged`` audit row — the Phase-2 router's
    training data AND the Pipeline-board win-rate panel's source. Best-effort:
    losing the row loses telemetry, never the image."""
    if pool is None:
        return
    from services.audit_event_schemas import validate_event_details

    payload: dict[str, Any] = {
        "winner": winner.name,
        "judge_ran": judge_ran,
        "brief": brief[:300],
        "candidates": [
            {"name": c.name, "score": c.score, "reason": c.reason[:200],
             "elapsed_s": c.meta.get("elapsed_s"),
             "width": c.meta.get("width"), "height": c.meta.get("height")}
            for c in candidates
        ],
    }
    if zimage_absent_reason:
        payload["zimage_absent_reason"] = zimage_absent_reason
    details = validate_event_details("image_fanout_judged", payload)
    try:
        await pool.execute(
            "INSERT INTO audit_log (timestamp, event_type, source, task_id, "
            "details, severity) VALUES (now(), $1, $2, $3, $4::jsonb, $5)",
            "image_fanout_judged", "services.image_fanout", task_id,
            json.dumps(details), "info",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[IMAGE_FANOUT] outcome row insert failed (telemetry only, the "
            "win-rate panel under-reports): %s", exc,
        )


# ---------------------------------------------------------------------------
# Orchestrator — called by the featured stage
# ---------------------------------------------------------------------------


async def run_featured_fanout(
    *,
    prompt: str,
    negative: str,
    zimage_path: str | None,
    zimage_meta: dict[str, Any] | None,
    site_config: Any,
    pool: Any,
    task_id: str | None,
) -> tuple[str, dict[str, Any]] | None:
    """Render the ComfyUI candidates, judge everything present, return the
    winner ``(path, meta)``.

    ``zimage_path`` is the stage's own OCR-gated render (None when it failed
    or was gate-blocked — the fan-out's other candidates then cover for it,
    which is exactly the class the bake-off showed z-image cannot serve).
    Returns ``None`` only when NO candidate rendered — the stage then falls
    through to its existing Pexels-stock path unchanged.
    """
    wanted = _csv(_sc_get(
        site_config, "image_fanout_candidates", _DEFAULT_CANDIDATES))
    priority = _csv(_sc_get(
        site_config, "image_fanout_priority", _DEFAULT_PRIORITY))
    server_url = str(_sc_get(
        site_config, "image_fanout_comfyui_url", _DEFAULT_COMFY_URL),
    ).rstrip("/")
    timeout_s = _sc_num(
        site_config, "image_fanout_render_timeout_s", _DEFAULT_RENDER_TIMEOUT_S)

    candidates: list[FanoutCandidate] = []
    if zimage_path and "zimage" in wanted:
        candidates.append(FanoutCandidate(
            name="zimage", path=zimage_path, meta=zimage_meta or {}))

    comfy_wanted = [n for n in wanted if n in _COMFY_CANDIDATES]
    if comfy_wanted:
        # image-gen must be out of VRAM before the ComfyUI models load — the
        # decline-gated hard unload is a cheap no-op when it holds nothing.
        try:
            from services.gpu_scheduler import gpu

            await gpu._unload_image_gen(hard=True)
        except Exception as exc:  # noqa: BLE001  # silent-ok: reclaim is an
            # optimisation — a failure reverts to pre-unload odds, never
            # blocks the fan-out (the candidate render then fails loudly).
            logger.warning(
                "[IMAGE_FANOUT] image-gen pre-unload failed (%s) — "
                "rendering anyway", exc,
            )
        seed = random.randrange(2**62)
        for name in comfy_wanted:
            graph = _build_candidate_graph(
                name, prompt=prompt, negative=negative, seed=seed,
                site_config=site_config,
            )
            if graph is None:
                continue
            path, meta = await _render_via_comfy(
                name, graph, server_url=server_url, timeout_s=timeout_s,
            )
            if path:
                # Stamp the size this candidate actually rendered at. The
                # Phase-2 router learns from these rows, and resolution is a
                # confound it has to be able to see: without this, retuning a
                # candidate's size silently splits the dataset into
                # before/after halves that look identical.
                cw, ch = _candidate_dimensions(name, site_config)
                meta = {**meta, "width": cw, "height": ch}
                candidates.append(FanoutCandidate(name=name, path=path, meta=meta))
            else:
                logger.warning(
                    "[IMAGE_FANOUT] candidate %s failed: %s",
                    name, meta.get("failure", "unknown"),
                )

    if not candidates:
        return None

    judge_wanted = fanout_enabled(site_config) and str(_sc_get(
        site_config, "image_fanout_judge_enabled", True),
    ).strip().lower() not in ("false", "0", "no", "off")
    judge_ran = False
    if judge_wanted and len(candidates) > 1:
        for c in candidates:
            await _score_candidate(
                c, brief=prompt, site_config=site_config, pool=pool)
        judge_ran = any(c.score is not None for c in candidates)

    winner = _pick_winner(candidates, priority)
    # Router-dataset completeness: when the production model never made it
    # into the fan-out, record WHY (the stage's failure meta rides in via
    # zimage_meta) — 24/32 early rows were zimage-less with the reason
    # unrecorded, which made the absence look like a preference.
    zimage_absent_reason = ""
    if "zimage" in wanted and not any(c.name == "zimage" for c in candidates):
        zmeta = zimage_meta or {}
        zimage_absent_reason = str(
            zmeta.get("failure")
            or ("ocr_gate_rejected" if zmeta.get("ocr_gate_rejected") else "")
            or "render returned nothing",
        )[:200]
    await _record_outcome(
        pool, task_id=task_id, brief=prompt, candidates=candidates,
        winner=winner, judge_ran=judge_ran,
        zimage_absent_reason=zimage_absent_reason,
    )
    logger.info(
        "[IMAGE_FANOUT] winner=%s (judge_ran=%s) scores=%s",
        winner.name, judge_ran,
        {c.name: c.score for c in candidates},
    )

    # Free the ComfyUI models before the post moves on (fix for the 8-day
    # silent-503 window, 2026-08-16..24): with no idle unload, a resident
    # Qwen (~28GB) starved the NEXT post's z-image load server-side —
    # image-gen answered 503 twice and the production candidate quietly
    # vanished from 24/32 fan-outs (inline images degraded the same way).
    # The rung declines while a ComfyUI render is queued and no-ops when the
    # sidecar is down. Cost: the next fan-out pays the model reload inside
    # its own per-candidate budget — correctness over warm-cache speed.
    if comfy_wanted:
        try:
            from services.gpu_scheduler import gpu

            await gpu._unload_comfyui()
        except Exception as exc:  # noqa: BLE001  # silent-ok: freeing is an
            # optimisation for the NEXT render; failing to free reverts to
            # the pre-fix odds and prepare_mode("image_gen") retries it.
            logger.warning(
                "[IMAGE_FANOUT] post-fanout comfyui free failed (%s)", exc,
            )

    meta = dict(winner.meta)
    meta["fanout"] = {
        "winner": winner.name,
        "judge_ran": judge_ran,
        "scores": {c.name: c.score for c in candidates},
    }
    return winner.path, meta


__all__ = [
    "FanoutCandidate",
    "fanout_enabled",
    "klein_graph",
    "run_featured_fanout",
    "schnell_graph",
    "qwen_graph",
]

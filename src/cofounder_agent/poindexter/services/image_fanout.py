"""Featured-image fan-out — render N model candidates, judge, ship the best.

Phase 1 of the multi-provider image plan (2026-08-15 bake-off follow-up):
for the FEATURED image only, the same brief is rendered by up to four
models and a vision judge picks the winner —

- ``zimage`` — the production image-gen server render. The
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

**Every candidate faces the same text scan before it is judged.** Until
2026-09-23 only ``zimage`` was OCR-gated (inside image-gen's ``/generate``);
the ComfyUI three were never scanned, so the one provider held to the no-text
rule was the one being benched — ejected from 26% of contests while the
unscanned three won 77% of heroes. ``services/image_text_scan.py`` now scans
all four with one instrument and one threshold (``image_ocr_gate_max_chars``).
A candidate over it is EXCLUDED — recorded under the row's ``excluded`` list,
not ``candidates`` — so the win-rate panel can tell "lost" from "never
competed". If every candidate is excluded the fan-out returns ``(None, meta)``:
an OCR rejection is a verdict, and the stage takes its no-image path.

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

from poindexter.utils.exception_format import describe_exception

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

# Appended to every ComfyUI candidate's positive prompt. NOT the text control
# — the 2026-07 bake-off measured a "textless" positive clause leaving 25.33
# leaked chars/image — it only shifts the odds. The control is the text scan
# every candidate faces in ``_scan_candidates`` before judging.
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
    #: ``ImageTextScan.to_dict()`` for this candidate — None only when the
    #: scan step never ran (it always runs in ``run_featured_fanout``).
    text_scan: dict[str, Any] | None = None


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
    # An EMPTY completion is not a malformed one, and conflating the two cost
    # a real investigation. qwen3-vl spends the whole output budget in its
    # reasoning channel and returns empty ``content`` — 31 of 115 candidate
    # scores over the first 12 days, every one of which had generated exactly
    # ``max_tokens`` output tokens (measured in Langfuse, not inferred). The
    # old blanket "unparseable vision response" made that look like bad JSON
    # and sent the next reader hunting for a parser bug that was not there.
    # ``_judge_token_budget`` is the fix; this label is how you SEE it recur.
    if not text.strip():
        return None, "empty vision response (judge budget exhausted)"
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


def resolve_judge_model(site_config: Any) -> str:
    """The vision model that scores fan-out candidates.

    ``image_fanout_judge_model`` when set, else ``qa_vision_model``. The
    fallback keeps every existing install on exactly the model it judges with
    today, so this is a seam, not a change of behaviour.

    It exists because the fan-out judge had no pin of its own and simply read
    ``qa_vision_model`` — the setting the *article* vision rail is tuned on.
    On 2026-09-09 14:21 that key was repointed at
    ``qwen3-vl:30b-a3b-instruct`` for `qa.vision`, and the fan-out judge
    silently changed model with it, mid-calibration. The judged rows are the
    Phase-2 router's training data; a decision made about a different rail
    must not be able to change how that data is generated without anyone
    choosing it. Pinning here also lets the two rails diverge, which they
    eventually should: one grades prose against an article, the other grades
    a hero render against a brief.

    The model is recorded on every judged row (see ``_record_outcome``) so a
    regime boundary is visible IN the dataset rather than reconstructible
    from ``app_settings.updated_at`` after the fact — which is how this one
    was found.
    """
    pinned = str(_sc_get(site_config, "image_fanout_judge_model", "") or "").strip()
    if pinned:
        return pinned
    return str(_sc_get(site_config, "qa_vision_model", "") or "").strip()


def _judge_token_budget(model: str, base: int, site_config: Any) -> int:
    """Raise the judge's output budget when the vision model is a thinking one.

    Same model, same failure, same fix as
    ``multi_model_qa._maybe_bump_vision_thinking_budget`` (the
    ``vision_scorer_unavailable`` RCA of 2026-07-12) — the fan-out judge
    simply never inherited it. qwen3-vl's reasoning channel shares
    ``max_tokens`` with the JSON answer, and when the trace exhausts the
    budget the call returns EMPTY content rather than truncated JSON, so it
    fails the parse having looked like a healthy request the whole way.

    Raising 1024 -> 2048 (2026-08-25) only moved the loss rate 30.6% ->
    20.9%, because a reasoning trace is the wrong order of magnitude for a
    nudge; the established budget for this model in this role is 8000.

    Deliberately reuses ``qa_vision_thinking_num_predict`` instead of adding
    a second knob: it is the same model doing the same job, and two budgets
    that have to agree is one more pair that can drift apart. Non-thinking
    vision models keep ``base``; an already-larger ``base`` is never lowered.
    """
    try:
        from poindexter.services.llm_providers.thinking_models import (
            is_thinking_model,
            resolve_thinking_substrings,
        )

        if not is_thinking_model(
            model, substrings=resolve_thinking_substrings(site_config),
        ):
            return base
    except Exception as exc:  # noqa: BLE001 — a registry miss must not decide
        # a judge call's fate; the configured base is the documented fallback.
        logger.warning(
            "[IMAGE_FANOUT] thinking-model check failed for %s (%s) — using "
            "base judge budget %d", model, describe_exception(exc), base,
        )
        return base
    return max(base, int(_sc_num(
        site_config, "qa_vision_thinking_num_predict", 8000)))


async def _score_candidate(
    candidate: FanoutCandidate, *, brief: str, site_config: Any, pool: Any,
) -> None:
    """Score one candidate in place. Fail-soft: score stays ``None``."""
    model = resolve_judge_model(site_config)
    if not model or pool is None:
        candidate.reason = "judge unavailable (no model/pool)"
        return
    # Stamp the model BEFORE the call: a candidate whose judge call fails is
    # still evidence about that model, and a row that records the model only
    # on success would hide exactly the failures worth attributing.
    candidate.meta["judge_model"] = model
    try:
        with open(candidate.path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        candidate.reason = f"unreadable candidate file: {exc}"
        return

    from poindexter.services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.featured_image_fanout", brief=brief,
    )

    from poindexter.services.llm_providers.dispatcher import dispatch_complete

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }]
    # qwen3-vl's reasoning trace shares this budget with the JSON answer, and
    # losing that race returns EMPTY content, not truncated JSON. The base is
    # the operator's floor; a thinking vision model is lifted to the
    # established thinking budget — see ``_judge_token_budget``.
    max_tokens = _judge_token_budget(
        model,
        int(_sc_num(site_config, "image_fanout_judge_max_tokens", 2048)),
        site_config,
    )
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


async def _scan_candidates(
    candidates: list[FanoutCandidate], *, site_config: Any,
) -> list[FanoutCandidate]:
    """Text-scan every candidate; return the ones the verdict EXCLUDES.

    Sequential on purpose: the scanner is one CPU-bound OCR reader on the
    image-gen server, so parallel requests would only queue there. Each
    candidate is a ``generate``-kind render, and all four are measured by the
    same instrument at the same threshold — the symmetry is the point.

    The zimage candidate is scanned too even though image-gen's own gate
    already passed it: that gives it a coverage figure like its rivals, and
    keeps the rule in one place instead of trusting a second copy of it.

    ``unavailable`` keeps the candidate in the contest (unless fail-closed is
    configured) with the status recorded — scanning being down must not turn
    into "no hero image at all", but it must not read as clean either.

    Once one scan comes back ``unavailable`` the rest are recorded unavailable
    without asking again: the scanner is one server, and a down server would
    otherwise cost every candidate its full retry budget and push the stage
    toward its node timeout (``text_scan_budget_seconds`` assumes this).
    """
    from dataclasses import replace

    from poindexter.services import image_text_scan

    settings = image_text_scan.TextScanSettings.from_site_config(site_config)
    excluded: list[FanoutCandidate] = []
    scanner_down: Any = None
    for c in candidates:
        if scanner_down is not None:
            scan = replace(
                scanner_down,
                reason=f"not attempted — scanner unavailable earlier in this "
                       f"fan-out: {scanner_down.reason}"[:300],
            )
        else:
            scan = await image_text_scan.scan_image_text(
                c.path, kind=image_text_scan.KIND_GENERATE,
                site_config=site_config, settings=settings,
            )
            if scan.status == image_text_scan.STATUS_UNAVAILABLE:
                scanner_down = scan
        c.text_scan = scan.to_dict()
        if image_text_scan.should_exclude(scan, settings):
            excluded.append(c)
            logger.warning(
                "[IMAGE_FANOUT] %s excluded by the text scan (status=%s, "
                "chars=%s, coverage=%s%%, max_chars=%s)",
                c.name, scan.status, scan.text_chars, scan.coverage_pct,
                scan.max_chars,
            )
    return excluded


def text_scan_budget_seconds(site_config: Any) -> int:
    """Seconds the fan-out's text scans can take, for the stage's node floor.

    The first scan may land while image-gen is coming back from the hard
    unload this service issues before the ComfyUI renders, so it gets the full
    retry budget. The remaining candidates hit a warm server (or are skipped
    once the scanner is known down — see ``_scan_candidates``), so one request
    timeout each.
    """
    from poindexter.services.image_text_scan import TextScanSettings

    cfg = TextScanSettings.from_site_config(site_config)
    if not cfg.enabled:
        return 0
    first = cfg.attempts * cfg.timeout_s + (cfg.attempts - 1) * cfg.backoff_s
    others = (len(_csv(_sc_get(
        site_config, "image_fanout_candidates", _DEFAULT_CANDIDATES))) - 1)
    return int(first + max(0, others) * cfg.timeout_s)


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


def _retain_enabled(site_config: Any) -> bool:
    val = _sc_get(site_config, "image_fanout_retain_candidates", True)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


async def _retain_candidates(
    candidates: list[FanoutCandidate], *, task_id: str | None,
    site_config: Any,
) -> None:
    """Upload every candidate to the object store, stamping the public URL
    onto its meta so a judged row can be checked against the images it scored.

    Without this the fan-out is unauditable BY CONSTRUCTION, and that is not
    a theoretical loss: candidates are ``tempfile.mkstemp`` files, only the
    winner's path is returned to the stage, and the losers survive in the
    worker's ``/tmp`` only until the next container restart. The row carried
    name/score/reason and no reference of any kind, so "did the judge's 40
    actually match that image?" could not be answered for ANY of the first 48
    rows — which blocks calibrating the judge, and the judge's scores are what
    the Phase-2 routing map is trained on.

    The winner is uploaded here too, even though the stage separately
    publishes it as the post's featured image. This key holds what the JUDGE
    saw, before any downstream resize or re-encode, and a row that stands on
    its own beats one that half-points into another subsystem's naming.

    Best-effort per candidate: a retention miss costs an audit trail, never an
    image. Objects land under a dated prefix so age-based pruning stays a
    ``list_keys`` away (not built here — nothing prunes them yet).
    """
    if site_config is None or not candidates or not _retain_enabled(site_config):
        return
    from poindexter.services.r2_upload_service import R2UploadService

    svc = R2UploadService(site_config=site_config)
    now = time.gmtime()
    day = time.strftime("%Y%m%d", now)
    # Time-suffixed so a RE-RUN of the same task cannot overwrite the images
    # an earlier judged row already points at — the row and the image it
    # describes have to stay a matched pair, which is the whole point.
    stamp = time.strftime("%H%M%S", now)
    for c in candidates:
        key = f"fanout/{day}/{task_id or 'no-task'}/{c.name}-{stamp}.png"
        try:
            url = await svc.upload_to_r2(c.path, key, content_type="image/png")
        except Exception as exc:  # noqa: BLE001 — see docstring; the audit
            # trail is the casualty, and the render must still ship.
            logger.warning(
                # describe_exception, never a bare %s: an object-store upload
                # raises httpx timeouts whose str() is the EMPTY STRING, and
                # this module already lost weeks to a "render failed ()" log
                # line that named no cause (poindexter#3229).
                "[IMAGE_FANOUT] candidate retention failed for %s (%s) — its "
                "row will carry no image reference",
                c.name, describe_exception(exc),
            )
            continue
        if url:
            c.meta["url"] = url


async def _record_outcome(
    pool: Any, *, task_id: str | None, brief: str,
    candidates: list[FanoutCandidate], winner: FanoutCandidate | None,
    judge_ran: bool, zimage_absent_reason: str = "",
    excluded: list[FanoutCandidate] | None = None,
) -> None:
    """Write the ``image_fanout_judged`` audit row — the Phase-2 router's
    training data AND the Pipeline-board win-rate panel's source. Best-effort:
    losing the row loses telemetry, never the image.

    ``candidates`` are the ones that COMPETED. Text-scan exclusions go under
    ``excluded`` instead: they never faced the judge, and counting them as
    candidates would read a never-competed render as a loss (and, with its
    NULL score, as a judge failure on the loss-rate panel). ``winner`` is None
    only when every candidate was excluded — the row is still written, because
    a contest nobody could enter is exactly what the dataset must show.
    """
    if pool is None:
        return
    from poindexter.services.audit_event_schemas import validate_event_details

    def _entry(c: FanoutCandidate) -> dict[str, Any]:
        return {
            "name": c.name, "score": c.score, "reason": c.reason[:200],
            "elapsed_s": c.meta.get("elapsed_s"),
            "width": c.meta.get("width"), "height": c.meta.get("height"),
            # The image this score describes. None (key omitted) when
            # retention is off or the upload missed — see _retain_candidates.
            "url": c.meta.get("url"),
            # The vision model that produced this score. Without it, a
            # judge-model swap is invisible in the dataset and the rows on
            # either side read as one population — see resolve_judge_model.
            "judge_model": c.meta.get("judge_model"),
            # Measured text (chars + frame coverage) and the verdict, so a
            # score can be read against how much text the image carried.
            "text_scan": c.text_scan,
        }

    payload: dict[str, Any] = {
        "winner": winner.name if winner is not None else None,
        "judge_ran": judge_ran,
        "brief": brief[:300],
        "candidates": [_entry(c) for c in candidates],
        "excluded": [_entry(c) for c in (excluded or [])],
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
) -> tuple[str | None, dict[str, Any]] | None:
    """Render the ComfyUI candidates, text-scan and judge everything present,
    return the winner ``(path, meta)``.

    ``zimage_path`` is the stage's own OCR-gated render (None when it failed
    or was gate-blocked — the fan-out's other candidates then cover for it,
    which is exactly the class the bake-off showed z-image cannot serve).

    Three outcomes:

    * ``(path, meta)`` — a winner.
    * ``(None, meta)`` — candidates rendered but the text scan excluded every
      one. A verdict, not a window: the stage must take its no-image path and
      must NOT fall back to ``zimage_path`` (it was scanned and excluded too).
    * ``None`` — NO candidate rendered; the stage's existing path is unchanged.
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
            from poindexter.services.gpu_scheduler import gpu

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

    # Before judging: every candidate faces the same text scan, so no model is
    # held to a rule its rivals skip (see the module docstring).
    excluded = await _scan_candidates(candidates, site_config=site_config)
    rendered = candidates
    excluded_ids = {id(c) for c in excluded}
    candidates = [c for c in rendered if id(c) not in excluded_ids]

    judge_wanted = fanout_enabled(site_config) and str(_sc_get(
        site_config, "image_fanout_judge_enabled", True),
    ).strip().lower() not in ("false", "0", "no", "off")
    judge_ran = False
    if judge_wanted and len(candidates) > 1:
        for c in candidates:
            await _score_candidate(
                c, brief=prompt, site_config=site_config, pool=pool)
        judge_ran = any(c.score is not None for c in candidates)

    winner = _pick_winner(candidates, priority) if candidates else None
    # Router-dataset completeness: when the production model never made it
    # into the fan-out, record WHY (the stage's failure meta rides in via
    # zimage_meta) — 24/32 early rows were zimage-less with the reason
    # unrecorded, which made the absence look like a preference.
    #
    # EVERY zimage-less row carries a reason, including the deliberate one:
    # a row with no reason at all is indistinguishable from a producer bug,
    # so "operator took zimage out of image_fanout_candidates" has to say so
    # rather than fall through as silence. (2026-08-27 21:30 was exactly
    # this — one unexplained absence that cost a round of forensics to rule
    # out as a starvation.)
    zimage_absent_reason = ""
    if any(c.name == "zimage" for c in excluded):
        # Same label the stage's own 422 produces: both mean "the text rule
        # kept it out", which is what the panel groups on.
        zimage_absent_reason = "ocr_gate_rejected"
    elif not any(c.name == "zimage" for c in candidates):
        if "zimage" not in wanted:
            zimage_absent_reason = "not in candidates"
        else:
            zmeta = zimage_meta or {}
            zimage_absent_reason = str(
                zmeta.get("failure")
                or ("ocr_gate_rejected" if zmeta.get("ocr_gate_rejected")
                    else "")
                or "render returned nothing",
            )[:200]
    # Before the row is written, and before the ComfyUI free below: the
    # candidate temp files are still on disk only until this frame ends.
    # Excluded candidates are retained too: "what did the text scan reject?"
    # has to be answerable by looking, the same as "did the judge's score
    # match the image?".
    await _retain_candidates(
        rendered, task_id=task_id, site_config=site_config)
    await _record_outcome(
        pool, task_id=task_id, brief=prompt, candidates=candidates,
        winner=winner, judge_ran=judge_ran,
        zimage_absent_reason=zimage_absent_reason, excluded=excluded,
    )
    logger.info(
        "[IMAGE_FANOUT] winner=%s (judge_ran=%s) scores=%s excluded=%s",
        winner.name if winner else None, judge_ran,
        {c.name: c.score for c in candidates},
        [c.name for c in excluded],
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
            from poindexter.services.gpu_scheduler import gpu

            await gpu._unload_comfyui()
        except Exception as exc:  # noqa: BLE001  # silent-ok: freeing is an
            # optimisation for the NEXT render; failing to free reverts to
            # the pre-fix odds and prepare_mode("image_gen") retries it.
            logger.warning(
                "[IMAGE_FANOUT] post-fanout comfyui free failed (%s)", exc,
            )

    fanout_meta = {
        "winner": winner.name if winner else None,
        "judge_ran": judge_ran,
        "scores": {c.name: c.score for c in candidates},
        "excluded": [c.name for c in excluded],
    }
    if winner is None:
        logger.warning(
            "[IMAGE_FANOUT] every rendered candidate (%s) was excluded by the "
            "text scan — no fan-out winner; the stage takes its no-image path",
            ", ".join(c.name for c in excluded),
        )
        return None, {"fanout": fanout_meta, "ocr_gate_rejected": True}
    meta = dict(winner.meta)
    meta["fanout"] = fanout_meta
    meta["text_scan"] = winner.text_scan
    return winner.path, meta


__all__ = [
    "FanoutCandidate",
    "text_scan_budget_seconds",
    "fanout_enabled",
    "klein_graph",
    "run_featured_fanout",
    "schnell_graph",
    "qwen_graph",
]

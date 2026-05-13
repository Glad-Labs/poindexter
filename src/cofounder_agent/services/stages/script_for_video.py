"""ScriptForVideoStage — re-write a published post into video scripts.

Per the Glad-Labs/poindexter#143 plan: "quality first via local
inference." This Stage is the entry point of the video pipeline. It
takes the post's title + body and asks the writer model (the same
local Ollama model that drafts blog content) to produce a
*video-paced* script — short conversational sentences with explicit
scene boundaries — for both long-form and short-form output in a
single trip through the pipeline.

Two calls (long-form, short-form), not one. They have different
constraints (5-10 minute landscape video vs ≤60s vertical hook), and
collapsing them into one prompt produces lazy outputs in practice.
Cost is electricity-only — Matt's explicit call: spend the local
GPU minutes on quality.

## Context reads

- ``task_id`` (str) — pipeline task ID
- ``title`` (str) — post H1
- ``content`` (str) — post body (markdown OK; the model strips it)
- ``site_config`` — DI seam (Phase H, GH#95)

## Context writes

- ``video_script`` (dict) — see :func:`_default_script` for the shape:
  ``{"long_form": {...}, "short_form": {...}}``
- ``stages["video.script"]`` (bool)

Long-form scenes target 30-45s each (≈8-12 scenes for a 5min video).
Short-form scenes target 10-15s each (≈3-5 scenes for a 45-55s clip).
Numbers are hints, not contracts — TTS narration timing is what
actually drives final scene durations downstream.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


# Defaults exposed as module constants so tests + ops can verify the
# numeric targets without re-reading the prompt.
_LONG_FORM_TARGET_SCENES = 10
_SHORT_FORM_TARGET_SCENES = 4
_LONG_FORM_SCENE_SECONDS = 30
_SHORT_FORM_SCENE_SECONDS = 13


def _default_script() -> dict[str, Any]:
    """Empty/default script shape — used when both LLM calls fail.

    Defined once as the canonical shape so downstream Stages can rely
    on the structure even when the model produced nothing usable.
    """
    return {
        "long_form": {
            "intro_hook": "",
            "outro_cta": "",
            "scenes": [],
        },
        "short_form": {
            "intro_hook": "",
            "scenes": [],
        },
    }


def _strip_markdown(text: str) -> str:
    """Cheap markdown stripping for prompt budget hygiene.

    Same intent as ``services.podcast_service._strip_markdown`` but
    inlined here to avoid pulling that module's imports into the video
    pipeline. Drop H1-H6 markers, code fences, and link syntax;
    everything else passes through.
    """
    out = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    out = re.sub(r"```[a-zA-Z]*\n.*?```", "", out, flags=re.DOTALL)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    out = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", out)
    out = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", out)
    return out.strip()


def _build_long_form_prompt(title: str, body: str) -> str:
    return (
        f"You are scripting a {_LONG_FORM_TARGET_SCENES * _LONG_FORM_SCENE_SECONDS // 60}-minute "
        f"YouTube video that adapts a published article. Output is JSON ONLY — "
        f"no preamble, no markdown fences, just a single JSON object.\n\n"
        f"Required JSON shape:\n"
        "{\n"
        '  "intro_hook": "<single sentence the narrator opens with>",\n'
        '  "outro_cta": "<single sentence call to action at the end>",\n'
        '  "scenes": [\n'
        "    {\n"
        '      "narration_text": "<2-4 short conversational sentences for this scene, no markdown, no SSML>",\n'
        '      "visual_prompt": "<Stable Diffusion XL prompt — photorealistic, cinematic lighting, no people, no text>",\n'
        f'      "duration_s_hint": {_LONG_FORM_SCENE_SECONDS}\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Constraints:\n"
        f"- Produce {_LONG_FORM_TARGET_SCENES} scenes (range {_LONG_FORM_TARGET_SCENES - 2}-{_LONG_FORM_TARGET_SCENES + 2} acceptable).\n"
        f"- Each narration_text MUST be conversational spoken English. Do not "
        f"include URLs, code, or lists — narrate them in prose. No filler "
        f"phrases (\"in this video\", \"let's talk about\").\n"
        f"- Each visual_prompt MUST end with: cinematic lighting, no people, "
        f"no text, no faces, 4k.\n"
        f"- intro_hook MUST start with a question or surprising fact. Hard cap 25 words.\n"
        f"- outro_cta MUST mention the article URL by phrasing only "
        f"(\"link in description\"). Hard cap 20 words.\n\n"
        f"ARTICLE TITLE: {title}\n\n"
        f"ARTICLE BODY:\n{body[:6000]}\n\n"
        f"Return JSON only:"
    )


def _build_short_form_prompt(title: str, body: str) -> str:
    return (
        f"You are scripting a 45-60 second vertical short-form video "
        f"(YouTube Shorts / TikTok / Reels) that adapts a published article. "
        f"Output is JSON ONLY — no preamble, no markdown fences, just a "
        f"single JSON object.\n\n"
        f"Required JSON shape:\n"
        "{\n"
        '  "intro_hook": "<single high-tension sentence — first 3 seconds make or break retention>",\n'
        '  "scenes": [\n'
        "    {\n"
        '      "narration_text": "<1-2 punchy sentences for this scene>",\n'
        '      "visual_prompt": "<Stable Diffusion XL prompt — photorealistic, cinematic lighting, vertical 9:16 framing, no people, no text>",\n'
        f'      "duration_s_hint": {_SHORT_FORM_SCENE_SECONDS}\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Constraints:\n"
        f"- Produce exactly {_SHORT_FORM_TARGET_SCENES} scenes "
        f"(scene 1 is the hook payoff, last scene is the CTA — viewer "
        f"should leave wanting more).\n"
        f"- Total runtime ≤ 60 seconds.\n"
        f"- intro_hook is its own beat; do not double it into scene 1.\n"
        f"- visual_prompt MUST request vertical 9:16 framing.\n"
        f"- No filler phrases. Cut the narration to the bone.\n\n"
        f"ARTICLE TITLE: {title}\n\n"
        f"ARTICLE BODY:\n{body[:4000]}\n\n"
        f"Return JSON only:"
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from LLM output.

    Local writer models occasionally wrap JSON in code fences or
    leading prose despite the prompt's instructions. Try strict parse
    first; on failure, scan for the first balanced ``{...}`` block.
    Returns ``None`` when nothing parses.
    """
    if not text:
        return None
    stripped = text.strip()
    # Strip code fences if present.
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # Fall back: locate first {...} block by brace counting.
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(stripped)):
        ch = stripped[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(stripped[start : idx + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _normalize_scenes(
    raw_scenes: Any,
    fallback_duration: int,
) -> list[dict[str, Any]]:
    """Coerce the LLM's scene list into a clean canonical shape.

    Drops scenes missing required fields, clamps duration_s_hint into
    a reasonable range, and trims whitespace. Robust to extra keys —
    the model occasionally adds ``"camera": "..."`` or
    ``"music": "..."``; we keep them in the dict so downstream Stages
    can use them if useful.
    """
    if not isinstance(raw_scenes, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for raw in raw_scenes:
        if not isinstance(raw, dict):
            continue
        narration = str(raw.get("narration_text") or "").strip()
        visual = str(raw.get("visual_prompt") or "").strip()
        if not narration or not visual:
            continue
        try:
            duration = int(raw.get("duration_s_hint") or fallback_duration)
        except (TypeError, ValueError):
            duration = fallback_duration
        # Clamp to (5s, 90s) to keep one runaway scene from breaking
        # the composition pacing.
        duration = max(5, min(90, duration))
        scene = dict(raw)  # preserve unknown extras
        scene["narration_text"] = narration
        scene["visual_prompt"] = visual
        scene["duration_s_hint"] = duration
        cleaned.append(scene)
    return cleaned


class ScriptForVideoStage:
    """Stage 1 of the video pipeline — re-write into scene scripts."""

    name = "video.script"
    description = "Re-write the post into long-form + short-form video scripts"
    # Two LLM calls back to back. Generous budget for slow first-load
    # of the writer model on a cold pipeline.
    timeout_seconds = 300
    halts_on_failure = False  # downstream Stages handle partial scripts

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],  # noqa: ARG002 — Stage Protocol signature
    ) -> StageResult:
        title = (context.get("title") or "").strip()
        content_text = (context.get("content") or "").strip()
        if not title or not content_text:
            return StageResult(
                ok=False,
                detail="missing title or content — nothing to script",
                context_updates={"video_script": _default_script()},
                metrics={"skipped": True},
            )

        site_config = context.get("site_config")
        if site_config is None:
            return StageResult(
                ok=False,
                detail="site_config missing on context (Phase H DI seam)",
                context_updates={"video_script": _default_script()},
                metrics={"skipped": True},
            )

        # Lazy imports — the gpu lock + dispatcher both pull large
        # transitive deps that we don't want during pytest collection
        # of unrelated stages.
        from services.gpu_scheduler import gpu
        from services.llm_providers.dispatcher import (
            dispatch_complete,
            resolve_tier_model,
        )

        # Writer model preference (Glad-Labs/poindexter#407):
        # 1. Operator-pinned model via app_settings.cost_tier.standard.model
        #    — resolved through the cost-tier API so the writer pipeline
        #    routes through the dispatcher (litellm → Langfuse).
        # 2. Legacy per-call-site backstop: pipeline_writer_model or
        #    default_ollama_model. We honor these so existing operator
        #    overrides keep working.
        pool = getattr(site_config, "_pool", None)
        model = ""
        if pool is not None:
            try:
                model = await resolve_tier_model(pool, "standard")
            except (RuntimeError, ValueError) as tier_exc:
                logger.debug(
                    "[video.script] cost_tier.standard resolution failed "
                    "(%s); falling back to pipeline_writer_model",
                    tier_exc,
                )
        if not model:
            model = (
                site_config.get("pipeline_writer_model", "")
                or site_config.get("default_ollama_model", "auto")
            )
        if isinstance(model, str) and model.startswith("ollama/"):
            model = model.removeprefix("ollama/")

        clean_body = _strip_markdown(content_text)

        script = _default_script()
        long_form_ok = False
        short_form_ok = False
        details: list[str] = []

        # --- Long-form pass ---
        try:
            async with gpu.lock(
                "ollama",
                model=model,
                task_id=context.get("task_id"),
                phase="video.script.long",
            ):
                completion = await dispatch_complete(
                    pool=pool,
                    messages=[
                        {"role": "user", "content": _build_long_form_prompt(title, clean_body)},
                    ],
                    model=model,
                    tier="standard",
                    temperature=0.6,
                    max_tokens=2400,
                )
            payload = _extract_json(getattr(completion, "text", "") or "")
            if isinstance(payload, dict):
                script["long_form"]["intro_hook"] = str(payload.get("intro_hook") or "").strip()
                script["long_form"]["outro_cta"] = str(payload.get("outro_cta") or "").strip()
                script["long_form"]["scenes"] = _normalize_scenes(
                    payload.get("scenes"), _LONG_FORM_SCENE_SECONDS,
                )
                long_form_ok = bool(script["long_form"]["scenes"])
                details.append(
                    f"long_form: {len(script['long_form']['scenes'])} scenes",
                )
            else:
                details.append("long_form: model returned no parseable JSON")
        except Exception as exc:
            logger.warning("[video.script] long-form pass failed: %s", exc)
            details.append(f"long_form: {type(exc).__name__}")

        # --- Short-form pass ---
        try:
            async with gpu.lock(
                "ollama",
                model=model,
                task_id=context.get("task_id"),
                phase="video.script.short",
            ):
                completion = await dispatch_complete(
                    pool=pool,
                    messages=[
                        {"role": "user", "content": _build_short_form_prompt(title, clean_body)},
                    ],
                    model=model,
                    tier="standard",
                    temperature=0.7,  # slightly higher — hooks reward variety
                    max_tokens=1200,
                )
            payload = _extract_json(getattr(completion, "text", "") or "")
            if isinstance(payload, dict):
                script["short_form"]["intro_hook"] = str(payload.get("intro_hook") or "").strip()
                script["short_form"]["scenes"] = _normalize_scenes(
                    payload.get("scenes"), _SHORT_FORM_SCENE_SECONDS,
                )
                short_form_ok = bool(script["short_form"]["scenes"])
                details.append(
                    f"short_form: {len(script['short_form']['scenes'])} scenes",
                )
            else:
                details.append("short_form: model returned no parseable JSON")
        except Exception as exc:
            logger.warning("[video.script] short-form pass failed: %s", exc)
            details.append(f"short_form: {type(exc).__name__}")

        stages = context.setdefault("stages", {})
        # Stage is "ok" if EITHER pass produced a script — long form is
        # the primary goal; short-form failure is degraded but
        # recoverable in the per-format Stages.
        ok = long_form_ok or short_form_ok
        stages[self.name] = ok

        return StageResult(
            ok=ok,
            detail=" | ".join(details) or "no output produced",
            context_updates={
                "video_script": script,
                "stages": stages,
            },
            metrics={
                "long_form_scenes": len(script["long_form"]["scenes"]),
                "short_form_scenes": len(script["short_form"]["scenes"]),
                "long_form_ok": long_form_ok,
                "short_form_ok": short_form_ok,
                "writer_model": model,
            },
        )

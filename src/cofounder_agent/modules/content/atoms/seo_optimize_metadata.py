"""seo.optimize_metadata — query-aware re-optimization of an existing post's
title + meta description (the seo_refresh graph's optimizer).

Differs from seo.generate_all_metadata in three ways:
  1. Targets a specific GSC query (state['target_query']) when present, falling
     back to the post's topic/primary keyword when empty (Phase-1 page-level data).
  2. Optimizes for CTR on an ALREADY-RANKING page — preserve intent, sharpen the
     hook. The body (state['content']) is read-only here (meta_only scope).
  3. On LLM/parse failure it KEEPS the existing live meta rather than degrading to
     a programmatic guess — a failed refresh must never worsen a published post.

Exposes SeoMetadataOptimizer-style `optimize()` as a standalone callable so the
deferred generation-time rewire (design §2) can reuse one implementation.

Issue: Glad-Labs/poindexter#763 (epic #762).
"""
from __future__ import annotations

import json
import re
from typing import Any

from modules.content.atoms import _seo_common as sc
from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="seo.optimize_metadata",
    type="atom",
    version="1.0.0",
    description=(
        "Re-optimize an existing post's seo_title + seo_description toward a "
        "target GSC query for CTR. Keeps existing meta on LLM failure. "
        "Body is read-only (meta_only scope)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="existing body (read-only)"),
        FieldSpec(name="topic", type="str", description="fallback keyword when target_query empty"),
        FieldSpec(name="target_query", type="str", description="GSC query to optimize toward", required=False),
        FieldSpec(name="seo_title", type="str", description="current seo_title (kept on failure)", required=False),
        FieldSpec(name="seo_description", type="str", description="current meta (kept on failure)", required=False),
    ),
    outputs=(
        FieldSpec(name="seo_title", type="str", description="<=60 char optimized title"),
        FieldSpec(name="seo_description", type="str", description="<=160 char optimized meta"),
        FieldSpec(name="stages", type="dict", description="sets seo_metadata_optimized"),
    ),
    requires=("content",),
    produces=("seo_title", "seo_description", "stages"),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)


def _extract_json(text: str) -> dict[str, Any] | None:
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fence.group(1) if fence else text
    for chunk in (candidate, text):
        chunk = chunk.strip()
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            # Chunk isn't whole-string JSON — expected; fall through to the
            # brace-substring extraction below before trying the next chunk.
            pass
        brace = re.search(r"\{[\s\S]*\}", chunk)
        if brace:
            try:
                parsed = json.loads(brace.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                # Brace substring wasn't valid JSON either — expected; the loop
                # moves on to the next chunk (or returns None if exhausted).
                pass
    return None


async def optimize(state: dict[str, Any]) -> tuple[str, str]:
    """Optimizer core — returns (seo_title, seo_description). Reusable by both
    the seo_refresh graph and (deferred) the generation path. Raises on
    persistent LLM/parse failure; the atom wrapper catches + keeps existing meta.
    """
    target_query = (state.get("target_query") or "").strip()
    tags = state.get("tags") or []
    fallback_kw = (tags[0] if tags else state.get("topic")) or state.get("topic") or ""
    primary_keyword = target_query or fallback_kw

    raw = await sc.run_seo_llm(
        state,
        "atoms.seo.optimize_metadata",
        target_query=target_query,
        primary_keyword=primary_keyword,
        current_title=state.get("seo_title") or state.get("title") or "",
        current_description=state.get("seo_description") or "",
        content=sc.content_excerpt(state.get("content") or ""),
        max_attempts=ATOM_META.retry.max_attempts,
        backoff_s=ATOM_META.retry.backoff_s,
    )
    parsed = _extract_json(raw)
    if parsed is None:
        raise ValueError(f"seo.optimize_metadata: unparseable LLM output: {raw[:160]!r}")

    raw_title = str(parsed.get("title") or "").strip()
    raw_desc = str(parsed.get("description") or "").strip()
    title = sc.clean_title(raw_title, 60) if raw_title else (
        state.get("seo_title") or state.get("title") or ""
    )
    desc = sc.clamp_words(raw_desc, 160) if raw_desc else (state.get("seo_description") or "")
    return title, desc


async def run(state: dict[str, Any]) -> dict[str, Any]:
    stages = dict(state.get("stages") or {})
    if state.get("site_config") is None:
        return {}
    try:
        title, desc = await optimize(state)
    except Exception as exc:  # noqa: BLE001 — keep existing meta; never blank a live post
        sc.degraded("optimize_metadata", exc)
        title = state.get("seo_title") or state.get("title") or ""
        desc = state.get("seo_description") or ""
    stages["seo_metadata_optimized"] = True
    return {"seo_title": title, "seo_description": desc, "stages": stages}


__all__ = ["ATOM_META", "run", "optimize"]

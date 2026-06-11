"""seo.generate_all_metadata — single structured LLM call for all SEO metadata.

Replaces the three-atom serial chain (seo.generate_title → seo.generate_description
→ seo.extract_keywords) in the canonical_blog graph_def with one call that
returns ``{title, description, keywords}`` as a JSON object.

The three original atoms are retained as standalone importable units; this atom
simply collapses them into one LLM round-trip so the pipeline saves ~2 min/post.

Fallback behaviour mirrors the individual atoms: on LLM failure or a missing
JSON field, each field falls back to its programmatic derivation independently.
On JSON parse failure, ALL three fields fall back to programmatic derivations
(logged as a single degradation event).

Issue: Glad-Labs/poindexter#734.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from modules.content.atoms import _seo_common as sc
from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from utils.title_utils import derive_seo_title

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="seo.generate_all_metadata",
    type="atom",
    version="1.0.0",
    description=(
        "Single structured LLM call that produces seo_title + seo_description + "
        "seo_keywords in one round-trip, replacing the three serial seo.* atoms "
        "(saves ~2 min/post). Each field degrades independently to its programmatic "
        "fallback on parse or LLM failure."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="tags", type="list[str]", description="tags; tags[0] is the primary keyword", required=False),
        FieldSpec(name="seo_title", type="str", description="pre-existing seo_title if any (used as fallback input to description/keywords)", required=False),
    ),
    outputs=(
        FieldSpec(name="seo_title", type="str", description="<=60 char SEO title"),
        FieldSpec(name="seo_description", type="str", description="<=160 char meta description"),
        FieldSpec(name="seo_keywords", type="str", description="comma-joined keywords"),
        FieldSpec(name="seo_keywords_list", type="list[str]", description="structured keywords"),
        FieldSpec(name="stages", type="dict", description="sets 4_seo_metadata_generated"),
    ),
    requires=("content",),
    produces=("seo_title", "seo_description", "seo_keywords", "seo_keywords_list", "stages"),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)

_MIN_KEYWORDS = 3
_MAX_KEYWORDS = 10


def _parse_keywords(raw: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,\n]", raw or ""):
        kw = part.strip().lstrip("-*0123456789. ").strip().lower()
        if kw:
            out.append(kw)
    return out


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from ``text``, tolerating markdown code fences
    and leading/trailing prose the model may emit."""
    # Strip ```json ... ``` fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fence_match.group(1) if fence_match else text
    # Try direct parse first, then find the first {...} block
    for chunk in (candidate, text):
        chunk = chunk.strip()
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        brace_match = re.search(r"\{[\s\S]*\}", chunk)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    return None


def _build_keywords(raw_kw: str, state: dict[str, Any], seo_title: str) -> list[str]:
    """Parse + sanitise keywords from the structured response, backfilling
    from the frequency extractor to honour the 3-keyword floor."""
    content = (state.get("content") or "").lower()
    haystack = content + " " + seo_title.lower()
    seen: set[str] = set()
    kws: list[str] = []
    for kw in _parse_keywords(raw_kw):
        if kw in seen:
            continue
        if all(tok in haystack for tok in kw.split()):  # anti-hallucination
            seen.add(kw)
            kws.append(kw)
        if len(kws) >= _MAX_KEYWORDS:
            break
    if len(kws) < _MIN_KEYWORDS:
        for kw in sc.fallback_keywords(state, count=_MAX_KEYWORDS):
            k = kw.lower()
            if k not in seen:
                seen.add(k)
                kws.append(k)
            if len(kws) >= _MIN_KEYWORDS:
                break
    if not kws:
        kws = [k.lower() for k in sc.fallback_keywords(state)]
    return kws[:_MAX_KEYWORDS]


def _finish(title: str, description: str, keywords: list[str], state: dict[str, Any]) -> dict[str, Any]:
    stages = dict(state.get("stages") or {})
    stages["4_seo_metadata_generated"] = True
    return {
        "seo_title": title,
        "seo_description": description,
        "seo_keywords": ", ".join(keywords),
        "seo_keywords_list": keywords,
        "stages": stages,
    }


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    if not content or state.get("site_config") is None:
        return {}

    topic = state.get("topic") or ""
    tags = state.get("tags") or []
    primary_keyword = (tags[0] if tags else topic) or topic

    try:
        raw = await sc.run_seo_llm(
            state,
            "atoms.seo.generate_all_metadata",
            topic=topic,
            primary_keyword=primary_keyword,
            content=sc.content_excerpt(content),
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
    except Exception as exc:  # noqa: BLE001 — degrade all fields, never propagate
        sc.degraded("all_metadata", exc)
        title = sc.fallback_title(state)
        desc = sc.fallback_description(state)
        kws = [k.lower() for k in sc.fallback_keywords(state)]
        return _finish(title, desc, kws, state)

    parsed = _extract_json(raw)

    if parsed is None:
        logger.warning(
            "[seo.generate_all_metadata] could not parse JSON from LLM output; "
            "degrading all fields to programmatic fallbacks. raw=%r",
            raw[:200],
        )
        sc.degraded("all_metadata_json_parse", Exception("JSON parse failed"))
        title = sc.fallback_title(state)
        desc = sc.fallback_description(state)
        kws = [k.lower() for k in sc.fallback_keywords(state)]
        return _finish(title, desc, kws, state)

    # --- title ---
    raw_title = str(parsed.get("title") or "").strip()
    if raw_title:
        title = derive_seo_title(sc.clean_oneline(raw_title), max_len=60)
    else:
        title = sc.fallback_title(state)

    # --- description ---
    raw_desc = str(parsed.get("description") or "").strip()
    desc = sc.clamp_words(raw_desc, 160) if raw_desc else sc.fallback_description(state)

    # --- keywords ---
    raw_kw = str(parsed.get("keywords") or "").strip()
    kws = _build_keywords(raw_kw, state, title) if raw_kw else [k.lower() for k in sc.fallback_keywords(state)]

    return _finish(title, desc, kws, state)


__all__ = ["ATOM_META", "run"]

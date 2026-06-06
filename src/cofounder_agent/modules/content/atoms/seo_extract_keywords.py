"""seo.extract_keywords — LLM-proposed, content-grounded SEO keywords.

Replaces the old word-frequency extractor. The LLM proposes search-intent
keywords; a programmatic guard dedupes, lowercases, drops any keyword whose
tokens don't appear in content+title (anti-hallucination), caps at 10, and
backfills from the frequency extractor to a floor of 3. Degrades to pure
frequency extraction on LLM failure. Writes the final SEO-metadata flag.
Issue Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import re
from typing import Any

from modules.content.atoms import _seo_common as sc
from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="seo.extract_keywords",
    type="atom",
    version="1.0.0",
    description=(
        "LLM SEO keywords grounded in content (dedup/cap/anti-hallucination "
        "guard); degrades to frequency extraction on LLM failure."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="seo_title", type="str", description="title from seo.generate_title"),
        FieldSpec(name="topic", type="str", description="article topic", required=False),
    ),
    outputs=(
        FieldSpec(name="seo_keywords", type="str", description="comma-joined keywords"),
        FieldSpec(name="seo_keywords_list", type="list[str]", description="structured keywords"),
        FieldSpec(name="stages", type="dict", description="sets 4_seo_metadata_generated"),
    ),
    requires=("content", "seo_title"),
    produces=("seo_keywords", "seo_keywords_list", "stages"),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)

_MIN_KEYWORDS = 3
_MAX_KEYWORDS = 10


def _parse(raw: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,\n]", raw or ""):
        kw = part.strip().lstrip("-*0123456789. ").strip().lower()
        if kw:
            out.append(kw)
    return out


def _finish(state: dict[str, Any], kws: list[str]) -> dict[str, Any]:
    stages = dict(state.get("stages") or {})
    stages["4_seo_metadata_generated"] = True
    return {"seo_keywords": ", ".join(kws), "seo_keywords_list": kws, "stages": stages}


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    if not content or state.get("site_config") is None:
        return {}
    topic = state.get("topic") or ""
    seo_title = state.get("seo_title") or topic
    haystack = (content + " " + seo_title).lower()
    try:
        raw = await sc.run_seo_llm(
            state,
            "atoms.seo.extract_keywords",
            seo_title=seo_title,
            topic=topic,
            content=sc.content_excerpt(content),
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
        seen: set[str] = set()
        kws: list[str] = []
        for kw in _parse(raw):
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
    except Exception as exc:  # noqa: BLE001 — degrade, never propagate
        sc.degraded("keywords", exc)
        kws = [k.lower() for k in sc.fallback_keywords(state)]
    return _finish(state, kws[:_MAX_KEYWORDS])


__all__ = ["ATOM_META", "run"]

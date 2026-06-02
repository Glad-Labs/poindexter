"""seo.generate_description — LLM-written meta description (150-160 chars).

Reads the freshly-generated ``seo_title`` so the description complements the
title. Replaces the old stage's first-paragraph slice. Degrades to that slice
on persistent LLM failure. Issue Glad-Labs/poindexter#362.
"""
from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.atoms import _seo_common as sc

ATOM_META = AtomMeta(
    name="seo.generate_description",
    type="atom",
    version="1.0.0",
    description=(
        "LLM-written meta description (<=160 chars) coherent with seo_title; "
        "degrades to first-paragraph slice on LLM failure."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="seo_title", type="str", description="title from seo.generate_title"),
        FieldSpec(name="topic", type="str", description="article topic", required=False),
    ),
    outputs=(FieldSpec(name="seo_description", type="str", description="<=160 char meta description"),),
    requires=("content", "seo_title"),
    produces=("seo_description",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    if not content or state.get("site_config") is None:
        return {}
    topic = state.get("topic") or ""
    seo_title = state.get("seo_title") or topic
    try:
        raw = await sc.run_seo_llm(
            state,
            "atoms.seo.generate_description",
            seo_title=seo_title,
            topic=topic,
            content=sc.content_excerpt(content),
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
        desc = sc.clamp_words(raw, 160) if raw.strip() else ""
        if not desc:
            desc = sc.fallback_description(state)
    except Exception as exc:  # noqa: BLE001 — degrade, never propagate
        sc.degraded("description", exc)
        desc = sc.fallback_description(state)
    return {"seo_description": desc}


__all__ = ["ATOM_META", "run"]

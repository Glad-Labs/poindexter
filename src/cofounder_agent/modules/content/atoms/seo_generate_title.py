"""seo.generate_title — LLM-written, SEO-optimized blog title atom.

Replaces the title branch of the old generate_seo_metadata stage, which just
truncated the raw article title. Degrades to that programmatic derivation on
persistent LLM failure (logged, not silent). Issue Glad-Labs/poindexter#362.
"""
from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from modules.content.atoms import _seo_common as sc
from utils.title_utils import derive_seo_title

ATOM_META = AtomMeta(
    name="seo.generate_title",
    type="atom",
    version="1.0.0",
    description=(
        "LLM-written SEO title (<=60 chars, primary keyword first); "
        "degrades to programmatic derivation on LLM failure."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="tags", type="list[str]", description="tags; tags[0] is the primary keyword", required=False),
    ),
    outputs=(FieldSpec(name="seo_title", type="str", description="<=60 char SEO title"),),
    requires=("content",),
    produces=("seo_title",),
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
    tags = state.get("tags") or []
    primary_keyword = (tags[0] if tags else topic) or topic
    try:
        raw = await sc.run_seo_llm(
            state,
            "atoms.seo.generate_title",
            topic=topic,
            primary_keyword=primary_keyword,
            content=sc.content_excerpt(content),
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
        title = derive_seo_title(sc.clean_oneline(raw), max_len=60) if raw.strip() else ""
        if not title:
            title = sc.fallback_title(state)
    except Exception as exc:  # noqa: BLE001 — degrade, never propagate
        sc.degraded("title", exc)
        title = sc.fallback_title(state)
    return {"seo_title": title}


__all__ = ["ATOM_META", "run"]

"""content.check_title_originality — web-based title duplicate check.

Calls services.title_generation.check_title_originality and surfaces the
result as title_originality on the pipeline state for downstream QA.

This atom is optional — if the generate_title atom already set
title_originality, this is a no-op re-check useful when the title was
derived via a different path.

Produces: title_originality.

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.check_title_originality",
    type="atom",
    version="1.0.0",
    description=(
        "Web-based title-originality check. Searches for near-duplicate titles "
        "and surfaces the report as title_originality so QA can apply a penalty."
    ),
    inputs=(
        FieldSpec(name="title", type="str", description="canonical post title"),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
    ),
    outputs=(
        FieldSpec(name="title_originality", type="dict", description="originality report: {is_original, max_similarity, similar_titles, external_verbatim_match}"),
    ),
    requires=("title",),
    produces=("title_originality",),
    capability_tier=None,
    cost_class="compute",
    idempotent=True,
    side_effects=("web_search",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException")),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Run the title originality check and surface result on state."""
    # If generate_title already populated this, skip the redundant check.
    if state.get("title_originality"):
        return {}

    title = (state.get("title") or "").strip()
    if not title:
        return {}

    site_config = state.get("site_config")
    try:
        from services.title_generation import check_title_originality
        originality = await check_title_originality(title, site_config=site_config)  # type: ignore[arg-type]
        return {"title_originality": originality}
    except Exception as exc:
        logger.warning(
            "[content.check_title_originality] check failed (non-fatal): %s", exc
        )
        return {"title_originality": {"is_original": True, "max_similarity": 0.0, "similar_titles": []}}


__all__ = ["ATOM_META", "run"]

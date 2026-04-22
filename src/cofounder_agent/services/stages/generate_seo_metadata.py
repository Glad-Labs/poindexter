"""GenerateSeoMetadataStage — stage 4 of the content pipeline.

Generates SEO title / description / keywords from the finished draft.
Full port of ``_stage_generate_seo_metadata`` — no wrapper, small
enough to migrate cleanly.

## Context reads

- ``topic`` (str), ``tags`` (list[str]), ``content`` (str)

## Context writes

- ``seo_title`` (str, capped at 60 chars)
- ``seo_description`` (str, capped at 160 chars)
- ``seo_keywords`` (comma-separated string, for DB persistence)
- ``seo_keywords_list`` (list[str], for stages that prefer structured)
- ``stages["4_seo_metadata_generated"] = True``
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class GenerateSeoMetadataStage:
    name = "generate_seo_metadata"
    description = "Generate SEO title, description, and keywords from the draft"
    timeout_seconds = 120
    # Legacy raised ValueError on invalid result (critical).
    halts_on_failure = True

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.ai_content_generator import get_content_generator
        from services.seo_content_generator import get_seo_content_generator

        # Phase H step 4.3 (GH#95): read site_config from the pipeline
        # context instead of reaching for the module-level singleton.
        _sc = context.get("site_config")
        if _sc is None:
            # Transitional fallback — removed in Phase H step 5 when the
            # singleton is deleted. context always has site_config when
            # invoked through process_content_generation_task (which is
            # the only production caller). Tests that don't wire a
            # context["site_config"] still work via this fallback.
            from services.site_config import site_config as _sc

        topic = context.get("topic", "")
        tags = context.get("tags") or []
        content_text = context.get("content", "")

        if not content_text:
            return StageResult(
                ok=False,
                detail="context missing content (run generate_content first)",
            )

        logger.info("STAGE 4: Generating SEO metadata...")

        seo_generator = get_seo_content_generator(
            get_content_generator(), site_config=_sc,
        )
        seo_assets = seo_generator.metadata_gen.generate_seo_assets(
            title=topic, content=content_text, topic=topic,
        )

        if not seo_assets or not isinstance(seo_assets, dict):
            logger.error("SEO generation returned None or invalid format")
            raise ValueError("SEO metadata generation failed: invalid result")

        # Keywords → normalized list (dedupe, trim, cap at 10)
        raw_keywords = seo_assets.get("meta_keywords") or (tags or [])
        keywords = _normalize_keywords(raw_keywords)

        # Title: derive from the canonical title with word-boundary truncation
        # (GH-85). Legacy seo_title[:60] mid-word chop removed.
        from utils.title_utils import derive_seo_title
        canonical_title = (
            context.get("canonical_title")
            or context.get("title")
            or seo_assets.get("seo_title")
            or topic
        )
        seo_title = derive_seo_title(canonical_title, max_len=60)
        # Description still uses legacy slice but at 160 chars this is
        # less likely to mid-word-cut; tracked for later refinement.
        seo_description = (seo_assets.get("meta_description") or topic)[:160]

        stages = context.setdefault("stages", {})
        stages["4_seo_metadata_generated"] = True

        logger.info("SEO metadata generated:")
        logger.info("   Title: %s", seo_title)
        logger.info("   Description: %s...", seo_description[:80])
        logger.info("   Keywords: %s...", ", ".join(keywords[:5]))

        return StageResult(
            ok=True,
            detail=f"{len(keywords)} keywords",
            context_updates={
                "seo_title": seo_title,
                "seo_description": seo_description,
                "seo_keywords": ", ".join(keywords),
                "seo_keywords_list": keywords,
                "stages": stages,
            },
            metrics={
                "seo_title_length": len(seo_title),
                "seo_description_length": len(seo_description),
                "keyword_count": len(keywords),
            },
        )


def _normalize_keywords(raw: Any) -> list[str]:
    """Legacy keyword normalization: dedupe + trim + cap at 10."""
    if isinstance(raw, list):
        kws = [kw.strip() for kw in raw if isinstance(kw, str) and kw and kw.strip()]
        return kws[:10]
    if isinstance(raw, str):
        stripped = raw.strip()
        return [stripped][:10] if stripped else []
    return []

"""content.compile_meta — pure transforms + scoring before DB write.

Extracted from FinalizeTaskStage. No DB writes. Computes:
- append_sources_section (citation auto-append)
- generate_excerpt (deterministic)
- format_qa_feedback_from_reviews (approver UI)
- final_quality_score composite
- preview_token (reuse or mint)
- preview_url

Produces: content (with sources), excerpt, qa_feedback_formatted,
          quality_score, preview_token, preview_url.

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.compile_meta",
    type="atom",
    version="1.0.0",
    description=(
        "Pure transforms + scoring with no DB writes: sources-section append, "
        "excerpt derivation, QA-feedback formatting, quality-score composite, "
        "preview-token mint (or reuse)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="finalized body"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="seo_title", type="str", description="SEO title", required=False),
        FieldSpec(name="quality_result", type="object", description="QualityResult", required=False),
        FieldSpec(name="quality_score", type="float", description="QA final score", required=False),
        FieldSpec(name="qa_reviews", type="list", description="reviewer results", required=False),
        FieldSpec(name="qa_final_score", type="float", description="QA final score override", required=False),
        FieldSpec(name="qa_approved", type="bool", description="QA approval flag", required=False),
        FieldSpec(name="preview_token", type="str", description="existing token from verify_task", required=False),
        FieldSpec(name="platform", type="object", description="capability handle", required=False),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="body with sources section appended"),
        FieldSpec(name="excerpt", type="str", description="short excerpt for approver UI"),
        FieldSpec(name="qa_feedback_formatted", type="str", description="human-readable QA feedback"),
        FieldSpec(name="quality_score", type="float", description="final composite quality score"),
        FieldSpec(name="preview_token", type="str", description="hex token for /preview/{token}"),
        FieldSpec(name="preview_url", type="str", description="full preview URL"),
    ),
    requires=("content",),
    produces=("content", "excerpt", "qa_feedback_formatted", "quality_score", "preview_token", "preview_url"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Run all pure meta-compilation transforms."""
    from services.quality_models import ensure_quality_assessment
    from services.text_utils import normalize_text as _normalize_text

    content_text = state.get("content") or ""
    topic = state.get("topic", "")
    seo_title = state.get("seo_title") or ""
    platform = state.get("platform")

    # Normalize text fields.
    content_text = _normalize_text(content_text)
    if seo_title:
        seo_title = _normalize_text(seo_title)

    # Sources-section auto-append.
    try:
        from services.citation_verifier import append_sources_section, extract_urls
        _flag = (
            platform.config.get("auto_append_sources_section", "true")
            if platform is not None else "true"
        )
        if (_flag or "true").lower() not in ("false", "0", "no"):
            _site_url = (
                platform.config.get("site_url") if platform is not None else None
            ) or None
            _urls = extract_urls(content_text, site_url=_site_url)
            if _urls:
                content_text = append_sources_section(content_text, _urls)
    except Exception as _sources_err:
        logger.debug("[content.compile_meta] Sources-section append skipped: %s", _sources_err)

    # Excerpt derivation.
    from services.excerpt_generator import generate_excerpt
    excerpt_text = generate_excerpt(title=seo_title or topic, content=content_text)

    # QA feedback formatting.
    from modules.content.multi_model_qa import format_qa_feedback_from_reviews
    qa_reviews = state.get("qa_reviews") or []
    qa_feedback_text = ""
    if qa_reviews:
        qa_feedback_text = format_qa_feedback_from_reviews(
            qa_reviews,
            final_score=state.get("qa_final_score"),
            approved=state.get("qa_approved"),
        )

    # Quality score composite.
    quality_result = ensure_quality_assessment(state.get("quality_result"))
    qa_score_from_context = state.get("quality_score")
    early_eval_score = quality_result.overall_score if quality_result else 0
    final_quality_score = round(float(
        qa_score_from_context if qa_score_from_context is not None else early_eval_score
    ))

    # Preview token: reuse from verify_task when available (#563).
    preview_token = (state.get("preview_token") or "").strip() or secrets.token_hex(16)

    # Preview URL.
    preview_url = ""
    try:
        site_config = state.get("site_config")
        if site_config is not None:
            base = (site_config.get("preview_base_url") or "").rstrip("/")
        elif platform is not None:
            base = (platform.config.get("preview_base_url") or "").rstrip("/")
        else:
            base = ""
        if base:
            preview_url = f"{base}/preview/{preview_token}"
    except Exception:
        pass

    return {
        "content": content_text,
        "excerpt": excerpt_text,
        "qa_feedback_formatted": qa_feedback_text,
        "quality_score": final_quality_score,
        "preview_token": preview_token,
        "preview_url": preview_url,
    }


__all__ = ["ATOM_META", "run"]

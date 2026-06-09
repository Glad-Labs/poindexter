"""content.normalize_draft — pure text transforms on the draft body.

Applies normalize_text, scrub_fabricated_links, and
_strip_leaked_image_prompts to the draft. No LLM calls, no DB writes.
Pure function — safe to re-run at any point.

Produces: content (normalized in place).

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.normalize_draft",
    type="atom",
    version="1.0.0",
    description=(
        "Pure text transforms: normalize smart-quotes/special chars, scrub "
        "fabricated internal links, strip leaked image-prompt descriptions."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="raw draft body"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="normalized draft body"),
    ),
    requires=("content",),
    produces=("content",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


# ---------------------------------------------------------------------------
# Leaked-image-prompt patterns (mirror of generate_content._strip_leaked_image_prompts)
# ---------------------------------------------------------------------------

_LEAKED_IMAGE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r'(?m)^\s*\*(?:A |An |Imagine |Visual |Split|Close)[^*]{40,}\*\s*$'),
    re.compile(r'(?m)^\s*:\s*\*[A-Z][^*]{30,}\*\s*$'),
    re.compile(r'\[IMAGE(?:-\d+)?:\s*[^\]]+\]'),
    re.compile(r'\[FIGURE:\s*[^\]]+\]'),
)

_COLLAPSE_BLANK_LINES = re.compile(r'\n{3,}')


def strip_leaked_image_prompts(content: str) -> str:
    """Remove image-description placeholders the LLM may have emitted in prose."""
    out = content
    for pat in _LEAKED_IMAGE_PATTERNS:
        out = pat.sub('', out)
    return _COLLAPSE_BLANK_LINES.sub('\n\n', out)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Apply normalize_text + scrub_fabricated_links + strip_leaked_image_prompts."""
    content_text = (state.get("content") or "").strip()
    if not content_text:
        return {}

    from services.llm_providers.thinking_models import strip_reasoning_artifacts
    from services.text_utils import normalize_text, scrub_fabricated_links

    # Defense-in-depth: strip leaked reasoning / chat-template control tokens
    # (e.g. "<|channel>thought<channel|>…") from the persisted body. Production
    # writes also strip at the provider boundary, but BOTH writer paths (legacy
    # + two_pass) converge on this node, so it guarantees a clean body even for
    # a future path that bypasses the provider seam. Fence-aware + idempotent.
    content_text = strip_reasoning_artifacts(content_text)

    # Build real-slug allowlist from content_generator cache if available.
    real_slug_set: set[str] = set()
    try:
        content_generator = state.get("_content_generator")
        if content_generator is not None:
            links_cache = getattr(content_generator, "_internal_links_cache", [])
            for link_line in links_cache:
                if "/posts/" in link_line:
                    slug = link_line.split("/posts/")[-1].strip().strip('"')
                    if slug:
                        real_slug_set.add(slug)
    except Exception as exc:
        logger.debug(
            "[content.normalize_draft] failed to build slug allowlist: %s", exc
        )

    content_text = normalize_text(content_text)
    title = state.get("title", "")
    if title:
        title = normalize_text(title)
    content_text = scrub_fabricated_links(content_text, known_slugs=real_slug_set)
    content_text = strip_leaked_image_prompts(content_text)

    result: dict[str, Any] = {"content": content_text}
    if title and title != state.get("title"):
        result["title"] = title
    return result


__all__ = ["ATOM_META", "run", "strip_leaked_image_prompts"]

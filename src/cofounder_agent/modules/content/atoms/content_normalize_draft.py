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
    # [IMAGE:] / [IMAGE-N:] / [HERO-IMAGE:] are intentional writer markers now
    # (blog-generation SKILL.md) — plan_image_markers processes them and
    # inject_images cleans residuals. Only [FIGURE:] remains a genuine leak.
    re.compile(r'\[FIGURE:\s*[^\]]+\]'),
)

_COLLAPSE_BLANK_LINES = re.compile(r'\n{3,}')


def strip_leaked_image_prompts(content: str) -> str:
    """Remove image-description placeholders the LLM may have emitted in prose."""
    out = content
    for pat in _LEAKED_IMAGE_PATTERNS:
        out = pat.sub('', out)
    return _COLLAPSE_BLANK_LINES.sub('\n\n', out)


# A list item: optional indent, then a bullet (``*``/``+``/``-``) or an ordered
# marker (``1.``/``1)``), then at least one space and some content. The trailing
# ``[ \t]+\S`` requirement is deliberate — it skips thematic breaks (``---`` /
# ``***``), inline emphasis (``*word*``), and decimals (``3.5``) that would
# otherwise look like list markers.
_LIST_ITEM_RE = re.compile(r'^[ \t]*(?:[*+-]|\d+[.)])[ \t]+\S')


def ensure_blank_line_before_lists(content: str) -> str:
    """Insert the blank line a list block needs when a paragraph runs into it.

    Writers sometimes emit the intro line and the first bullet with no blank
    line between them (``... you:\\n* item``). CommonMark then renders the whole
    run as one paragraph with literal ``*`` markers instead of a list. This
    inserts the missing blank line before the first item of such a block. Items
    already preceded by a blank line, or following another list item, are left
    untouched, so the transform is idempotent.
    """
    lines = content.split('\n')
    out: list[str] = []
    for line in lines:
        if _LIST_ITEM_RE.match(line) and out:
            prev = out[-1]
            if prev.strip() != '' and not _LIST_ITEM_RE.match(prev):
                out.append('')
        out.append(line)
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Leaked planning-scaffold strip (#1963)
# ---------------------------------------------------------------------------
#
# Moved to ``atoms/_scaffold_helpers.py`` (2026-07-23): the qa.rewrite rescue
# path needs the same strip (its revision-briefing echo never re-enters this
# node — the loop resumes at qa.programmatic), and atoms must not import
# sibling atoms, so the logic lives in a shared ``_``-library now. The names
# below stay re-exported for existing imports/tests.
from modules.content.atoms._scaffold_helpers import (  # noqa: E402
    FIRST_HEADING_RE as _FIRST_HEADING_RE,
    SCAFFOLD_TELL_RE as _SCAFFOLD_TELL_RE,
    strip_leaked_planning_scaffold,
)


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

    # Strip a leaked planning/outline scaffold the writer emitted before the
    # article (#1963, prod task 0f70f736). Runs right after the control-token
    # strip so a scaffold that was itself wrapped in reasoning tokens is handled
    # too. Pure + idempotent; no-op when the body is clean.
    content_text = strip_leaked_planning_scaffold(content_text)

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
        # An EMPTY allowlist is not a soft degradation: it makes
        # scrub_fabricated_links (four lines below) maximally aggressive, so
        # every legitimate internal link in the post is treated as fabricated
        # and deleted. Non-paging info finding — the writer must never be
        # blocked by its own telemetry. Same fix as writer_core's
        # _build_real_slug_allowlist (OLD-debt batch 1); this is the atom-side
        # twin that runs on the graph_def path.
        from utils.findings import emit_finding

        logger.warning(
            "[content.normalize_draft] failed to build slug allowlist: %s", exc
        )
        emit_finding(
            source="content.normalize_draft",
            kind="slug_allowlist_build_failed",
            title="normalize_draft could not build the real-slug allowlist",
            body=(
                f"Reading the internal-links cache raised {type(exc).__name__}: "
                f"{exc}. The allowlist is empty, so scrub_fabricated_links will "
                f"treat every internal link in this post as fabricated and "
                f"remove it — including legitimate ones."
            ),
            dedup_key="normalize_draft_slug_allowlist_failed",
        )

    content_text = normalize_text(content_text)
    title = state.get("title", "")
    if title:
        title = normalize_text(title)
    content_text = scrub_fabricated_links(content_text, known_slugs=real_slug_set)
    content_text = strip_leaked_image_prompts(content_text)
    content_text = ensure_blank_line_before_lists(content_text)

    result: dict[str, Any] = {"content": content_text}
    if title and title != state.get("title"):
        result["title"] = title
    return result


__all__ = [
    "ATOM_META",
    "run",
    "strip_leaked_image_prompts",
    "ensure_blank_line_before_lists",
    "strip_leaked_planning_scaffold",
]

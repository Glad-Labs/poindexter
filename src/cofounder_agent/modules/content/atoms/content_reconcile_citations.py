"""content.reconcile_citations — deterministic citation repair (poindexter#765).

After the writer drafts, the research corpus (the name→URL pairs the writer was
handed) is still recoverable from ``state['research_context']``. The writer is
told to cite sources inline as markdown links but does so inconsistently —
naming a source in prose while dropping its URL (e.g. "GetMaxim points out…"
with no link, while "[arXiv](url)" right beside it is linked correctly).

This atom re-links those: at every *attribution site* whose subject matches a
corpus source by its distinctive domain/brand handle, it wraps the subject in a
markdown link to that source's exact URL. It is high-precision by construction —
it only edits text at attribution sites and only on a confident domain match,
so it never turns ordinary prose into links. Subjects with no confident corpus
match are left untouched; the advisory ``qa.unlinked_attribution`` rail flags
those for review (and a future grounded-LLM pass).

Placed after the writer block, BEFORE the QA rails, so the links it inserts flow
through ``qa.citations``' HTTP-HEAD dead-link check like any other citation.

Gated by ``app_settings.citation_reconcile_enabled`` (default true). No-op (and
returns nothing — the ``content`` channel keeps its prior value) when the flag
is off, there's no research corpus, or no attribution matches a corpus source.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.reconcile_citations",
    type="atom",
    version="1.0.0",
    description=(
        "Deterministic citation repair (#765): at each attribution site whose "
        "named subject matches a research-corpus source by domain handle, wrap "
        "the subject in a markdown link to that source's URL. No-op when "
        "citation_reconcile_enabled=false or no corpus match exists. "
        "High precision — only edits attribution sites, never plain prose."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body to repair"),
        FieldSpec(name="research_context", type="str", description="research corpus the writer used", required=False),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="body with corpus-matched attributions linked"),
    ),
    requires=("content",),
    produces=("content",),
    capability_tier=None,  # pure string ops — no LLM tier
    cost_class="free",
    idempotent=True,
    side_effects=(),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = state.get("content") or ""
    if not content.strip():
        return {}

    site_config = state.get("site_config")
    if site_config is not None:
        try:
            if not site_config.get_bool("citation_reconcile_enabled", True):
                return {}
        except Exception:  # noqa: BLE001 — config read must never break the pipeline
            pass

    research_context = state.get("research_context") or ""
    if not research_context.strip():
        return {}

    from modules.content.atoms._citation_match import (
        link_matched_attributions,
        parse_corpus,
    )

    sources = parse_corpus(research_context)
    if not sources:
        return {}

    new_content, linked = link_matched_attributions(content, sources)
    if not linked:
        return {}

    logger.info(
        "[content.reconcile_citations] linked %d corpus-matched attribution(s) "
        "(task=%s): %s",
        len(linked),
        str(state.get("task_id") or "?")[:8],
        ", ".join(f"{lk['subject']}→{lk['url']}" for lk in linked[:5]),
    )
    return {"content": new_content}


__all__ = ["ATOM_META", "run"]

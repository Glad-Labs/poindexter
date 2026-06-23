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

    # One guarded config read for all three flags — config reads must never
    # break the pipeline, so a single try/except covers the whole block.
    site_config = state.get("site_config")
    repoint_on = True
    strip_on = True
    mt_hosts: frozenset[str] | None = None
    if site_config is not None:
        try:
            if not site_config.get_bool("citation_reconcile_enabled", True):
                return {}
            repoint_on = site_config.get_bool("citation_repoint_enabled", True)
            strip_on = site_config.get_bool("citation_strip_unlinked_enabled", True)
            mt_list = site_config.get_list("citation_repoint_multitenant_hosts", "")
            mt_hosts = frozenset(h.lower() for h in mt_list) if mt_list else None
        except Exception:  # noqa: BLE001 — config read must never break the pipeline
            repoint_on, strip_on, mt_hosts = True, True, None

    research_context = state.get("research_context") or ""
    if not research_context.strip():
        return {}

    from modules.content.atoms._citation_match import (
        link_matched_attributions,
        parse_corpus,
        repoint_fabricated_citations,
        strip_unmatched_attributions,
    )

    sources = parse_corpus(research_context)
    if not sources:
        return {}

    # Scan-1: link UNLINKED attribution subjects that match a corpus source.
    new_content, linked = link_matched_attributions(content, sources)

    # Scan-3: re-point ALREADY-linked citations whose URL is a writer-invented
    # path on a single-brand corpus domain to that source's real URL — fixing a
    # 404 the trusted-host scrub keeps before qa.citations flags it dead.
    # Multi-tenant platforms (github/arxiv/dev.to/…) are excluded by construction.
    repointed: list[dict] = []
    if repoint_on:
        new_content, repointed = repoint_fabricated_citations(new_content, sources, mt_hosts)

    # Scan-4: STRIP residual unlinked attributions whose subject grounds to no
    # corpus source — remove the dangling attribution frame, keep the claim
    # (Matt 2026-06-23: strip rather than negatively prompt the writer). Runs
    # last, so scan-1/scan-3 link or re-point everything groundable first and
    # only the truly unmatched remain. The advisory ``qa.unlinked_attribution``
    # rail then flags whatever the strip frames were too conservative to remove.
    stripped: list[str] = []
    if strip_on:
        new_content, stripped = strip_unmatched_attributions(new_content, sources)

    if not linked and not repointed and not stripped:
        return {}

    if linked:
        logger.info(
            "[content.reconcile_citations] linked %d corpus-matched attribution(s) "
            "(task=%s): %s",
            len(linked),
            str(state.get("task_id") or "?")[:8],
            ", ".join(f"{lk['subject']}→{lk['url']}" for lk in linked[:5]),
        )
    if repointed:
        logger.info(
            "[content.reconcile_citations] re-pointed %d fabricated brand-domain "
            "citation(s) (task=%s): %s",
            len(repointed),
            str(state.get("task_id") or "?")[:8],
            ", ".join(f"{rp['text']}: {rp['old']}→{rp['new']}" for rp in repointed[:5]),
        )
    if stripped:
        logger.info(
            "[content.reconcile_citations] stripped %d ungroundable attribution(s) "
            "(task=%s): %s",
            len(stripped),
            str(state.get("task_id") or "?")[:8],
            ", ".join(stripped[:5]),
        )
    return {"content": new_content}


__all__ = ["ATOM_META", "run"]

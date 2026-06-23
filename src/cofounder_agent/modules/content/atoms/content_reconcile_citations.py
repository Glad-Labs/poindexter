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
        "Deterministic citation repair (#765): link corpus-matched attribution "
        "subjects, re-point fabricated brand-domain paths, strip ungroundable "
        "attributions, and give YouTube references proper [Channel](url) "
        "attribution (via the oEmbed endpoint). No-op when "
        "citation_reconcile_enabled=false. High precision — only edits "
        "attribution sites + YouTube links, never plain prose."
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
    capability_tier=None,  # deterministic string ops — no LLM tier
    cost_class="free",
    idempotent=True,
    # The YouTube pass calls youtube.com/oembed to resolve channel names
    # (fail-soft, read-only); excluded from contract_fingerprint by design.
    side_effects=("calls the YouTube oEmbed endpoint to resolve channel names",),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = state.get("content") or ""
    if not content.strip():
        return {}

    # One guarded config read for all flags — config reads must never break the
    # pipeline, so a single try/except covers the whole block.
    site_config = state.get("site_config")
    repoint_on = True
    strip_on = True
    youtube_on = True
    mt_hosts: frozenset[str] | None = None
    if site_config is not None:
        try:
            if not site_config.get_bool("citation_reconcile_enabled", True):
                return {}
            repoint_on = site_config.get_bool("citation_repoint_enabled", True)
            strip_on = site_config.get_bool("citation_strip_unlinked_enabled", True)
            youtube_on = site_config.get_bool("youtube_attribution_enabled", True)
            mt_list = site_config.get_list("citation_repoint_multitenant_hosts", "")
            mt_hosts = frozenset(h.lower() for h in mt_list) if mt_list else None
        except Exception:  # noqa: BLE001 — config read must never break the pipeline
            repoint_on, strip_on, youtube_on, mt_hosts = True, True, True, None

    new_content = content

    # Scan-5: YouTube attribution — corpus-INDEPENDENT (resolves channel names
    # via the YouTube oEmbed endpoint, not the research corpus), so it runs even
    # when the writer had no research. Turns a bare YouTube URL into a proper
    # ``[Channel](url)`` attribution and relabels a raw-text YouTube link
    # (Matt 2026-06-23: "proper attribution and not a raw youtube url").
    yt_changes: list[dict] = []
    if youtube_on:
        new_content, yt_changes = await _attribute_youtube(new_content, site_config)

    # Scans 1/3/4 are corpus-based — they need the research corpus the writer was
    # handed. Skip them (but keep any YouTube edit) when there's no corpus.
    linked: list[dict] = []
    repointed: list[dict] = []
    stripped: list[str] = []
    research_context = state.get("research_context") or ""
    if research_context.strip():
        from modules.content.atoms._citation_match import (
            link_matched_attributions,
            parse_corpus,
            repoint_fabricated_citations,
            strip_unmatched_attributions,
        )

        sources = parse_corpus(research_context)
        if sources:
            # Scan-1: link UNLINKED attribution subjects matching a corpus source.
            new_content, linked = link_matched_attributions(new_content, sources)

            # Scan-3: re-point ALREADY-linked citations whose URL is a writer-
            # invented path on a single-brand corpus domain to that source's real
            # URL — fixing a 404 the trusted-host scrub keeps before qa.citations
            # flags it dead. Multi-tenant platforms are excluded by construction.
            if repoint_on:
                new_content, repointed = repoint_fabricated_citations(
                    new_content, sources, mt_hosts,
                )

            # Scan-4: STRIP residual unlinked attributions whose subject grounds
            # to no corpus source — remove the dangling frame, keep the claim
            # (strip rather than negatively prompt the writer). Runs last, so
            # scan-1/scan-3 link or re-point everything groundable first; the
            # advisory ``qa.unlinked_attribution`` rail flags the rest.
            if strip_on:
                new_content, stripped = strip_unmatched_attributions(new_content, sources)

    if not linked and not repointed and not stripped and not yt_changes:
        return {}

    if yt_changes:
        logger.info(
            "[content.reconcile_citations] attributed %d YouTube link(s) "
            "(task=%s): %s",
            len(yt_changes),
            str(state.get("task_id") or "?")[:8],
            ", ".join(f"{c['kind']}:{c['author']}" for c in yt_changes[:5]),
        )
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


async def _attribute_youtube(
    content: str, site_config: Any,
) -> tuple[str, list[dict]]:
    """Rewrite YouTube references for proper attribution. No-op when there are
    no YouTube URLs or none resolve to a channel (fail-soft)."""
    from modules.content.atoms._youtube_attribution import (
        apply_youtube_attribution,
        find_youtube_urls,
    )

    urls = find_youtube_urls(content)
    if not urls:
        return content, []
    authors = await _resolve_youtube_authors(urls, site_config)
    if not authors:
        return content, []
    return apply_youtube_attribution(content, authors)


async def _resolve_youtube_authors(
    urls: list[str], site_config: Any,
) -> dict[str, str]:
    """Resolve ``{url: channel_name}`` via the YouTube oEmbed endpoint.

    Fail-soft and deterministic: each URL that errors / 404s (a private, dead,
    or fabricated video) is simply omitted, so its citation is left untouched
    rather than mangled. ``author_name`` is YouTube's authoritative channel name.
    """
    import httpx

    timeout = 8.0
    if site_config is not None:
        try:
            timeout = float(site_config.get_int("youtube_oembed_timeout_seconds", 8))
        except Exception:  # noqa: BLE001 — config read must never break the pipeline
            timeout = 8.0

    authors: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=4.0),
        ) as client:
            for url in urls:
                try:
                    resp = await client.get(
                        "https://www.youtube.com/oembed",
                        params={"url": url, "format": "json"},
                    )
                    if resp.status_code == 200:
                        author = (resp.json().get("author_name") or "").strip()
                        if author:
                            authors[url] = author
                except Exception:  # noqa: BLE001 — fail-soft per URL
                    continue
    except Exception:  # noqa: BLE001 — never break the pipeline on transport setup
        return {}
    return authors


__all__ = ["ATOM_META", "run"]

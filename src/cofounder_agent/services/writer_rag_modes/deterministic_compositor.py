"""DETERMINISTIC_COMPOSITOR writer mode — bundle-grounded narrative + deterministic links.

Renders the dev_diary post as a hybrid:

- **Header** (deterministic): ``# What we shipped — YYYY-MM-DD``
- **Narrative** (LLM): 2-3 short paragraphs covering what / how / why,
  produced by a tightly-scoped LLM call. The model sees ONLY the bundle
  text as input — no topic title, no angle/tone hints, no embedding
  snippets — and is held to "summarize this bundle, do not invent
  anything outside it." Compared to the legacy writer pipeline this
  drops the four biggest hallucination sources (generic blog wrapper,
  tutorial-style snippets, first_person tone hint, unrestricted topic
  string).
- **Links** (deterministic): bullet list of every merged PR + notable
  commit with real GitHub URLs. Always present. The reader has full
  ground-truth references regardless of what the narrative paragraph
  said.
- **Footer** (deterministic): "_Auto-compiled by Poindexter…_"

If the LLM call fails or returns empty, we fall back to a plain
"We shipped N PRs and M commits today." sentence — the deterministic
links list is unaffected. If the bundle itself is empty (quiet day),
the entire body is a single "Quiet day — no shipped work to report."
line; same footer.

Bundle schema (from services.topic_sources.dev_diary_source.DevDiaryContext):

    {
      "date": "YYYY-MM-DD",
      "merged_prs": [{"number", "title", "url", "merged_at", "author", "body"}],
      "notable_commits": [{"sha", "subject", "prefix", "author", "date"}],
      "brain_decisions": [...],
      "audit_resolved": [...],
      "recent_posts": [...],
      "cost_summary": {...},
    }
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from services.prompt_manager import get_prompt_manager

logger = logging.getLogger(__name__)


_REPO_BASE = "https://github.com/Glad-Labs/poindexter"
_FOOTER = "_Auto-compiled by Poindexter from today's commits and PRs._"


def _format_bundle_for_narrative(bundle: dict[str, Any]) -> str:
    """Render the bundle as plain text for the narrative LLM input.

    Drops everything but PR titles + bodies + commits. Strips
    auto-generated PR footers (the "Generated with Claude Code"
    marker) so the model doesn't mistake them for content. Caps each
    PR body at 1200 chars and the whole input at ~12K chars to keep
    the prompt within budget on busy days.
    """
    prs = bundle.get("merged_prs") or []
    commits = bundle.get("notable_commits") or []

    lines: list[str] = []
    lines.append(f"DATE: {bundle.get('date') or 'today'}")
    lines.append("")
    if prs:
        lines.append(f"MERGED PRs ({len(prs)}):")
        lines.append("")
        for p in prs[:30]:
            num = p.get("number") or "?"
            title = (p.get("title") or "").strip()
            body = (p.get("body") or "").strip()
            # Strip Claude Code footer + co-author trailers.
            for marker in (
                "🤖 Generated with",
                "Co-Authored-By:",
                "Generated with [Claude",
                "Generated-by: Claude",
            ):
                if marker in body:
                    body = body.split(marker, 1)[0].rstrip()
            body = body[:1200]
            lines.append(f"PR #{num}: {title}")
            if body:
                for body_line in body.splitlines():
                    lines.append(f"  {body_line}")
            lines.append("")
    if commits:
        lines.append(f"NOTABLE COMMITS ({len(commits)}):")
        lines.append("")
        for c in commits[:30]:
            sha = (c.get("sha") or "").strip()
            subject = (c.get("subject") or "").strip()
            if sha and subject:
                lines.append(f"- {sha}: {subject}")
        lines.append("")
    text = "\n".join(lines)
    # Hard cap the whole bundle to keep prompts reasonable.
    return text[:12000]


def _format_links_section(bundle: dict[str, Any]) -> str:
    """Render the deterministic links list (PRs + commits, with URLs)."""
    prs = bundle.get("merged_prs") or []
    commits = bundle.get("notable_commits") or []
    if not prs and not commits:
        return ""

    lines: list[str] = ["## PRs and commits", ""]
    for pr in prs:
        number = pr.get("number")
        title = (pr.get("title") or "").strip() or f"PR #{number}"
        url = (pr.get("url") or "").strip() or f"{_REPO_BASE}/pull/{number}"
        author = (pr.get("author") or "").strip()
        author_suffix = f" — {author}" if author else ""
        lines.append(f"- [PR #{number}]({url}) {title}{author_suffix}")

    if commits:
        lines.append("")
        lines.append("**Other commits:**")
        for c in commits:
            sha = (c.get("sha") or "").strip()
            subject = (c.get("subject") or "").strip()
            if sha and subject:
                commit_url = f"{_REPO_BASE}/commit/{sha}"
                lines.append(f"- [`{sha}`]({commit_url}) {subject}")
    lines.append("")
    return "\n".join(lines)


def _fallback_narrative(bundle: dict[str, Any]) -> str:
    """One-line summary used when the LLM narrative call fails."""
    prs = bundle.get("merged_prs") or []
    commits = bundle.get("notable_commits") or []
    pr_n = len(prs)
    commit_n = len(commits)
    parts: list[str] = []
    if pr_n:
        parts.append(f"{pr_n} PR{'s' if pr_n != 1 else ''}")
    if commit_n:
        parts.append(f"{commit_n} notable commit{'s' if commit_n != 1 else ''}")
    if not parts:
        return ""
    return f"The team shipped {' and '.join(parts)} today. See the full list below."


# 2026-05-16: the private ``_ollama_chat_text`` was deleted in favor of
# :func:`services.llm_text.ollama_chat_text`. The shared helper routes
# through the LLM provider dispatcher when a pool is available — making
# this writer mode honor ``plugin.llm_provider.primary.standard='litellm'``
# instead of bypassing the router with a direct httpx call to Ollama.
# The module-level alias keeps test patches at the historical name working.
from services.llm_text import ollama_chat_text as _ollama_chat_text


async def _generate_narrative(
    bundle: dict[str, Any], *, site_config: Any = None, pool: Any = None,
) -> str:
    """Call the writer LLM with a tight bundle-only prompt to produce
    the 2-3 paragraph narrative. Returns "" on failure — caller falls
    back to the deterministic one-liner.
    """
    try:
        # 2026-05-12 (poindexter#485): replaced 3 hardcoded glm-4.7-5090
        # fallbacks with the shared resolver. See batch 6 (PR #392).
        from services.llm_text import resolve_local_model
        model = resolve_local_model(site_config=site_config)

        bundle_text = _format_bundle_for_narrative(bundle)
        if not bundle_text.strip():
            return ""

        narrative_system_prompt = get_prompt_manager().get_prompt("narrative.system")
        full_prompt = (
            f"{narrative_system_prompt}\n\n"
            f"---\n\n"
            f"BUNDLE:\n\n{bundle_text}\n\n"
            f"---\n\n"
            f"Now produce concise prose summarizing what shipped, how, "
            f"and why. The post is as long as the work warrants — short "
            f"days produce short posts, busy days produce longer arcs. "
            f"Plain markdown prose. Output starts with the first letter "
            f"of paragraph one and ends with the last letter of the "
            f"closing paragraph."
        )

        # ``ollama_chat_text`` already runs ``maybe_unwrap_json`` on
        # the result, so the JSON-envelope defense the inline helper
        # used to do here is now centralized in the shared module.
        result = await _ollama_chat_text(
            full_prompt,
            model=model,
            site_config=site_config,
            pool=pool,
            timeout_setting="niche_ollama_chat_timeout_seconds",
        )
        prose = (result or "").strip()
        return prose
    except Exception as exc:
        logger.warning(
            "[DETERMINISTIC_COMPOSITOR] narrative LLM call failed: %s — "
            "falling back to one-line summary",
            exc,
        )
        return ""


async def compose_post(
    bundle: dict[str, Any], *, site_config: Any = None, pool: Any = None,
) -> str:
    """Render the dev_diary bundle as a Markdown post.

    Hybrid: deterministic header + LLM narrative + deterministic links
    list + deterministic footer. The LLM call sees only the bundle.

    ``pool`` is threaded through so the inner ``ollama_chat_text`` call
    routes via the dispatcher (LiteLLM / Ollama / OpenAI-compat per
    ``plugin.llm_provider.primary.standard``). When called without a
    pool (tests / bootstrap), the helper falls back to direct httpx.
    """
    date = (bundle.get("date") or "").strip() or "today"
    prs = bundle.get("merged_prs") or []
    commits = bundle.get("notable_commits") or []

    if not prs and not commits:
        return (
            f"# What we shipped — {date}\n\n"
            f"Quiet day — no shipped work to report.\n\n"
            f"{_FOOTER}\n"
        )

    narrative = await _generate_narrative(bundle, site_config=site_config, pool=pool)
    if not narrative:
        narrative = _fallback_narrative(bundle)

    parts: list[str] = []
    parts.append(f"# What we shipped — {date}")
    parts.append("")
    if narrative:
        parts.append(narrative)
        parts.append("")
    links = _format_links_section(bundle)
    if links:
        parts.append(links)
    parts.append(_FOOTER)
    parts.append("")

    return "\n".join(parts)


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool, **kw: Any) -> dict[str, Any]:
    """Writer-mode entrypoint. Renders the bundle as header + LLM
    narrative + deterministic links + footer.

    Matches the writer_rag_modes Protocol — accepts the standard
    dispatcher kwargs plus ``context_bundle`` (dev_diary) and returns
    ``{"draft": str, "mode": str}``. Ignores ``topic``/``angle`` because
    the bundle's date + PR list are the structural source of truth.
    """
    bundle = kw.get("context_bundle") or {}
    if isinstance(bundle, str):
        import json
        try:
            bundle = json.loads(bundle)
        except (ValueError, TypeError):
            bundle = {}

    if not isinstance(bundle, dict) or not bundle:
        logger.warning(
            "[DETERMINISTIC_COMPOSITOR] empty/missing context_bundle for %s — "
            "returning quiet-day post",
            niche_id,
        )
        bundle = {"date": "today", "merged_prs": [], "notable_commits": []}

    # Thread pool through so the inner LLM call goes via the
    # dispatcher (LiteLLM / Ollama / OpenAI-compat per app_settings).
    draft = await compose_post(
        bundle, site_config=kw.get("site_config"), pool=pool,
    )
    return {
        "draft": draft,
        "mode": "DETERMINISTIC_COMPOSITOR",
        "snippets_used": [],
        "external_lookups": [],
        "revision_loops": 0,
        "loop_capped": False,
    }

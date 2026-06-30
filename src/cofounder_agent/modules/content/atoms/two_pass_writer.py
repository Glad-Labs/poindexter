"""``atoms.two_pass_writer`` — internal-first draft, then conditional
external fact-augmentation loop.

The canonical_blog template's writer atom. dev_diary uses the simpler
``atoms.narrate_bundle`` (no RAG, single-call narrative); this atom
handles the RAG-grounded long-form path for the ``glad-labs`` niche
and any future niche that drafts from embeddings + optional external
research.

Implemented as a LangGraph state machine because the writer needs:

- Multi-pass with conditional re-entry (revise can surface new
  ``[EXTERNAL_NEEDED]`` markers that need another research pass)
- Bounded loop (``_MAX_REVISION_LOOPS=3`` prevents runaway)
- Future-friendly: when we add an auto-researcher agent or a
  draft-critic loop, they slot in as new nodes/edges rather than
  refactoring orchestration

History: lived at ``services/writer_rag_modes/two_pass.py`` until
2026-05-28, when the parent ``writer_rag_modes/`` namespace was
retired with its 4 dead siblings (TOPIC_ONLY / CITATION_BUDGET /
STORY_SPINE / DETERMINISTIC_COMPOSITOR). The dispatcher-with-one-
mode was the obvious drift signal — once TWO_PASS was the only
mode left, the dispatcher was pure ceremony.

State flow:

    embed_and_fetch → draft → detect_needs ┐
                                            │ if needs found and loops < max:
                                            ↓
                                  research_each → revise ─┐
                                                           │
                                                           ↓
                                                  detect_needs (loop)
                                                           │
                                                           ↓ if no needs OR loops capped
                                                          END

Deviations from plan:

- Imports `embed_text` from `services.topic_ranking` instead of
  `services.embedding_service` because the embedding_service module has
  no module-level `embed_text` helper — that helper lives in topic_ranking.
- Stores the asyncpg pool in a module-level `_POOL_REGISTRY` keyed by
  thread_id rather than directly in graph state. The MemorySaver
  checkpointer msgpack-serializes state on every step; an asyncpg.Pool
  (or a MagicMock thereof) is not serializable and recurses through
  attribute access. Holding the pool out-of-band keeps state msgpack-clean
  while preserving the same per-thread isolation the plan called for.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from services.logger_config import get_logger

# Module-level keyed-by-thread store for non-serializable state (the asyncpg
# pool). The MemorySaver checkpointer serializes state via msgpack, and an
# asyncpg.Pool is not msgpack-able (and a MagicMock pool in tests recurses
# forever during attribute introspection). Holding the pool out-of-band lets
# checkpointing serialize only the data it needs (snippets, drafts, needs).
_POOL_REGISTRY: dict[str, Any] = {}
# DI seam (glad-labs-stack#330) — same pattern as _POOL_REGISTRY: the
# dispatcher seeds the SiteConfig keyed by thread_id, nodes look it up
# from state["pool_thread"]. Site_config can't ride in the LangGraph
# state because it's not msgpack-serializable for the checkpointer.
_SITE_CONFIG_REGISTRY: dict[str, Any] = {}
# Phase 1 lab harness — per-task variant writer-model override.
# Strings are msgpack-friendly, so technically this could ride on
# state, but keeping all "things the caller injects per task" in
# parallel module-level registries keeps the lookup pattern uniform
# (and means the experiment harness doesn't widen the TypedDict).
_MODEL_OVERRIDE_REGISTRY: dict[str, str] = {}

logger = get_logger(__name__)


# Private repo URL scrub — defense in depth, mirrors the same set used
# by ``atoms.narrate_bundle`` (PR #680). Glad-Labs/glad-labs-stack is
# the private operator repo; only Glad-Labs/poindexter is public. The
# two_pass writer is grounded by embedding snippets + external web
# research — neither source feeds private-repo URLs into the prompt,
# but the model can still echo a URL from training data. This scrub
# runs on every returned draft before the caller persists it.
_PRIVATE_REPO_PULL_INLINE = re.compile(
    r"\[([^]]+)\]\(https?://github\.com/Glad-Labs/glad-labs-stack/pull/(\d+)\)"
)
_PRIVATE_REPO_COMMIT_INLINE = re.compile(
    r"\[([^]]+)\]\(https?://github\.com/Glad-Labs/glad-labs-stack/commit/"
    r"([0-9a-fA-F]{7})[0-9a-fA-F]*\)"
)
_PRIVATE_REPO_PULL_AUTOLINK = re.compile(
    r"<https?://github\.com/Glad-Labs/glad-labs-stack/pull/(\d+)>"
)
_PRIVATE_REPO_COMMIT_AUTOLINK = re.compile(
    r"<https?://github\.com/Glad-Labs/glad-labs-stack/commit/"
    r"([0-9a-fA-F]{7})[0-9a-fA-F]*>"
)
_PRIVATE_REPO_PULL_BARE = re.compile(
    r"https?://github\.com/Glad-Labs/glad-labs-stack/pull/(\d+)"
)
_PRIVATE_REPO_COMMIT_BARE = re.compile(
    r"https?://github\.com/Glad-Labs/glad-labs-stack/commit/"
    r"([0-9a-fA-F]{7})[0-9a-fA-F]*"
)
_PRIVATE_REPO_MENTION = re.compile(r"\bGlad-Labs/glad-labs-stack\b")


def _scrub_private_repo_refs(text: str) -> str:
    """Strip private-repo URLs from generated content (defense in depth)."""
    if not text:
        return text
    text = _PRIVATE_REPO_PULL_INLINE.sub(r"\1 (PR #\2)", text)
    text = _PRIVATE_REPO_COMMIT_INLINE.sub(r"\1 (`\2`)", text)
    text = _PRIVATE_REPO_PULL_AUTOLINK.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_AUTOLINK.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_PULL_BARE.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_BARE.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_MENTION.sub("Glad-Labs/poindexter", text)
    return text


# Prompt key in UnifiedPromptManager + YAML registry. YAML default at
# prompts/writer_rag_modes.yaml; Langfuse overrides take effect on the
# next get_prompt call. Per feedback_prompts_must_be_db_configurable.
_REVISE_PROMPT_KEY = "atoms.two_pass_writer.revise_prompt"


def _resolve_revise_prompt(
    *, draft: str, aug_block: str,
) -> tuple[str, str | None, int | None]:
    """Pull the TWO_PASS revise prompt + provenance metadata.

    Returns ``(prompt_text, prompt_template_key, prompt_template_version)``.
    Provenance feeds the lab's ``capability_outcomes.prompt_template_*``
    columns (Phase 0, 2026-05-28); ``(text, None, None)`` on the inline
    fallback path so the lab can see "no resolved prompt — fallback
    fired" instead of false-attributing the run to the key.

    Langfuse > YAML defaults > inline fallback. The inline constant only
    fires when the prompt registry hasn't been initialized (bootstrap /
    test paths). Production reads from YAML at minimum.
    """
    try:
        from services.prompt_manager import get_prompt_manager
        resolution = get_prompt_manager().get_prompt_resolution(
            _REVISE_PROMPT_KEY, draft=draft, aug_block=aug_block,
        )
        return resolution.text, resolution.key, resolution.version
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[writer_rag_modes.two_pass] prompt_manager lookup for %r "
            "failed (%s) — using inline fallback",
            _REVISE_PROMPT_KEY, exc,
        )
        return (
            _REVISE_PROMPT_FALLBACK.format(draft=draft, aug_block=aug_block),
            None,
            None,
        )


# Inline fallback — last-resort for bootstrap / test / registry-unreachable
# paths. Canonical prompt lives in prompts/writer_rag_modes.yaml.
_REVISE_PROMPT_FALLBACK = """\
Revise the following draft. For each [EXTERNAL_NEEDED: ...] marker, substitute
the corresponding external fact below and link it inline to its source URL.
Leave all other content as-is.

Return the COMPLETE revised post exactly once. Do not repeat, duplicate, or
append a second copy of any section, and do not pad the length — the revision
should be about as long as the original and end on a complete sentence.

If revision exposes a new claim that needs outside support, mark it
[EXTERNAL_NEEDED: ...] again so the next pass can fill it.

Original draft:
{draft}

External facts:
{aug_block}
"""


# Prompt key + inline fallback for the keep-best expansion pass (length
# enforcement). Same resolution chain as the revise prompt: Langfuse > YAML
# (SKILL.md) > inline fallback. Canonical default lives in
# skills/content/two-pass-writer/SKILL.md. Keep this text byte-identical to
# that section — the snapshot test pins registry == inline.
_EXPAND_PROMPT_KEY = "atoms.two_pass_writer.expand_prompt"

_EXPAND_PROMPT_FALLBACK = """\
The draft below is about {word_count} words, but this post should be closer to
{target_length} words. Expand it with genuine added substance — more concrete
detail, worked examples, and reasoning grounded in the existing content. Do NOT
pad, repeat, restate points already made, or add filler to reach a number; if a
section is already complete, leave it untouched.

Preserve every existing fact, link, heading, and the original voice. Return the
COMPLETE expanded post once, in Markdown, ending on a complete sentence — no
preamble and no notes about what you changed.

Draft:
{draft}
"""


def _resolve_expand_prompt(
    *, draft: str, target_length: int, word_count: int,
) -> str:
    """Resolve the expansion prompt (Langfuse > SKILL.md > inline fallback).

    Mirrors :func:`_resolve_revise_prompt`. The inline fallback only fires when
    the prompt registry is unreachable (bootstrap / test paths); production
    reads from the SKILL.md default at minimum.
    """
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _EXPAND_PROMPT_KEY,
            draft=draft,
            target_length=target_length,
            word_count=word_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[two_pass_writer] prompt_manager lookup for %r failed (%s) — "
            "using inline fallback", _EXPAND_PROMPT_KEY, exc,
        )
        return _EXPAND_PROMPT_FALLBACK.format(
            draft=draft, target_length=target_length, word_count=word_count,
        )


_NEED_PATTERN = re.compile(r"\[EXTERNAL_NEEDED:\s*([^\]]+)\]")

# Hard fallback used when ``writer_rag_two_pass_max_revision_loops`` is
# unset (test fixtures that don't seed site_config). Operators tune the
# real value via migration 0119. ``_MAX_REVISION_LOOPS`` is kept as a
# module constant so existing imports / tests that reference the symbol
# keep importing cleanly, but the live cap is resolved via
# ``_resolve_max_revision_loops()`` so a runtime app_settings change
# takes effect on the next graph invocation without a restart.
_MAX_REVISION_LOOPS = 3


def _resolve_max_revision_loops(site_config: Any = None) -> int:
    if site_config is None:
        return _MAX_REVISION_LOOPS
    try:
        return site_config.get_int(
            "writer_rag_two_pass_max_revision_loops", _MAX_REVISION_LOOPS,
        )
    except Exception:
        return _MAX_REVISION_LOOPS


class _State(TypedDict, total=False):
    topic: str
    angle: str
    snippets: list[dict[str, Any]]
    # NOTE: `pool` is NOT stored in state — it's a non-serializable
    # asyncpg.Pool (or test MagicMock) and would crash msgpack in the
    # checkpointer. See _POOL_REGISTRY above. We keep the field in the
    # TypedDict for type-shape compatibility but never write it.
    pool_thread: str  # lookup key into _POOL_REGISTRY
    draft: str
    needs: list[str]
    research_results: list[dict[str, Any]]
    external_lookups: list[dict[str, Any]]
    revision_loops: int
    loop_capped: bool
    # niches.writer_prompt_override flowed down from generate_content.py;
    # prepended to _draft_node's instruction. Empty string when no override
    # is set (the historical generic-instruction-only behaviour).
    writer_prompt_override: str
    # task_metadata.context_bundle flowed down from generate_content.py
    # (set by services/jobs/run_dev_diary_post.py for dev_diary tasks).
    # When non-empty, _draft_node inserts a GROUND TRUTH section in the
    # prompt with the actual PR titles + commits + decisions, so the
    # writer stops riffing on the topic title alone. Closes #353. The
    # dict shape matches DevDiaryContext.to_dict() — merged_prs,
    # notable_commits, brain_decisions, audit_resolved, recent_posts,
    # cost_summary, date.
    context_bundle: dict[str, Any]
    # Pre-collected external research corpus (ResearchService + RAG),
    # threaded down from GenerateContentStage via run(). Injected into the
    # draft prompt by _draft_node as a SOURCES section so the niche writer
    # grounds + cites against the same corpus the QA critic grades against.
    # Without it the niche writer drafted research-blind and the critic
    # rejected every glad-labs post for "ignoring the SOURCES corpus"
    # (2026-06-09 disconnect). Empty string when no research was gathered.
    research_context: str
    # Phase 0 lab observability (2026-05-28) — populated by
    # _revise_node when it resolves a prompt via UnifiedPromptManager.
    # Surface up through run() into the caller stage so they land on
    # capability_outcomes.prompt_template_{key,version}. None when the
    # registry was unreachable (bootstrap / test).
    prompt_template_key: str | None
    prompt_template_version: int | None
    # Pipeline task_id — threaded from generate_content.py via run() so
    # every ollama_chat_text call inside this graph can tag cost_logs rows
    # with the originating task. None when called outside a pipeline context.
    task_id: str | None
    # Requested word budget for the post, threaded from
    # generate_content.py (which reads pipeline_tasks.target_length, itself
    # set by the weighted picker). Passed to the draft prompt as a soft
    # length target and used by run()'s expansion guard. Defaults to 1200
    # when the caller doesn't supply one. Pins the length-uniformity bug:
    # before this, the niche writer got no length signal and every post
    # came out ~600 words regardless of the requested length.
    target_length: int


# Content-bearing source_tables the writer may ground a draft in when the
# operator hasn't set ``rag_source_filter``. The writer NEVER queries the
# embeddings table unfiltered (see ``_resolve_snippet_source_filter``).
_DEFAULT_SNIPPET_SOURCE_FILTER = ("posts",)


def _resolve_snippet_source_filter(site_config: Any = None) -> list[str]:
    """Resolve the ``source_table`` allowlist the writer may draw snippets from.

    Reads the CSV ``rag_source_filter`` app_setting (default ``'posts'``).
    Unlike the general ``rag_engine`` retriever — where an empty value means
    "all source_tables" — the writer NEVER queries the embeddings table
    unfiltered: an empty/unset value falls back to the built-in content
    allowlist (``posts``).

    The corpus is ~⅔ ``claude_sessions`` / ``brain`` / ``audit`` ops-logs, none
    of which are publishable content. An off-topic session transcript ranking
    near the topic vector would otherwise be handed to the writer as an
    "internal snippet" and reproduced wholesale into a draft (2026-06
    contamination incident; memory: ``project_rag_corpus_pollution``). Operators
    broaden the corpus by adding content-bearing tables to ``rag_source_filter``
    (e.g. ``'posts,samples'``).
    """
    if site_config is None:
        return list(_DEFAULT_SNIPPET_SOURCE_FILTER)
    try:
        csv = (site_config.get("rag_source_filter", "") or "").strip()
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        return list(_DEFAULT_SNIPPET_SOURCE_FILTER)
    parsed = [s.strip() for s in csv.split(",") if s.strip()]
    return parsed or list(_DEFAULT_SNIPPET_SOURCE_FILTER)


# -- nodes --

async def _embed_and_fetch_snippets(state: _State) -> _State:
    from services.topic_ranking import embed_text

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    snippet_limit = (
        site_config.get_int("writer_rag_two_pass_snippet_limit", 20)
        if site_config is not None else 20
    )
    source_filter = _resolve_snippet_source_filter(site_config)
    qvec = await embed_text(
        f"{state['topic']} — {state['angle']}", site_config=site_config,  # type: ignore[arg-type]
    )
    # Convert to pgvector text format. asyncpg has no built-in codec for
    # Python list → pgvector; passing the raw list crashes with
    # "expected str, got list" before the ::vector cast can run.
    # Pattern matches services/embeddings_db.py:151 (the established way
    # this codebase passes vectors to pgvector queries).
    qvec_str = "[" + ",".join(str(v) for v in qvec) + "]"
    pool = _POOL_REGISTRY[state["pool_thread"]]
    async with pool.acquire() as conn:
        # source_table filter (corpus-pollution guard): only ground drafts in
        # content-bearing tables, never the claude_sessions / brain / audit
        # ops-log bulk of the corpus. See _resolve_snippet_source_filter.
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             WHERE source_table = ANY($3::text[])
             ORDER BY embedding <=> $1::vector
             LIMIT $2
            """,
            qvec_str,
            snippet_limit,
            source_filter,
        )
    snippets = [{"source": r["source_table"], "ref": str(r["source_id"]),
                 "snippet": r["text_preview"]} for r in rows]
    return {**state, "snippets": snippets, "revision_loops": 0,
            "external_lookups": [], "loop_capped": False}


async def _draft_node(state: _State) -> _State:
    from modules.content.ai_content_generator import generate_with_context
    instruction = (
        "Write a first-draft blog post drawing ONLY from the provided internal "
        "snippets. Do NOT make up external facts, statistics, or quotes you cannot "
        "ground in a snippet. If you need an outside fact you don't have, mark it "
        "[EXTERNAL_NEEDED: <description>] in the draft so a follow-up pass can fill it in. "
        "[EXTERNAL_NEEDED: ...] is the ONLY placeholder you may emit. Never invent a "
        "citation stand-in: do not write labels like [INTERNAL SNIPPET], a bare `source` "
        "tag, or a markdown link whose target is not a real URL (e.g. (url), (link), "
        "(internal_context_link)). If you cannot cite a real URL for a claim, state the "
        "claim plainly with no citation marker at all."
    )
    # Prepend the niche-level writer prompt override (when present) so
    # niche-specific anti-hallucination rules / brand voice / scope
    # restrictions arrive before the mode-specific TWO_PASS instruction.
    # Wired in by migration 0141 + this PR; empty string when the niche
    # has no override set (preserves historical behaviour).
    override = (state.get("writer_prompt_override") or "").strip()
    if override:
        instruction = f"{override}\n\n---\n\n{instruction}"
    # Inject the context_bundle (set by the dev_diary job for dev_diary
    # tasks) as a GROUND TRUTH section. The writer must base claims on
    # these entries; when present, this is the authoritative source —
    # not the topic string, not the snippets. Closes #353. For niche-
    # batch / ad-hoc tasks this is empty and the section is skipped.
    bundle = state.get("context_bundle") or {}
    if bundle:
        ground_truth = _format_bundle_for_prompt(bundle)
        if ground_truth:
            instruction = (
                f"{instruction}\n\n---\n\n"
                f"GROUND TRUTH (today's actual activity — base every "
                f"claim on these entries, do NOT infer or invent details "
                f"the bundle doesn't contain. When you reference a PR or "
                f"commit, use the exact title and link to the URL given):\n\n"
                f"{ground_truth}"
            )
    # Inject the pre-collected external research corpus (ResearchService +
    # RAG, threaded from GenerateContentStage via run()) as a SOURCES
    # section. The QA critic grades the draft against this same corpus, so
    # without surfacing it to the writer the niche path drafted research-
    # blind and was rejected for "ignoring the SOURCES corpus" (2026-06-09).
    # Phrased to override the "ONLY internal snippets" line above: these are
    # vetted facts the writer SHOULD use in addition to the snippets.
    research_context = (state.get("research_context") or "").strip()
    if research_context:
        instruction = (
            f"{instruction}\n\n---\n\n"
            f"SOURCES (vetted external research already gathered for this "
            f"article — use these IN ADDITION to the internal snippets: ground "
            f"your key claims in them and cite them inline as markdown links "
            f"using the exact URLs provided. Do not invent other external facts "
            f"or sources beyond these and the snippets. If a claim has no matching "
            f"SOURCE URL, write it without any citation marker — never a placeholder "
            f"like [INTERNAL SNIPPET] or a link whose target is not a real URL"
            f"):\n\n{research_context}"
        )
    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    pool = _POOL_REGISTRY.get(state["pool_thread"])
    draft = await generate_with_context(
        topic=state["topic"], angle=state["angle"],
        snippets=state["snippets"], extra_instructions=instruction,
        site_config=site_config, pool=pool,
        task_id=state.get("task_id"),
        target_length=state.get("target_length", 1200),
    )
    return {**state, "draft": draft}


def _format_bundle_for_prompt(bundle: dict[str, Any]) -> str:
    """Render the dev_diary context bundle as plain text for the writer.

    Caps each section (top 8 PRs, top 8 commits, top 5 decisions, top 5
    audit, top 5 posts) to keep the prompt within token budget on large
    multi-day windows. Cost-summary is one line. Truncates individual
    titles at 200 chars. Order matches DevDiaryContext.to_dict().
    """
    lines: list[str] = []
    prs = (bundle.get("merged_prs") or [])[:8]
    if prs:
        lines.append("Merged PRs (use these — every PR claim must match a row here):")
        for p in prs:
            num = p.get("number")
            title = (p.get("title") or "")[:200]
            url = p.get("url") or ""
            author = p.get("author") or ""
            body = (p.get("body") or "").strip()
            tag = f"PR #{num}" if num else "PR"
            lines.append(f"- [{tag}] {title}")
            if url:
                lines.append(f"  {url}")
            if author:
                lines.append(f"  author: {author}")
            # PR body is the writer's primary grounding signal — without it
            # the writer guesses meaning from the title alone (see #353
            # follow-up). Cap to first 800 chars per PR to keep the prompt
            # within budget on multi-day windows; with 8 PRs × 800 chars
            # the bodies fit in ~6.5K chars total. Strip blank lines and
            # indent so the structure stays scannable.
            if body:
                # Compact: collapse runs of blank lines + first 800 chars.
                compact_body = "\n".join(
                    ln for ln in body.splitlines() if ln.strip()
                )[:800]
                lines.append("  description:")
                for body_line in compact_body.splitlines():
                    lines.append(f"    {body_line}")
        lines.append("")
    commits = (bundle.get("notable_commits") or [])[:8]
    if commits:
        lines.append("Notable commits:")
        for c in commits:
            sha = (c.get("sha") or "")[:7]
            subject = (c.get("subject") or "")[:200]
            lines.append(f"- [{sha}] {subject}" if sha else f"- {subject}")
        lines.append("")
    decisions = (bundle.get("brain_decisions") or [])[:5]
    if decisions:
        lines.append("Brain decisions (high-confidence calls):")
        for d in decisions:
            summary = (d.get("summary") or d.get("description") or "")[:200]
            if summary:
                lines.append(f"- {summary}")
        lines.append("")
    audit = (bundle.get("audit_resolved") or [])[:5]
    if audit:
        lines.append("Resolved audit events:")
        for a in audit:
            summary = (a.get("summary") or a.get("event") or "")[:200]
            if summary:
                lines.append(f"- {summary}")
        lines.append("")
    posts = (bundle.get("recent_posts") or [])[:5]
    if posts:
        lines.append("Recently published posts:")
        for p in posts:
            title = (p.get("title") or "")[:200]
            url = p.get("url") or ""
            lines.append(f"- {title}" + (f" — {url}" if url else ""))
        lines.append("")
    cost = bundle.get("cost_summary") or {}
    if cost:
        total = cost.get("total_usd")
        if total is not None:
            lines.append(f"Cost summary: ${total:.2f} across LLM calls today.")
            lines.append("")
    return "\n".join(lines).rstrip()


def _detect_needs(state: _State) -> _State:
    needs = _NEED_PATTERN.findall(state["draft"])
    return {**state, "needs": [n.strip() for n in needs]}


async def _research_each(state: _State) -> _State:
    from services.research_service import research_topic

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    max_sources = (
        site_config.get_int("writer_rag_two_pass_research_max_sources", 2)
        if site_config is not None else 2
    )
    results = []
    for need in state["needs"]:
        aug = await research_topic(
            query=need, max_sources=max_sources, site_config=site_config,  # type: ignore[arg-type]
        )
        results.append({"need": need, "research": aug})
    cumulative = list(state.get("external_lookups") or []) + results
    return {**state, "research_results": results, "external_lookups": cumulative}


def _emit_variant_fallback_finding(
    *,
    bad_model: str,
    default_model: str,
    reason: str,
) -> None:
    """Loud warning + persistent finding when a variant override is
    abandoned for the configured default writer.

    The writer-model flip is an approval canary (memory:
    ``feedback_writer_model_canary``) — when QA starts rejecting
    everything, the first thing to check is which model the writer
    actually ran. Silently swapping a broken variant model for the
    default would hide exactly that signal, so we (1) log at WARNING and
    (2) emit a typed ``finding`` row (``event_type='finding'``) that
    surfaces on the Findings dashboard and routes through the brain
    findings-dispatcher. ``severity='warn'`` → Discord per the dispatcher
    policy (a degraded-but-recovered experiment, not a page-worthy
    outage).

    Per ``feedback_no_silent_defaults``: the fallback is NOT silent. It
    keeps the pipeline producing content (a bad variant must never zero
    production) while making the override failure impossible to miss.
    """
    logger.warning(
        "[two_pass_writer] variant writer_model=%r failed (%s) — "
        "falling back to the configured default writer %r so the "
        "pipeline is not zeroed. Check the active experiment's variant "
        "model availability.",
        bad_model, reason, default_model,
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001 — emit path optional; never block fallback
        return
    try:
        emit_finding(
            source="atoms.two_pass_writer",
            kind="variant_writer_model_fallback",
            title=(
                f"Variant writer_model {bad_model!r} failed — fell back "
                f"to default writer {default_model!r}"
            ),
            body=(
                f"A lab-harness experiment variant assigned writer_model="
                f"{bad_model!r}, but the revise pass {reason}. The writer "
                f"fell back to the configured default ({default_model!r}) "
                f"so the content pipeline still produced output. Verify the "
                f"variant model is installed/configured before re-activating "
                f"the experiment; an unavailable override would otherwise "
                f"zero every task it is assigned to. Refs poindexter#574."
            ),
            severity="warn",
            dedup_key=f"variant_writer_model_fallback:{bad_model}",
            extra={
                "bad_model": bad_model,
                "default_model": default_model,
                "reason": reason,
            },
        )
    except Exception:  # noqa: BLE001 — finding emission must never raise here
        pass


def _emit_empty_revise_kept_prior_finding(*, model: str) -> None:
    """Loud-but-recovered canary: the default-path revise returned empty on
    both the initial call and its retry, so the writer kept the prior draft
    instead of zeroing it (poindexter#691).

    Self-heal is not silent (``feedback_self_heal_not_suppress``): the pipeline
    keeps producing content (a transient empty revise must never zero a good
    draft) while the empty-output condition stays visible on the Findings
    dashboard. ``severity='warn'`` → Discord per the dispatcher policy.
    """
    logger.warning(
        "[two_pass_writer] default revise model=%r returned empty twice — kept "
        "the prior draft (markers stripped) instead of zeroing it. A reasoning "
        "writer model intermittently emits empty content; verify model health "
        "if this recurs. Refs poindexter#691.",
        model,
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001 — emit path optional; never block recovery
        return
    try:
        emit_finding(
            source="modules.content.atoms.two_pass_writer",
            kind="writer_empty_draft_kept_prior",
            title=f"Default revise model {model!r} returned empty — kept prior draft",
            body=(
                f"The default-path revise call (model {model!r}) returned empty "
                f"content on both the initial attempt and one retry. The writer "
                f"kept the pre-revision draft (unresolved [EXTERNAL_NEEDED] "
                f"markers stripped) so the post is not zeroed. A reasoning model "
                f"that spends its budget in the thinking channel returns empty "
                f"content intermittently; verify model health if this recurs. "
                f"Refs poindexter#691."
            ),
            severity="warn",
            dedup_key=f"writer_empty_draft_kept_prior:{model}",
            extra={"model": model, "reason": "revise returned empty twice"},
        )
    except Exception:  # noqa: BLE001 — finding emission must never raise here
        pass


async def _revise_node(state: _State) -> _State:
    # 2026-05-16: switched from ``_ollama_chat_json`` (which forces
    # ``format=json`` on Ollama and returns a JSON-wrapped string) to
    # the plain-text ``ollama_chat_text`` helper. Captured 2026-05-15:
    # ``pipeline_versions.id=1851`` shipped ``{"content": "..."}\n}``
    # as the post body — the writer produced JSON because Ollama was
    # told to, then nothing un-wrapped the envelope before validation,
    # which then critical-flagged "Content appears truncated — ends
    # with '}'". ``ollama_chat_text`` also runs ``maybe_unwrap_json``
    # internally as belt-and-suspenders if a model still emits a JSON
    # envelope unprompted.
    from services.llm_text import ollama_chat_text, resolve_local_model

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    pool = _POOL_REGISTRY.get(state["pool_thread"])
    task_id = state.get("task_id")
    # Phase 1 lab harness — when the caller passed a writer_model_override
    # (because pick_variant assigned a model-axis variant), it lives in
    # the parallel _MODEL_OVERRIDE_REGISTRY (string is msgpack-friendly
    # but keeping the override registry pattern uniform with pool / site
    # config). resolve_local_model accepts the explicit string and returns
    # it after stripping the ``ollama/`` prefix — no app_settings hit on
    # the variant path.
    model_override = _MODEL_OVERRIDE_REGISTRY.get(state["pool_thread"])
    # The default writer model — what the pipeline uses when no experiment
    # is running. Resolved with ``model=None`` so it reads the
    # ``pipeline_writer_model`` pin. This is the fallback target when a
    # variant override is unavailable: a single bad variant model must
    # NEVER zero the whole pipeline (poindexter#574).
    default_model = resolve_local_model(model=None, site_config=site_config)
    model = resolve_local_model(model=model_override, site_config=site_config)
    aug_block = "\n\n".join(
        f"[EXTERNAL_NEEDED: {r['need']}] → {r['research']}"
        for r in state["research_results"]
    )
    # 2026-05-12 (poindexter#485): migrated the inline f-string revise
    # prompt to UnifiedPromptManager. YAML default at
    # prompts/writer_rag_modes.yaml; Langfuse overrides take effect on
    # the next call.
    # 2026-05-16: pass ``pool`` so the call dispatches through the
    # configured LLM provider (LiteLLM / Ollama / etc.) instead of
    # hardwiring to local Ollama.
    # 2026-05-28: capture (key, version) provenance so the writer
    # surfaces them into the run-level return — feeds capability_outcomes
    # via the dispatcher's metrics dict in the caller stage.
    revise_prompt, prompt_template_key, prompt_template_version = (
        _resolve_revise_prompt(
            draft=state["draft"], aug_block=aug_block,
        )
    )

    async def _call(call_model: str) -> str:
        return await ollama_chat_text(
            revise_prompt,
            model=call_model,
            site_config=site_config,
            pool=pool,
            timeout_setting="niche_ollama_chat_timeout_seconds",
            task_id=task_id,
            phase="two_pass_revise",
        )

    # poindexter#574 — variant-override fallback. When a variant assigned
    # a writer model and that model is unavailable/misconfigured, the
    # revise call either RAISES (Ollama 404 "model not found" surfaces as
    # a dispatch error) or returns EMPTY content (e.g. a reasoning model
    # that spends its whole budget in the thinking channel). Either way,
    # without a fallback a single bad experiment variant zeros every task
    # it is assigned to. Catch both shapes, fall back to the configured
    # default writer, and emit a loud canary (warning + finding). The
    # no-override path is byte-identical to before: only one call, no
    # fallback machinery.
    using_override = bool(model_override) and model != default_model
    if not using_override:
        # poindexter#691 — default-path empty-revise guard. A reasoning writer
        # model can intermittently return EMPTY content (all tokens spent in
        # the thinking channel). Pre-#691 this path had no empty check, so an
        # empty revise silently OVERWROTE a good prior draft with '' — which
        # then flowed into QA as a misleading reviewer_count:0 reject. Retry
        # ONCE with the same model (preserve writer quality — do NOT downgrade
        # the article body to a weaker model), and if still empty keep the
        # PRIOR draft instead of zeroing it.
        new_draft = await _call(model)
        if not (new_draft or "").strip():
            retry = await _call(model)
            if (retry or "").strip():
                new_draft = retry
            else:
                prior = state.get("draft") or ""
                # Strip unresolved [EXTERNAL_NEEDED] markers so detect_needs
                # terminates the loop (instead of re-looping to the cap on the
                # same markers) and the markers don't leak into the post.
                new_draft = _NEED_PATTERN.sub("", prior).strip() or prior
                _emit_empty_revise_kept_prior_finding(model=model)
    else:
        try:
            new_draft = await _call(model)
            failure_reason = (
                "returned empty content" if not (new_draft or "").strip() else None
            )
        except Exception as exc:  # noqa: BLE001 — any override failure → fallback
            new_draft = ""
            failure_reason = f"raised {type(exc).__name__}: {str(exc)[:200]}"
        if failure_reason is not None:
            _emit_variant_fallback_finding(
                bad_model=model,
                default_model=default_model,
                reason=failure_reason,
            )
            # Retry once with the configured default. This call is NOT
            # wrapped — if the DEFAULT writer also fails, that is a real
            # production outage (not an experiment bug) and must surface
            # loudly per ``feedback_no_silent_defaults``.
            new_draft = await _call(default_model)

    return {
        **state,
        "draft": new_draft,
        "revision_loops": state.get("revision_loops", 0) + 1,
        # Stash on state so run() can surface them on the writer return
        # for the caller stage to forward into capability_outcomes.
        "prompt_template_key": prompt_template_key,
        "prompt_template_version": prompt_template_version,
    }


def _mark_capped(state: _State) -> _State:
    return {**state, "loop_capped": True}


# -- conditional edges --

def _needs_or_done(state: _State) -> str:
    """After detect_needs: route to research_each if needs found AND we haven't
    hit the loop cap, else END (or _done_capped if we're capping)."""
    if not state.get("needs"):
        return END
    site_config = _SITE_CONFIG_REGISTRY.get(state.get("pool_thread", ""))
    if state.get("revision_loops", 0) >= _resolve_max_revision_loops(site_config):
        return "_done_capped"
    return "research_each"


def _build_graph():
    g = StateGraph(_State)
    g.add_node("embed_and_fetch", _embed_and_fetch_snippets)
    g.add_node("draft", _draft_node)
    g.add_node("detect_needs", _detect_needs)
    g.add_node("research_each", _research_each)
    g.add_node("revise", _revise_node)
    g.add_node("_done_capped", _mark_capped)

    g.set_entry_point("embed_and_fetch")
    g.add_edge("embed_and_fetch", "draft")
    g.add_edge("draft", "detect_needs")
    g.add_conditional_edges("detect_needs", _needs_or_done, {
        "research_each": "research_each",
        "_done_capped": "_done_capped",
        END: END,
    })
    g.add_edge("research_each", "revise")
    g.add_edge("revise", "detect_needs")
    g.add_edge("_done_capped", END)

    # MemorySaver checkpointer means the graph CAN be paused mid-run and
    # resumed — useful when an operator interrupts mid-revision in v2.
    # v1 uses in-memory; v2 swaps to a postgres checkpointer for
    # cross-process durability.
    return g.compile(checkpointer=MemorySaver())


_GRAPH = _build_graph()


def _resolve_min_length_ratio(site_config: Any = None) -> float:
    """Fraction of ``target_length`` below which the keep-best expansion pass
    fires. DB-configurable via ``writer_min_length_ratio`` (default 0.7)."""
    if site_config is None:
        return 0.7
    try:
        return float(site_config.get_float("writer_min_length_ratio", 0.7))
    except Exception:  # noqa: BLE001 — defensive against test stubs
        # silent-ok: optional tuning knob — fall back to the documented
        # default when a stubbed site_config raises (matches
        # _resolve_max_revision_loops above).
        return 0.7


def _expansion_enabled(site_config: Any = None) -> bool:
    """Master switch for the expansion pass (``writer_length_expansion_enabled``,
    default true). Off → the writer returns the graph's draft unchanged."""
    if site_config is None:
        return True
    try:
        raw = site_config.get("writer_length_expansion_enabled", "true")
        return str(raw).lower() in ("true", "1", "yes")
    except Exception:  # noqa: BLE001 — defensive against test stubs
        # silent-ok: optional feature flag — fall back to the default-on
        # value when a stubbed site_config raises (matches
        # _resolve_max_revision_loops above).
        return True


# ---------------------------------------------------------------------------
# Prompt-echo guard (poindexter#2009 follow-up).
#
# gemma-4-31B-it-qat (the 4-bit QAT ``pipeline_writer_model``) intermittently
# regurgitates the prompt preamble — the topic line, the angle, the niche
# ``writer_prompt_override``, the revise/expand instructions, the citation
# rules, and its own planning notes — as the OPENING of the article instead of
# executing them. Captured 2026-06-29 (task ba4d627a): the stored canonical_blog
# content opened with the topic, then "Technical/Professional.", then the niche
# descriptor, then "Expand from ~57 words to closer to 1500 words. Add genuine
# substance...". The real article body sat underneath. The #2009 keep-best
# expansion compounded it — an echoed expansion is longer than a thin original,
# so keep-best adopted the echo-contaminated version.
#
# This guard deterministically strips a contiguous echoed/scaffolding preamble
# off the FRONT of a draft (no LLM call). It is GATED on a high-precision
# identity-echo signature — the draft's first lines restating the known topic /
# angle / niche override — so it is a no-op on clean drafts (a real article body
# never opens by listing its own topic, angle, and niche as separate short
# lines). When it can't recover a substantial body it keeps the original
# untouched (never zeroes a draft) and the caller emits a finding so the
# model-quality signal stays visible (feedback_self_heal_not_suppress).

_ECHO_SCAFFOLD_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"expand (this|the|from|it)\b",
        r"revise (the|a|this)\b",
        r"input:\s",
        r"goal:\s",
        r"constraints?:?\s*$",
        r"context:\s",
        r"title:\s",
        r"add genuine (added )?substance\b",
        r"return (the )?complete\b",
        r"no padding\b",
        r"preserve (every|existing|all)\b",
        r"end on a complete sentence\b",
        r"do not (repeat|pad|restate|invent|make up|add)\b",
        r"first[- ]person\b",
        r"full markdown links\b",
        r"no fake url",
        r"every (name|fact|stat|claim)\b",
        r"internal consistency\b",
        # the model narrating the task / metadata to itself
        r"the original draft\b",
        r"current content\b",
        r"the .?draft.? (provided|is|below|consists)\b",
        r"i (need to|will|should|'?ll|am going to)\b",
        r"let me\b",
        r"okay[,.]",
        r"here(’s| is| are) (the|my)\b",
    )
]


def _norm_echo(line: str) -> str:
    """Normalize a line for echo comparison: drop leading markdown/list markers,
    lowercase, strip surrounding punctuation/quotes."""
    s = re.sub(r"^[\s>#*\-•\d.)]+", "", line.strip())
    return s.strip(" \t.:*_`\"'()").lower()


def _sep_norm(s: str) -> str:
    """Collapse separator punctuation (| / ,) + whitespace so the angle echo
    'Technical/Professional' matches the angle input 'technical | professional'."""
    return re.sub(r"\s+", " ", re.sub(r"[|/,]", " ", s)).strip()


def _echo_tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 1]


def _token_overlap(line_norm: str, target_text: str) -> float:
    """Fraction of the LINE's tokens that appear in ``target_text``. Used to
    catch paraphrased echoes (e.g. a brand line condensed from the override)."""
    lt = _echo_tokens(line_norm)
    if not lt:
        return 0.0
    tt = set(_echo_tokens(target_text))
    if not tt:
        return 0.0
    return sum(1 for t in lt if t in tt) / len(lt)


def _is_preamble_line(
    raw: str, norm: str, *,
    topic_norm: str, angle_norm: str, override_norms: set[str], override_fulltext: str,
) -> bool:
    """Classify a single LEADING line as echoed prompt scaffolding."""
    if not norm:
        return False
    # Scaffold / instruction / planning echoes (specific, anchored phrases).
    if any(p.match(norm) for p in _ECHO_SCAFFOLD_PATTERNS):
        return True
    # A markdown heading is real content UNLESS it's an exact topic restatement
    # (a redundant H1 — the canonical title is generated separately).
    if raw.lstrip().startswith("#"):
        return bool(topic_norm) and norm == topic_norm
    if angle_norm and _sep_norm(norm) == angle_norm:
        return True
    if norm in override_norms:
        return True
    # Short identity echoes (topic restatement / paraphrased brand line). The
    # <15-word gate keeps real prose intros (which are longer) out of scope.
    if 0 < len(_echo_tokens(norm)) < 15:
        if topic_norm and _token_overlap(norm, topic_norm) >= 0.75:
            return True
        if override_fulltext and _token_overlap(norm, override_fulltext) >= 0.7:
            return True
    return False


def _has_echo_signature(
    lines: list[str], *,
    topic_norm: str, angle_norm: str, override_norms: set[str], override_fulltext: str,
) -> bool:
    """High-precision trigger: do the first few non-blank lines restate at least
    two of {topic, angle, niche-override}? A clean article body never does."""
    seen = 0
    cats: set[str] = set()
    for raw in lines:
        norm = _norm_echo(raw)
        if not norm:
            continue
        seen += 1
        short = 0 < len(_echo_tokens(norm)) < 15
        if topic_norm and (norm == topic_norm or (short and _token_overlap(norm, topic_norm) >= 0.75)):
            cats.add("topic")
        elif angle_norm and _sep_norm(norm) == angle_norm:
            cats.add("angle")
        elif (norm in override_norms) or (
            override_fulltext and short and _token_overlap(norm, override_fulltext) >= 0.7
        ):
            cats.add("brand")
        if seen >= 4:
            break
    return len(cats) >= 2


def _strip_echoed_preamble(
    draft: str, *, topic: str, angle: str,
    writer_prompt_override: str = "", max_scan_lines: int = 40,
) -> tuple[str, int]:
    """Strip a contiguous prompt-echo/scaffolding preamble off the front of a
    draft. Returns ``(clean_draft, lines_stripped)``.

    No-op (returns the input + 0) unless a strong identity-echo signature is
    present AND a substantial body (≥50 words) survives the strip — the guard
    never truncates a draft to nothing.
    """
    if not draft or not draft.strip():
        return draft, 0
    topic_norm = _norm_echo(topic or "")
    angle_norm = _sep_norm(_norm_echo(angle or ""))
    override_norms = {
        _norm_echo(ln) for ln in (writer_prompt_override or "").splitlines()
        if _norm_echo(ln)
    }
    override_fulltext = _norm_echo(re.sub(r"\s+", " ", writer_prompt_override or ""))
    lines = draft.splitlines()
    if not _has_echo_signature(
        lines[:max_scan_lines], topic_norm=topic_norm, angle_norm=angle_norm,
        override_norms=override_norms, override_fulltext=override_fulltext,
    ):
        return draft, 0

    last_pre = -1
    i = 0
    while i < len(lines) and i < max_scan_lines:
        norm = _norm_echo(lines[i])
        if not norm:  # blank/separator line inside the preamble — skip
            i += 1
            continue
        if _is_preamble_line(
            lines[i], norm, topic_norm=topic_norm, angle_norm=angle_norm,
            override_norms=override_norms, override_fulltext=override_fulltext,
        ):
            last_pre = i
            i += 1
            continue
        break  # first real body line
    if last_pre < 0:
        return draft, 0

    remainder = "\n".join(lines[last_pre + 1:]).lstrip("\n")
    stripped = sum(1 for ln in lines[: last_pre + 1] if _norm_echo(ln))
    # Never zero/gut a draft: if the strip doesn't leave a substantial body the
    # contamination is a human-review problem (surfaced via the finding), not
    # something to silently truncate to nothing.
    if len(remainder.split()) < 50:
        return draft, 0
    return remainder, stripped


def _emit_prompt_echo_finding(*, stripped_lines: int, task_id: str | None) -> None:
    """Loud-but-recovered canary: the writer regurgitated prompt scaffolding as
    article content and the guard stripped it. Self-heal is not silent
    (feedback_self_heal_not_suppress) — the pipeline keeps the recovered body
    while the model-quality signal stays visible on the Findings dashboard.
    ``severity='warn'`` → Discord per the dispatcher policy."""
    logger.warning(
        "[two_pass_writer] writer echoed %d prompt/scaffolding line(s) at the top "
        "of the draft; stripped them and kept the body. Recurring echoes indicate "
        "the writer model is not following instructions on long prompts.",
        stripped_lines,
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001  # silent-ok: emit is best-effort; the WARNING log above already surfaced the echo
        return
    try:
        emit_finding(
            source="modules.content.atoms.two_pass_writer",
            kind="writer_prompt_echo_stripped",
            title=f"Writer echoed {stripped_lines} prompt line(s) — stripped preamble",
            body=(
                f"The two_pass writer reproduced {stripped_lines} line(s) of prompt "
                f"preamble/scaffolding (topic / angle / niche override / "
                f"instructions) at the top of the draft instead of executing them. "
                f"The deterministic echo guard stripped the preamble and kept the "
                f"article body. If this recurs, the writer model "
                f"(pipeline_writer_model) is failing to follow instructions on long "
                f"prompts — consider a stronger writer."
            ),
            severity="warn",
            dedup_key="writer_prompt_echo_stripped",
            extra={"stripped_lines": stripped_lines, "task_id": task_id},
        )
    except Exception:  # noqa: BLE001  # silent-ok: finding emission must never raise; the WARNING log above is the durable signal
        pass


async def _maybe_expand_to_target(
    draft: str,
    *,
    target_length: int,
    site_config: Any,
    pool: Any,
    task_id: str | None,
    topic: str = "",
    angle: str = "",
    writer_prompt_override: str = "",
) -> tuple[str, dict[str, Any]]:
    """Keep-best soft expansion: when a draft lands under
    ``target_length * writer_min_length_ratio``, run ONE expansion pass and
    return whichever of (original, expanded) has more words.

    A thin or empty model response can never shrink or zero the post — the
    original is kept whenever the expansion fails to actually lengthen it. The
    whole pass is gated by ``writer_length_expansion_enabled`` and is fully
    fail-safe (any error keeps the original draft). Pins the length-uniformity
    bug: local writer models under-deliver on long targets even when the draft
    prompt asks for ~N words, so a single bounded expansion nudges thin drafts
    toward the requested budget without padding.
    """
    words_before = len((draft or "").split())
    meta: dict[str, Any] = {
        "expanded": False,
        "words_before": words_before,
        "words_after": words_before,
        "echo_stripped": 0,
    }
    if not (draft or "").strip() or target_length <= 0:
        return draft, meta
    if not _expansion_enabled(site_config):
        return draft, meta
    threshold = int(target_length * _resolve_min_length_ratio(site_config))
    if words_before >= threshold:
        return draft, meta

    from services.llm_text import ollama_chat_text, resolve_local_model
    model = resolve_local_model(model=None, site_config=site_config)
    prompt = _resolve_expand_prompt(
        draft=draft, target_length=target_length, word_count=words_before,
    )
    try:
        expanded = await ollama_chat_text(
            prompt,
            model=model,
            site_config=site_config,
            pool=pool,
            timeout_setting="niche_ollama_chat_timeout_seconds",
            task_id=task_id,
            phase="two_pass_expand",
        )
    except Exception as exc:  # noqa: BLE001 — expansion is best-effort
        logger.warning(
            "[two_pass_writer] expansion pass failed (%s) — keeping the "
            "original %d-word draft", exc, words_before,
        )
        return draft, meta

    expanded = (expanded or "").strip()
    # Strip any prompt-echo the expansion model prepended BEFORE the keep-best
    # comparison — otherwise an echoed expansion (which is "longer" only because
    # it dumped the expand prompt at the top) would win keep-best and bake the
    # scaffolding into the post. This is exactly the #2009 compounding observed
    # on task ba4d627a ("Expand from ~57 words to closer to 1500 words...").
    expanded, exp_echo = _strip_echoed_preamble(
        expanded, topic=topic, angle=angle,
        writer_prompt_override=writer_prompt_override,
    )
    meta["echo_stripped"] = exp_echo
    words_after = len(expanded.split())
    # Keep-best: only adopt the expansion when it genuinely lengthened the
    # draft. An empty / shorter response keeps the original (never zero/shrink).
    if words_after > words_before:
        logger.info(
            "[two_pass_writer] expanded draft %d → %d words (target %d)",
            words_before, words_after, target_length,
        )
        meta["expanded"] = True
        meta["words_after"] = words_after
        return expanded, meta
    logger.info(
        "[two_pass_writer] expansion did not lengthen the draft "
        "(%d → %d words) — keeping the original", words_before, words_after,
    )
    return draft, meta


async def run(*, topic: str, angle: str, niche_id: UUID | str | None, pool, task_id: str | None = None, **kw: Any) -> dict[str, Any]:
    """Run the two-pass writer graph and return the final draft + metadata.

    Kwargs that flow into graph state:

    - ``site_config`` — for snippet-limit / model resolution settings.
    - ``writer_prompt_override`` — niche-level prompt prepended to the
      writer instruction.
    - ``context_bundle`` — when set (currently only by dev_diary, which
      now uses ``narrate_bundle`` directly so this is effectively
      dormant for live traffic), the writer includes a GROUND TRUTH
      section in the prompt with structured facts.
    - ``writer_model_override`` — Phase 1 lab harness. When set
      (string), the writer's ``_revise_node`` uses this model exactly
      instead of resolving from app_settings. Routed via
      ``_MODEL_OVERRIDE_REGISTRY`` (same per-thread pattern as the
      pool / site_config). None / unset = production default.
      **Fallback (poindexter#574):** if the overridden model is
      unavailable — the revise call raises or returns empty content —
      ``_revise_node`` falls back to the configured default writer
      (the ``pipeline_writer_model`` pin) and
      emits a loud warning + a ``finding`` row. A single bad experiment
      variant can never zero the whole pipeline.

    Defense in depth: every returned ``draft`` runs through the private-
    repo URL scrub before returning, mirroring the post-LLM scrub layer
    added to ``narrate_bundle`` in PR #680. Any GitHub URL the model
    might echo from training data gets rewritten to ``(PR #N)`` plain
    text before the caller persists the post.
    """
    thread_id = f"two_pass-{niche_id}-{topic[:32]}"
    _POOL_REGISTRY[thread_id] = pool
    _SITE_CONFIG_REGISTRY[thread_id] = kw.get("site_config")
    writer_model_override = kw.get("writer_model_override")
    if writer_model_override:
        _MODEL_OVERRIDE_REGISTRY[thread_id] = str(writer_model_override)
    try:
        cb_kw = kw.get("context_bundle") or {}
        initial: _State = {
            "topic": topic,
            "angle": angle,
            "pool_thread": thread_id,
            "writer_prompt_override": str(kw.get("writer_prompt_override") or ""),
            "context_bundle": cb_kw if isinstance(cb_kw, dict) else {},
            "research_context": str(kw.get("research_context") or ""),
            "task_id": task_id,
            "target_length": int(kw.get("target_length") or 1200),
        }
        config = {"configurable": {"thread_id": thread_id}}
        final = await _GRAPH.ainvoke(initial, config=config)
        writer_prompt_override = str(kw.get("writer_prompt_override") or "")
        # Prompt-echo guard: gemma-4-31B-it-qat intermittently dumps the prompt
        # preamble (topic / angle / niche override / instructions / planning
        # notes) at the top of the draft instead of executing it. Strip a
        # contiguous echoed preamble off the graph draft BEFORE the expansion
        # pass so (a) the persisted body is clean and (b) the keep-best length
        # comparison runs on the real word count, not an echo-inflated one.
        # No-op on clean drafts (gated on a strong identity-echo signature).
        base_draft, echo_pre = _strip_echoed_preamble(
            final.get("draft") or "", topic=topic, angle=angle,
            writer_prompt_override=writer_prompt_override,
        )
        # Keep-best expansion guard: local writers under-deliver on long
        # targets even when the draft prompt asks for ~N words. If the final
        # draft is under ``target_length * writer_min_length_ratio``, run ONE
        # expansion pass and keep whichever of (original, expanded) is longer —
        # expansion can never shrink or zero the post. Pins the length-
        # uniformity bug (every niche post used to land at the model's natural
        # ~600-word default regardless of the requested length).
        draft_out, expand_meta = await _maybe_expand_to_target(
            base_draft,
            target_length=int(kw.get("target_length") or 1200),
            site_config=kw.get("site_config"),
            pool=pool,
            task_id=task_id,
            topic=topic,
            angle=angle,
            writer_prompt_override=writer_prompt_override,
        )
        echo_stripped = echo_pre + int(expand_meta.get("echo_stripped", 0))
        if echo_stripped:
            _emit_prompt_echo_finding(stripped_lines=echo_stripped, task_id=task_id)
        return {
            "draft": _scrub_private_repo_refs(draft_out),
            "snippets_used": final.get("snippets", []),
            "external_lookups": final.get("external_lookups", []),
            "revision_loops": final.get("revision_loops", 0),
            "loop_capped": final.get("loop_capped", False),
            # Prompt-echo guard observability — how many scaffolding/preamble
            # lines the writer regurgitated and the guard stripped (0 = clean).
            "prompt_echo_stripped": echo_stripped,
            # Length-enforcement observability — did the expansion pass fire,
            # and the word counts before/after. Surfaced so the caller stage
            # can record it (capability_outcomes / metrics).
            "length_expanded": expand_meta["expanded"],
            "words_before_expand": expand_meta["words_before"],
            "words_after_expand": expand_meta["words_after"],
            # Phase 0 lab observability (2026-05-28). When the writer
            # never reached _revise_node (no [EXTERNAL_NEEDED] markers
            # in the draft) these stay None — that's accurate: the
            # revise prompt wasn't resolved, so there's nothing to
            # attribute the outcome to via this seam. The DRAFT prompt
            # itself is currently inline in _draft_node; future PR can
            # migrate it to UnifiedPromptManager and surface its
            # provenance the same way.
            "prompt_template_key": final.get("prompt_template_key"),
            "prompt_template_version": final.get("prompt_template_version"),
        }
    finally:
        _POOL_REGISTRY.pop(thread_id, None)
        _SITE_CONFIG_REGISTRY.pop(thread_id, None)
        _MODEL_OVERRIDE_REGISTRY.pop(thread_id, None)

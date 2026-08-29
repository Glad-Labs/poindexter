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

import math
import re
from collections.abc import Sequence
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from services.logger_config import get_logger
from services.rag_scrub import scrub_private_repo_refs as _scrub_private_repo_refs

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
    # Writer prompt-size observability (poindexter#868) — captured by
    # _draft_node at each context-section assembly point, before
    # concatenation onto `instruction`. 0 (not missing) when a section is
    # absent for this task.
    writer_prompt_draft_chars: int
    writer_prompt_snippet_chars: int
    writer_prompt_research_chars: int
    writer_prompt_context_bundle_chars: int
    writer_prompt_override_chars: int
    writer_prompt_internal_grounding_chars: int
    # Accumulated across every _revise_node invocation in this run (mirrors
    # how revision_loops already accumulates below). 0 when no revision ran.
    writer_prompt_revise_chars: int
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
    # Prior-work anchor (#822) — the internal match threaded from
    # generate_content.py via run(). {source_table, source_id, preview,
    # similarity} or empty. Plain dict → msgpack-clean for the checkpointer.
    internal_grounding: dict[str, Any]
    # Observability set by _draft_node: did an anchor actually reach the prompt,
    # and from which corpus type. Surfaced up through run() to the caller stage.
    internal_grounding_injected: bool
    internal_grounding_source_table: str | None


# Content-bearing source_tables the writer may ground a draft in when the
# operator hasn't set ``rag_source_filter``. The writer NEVER queries the
# embeddings table unfiltered (see ``_resolve_snippet_source_filter``).
_DEFAULT_SNIPPET_SOURCE_FILTER = ("posts",)


def _resolve_snippet_source_filter(site_config: Any = None) -> list[str]:
    """Resolve the ``source_table`` allowlist the writer may draw snippets from.

    Reads the writer-specific CSV ``writer_rag_source_filter`` first, falling
    back to the general ``rag_source_filter`` app_setting, then the built-in
    ``posts`` allowlist. ``writer_rag_source_filter`` is decoupled from
    ``rag_source_filter`` (which other RAG consumers — internal-link discovery,
    dedup — read) so the writer can ground on first-party ``claude_sessions``
    without those sessions leaking into internal-link suggestions.

    Unlike the general ``rag_engine`` retriever — where an empty value means
    "all source_tables" — the writer NEVER queries the embeddings table
    unfiltered: an empty/unset value falls back to the built-in content
    allowlist (``posts``).

    The corpus is ~⅔ ``claude_sessions`` / ``brain`` / ``audit`` ops-logs, none
    of which are publishable content. An off-topic session transcript ranking
    near the topic vector would otherwise be handed to the writer as an
    "internal snippet" and reproduced wholesale into a draft (2026-06
    contamination incident; memory: ``project_rag_corpus_pollution``). Operators
    broaden the corpus by adding content-bearing tables to
    ``writer_rag_source_filter`` (e.g. ``'claude_sessions,posts'``).
    """
    if site_config is None:
        return list(_DEFAULT_SNIPPET_SOURCE_FILTER)
    try:
        csv = (site_config.get("writer_rag_source_filter", "") or "").strip()
        if not csv:
            csv = (site_config.get("rag_source_filter", "") or "").strip()
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        return list(_DEFAULT_SNIPPET_SOURCE_FILTER)
    parsed = [s.strip() for s in csv.split(",") if s.strip()]
    return parsed or list(_DEFAULT_SNIPPET_SOURCE_FILTER)


# -- retrieval de-echo (MMR + near-duplicate ceiling) --
#
# Grounding on the top-N nearest `posts` by raw cosine lets a dense topic
# cluster saturate the writer's context with the same opening restated 3-4
# ways, so it parrots it (the 2026-06 "VRAM is the only currency" 4-post echo;
# memory: project_rag_corpus_pollution). These select a DIVERSE grounding set
# from an oversampled candidate pool: drop near-identical priors (fail-open),
# then MMR-rank so an echo cluster collapses to a single representative snippet
# instead of dominating every slot.


def _parse_vector(raw: Any) -> list[float]:
    """Turn pgvector's string/list representation into ``list[float]``.

    asyncpg has no built-in codec for the ``vector`` type, so a selected
    ``embedding`` column comes back as its text form ``'[a,b,c]'``. Mirrors
    ``services/integrations/handlers/retention_embeddings_collapse._parse_vector``.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(v) for v in raw]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return []
    return [float(v) for v in text.split(",") if v.strip()]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors.

    Returns ``0.0`` for a zero-magnitude vector (no direction → never NaN) and
    for a dimension mismatch (a wrong-shape vector is treated as "not similar"
    rather than crashing the whole draft).
    """
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _select_snippets(
    candidates: list[dict],
    *,
    k: int,
    dedup_ceiling: float,
    mmr_lambda: float,
    source_caps: dict[str, int] | None = None,
) -> list[dict]:
    """Pick up to ``k`` diverse grounding snippets from an oversampled pool.

    Each candidate carries a precomputed query-similarity ``relevance`` (from the
    pgvector ``1 - (embedding <=> query)`` at fetch time) and its parsed
    ``vec`` (for inter-candidate similarity).

    Three levers, all DB-tunable:

    1. **Near-duplicate ceiling** — a candidate whose query-similarity is
       ``>= dedup_ceiling`` is a near-republish of what we're writing; drop it
       so it can't become snippet #1 and get parroted. **Fail-open**: if that
       would empty the pool, keep the originals (thin grounding beats none).
    2. **MMR** (Maximal Marginal Relevance) — greedily maximise
       ``lambda * relevance - (1 - lambda) * max_sim_to_already_selected``, so an
       echo cluster collapses to a single representative and the rest of the
       slots go to diverse posts. ``mmr_lambda = 1.0`` disables the diversity
       term (pure relevance ranking — the MMR escape hatch).
    3. **Per-source caps** — ``source_caps`` (e.g. ``{"posts": 2}``) bounds how
       many snippets any one ``source`` may contribute, so first-party (session)
       grounding isn't crowded out by — or crowding out — a single source. A
       capped-out source becomes ineligible for the rest of the MMR loop.
    """
    source_caps = source_caps or {}
    if not candidates:
        return []
    pool = [c for c in candidates if c.get("relevance", 0.0) < dedup_ceiling]
    if not pool:  # fail-open — the ceiling never zeroes grounding
        pool = list(candidates)

    selected: list[dict] = []
    per_source: dict[str, int] = {}
    remaining = list(pool)
    while remaining and len(selected) < k:
        best_i, best_score = None, None
        for i, cand in enumerate(remaining):
            src = cand.get("source")
            cap = source_caps.get(src)
            if cap is not None and per_source.get(src, 0) >= cap:
                continue  # this source is at its cap — ineligible
            diversity_penalty = (
                max(_cosine(cand.get("vec", []), s.get("vec", [])) for s in selected)
                if selected else 0.0
            )
            score = mmr_lambda * cand.get("relevance", 0.0) - (
                1.0 - mmr_lambda
            ) * diversity_penalty
            if best_score is None or score > best_score:
                best_score, best_i = score, i
        if best_i is None:
            break  # every remaining candidate is capped-out
        chosen = remaining.pop(best_i)
        selected.append(chosen)
        per_source[chosen.get("source")] = per_source.get(chosen.get("source"), 0) + 1
    return selected


def _parse_source_caps(site_config: Any = None) -> dict[str, int]:
    """Parse ``writer_rag_source_caps`` (CSV ``source:N``) → ``{source: cap}``.

    Empty/malformed entries are skipped (no cap). Deterministic, DB-tunable.
    """
    if site_config is None:
        return {}
    try:
        csv = (site_config.get("writer_rag_source_caps", "") or "").strip()
    except Exception:  # noqa: BLE001  # silent-ok: optional cap read — stub/malformed config yields no caps
        return {}
    caps: dict[str, int] = {}
    for part in csv.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        src, _, num = part.partition(":")
        src = src.strip()
        try:
            caps[src] = int(num.strip())
        except ValueError:
            continue
    return caps


# ---------------------------------------------------------------------------
# Degenerate-draft guard (poindexter#806, defense in depth alongside #691).
#
# A writer model that burns its generation budget in a hidden reasoning
# channel can emit a near-empty, NON-blank response — e.g. a handful of
# ellipsis/dot characters. ``"...".strip()`` is truthy, so a plain emptiness
# check (poindexter#691) lets this shape through as a "successful" call.
# Left unchecked, a degenerate draft reaches the keep-best expansion pass
# (``_maybe_expand_to_target``): because the expand prompt includes ONLY the
# draft text — never the topic/angle — a model asked to "expand" a dots-only
# draft has nothing to work with but the dots and invents a whole
# ellipsis-themed article as its "expansion", which then wins keep-best
# (trivially longer than a 3-character original) and gets persisted as the
# post. Confirmed against three prod tasks (9921678f / e46b449c / ecaf0c01):
# one had a genuinely degenerate TOPIC upstream (topic generation is a
# separate bug, out of scope here), the other two had perfectly good topics
# yet still got hijacked purely because their draft/revise output happened
# to come out near-empty — the expand prompt's silence on topic/angle means
# it can't recover the real subject once its only input is degenerate.
#
# ``_classify_draft_substance`` is the single check shared by all three
# guard points below (draft retry, revise retry, expand-entry skip) so the
# "what counts as degenerate" definition can't drift between them. The
# default threshold (2 real words) is deliberately low — it catches the
# confirmed failure shape (0-1 real words: pure punctuation/dots) without
# false-positiving on legitimately terse-but-real content; tune upward via
# ``writer_min_substance_words`` if a less obvious degenerate shape is ever
# observed in production.
_REAL_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _classify_draft_substance(text: str, *, min_words: int) -> str:
    """Classify LLM output for the #691/#806 retry-guard family.

    Returns ``"ok"`` when the text clears the substance floor, ``"empty"``
    when it's blank/whitespace-only, or ``"degenerate"`` when it's non-blank
    but has fewer than ``min_words`` real (2+ letter alphabetic) words —
    catches a response dominated by ellipses/punctuation/digits that would
    pass a plain ``.strip()`` truthiness check.
    """
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    if len(_REAL_WORD_RE.findall(stripped)) < min_words:
        return "degenerate"
    return "ok"


def _resolve_min_substance_words(site_config: Any = None) -> int:
    """Minimum real-word count for :func:`_classify_draft_substance`.

    DB-configurable via ``writer_min_substance_words`` (default 2).
    """
    if site_config is None:
        return 2
    try:
        return site_config.get_int("writer_min_substance_words", 2)
    except Exception:  # noqa: BLE001 — defensive against test stubs
        # silent-ok: optional tuning knob — fall back to the documented
        # default when a stubbed site_config raises (matches
        # _resolve_max_revision_loops above).
        return 2


def _emit_degenerate_first_draft_kept_finding(*, task_id: str | None) -> None:
    """Loud-but-recovered canary: the first-draft call returned degenerate
    (non-empty, low-substance) output on both the initial attempt and its
    retry. There is no prior draft to fall back to (this IS the first
    draft), so the degenerate output is kept as-is. Self-heal is not silent
    (``feedback_self_heal_not_suppress``): the pipeline keeps producing
    content while the condition stays visible on the Findings dashboard.
    ``severity='warn'`` → Discord per the dispatcher policy.
    """
    logger.warning(
        "[two_pass_writer] first-draft call returned degenerate "
        "(non-empty, low-substance) output twice — kept it as-is; no prior "
        "draft exists to fall back to. A reasoning writer model "
        "intermittently emits only ellipses/punctuation; verify model "
        "health if this recurs. Refs poindexter#806.",
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001 — silent-ok: warning log above already fired; finding is a best-effort supplementary signal
        return
    try:
        emit_finding(
            source="modules.content.atoms.two_pass_writer",
            kind="writer_degenerate_draft_kept",
            title="First draft returned degenerate output — kept as-is",
            body=(
                "The first-draft call returned non-empty but low-substance "
                "content (e.g. dots/punctuation) on both the initial attempt "
                "and one retry. No prior draft exists at this point in the "
                "graph, so the degenerate output was kept rather than "
                "fabricated. A reasoning model that spends its budget in the "
                "thinking channel returns near-empty content intermittently; "
                "verify model health if this recurs. Refs poindexter#806."
            ),
            severity="warn",
            dedup_key="writer_degenerate_draft_kept",
            extra={"task_id": task_id, "reason": "first draft degenerate twice"},
        )
    except Exception:  # noqa: BLE001 — silent-ok: warning log above already fired; finding is a best-effort supplementary signal
        pass


def _emit_degenerate_revise_kept_prior_finding(*, model: str) -> None:
    """Loud-but-recovered canary: the default-path revise returned non-empty
    but degenerate (low-substance) content on both the initial call and its
    retry, so the writer kept the prior draft instead of adopting it —
    extends poindexter#691 (which only caught truly-empty output) to the
    poindexter#806 dots/punctuation shape. Distinct finding kind from the
    empty-revise guard so the two failure flavours stay distinguishable on
    the Findings dashboard. ``severity='warn'`` → Discord per the dispatcher
    policy.
    """
    logger.warning(
        "[two_pass_writer] default revise model=%r returned degenerate "
        "(non-empty, low-substance) content twice — kept the prior draft "
        "instead of adopting it. A reasoning writer model intermittently "
        "emits only ellipses/punctuation; verify model health if this "
        "recurs. Refs poindexter#806.",
        model,
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001 — silent-ok: warning log above already fired; finding is a best-effort supplementary signal
        return
    try:
        emit_finding(
            source="modules.content.atoms.two_pass_writer",
            kind="writer_degenerate_draft_kept_prior",
            title=(
                f"Default revise model {model!r} returned degenerate "
                f"output — kept prior draft"
            ),
            body=(
                f"The default-path revise call (model {model!r}) returned "
                f"non-empty but low-substance content (e.g. dots/punctuation) "
                f"on both the initial attempt and one retry. The writer kept "
                f"the pre-revision draft (unresolved [EXTERNAL_NEEDED] "
                f"markers stripped) so the post is not contaminated with a "
                f"topic-less expansion. A reasoning model that spends its "
                f"budget in the thinking channel returns degenerate content "
                f"intermittently; verify model health if this recurs. "
                f"Refs poindexter#806."
            ),
            severity="warn",
            dedup_key=f"writer_degenerate_draft_kept_prior:{model}",
            extra={"model": model, "reason": "revise returned degenerate twice"},
        )
    except Exception:  # noqa: BLE001 — silent-ok: warning log above already fired; finding is a best-effort supplementary signal
        pass


def _emit_degenerate_expand_input_finding(
    *, task_id: str | None, word_count: int,
) -> None:
    """Loud-but-recovered canary: the keep-best expansion pass was handed a
    degenerate (non-empty, low-substance) draft and refused to expand it.

    The expand prompt includes ONLY the draft text — never the topic/angle —
    so a model asked to "expand" a dots-only draft has nothing to work with
    but the dots and would invent an unrelated topic (the confirmed prod
    failure: poindexter#806, tasks 9921678f / e46b449c / ecaf0c01). Skipping
    the call keeps the draft honestly broken (obvious to downstream QA)
    instead of laundering it into a plausible-but-wrong article.
    ``severity='warn'`` → Discord per the dispatcher policy.
    """
    logger.warning(
        "[two_pass_writer] expansion entry received a degenerate draft "
        "(%d real word(s)) — skipped the expand call rather than risk it "
        "inventing an unrelated topic from the draft's punctuation alone. "
        "Refs poindexter#806.",
        word_count,
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001 — silent-ok: warning log above already fired; finding is a best-effort supplementary signal
        return
    try:
        emit_finding(
            source="modules.content.atoms.two_pass_writer",
            kind="writer_degenerate_draft_input",
            title="Expansion entry skipped a degenerate draft",
            body=(
                f"The keep-best expansion pass received a draft with only "
                f"{word_count} real word(s) — dots/punctuation-dominated. "
                f"The expand prompt does not restate the topic, so expanding "
                f"a degenerate draft risks the model inventing an unrelated "
                f"topic from the punctuation alone (the confirmed prod "
                f"failure behind poindexter#806). The expand call was "
                f"skipped and the draft was left unchanged; it will reach "
                f"QA as-is, which correctly flags severely off-topic/thin "
                f"content. If this recurs, check the upstream draft/revise "
                f"producer — the pipeline_writer_model may be stopping "
                f"early."
            ),
            severity="warn",
            dedup_key="writer_degenerate_draft_input",
            extra={"task_id": task_id, "word_count": word_count},
        )
    except Exception:  # noqa: BLE001 — silent-ok: warning log above already fired; finding is a best-effort supplementary signal
        pass


# -- nodes --

async def _embed_and_fetch_snippets(state: _State) -> _State:
    from services.topic_ranking import embed_text

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    if site_config is not None:
        snippet_limit = site_config.get_int("writer_rag_two_pass_snippet_limit", 20)
        multiplier = site_config.get_int("writer_rag_candidate_multiplier", 3)
        dedup_ceiling = site_config.get_float("writer_rag_dedup_ceiling", 0.93)
        mmr_lambda = site_config.get_float("writer_rag_mmr_lambda", 0.5)
    else:
        snippet_limit, multiplier, dedup_ceiling, mmr_lambda = 20, 3, 0.93, 0.5
    source_filter = _resolve_snippet_source_filter(site_config)
    source_caps = _parse_source_caps(site_config)
    qvec = await embed_text(
        f"{state['topic']} — {state['angle']}", site_config=site_config,  # type: ignore[arg-type]
    )
    # Convert to pgvector text format. asyncpg has no built-in codec for
    # Python list → pgvector; passing the raw list crashes with
    # "expected str, got list" before the ::vector cast can run.
    # Pattern matches services/embeddings_db.py:151 (the established way
    # this codebase passes vectors to pgvector queries).
    qvec_str = "[" + ",".join(str(v) for v in qvec) + "]"
    # Oversample the candidate pool so MMR + the near-duplicate ceiling have
    # room to de-echo before selecting the final snippet_limit. See
    # _select_snippets / project_rag_corpus_pollution (the INVERSE self-echo).
    candidate_limit = snippet_limit * max(1, multiplier)
    pool = _POOL_REGISTRY[state["pool_thread"]]
    async with pool.acquire() as conn:
        # source_table filter (corpus-pollution guard): only ground drafts in
        # content-bearing tables, never the claude_sessions / brain / audit
        # ops-log bulk of the corpus. See _resolve_snippet_source_filter.
        # `1 - (embedding <=> query)` is cosine SIMILARITY (pgvector `<=>` is
        # cosine distance); it feeds the near-duplicate ceiling + MMR relevance.
        rows = await conn.fetch(
            """
            SELECT source_table, source_id,
                   COALESCE(chunk_text, text_preview) AS snippet_text, embedding,
                   1 - (embedding <=> $1::vector) AS relevance
              FROM embeddings
             WHERE source_table = ANY($3::text[])
             ORDER BY embedding <=> $1::vector
             LIMIT $2
            """,
            qvec_str,
            candidate_limit,
            source_filter,
        )
    candidates = [
        {
            "source": r["source_table"],
            "ref": str(r["source_id"]),
            # The FULL matched chunk (poindexter#1033's payload column), not
            # the 500-char preview. This query is the writer's own retrieval
            # path — it does NOT go through rag_engine/MemoryClient, so the
            # #3452 fix did not reach it, and the single largest consumer of
            # retrieved text kept grounding drafts in prefixes. What the
            # writer is finally SHOWN is capped, and windowed onto the query,
            # at prompt-build time — see ai_content_generator's
            # _format_snippet_block. Carrying the whole chunk this far is what
            # lets that cap pick the relevant part rather than inheriting
            # whatever the first 500 characters happened to be.
            "snippet": r["snippet_text"],
            "relevance": float(r["relevance"]),
            "vec": _parse_vector(r["embedding"]),
        }
        for r in rows
    ]
    selected = _select_snippets(
        candidates,
        k=snippet_limit,
        dedup_ceiling=dedup_ceiling,
        mmr_lambda=mmr_lambda,
        source_caps=source_caps,
    )
    # Read backstop: scrub each snippet before it enters the writer prompt.
    # FAIL CLOSED — a scrub error drops the snippet rather than passing
    # unscrubbed operator text to a public writer (defense in depth over the
    # tap's write-path scrub; catches any source the write path missed).
    from services.rag_scrub import scrub_rag_text
    snippets: list[dict[str, Any]] = []
    for c in selected:
        try:
            clean = scrub_rag_text(c["snippet"] or "")
        except Exception as exc:  # noqa: BLE001 — never ground on unscrubbed text
            logger.warning(
                "[two_pass] snippet scrub failed (%s) — dropping ref %s",
                exc, c.get("ref"),
            )
            continue
        snippets.append({"source": c["source"], "ref": c["ref"], "snippet": clean})
    return {**state, "snippets": snippets, "revision_loops": 0,
            "external_lookups": [], "loop_capped": False,
            "writer_prompt_revise_chars": 0}


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
    context_bundle_chars = 0
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
            context_bundle_chars = len(ground_truth)
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
    # Prior-work anchor (#822) — optional soft framing section appended AFTER
    # the SOURCES block. Fail-open: any issue → no section, prompt unchanged.
    ig = state.get("internal_grounding") or {}
    ig_injected = False
    ig_source_table: str | None = None
    internal_grounding_chars = 0
    if _internal_grounding_enabled(site_config) and ig:
        section, ig_source_table = await _build_internal_grounding_section(
            ig, site_config=site_config, pool=pool,
        )
        if section:
            instruction = f"{instruction}\n\n---\n\n{section}"
            ig_injected = True
            internal_grounding_chars = len(section)

    draft_metrics: dict[str, int] = {}

    async def _call_draft() -> str:
        return await generate_with_context(
            topic=state["topic"], angle=state["angle"],
            snippets=state["snippets"], extra_instructions=instruction,
            site_config=site_config, pool=pool,
            task_id=state.get("task_id"),
            target_length=state.get("target_length", 1200),
            # Disable the writer model's reasoning channel (default) so a
            # thinking-capable model doesn't burn its budget reasoning and
            # truncate the visible draft. 2026-07-06 investigation.
            think=_resolve_writer_think(site_config),
            prompt_metrics=draft_metrics,
        )

    min_substance_words = _resolve_min_substance_words(site_config)
    draft = await _call_draft()
    # poindexter#806 — a writer model that burns its generation budget in a
    # hidden reasoning channel can emit a near-empty, non-blank response
    # (e.g. a handful of dots). Retry once before accepting it: a cheap
    # self-heal that avoids handing a degenerate draft to the rest of the
    # pipeline (revise/expand/QA/image-gen all run on whatever this
    # returns). No prior draft exists yet, so a still-degenerate retry is
    # kept as-is (never fabricated) with a visibility finding.
    if _classify_draft_substance(draft, min_words=min_substance_words) != "ok":
        retry_draft = await _call_draft()
        if _classify_draft_substance(retry_draft, min_words=min_substance_words) == "ok":
            draft = retry_draft
        else:
            _emit_degenerate_first_draft_kept_finding(task_id=state.get("task_id"))
    return {
        **state,
        "draft": draft,
        "internal_grounding_injected": ig_injected,
        "internal_grounding_source_table": ig_source_table,
        "writer_prompt_draft_chars": draft_metrics.get("prompt_chars", 0),
        "writer_prompt_snippet_chars": draft_metrics.get("snippet_chars", 0),
        "writer_prompt_research_chars": len(research_context),
        "writer_prompt_context_bundle_chars": context_bundle_chars,
        "writer_prompt_override_chars": len(override),
        "writer_prompt_internal_grounding_chars": internal_grounding_chars,
    }


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
    except Exception:  # noqa: BLE001 — silent-ok: WARNING already logged above; the finding import is optional belt-and-suspenders and must never block the writer fallback
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
    except Exception:  # noqa: BLE001 — silent-ok: WARNING already logged above; finding emission is belt-and-suspenders and must never raise inside the fallback/recovery path
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
    except Exception:  # noqa: BLE001 — silent-ok: WARNING already logged above; the finding import is optional belt-and-suspenders and must never block the writer recovery
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
    except Exception:  # noqa: BLE001 — silent-ok: WARNING already logged above; finding emission is belt-and-suspenders and must never raise inside the fallback/recovery path
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
    from services.llm_text import ollama_chat_text, resolve_writer_model

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    pool = _POOL_REGISTRY.get(state["pool_thread"])
    task_id = state.get("task_id")
    # Phase 1 lab harness — when the caller passed a writer_model_override
    # (because pick_variant assigned a model-axis variant), it lives in
    # the parallel _MODEL_OVERRIDE_REGISTRY (string is msgpack-friendly
    # but keeping the override registry pattern uniform with pool / site
    # config). resolve_writer_model accepts the explicit string and returns
    # it after stripping the ``ollama/`` prefix — no app_settings hit on
    # the variant path.
    model_override = _MODEL_OVERRIDE_REGISTRY.get(state["pool_thread"])
    # The default writer model — what the pipeline uses when no experiment
    # is running. Resolved with ``model=None`` so it reads the
    # ``pipeline_writer_model`` pin. This is the fallback target when a
    # variant override is unavailable: a single bad variant model must
    # NEVER zero the whole pipeline (poindexter#574).
    default_model = resolve_writer_model(model=None, site_config=site_config)
    model = resolve_writer_model(model=model_override, site_config=site_config)
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
    # Writer prompt-size observability (poindexter#868) — measured once per
    # node invocation, not once per underlying _call() retry: a retry (main
    # attempt + variant-fallback retry) resends this exact same
    # revise_prompt, so counting it twice would double-count identical text.
    revise_prompt_chars = len(revise_prompt)

    writer_think = _resolve_writer_think(site_config)

    async def _call(call_model: str) -> str:
        return await ollama_chat_text(
            revise_prompt,
            model=call_model,
            site_config=site_config,
            pool=pool,
            timeout_setting="niche_ollama_chat_timeout_seconds",
            task_id=task_id,
            phase="two_pass_revise",
            # Same reasoning-channel disable as the draft call — revise
            # rewrites prose, not chain-of-thought.
            think=writer_think,
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
        # then flowed into QA as a misleading reviewer_count:0 reject.
        # poindexter#806 extends the check to non-empty-but-degenerate output
        # (e.g. dots/punctuation) that ``.strip()`` truthiness lets through.
        # Retry ONCE with the same model (preserve writer quality — do NOT
        # downgrade the article body to a weaker model), and if still
        # empty/degenerate keep the PRIOR draft instead of adopting it.
        min_substance_words = _resolve_min_substance_words(site_config)
        new_draft = await _call(model)
        substance = _classify_draft_substance(new_draft, min_words=min_substance_words)
        if substance != "ok":
            retry = await _call(model)
            if _classify_draft_substance(retry, min_words=min_substance_words) == "ok":
                new_draft = retry
            else:
                prior = state.get("draft") or ""
                # Strip unresolved [EXTERNAL_NEEDED] markers so detect_needs
                # terminates the loop (instead of re-looping to the cap on the
                # same markers) and the markers don't leak into the post.
                new_draft = _NEED_PATTERN.sub("", prior).strip() or prior
                if substance == "empty":
                    _emit_empty_revise_kept_prior_finding(model=model)
                else:
                    _emit_degenerate_revise_kept_prior_finding(model=model)
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
        "writer_prompt_revise_chars": (
            state.get("writer_prompt_revise_chars", 0) + revise_prompt_chars
        ),
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


def _resolve_writer_think(site_config: Any = None) -> bool | None:
    """``think`` flag for the writer's draft / revise / expand LLM calls.

    Returns ``False`` (disable the reasoning channel) when
    ``writer_disable_thinking`` is on — the default. A thinking-capable writer
    model (e.g. the gemma-4-31B-it-qat QAT default) otherwise burns its
    generation budget in a hidden reasoning channel and the visible draft
    truncates mid-sentence or at a bare heading (2026-07-06 investigation).
    Returns ``None`` (omit the field, leave the call unchanged) when the switch
    is off — for deliberately letting a reasoning writer chain-of-think.
    """
    if site_config is None:
        return False
    try:
        raw = site_config.get("writer_disable_thinking", "true")
        return False if str(raw).lower() in ("true", "1", "yes") else None
    except Exception:  # noqa: BLE001 — defensive against test stubs
        # silent-ok: optional feature flag — default to disabling thinking
        # (the safe writer default) when a stubbed site_config raises.
        return False


# ---------------------------------------------------------------------------
# Prior-work anchor (#822 writer-consumer half).
#
# The discovery-side ranker (topic_batch_service, #2266) threads the single
# most-related internal item for a grounded external topic into the task
# metadata as {source_table, source_id, preview, similarity}. _draft_node turns
# it into an OPTIONAL soft "PRIOR WORK" section — a framing anchor, never a
# forced source. Fail-open at every step (→ no section); scrub FAIL-CLOSED (the
# grounding corpus includes non-publishable memory / claude_sessions rows).


def _internal_grounding_enabled(site_config: Any = None) -> bool:
    """Master switch for the writer-side prior-work anchor
    (``writer_internal_grounding_enabled``, default true). Off → no anchor.
    Mirrors ``_expansion_enabled``'s fail-safe default-on posture."""
    if site_config is None:
        return True
    try:
        raw = site_config.get("writer_internal_grounding_enabled", "true")
        return str(raw).lower() in ("true", "1", "yes")
    except Exception:  # noqa: BLE001 — defensive against test stubs
        # silent-ok: optional feature flag — default the anchor ON when a
        # stubbed site_config raises (matches _expansion_enabled).
        return True


async def _resolve_post_url(pool: Any, post_id: str, site_config: Any) -> str | None:
    """Resolve a published post's public URL from its id.

    Returns ``{site_url}/posts/{slug}`` (the form ai_content_generator uses to
    present internal links to this same writer), falling back to the relative
    ``/posts/{slug}`` when ``site_url`` is unset. None on any failure (blank id,
    no pool, no row, query error) → the caller uses the framing-only variant.
    ``embeddings.source_id`` for a post is ``str(posts.id)``, so the lookup
    casts ``id::text`` to stay type-safe for any posts.id column type. Never
    raises."""
    if not post_id or pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT slug FROM posts WHERE id::text = $1", post_id,
            )
    except Exception as exc:  # noqa: BLE001 — URL is best-effort, never fatal
        logger.warning("[two_pass] post-url lookup failed (%s) — framing-only", exc)
        return None
    slug = (row.get("slug") if row else None) or ""
    if not slug:
        return None
    try:
        site_url = (site_config.get("site_url", "") if site_config else "") or ""
    except Exception:  # noqa: BLE001
        site_url = ""
    site_url = site_url.rstrip("/")
    return f"{site_url}/posts/{slug}" if site_url else f"/posts/{slug}"


async def _build_internal_grounding_section(
    ig: dict[str, Any], *, site_config: Any, pool: Any,
) -> tuple[str, str | None]:
    """Compose the optional "PRIOR WORK" prompt section from a grounding match.

    Returns ``(section_text, source_table)`` when an anchor is injected, else
    ``("", None)``. Additive and non-raising: every failing check returns
    ``("", None)``.

    Order: eligibility (source_table ∈ writer_rag_source_filter) → scrub
    FAIL-CLOSED → post-URL resolution (posts only) → compose linkable /
    non-linkable variant.
    """
    source_table = str(ig.get("source_table") or "")
    if not source_table:
        return "", None
    # Eligibility rides the writer's existing corpus allowlist (default
    # ('posts',)) so ops content stays out of the public writer prompt unless
    # the operator broadens writer_rag_source_filter to include it.
    if source_table not in _resolve_snippet_source_filter(site_config):
        return "", None
    # Scrub FAIL-CLOSED — never inject unscrubbed operator text.
    from services.rag_scrub import scrub_rag_text
    try:
        preview = scrub_rag_text(str(ig.get("preview") or "")).strip()
    except Exception as exc:  # noqa: BLE001 — leak-safe: drop the anchor
        logger.warning(
            "[two_pass] internal_grounding preview scrub failed (%s) — no anchor", exc,
        )
        return "", None
    if not preview:
        return "", None
    url = None
    if source_table == "posts":
        url = await _resolve_post_url(pool, str(ig.get("source_id") or ""), site_config)
    if url:
        section = (
            "PRIOR WORK (something we've already published that this topic "
            "connects to — if there's a genuine throughline, open on it or weave "
            "the connection into your take in our own voice, and link it inline "
            "using the exact URL below. If the connection is thin, ignore this "
            "entirely: do NOT force it, and never copy the text below verbatim):"
            f"\n\n\"{preview}\"\n{url}"
        )
    else:
        section = (
            "PRIOR WORK (an internal note or past experience of ours that this "
            "topic connects to — if there's a genuine throughline, draw on it to "
            "ground your take in first-hand experience, in our own voice. Don't "
            "quote it, don't force the connection, and don't name internal tools "
            "or systems. If the connection is thin, ignore this entirely):"
            f"\n\n\"{preview}\""
        )
    return section, source_table


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
# echo signature — either the draft's first lines restating the known topic /
# angle / niche override (identity echo), or two+ of them matching the
# instruction-imperative patterns (instruction echo; added 2026-07-01 after
# tasks e46b449c / 9921678f leaked a PARAPHRASED expand prompt with zero
# identity lines) — so it is a no-op on clean drafts (a real article body
# never opens by listing its own topic/angle/niche as separate short lines,
# nor with two prompt imperatives in its first few lines). When it can't
# recover a substantial body it keeps the original untouched (never zeroes a
# draft) and the caller emits a finding so the model-quality signal stays
# visible (feedback_self_heal_not_suppress).

# Instruction-IMPERATIVE echoes — phrases from the writer/revise/expand
# prompts that finished prose essentially never opens a line with. This
# subset is precise enough to serve as a signature TRIGGER on its own (see
# ``_has_echo_signature``): the 2026-07-01 regression (tasks e46b449c +
# 9921678f) opened with a PARAPHRASE of the expand prompt ("Expand a draft
# from ~416 words ... to closer to 651 words. Genuine added substance ...")
# and echoed no topic/angle/brand lines at all, so the identity-only gate
# never opened and the leak reached the approval queue.
_ECHO_INSTRUCTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"expand (with|a|an|this|the|from|it)\b",
        r"revise (the|a|this)\b",
        r"add genuine (added )?substance\b",
        r"genuine (added )?substance\b",
        r"return (the )?complete\b",
        r"no padding\b",
        r"no preamble\b",
        r"preserve (every|existing|all)\b",
        r"end on a complete sentence\b",
        r"do not (repeat|pad|restate|invent|make up|add)\b",
    )
]

# Task-narration / metadata echoes — the model talking to itself. These
# classify lines during the STRIP but are too false-positive-prone to open
# the gate alone (a legit founder-voice intro can start "Let me show you" or
# "I'll be honest"), so they are excluded from the trigger subset above.
_ECHO_NARRATION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"input:\s",
        r"goal:\s",
        r"constraints?:?\s*$",
        r"context:\s",
        r"title:\s",
        r"markdown format\b",
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

_ECHO_SCAFFOLD_PATTERNS = [*_ECHO_INSTRUCTION_PATTERNS, *_ECHO_NARRATION_PATTERNS]


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
    """High-precision trigger for the preamble strip. Opens on either:

    * **identity echo** — the first few non-blank lines restate at least two
      of {topic, angle, niche-override}. A clean article body never does.
    * **instruction echo** — at least two of those lines match the
      instruction-imperative patterns ("Expand a draft...", "Do NOT pad...",
      "Preserve all facts..."), or one does alongside one identity echo. The
      2026-07-01 regression (e46b449c / 9921678f) paraphrased the expand
      prompt with ZERO identity lines, so identity alone is not enough. One
      instruction-shaped line alone never triggers — a real article can open
      with a single imperative ("Do not pad your Docker images...").
    """
    seen = 0
    cats: set[str] = set()
    instruction_lines = 0
    for raw in lines:
        norm = _norm_echo(raw)
        if not norm:
            continue
        seen += 1
        if any(p.match(norm) for p in _ECHO_INSTRUCTION_PATTERNS):
            instruction_lines += 1
        short = 0 < len(_echo_tokens(norm)) < 15
        if topic_norm and (norm == topic_norm or (short and _token_overlap(norm, topic_norm) >= 0.75)):
            cats.add("topic")
        elif angle_norm and _sep_norm(norm) == angle_norm:
            cats.add("angle")
        elif (norm in override_norms) or (
            override_fulltext and short and _token_overlap(norm, override_fulltext) >= 0.7
        ):
            cats.add("brand")
        # Window: 6 non-blank lines (was 4 pre-2026-07). Instruction blocks
        # run longer than identity blocks — e46b449c's third instruction line
        # and 9921678f's first bullet both sit past the old window.
        if seen >= 6:
            break
    return (
        len(cats) >= 2
        or instruction_lines >= 2
        or (len(cats) >= 1 and instruction_lines >= 1)
    )


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


# ---------------------------------------------------------------------------
# Planning-dump strip (#2036 follow-up, 2026-07-01).
#
# The instruction/identity echo guard above cannot see a PURE planning dump:
# task e46b449c opened with "*   Topic: Ellipses...", "*   Source Material
# provided:" — outline bullets inventorying the prompt's inputs — with zero
# instruction imperatives and only one identity-shaped line, so
# _has_echo_signature stayed closed and the dump rode the draft all the way
# to qa.programmatic, where the validator's planning_dump rule (#2036) now
# hard-rejects it. That reject costs the whole run (draft, images, every QA
# rail) even though the real article usually sits right under the dump,
# fused onto the last bullet. This guard self-heals at the writer instead.
#
# Gate parity: the trigger IS the validator's detect_planning_dump_preamble
# (>=6 bullets dominating >=60% of the pre-heading opening + >=2 planning-
# vocabulary families, 0/301 false positives on the published-post audit) —
# one detector, two consumers, so the strip fires exactly when the validator
# would reject and the two can never drift apart. The strip itself removes
# the opening only THROUGH ITS LAST BULLET line, which is what a naive
# strip-to-first-heading gets wrong twice: an intro paragraph sitting
# between the last bullet and the first heading survives, and an intro FUSED
# onto the last bullet ("...crime novel).An **ellipsis**...") is split at
# the glued sentence boundary and kept.

# Mirrors of the validator's structural regexes (strip here, detect there —
# the same duplication precedent as content_normalize_draft._FIRST_HEADING_RE
# vs _FIRST_HEADING_RE_FOR_VALIDATOR).
_PLANNING_BULLET_LINE_RE = re.compile(r"^[ \t]*(?:[*+\-]|\d+[.)])[ \t]+\S")
_PLANNING_FIRST_HEADING_RE = re.compile(
    r"(?:^|(?<=[^\n#]))(#{1,6}[ \t]+\S)", re.MULTILINE,
)
# Glued sentence boundary inside the last dump bullet: sentence-ending
# punctuation immediately followed — no whitespace — by a capitalized (or
# bold/quoted) article start. Ordinary prose always has a space there.
_FUSED_ARTICLE_BOUNDARY_RE = re.compile(r"(?<=[.!?])(?=[A-Z\"“*])")
# The fused tail must be a substantial sentence, not an acronym fragment
# ("U.S.A. style" would otherwise split at every internal dot).
_FUSED_TAIL_MIN_WORDS = 10
# Never gut a draft: below this survivor size the original is kept and the
# contamination stays a validator/finding problem (same floor as the echo
# strip above).
_PLANNING_STRIP_MIN_SURVIVING_WORDS = 50
# Mirror of the detector's _PLANNING_DUMP_MAX_OPENING_LINES — bounds the
# blast radius of a strip to the same window the detector reasons about.
_PLANNING_STRIP_MAX_OPENING_LINES = 50


def _strip_planning_dump_preamble(draft: str) -> tuple[str, int]:
    """Strip an opening planning/outline bullet dump off the front of a draft.

    Returns ``(clean_draft, non_blank_lines_stripped)`` — ``(draft, 0)``
    whenever the validator's detector stays silent, when fewer than 50 words
    would survive, or when the dump has no recoverable article under it.

    Structural, unlike :func:`_strip_echoed_preamble`: no topic/angle/override
    context is needed, because the signature is the bullet-dominated opening
    itself. Code spans are masked before gating (offset-preserving, so slice
    positions computed on the masked text apply to the raw draft) — a post
    that QUOTES a planning dump in a fence is never touched.
    """
    if not draft or not draft.strip():
        return draft, 0
    from modules.content.content_validator import (
        _strip_code_spans,
        detect_planning_dump_preamble,
    )
    masked = _strip_code_spans(draft)
    if not detect_planning_dump_preamble(masked):
        return draft, 0

    # Opening region, mirroring the detector: skip one leading heading line
    # (the generated H1 title stays), then everything up to the first —
    # possibly glued — heading is the opening.
    opening_start = 0
    lead_ws = len(masked) - len(masked.lstrip())
    if masked[lead_ws:].startswith("#"):
        first_line, sep, _ = masked[lead_ws:].partition("\n")
        if re.match(r"#{1,6}[ \t]+\S", first_line):
            opening_start = lead_ws + len(first_line) + len(sep)
    heading = _PLANNING_FIRST_HEADING_RE.search(masked, opening_start)
    opening_end = heading.start(1) if heading else len(masked)

    # Find the LAST bullet line inside the opening (within the same
    # non-blank-line window the detector counts).
    last_bullet_span: tuple[int, int] | None = None
    non_blank_seen = 0
    pos = opening_start
    while pos < opening_end:
        nl = masked.find("\n", pos, opening_end)
        line_end = nl if nl != -1 else opening_end
        line = masked[pos:line_end]
        if line.strip():
            non_blank_seen += 1
            if non_blank_seen > _PLANNING_STRIP_MAX_OPENING_LINES:
                break
            if _PLANNING_BULLET_LINE_RE.match(line):
                last_bullet_span = (pos, line_end)
        pos = line_end + 1 if nl != -1 else opening_end
    if last_bullet_span is None:  # defensive — detector implies bullets exist
        return draft, 0

    lb_start, lb_end = last_bullet_span
    strip_end = lb_end + 1 if draft[lb_end:lb_end + 1] == "\n" else lb_end
    # Fused-article split: scan the masked line (so a boundary inside an
    # inline code span can't match), slice the raw draft at the same offset.
    for m in _FUSED_ARTICLE_BOUNDARY_RE.finditer(masked, lb_start, lb_end):
        tail = draft[m.start():lb_end]
        if len(tail.split()) >= _FUSED_TAIL_MIN_WORDS:
            strip_end = m.start()
            break

    # Whatever sits between the strip point and the heading is either the
    # article's intro (substantial — keep it) or leftover model narration
    # ("(Proceeding to generate based on these steps).") glued ahead of the
    # heading — junk. The same >= _FUSED_TAIL_MIN_WORDS floor discriminates;
    # eating a junk tail also re-anchors a heading the writer glued mid-line
    # (ba4d627a shape), same as content_normalize_draft's heading re-anchor.
    if heading is not None and strip_end < opening_end:
        if len(draft[strip_end:opening_end].split()) < _FUSED_TAIL_MIN_WORDS:
            strip_end = opening_end

    remainder = draft[strip_end:].lstrip("\n")
    if len(remainder.split()) < _PLANNING_STRIP_MIN_SURVIVING_WORDS:
        return draft, 0
    stripped_lines = sum(
        1 for ln in draft[opening_start:strip_end].splitlines() if ln.strip()
    )
    kept_prefix = draft[:opening_start]
    if kept_prefix.strip():
        return kept_prefix.rstrip("\n") + "\n\n" + remainder, stripped_lines
    return remainder, stripped_lines


def _emit_planning_dump_finding(*, stripped_lines: int, task_id: str | None) -> None:
    """Loud-but-recovered canary: the writer opened the draft with a planning/
    outline bullet dump and the structural guard stripped it. Self-heal is not
    silent (feedback_self_heal_not_suppress) — the pipeline keeps the recovered
    article while the model-quality signal stays visible on the Findings
    dashboard, distinct from the instruction-echo kind so the two failure
    flavours can be tracked separately. ``severity='warn'`` → Discord."""
    logger.warning(
        "[two_pass_writer] writer opened the draft with a planning/outline dump "
        "(%d bullet/preamble line(s)); stripped it and kept the article. "
        "Recurring dumps mean the writer model is emitting its plan instead of "
        "executing it — the same shape the validator's planning_dump rule "
        "(#2036) hard-rejects at qa.programmatic.",
        stripped_lines,
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001  # silent-ok: emit is best-effort; the WARNING log above already surfaced the dump
        return
    try:
        emit_finding(
            source="modules.content.atoms.two_pass_writer",
            kind="writer_planning_dump_stripped",
            title=(
                f"Writer emitted a planning dump ({stripped_lines} line(s)) — "
                f"stripped preamble"
            ),
            body=(
                f"The two_pass writer opened the draft with {stripped_lines} "
                f"line(s) of planning/outline bullets (topic/source inventory, "
                f"section plans, notes-to-self) instead of the article. The "
                f"structural guard — gated on the validator's "
                f"detect_planning_dump_preamble (#2036) — stripped the dump and "
                f"kept the article body, including an intro fused onto the last "
                f"bullet. Without the strip this draft would hard-reject at "
                f"qa.programmatic after the full pipeline run. If this recurs, "
                f"the writer model (pipeline_writer_model) is dumping its plan "
                f"on long prompts — consider a stronger writer."
            ),
            severity="warn",
            dedup_key="writer_planning_dump_stripped",
            extra={"stripped_lines": stripped_lines, "task_id": task_id},
        )
    except Exception:  # noqa: BLE001  # silent-ok: finding emission must never raise; the WARNING log above is the durable signal
        pass


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


# ---------------------------------------------------------------------------
# Dangling-heading trim (early-stop / thinking-budget truncation guard,
# 2026-07-06).
#
# gemma-4-31B-it-qat (the ``pipeline_writer_model``) is a thinking-capable QAT
# model: its chat template opens a ``<|channel>thought`` reasoning channel, and
# the writer path does not disable it, so on some draws the model spends its
# generation budget reasoning and the VISIBLE draft stops early. A/B repro on
# the real draft prompt (scripts/experiments) put 8/8 gemma drafts under target,
# and a recurring shape is a draft that ENDS at a bare section heading
# ("## Local Inference vs", "## The Technical") with no body under it.
#
# That shape is structurally broken but slips through BOTH existing guards:
#   - ``content_normalize_draft`` strips a LEADING scaffold, never a trailing tail;
#   - the ``content_validator`` ``truncated_content`` rule EXEMPTS a trailing
#     heading (a heading legitimately has no terminal punctuation), so a draft
#     that ends AT one is never flagged as truncated.
# It also poisons the keep-best expansion: the dangling heading inflates the
# word count used for the expansion threshold, and the expand prompt is told to
# "preserve every existing ... heading", so the broken heading can survive
# verbatim into the persisted post.
#
# This guard deterministically drops a bare heading that sits as the final
# non-blank line of a draft (no LLM call), and runs BEFORE the expansion
# decision so (a) the persisted body ends on real prose and (b) the keep-best
# length comparison uses the true body length. It only ever removes trailing
# heading line(s) (looping to clean a stacked ``## A\n\n## B`` tail); it never
# touches a draft whose last non-blank line is prose / list / code, and it never
# zeroes a draft (a heading-only body is left untouched for the caller's finding
# + human review). Complements the mid-sentence case, which the validator's
# ``truncated_content`` rule already catches → reject → rescue.
_DANGLING_HEADING_RE = re.compile(r"(?:\A|\n)[ \t]*(#{1,6}[ \t][^\n]*?)[ \t]*\Z")


def _trim_dangling_heading(draft: str) -> tuple[str, int]:
    """Strip a bare Markdown heading sitting as the final non-blank line.

    Returns ``(clean_draft, headings_trimmed)`` — ``(draft, 0)`` when the last
    non-blank line is prose / list / code, or when trimming would leave nothing.
    Loops so a draft ending in stacked headings is fully cleaned.
    """
    if not draft or not draft.strip():
        return draft, 0
    body = draft
    trimmed = 0
    while True:
        stripped = body.rstrip()
        m = _DANGLING_HEADING_RE.search(stripped)
        if not m:
            break
        candidate = stripped[: m.start()].rstrip()
        if not candidate.strip():
            break  # heading-only body — never zero the draft
        body = candidate
        trimmed += 1
    return (body, trimmed) if trimmed else (draft, 0)


def _emit_dangling_tail_finding(*, trimmed: int, task_id: str | None) -> None:
    """Loud-but-recovered canary: the writer stopped at a bare trailing heading
    and the deterministic guard dropped it. Self-heal is not silent
    (feedback_self_heal_not_suppress) — the pipeline keeps the recovered body
    while the model-quality signal stays visible on the Findings dashboard,
    distinct from the echo/planning-dump kinds so the truncation flavour is
    tracked separately. ``severity='warn'`` → Discord per the dispatcher policy.
    """
    logger.warning(
        "[two_pass_writer] writer draft ended at a bare heading (%d trailing "
        "heading line(s), no body under it); trimmed it and kept the article. "
        "Recurring dangling headings mean the writer model is stopping early — "
        "the thinking-capable pipeline_writer_model can spend its budget in the "
        "reasoning channel and cut the visible draft short.",
        trimmed,
    )
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001  # silent-ok: emit is best-effort; the WARNING log above already surfaced the trim
        return
    try:
        emit_finding(
            source="modules.content.atoms.two_pass_writer",
            kind="writer_dangling_heading_trimmed",
            title=f"Writer stopped at a bare heading ({trimmed} line(s)) — trimmed tail",
            body=(
                f"The two_pass writer produced a draft that ended at {trimmed} "
                f"bare section heading(s) with no body underneath — an early-stop "
                f"truncation shape the ``truncated_content`` validator exempts "
                f"(headings legitimately lack terminal punctuation). The "
                f"deterministic guard trimmed the trailing heading(s) so the "
                f"persisted body ends on prose and the keep-best expansion sees "
                f"the true word count. Recurring hits mean the writer model "
                f"(pipeline_writer_model) is stopping early; the thinking-capable "
                f"gemma QAT model can burn its generation budget in the reasoning "
                f"channel — consider disabling thinking for the writer or a "
                f"stronger writer."
            ),
            severity="warn",
            dedup_key="writer_dangling_heading_trimmed",
            extra={"trimmed": trimmed, "task_id": task_id},
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
        "planning_stripped": 0,
        "dangling_trimmed": 0,
        "degenerate_input": False,
    }
    if not (draft or "").strip() or target_length <= 0:
        return draft, meta
    if not _expansion_enabled(site_config):
        return draft, meta
    # poindexter#806 — the primary guard. The expand prompt includes ONLY
    # the draft text, never the topic/angle, so a model asked to "expand" a
    # degenerate (non-empty, low-substance) draft has nothing to work with
    # but its punctuation and invents an unrelated topic — the confirmed
    # prod failure this guard closes (tasks 9921678f / e46b449c /
    # ecaf0c01). Refuse the call entirely rather than risk laundering junk
    # into a plausible-but-wrong article; downstream QA correctly flags the
    # untouched degenerate draft as off-topic/thin.
    min_substance_words = _resolve_min_substance_words(site_config)
    if _classify_draft_substance(draft, min_words=min_substance_words) == "degenerate":
        meta["degenerate_input"] = True
        _emit_degenerate_expand_input_finding(task_id=task_id, word_count=words_before)
        return draft, meta
    threshold = int(target_length * _resolve_min_length_ratio(site_config))
    if words_before >= threshold:
        return draft, meta

    from services.llm_text import ollama_chat_text, resolve_writer_model
    model = resolve_writer_model(model=None, site_config=site_config)
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
            # Same reasoning-channel disable as draft/revise — the expansion
            # runs on the same writer model and must not truncate itself.
            think=_resolve_writer_think(site_config),
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
    # Same compounding hazard, planning-dump flavour: an expansion that opens
    # with an outline dump is "longer" only because of the dump, so it would
    # win keep-best and bake a hard qa.programmatic reject (#2036) into the
    # post. Strip structurally before the word-count comparison.
    expanded, exp_planning = _strip_planning_dump_preamble(expanded)
    meta["planning_stripped"] = exp_planning
    # Same compounding hazard, early-stop flavour: the expansion runs on the
    # same thinking-capable writer model, so it can ITSELF stop at a bare
    # trailing heading. Trim that before the keep-best comparison so a
    # dangling-heading expansion can't win keep-best and bake the broken tail
    # into the post.
    expanded, exp_dangling = _trim_dangling_heading(expanded)
    meta["dangling_trimmed"] = exp_dangling
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
    - ``internal_grounding`` — the {source_table, source_id, preview,
      similarity} match threaded from generate_content.py (#822). When set
      and eligible, _draft_node appends a soft "PRIOR WORK" anchor section.
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
            "internal_grounding": (
                kw.get("internal_grounding")
                if isinstance(kw.get("internal_grounding"), dict) else {}
            ),
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
        # Planning-dump guard (#2036 follow-up): a pure outline dump carries
        # zero instruction/identity lines, so the echo signature above stays
        # closed on it — the structural gate (the validator's own detector)
        # catches that shape and recovers the article fused under the dump.
        base_draft, dump_pre = _strip_planning_dump_preamble(base_draft)
        # Dangling-heading guard (2026-07-06): the thinking-capable writer model
        # can stop early and leave the draft ENDING at a bare section heading
        # with no body under it — a shape the truncated_content validator
        # exempts. Trim it BEFORE the expansion decision so (a) the persisted
        # body ends on prose and (b) the keep-best threshold + length comparison
        # use the true body length, not a heading-inflated one. No-op when the
        # last line is real content.
        base_draft, tail_pre = _trim_dangling_heading(base_draft)
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
        planning_stripped = dump_pre + int(expand_meta.get("planning_stripped", 0))
        if planning_stripped:
            _emit_planning_dump_finding(
                stripped_lines=planning_stripped, task_id=task_id,
            )
        dangling_trimmed = tail_pre + int(expand_meta.get("dangling_trimmed", 0))
        if dangling_trimmed:
            _emit_dangling_tail_finding(trimmed=dangling_trimmed, task_id=task_id)
        return {
            "draft": _scrub_private_repo_refs(draft_out),
            "snippets_used": final.get("snippets", []),
            "external_lookups": final.get("external_lookups", []),
            "revision_loops": final.get("revision_loops", 0),
            "loop_capped": final.get("loop_capped", False),
            # Prompt-echo guard observability — how many scaffolding/preamble
            # lines the writer regurgitated and the guard stripped (0 = clean).
            "prompt_echo_stripped": echo_stripped,
            # Planning-dump guard observability — bullet/preamble lines removed
            # by the structural strip (0 = clean), counted separately from the
            # instruction/identity echo so the two failure flavours stay
            # distinguishable on the Findings dashboard.
            "planning_dump_stripped": planning_stripped,
            # Dangling-heading guard observability — trailing bare heading(s)
            # trimmed off an early-stopped draft (0 = clean), counted separately
            # from the echo / planning-dump strips so the truncation flavour is
            # its own signal on the Findings dashboard.
            "dangling_heading_trimmed": dangling_trimmed,
            # Length-enforcement observability — did the expansion pass fire,
            # and the word counts before/after. Surfaced so the caller stage
            # can record it (capability_outcomes / metrics).
            "length_expanded": expand_meta["expanded"],
            "words_before_expand": expand_meta["words_before"],
            "words_after_expand": expand_meta["words_after"],
            # Degenerate-draft guard observability (poindexter#806) — did the
            # expansion entry refuse a non-empty-but-low-substance draft
            # (0 = clean), counted separately from the other guards so this
            # failure flavour is its own signal on the Findings dashboard.
            "degenerate_draft_input": expand_meta.get("degenerate_input", False),
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
            # Writer prompt-size observability (poindexter#868).
            "writer_prompt_draft_chars": final.get("writer_prompt_draft_chars", 0),
            "writer_prompt_snippet_chars": final.get("writer_prompt_snippet_chars", 0),
            "writer_prompt_research_chars": final.get("writer_prompt_research_chars", 0),
            "writer_prompt_context_bundle_chars": final.get("writer_prompt_context_bundle_chars", 0),
            "writer_prompt_override_chars": final.get("writer_prompt_override_chars", 0),
            "writer_prompt_internal_grounding_chars": final.get("writer_prompt_internal_grounding_chars", 0),
            "writer_prompt_revise_chars": final.get("writer_prompt_revise_chars", 0),
            # Prior-work anchor observability (#822) — did an anchor reach the
            # draft prompt this run, and from which corpus type (None = no anchor).
            "internal_grounding_anchor_injected": bool(
                final.get("internal_grounding_injected", False)
            ),
            "internal_grounding_source_table": final.get(
                "internal_grounding_source_table"
            ),
        }
    finally:
        _POOL_REGISTRY.pop(thread_id, None)
        _SITE_CONFIG_REGISTRY.pop(thread_id, None)
        _MODEL_OVERRIDE_REGISTRY.pop(thread_id, None)

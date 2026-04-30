"""Migration 0119: seed app_settings keys for the niche-pivot + RAG writer files.

Sweep target: the recently-shipped niche-pivot files (#278) had a number
of hardcoded literals (snippet LIMITs, decay factors, max-loop caps,
model names, timeouts) that fit the project's "Config in DB, not code"
principle from CLAUDE.md — operators / SaaS tenants should be able to
tune them per-deployment without editing code.

Per Matt's CLAUDE.md: "If you write a literal in production code, ask
'could a customer tune this?' — if yes, it goes in app_settings."

Each row is idempotent (``ON CONFLICT (key) DO NOTHING``) so re-running
the migration leaves any operator-set value alone. Keeping the same
literal as the FALLBACK in each ``site_config.get(...)`` call site means
behavior is preserved on installs that haven't applied this migration
yet.

Two follow-ups intentionally LEFT for a second pass:

* The ``GOAL_DESCRIPTIONS`` dict in ``topic_ranking.py`` is shipped as a
  single JSON-blob app_setting (``niche_goal_descriptions``). The cleaner
  long-term shape is a dedicated ``niche_goal_prompts`` table keyed by
  ``(niche_id, goal_type)`` so each operator can tune their own goal
  prose without colliding with other operators in a future SaaS context.
  That table-extraction is its own follow-up; storing the JSON now
  unblocks per-operator overrides without a schema change.
* The ``cost_tier`` model-router migration ("free / budget / standard /
  premium" lookup instead of raw model names) is a wider refactor — for
  this sweep, hardcoded ``"glm-4.7-5090:latest"`` strings in the niche
  files now defer to the existing ``pipeline_writer_model`` app_setting
  (introduced before #198), which is the closest pre-existing tier
  already wired through ``services/site_config``.

Cross-references:
- Niche-pivot design: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
- Niche-pivot tables: migration 0113
- Auto-discovery kill-switch: migration 0118
- Existing pipeline_writer_model usage: services/ai_content_generator.py:636
"""

import json

from services.logger_config import get_logger

logger = get_logger(__name__)


_GOAL_DESCRIPTIONS_DEFAULT = {
    "TRAFFIC":     "Topic likely to attract organic search traffic; trending keyword, broad appeal, evergreen demand.",
    "EDUCATION":   "Topic that teaches the reader something concrete and useful they didn't know before.",
    "BRAND":       "Topic that reinforces the operator's positioning and unique perspective.",
    "AUTHORITY":   "Topic that demonstrates the operator's depth and expertise on something specific.",
    "REVENUE":     "Topic that drives a commercial outcome: signups, sales, conversions, paid feature awareness.",
    "COMMUNITY":   "Topic that resonates with the operator's existing audience; sparks discussion, shares, replies.",
    "NICHE_DEPTH": "Topic that goes deep on the operator's niche specialty rather than broad-audience content.",
}


def _seed_settings() -> list[tuple[str, str, str, str]]:
    """Build the seed rows. Each tuple = (key, value, category, description).

    All non-secret. Defaults exactly match the literals previously hardcoded
    in the source files so behaviour is unchanged after migration applies.
    """
    return [
        # ------------------------------------------------------------------
        # topic_ranking.py
        # ------------------------------------------------------------------
        (
            "niche_embedding_model",
            "nomic-embed-text",
            "niche_pivot",
            (
                "Ollama embedding model used for niche topic ranking and "
                "writer-mode RAG snippet retrieval. Default 'nomic-embed-text' "
                "matches the prior hardcoded literal in topic_ranking.py "
                "(_embed_text_cached). Override with another locally-pulled "
                "embedding model (must produce 768-dim vectors to match "
                "embeddings.embedding column type)."
            ),
        ),
        (
            "niche_ollama_chat_timeout_seconds",
            "60",
            "niche_pivot",
            (
                "HTTP timeout (seconds) for direct Ollama /api/chat calls "
                "made by topic_ranking._ollama_chat_json — used by the LLM "
                "scorer, internal RAG distillation, story_spine outline pass, "
                "and two_pass revise pass. Default 60 matches the prior "
                "hardcoded httpx timeout. Higher = tolerate slower local "
                "models; lower = fail fast on a wedged Ollama."
            ),
        ),
        (
            "niche_goal_descriptions",
            json.dumps(_GOAL_DESCRIPTIONS_DEFAULT),
            "niche_pivot",
            (
                "JSON blob mapping each goal_type (TRAFFIC, EDUCATION, BRAND, "
                "AUTHORITY, REVENUE, COMMUNITY, NICHE_DEPTH) to the prose "
                "anchor used to compute its goal vector embedding. Operators "
                "tune the prose to nudge what each goal weight 'means' for "
                "their niche. Follow-up: extract to a dedicated "
                "niche_goal_prompts (niche_id, goal_type) table so each "
                "operator can override per-niche; current single-blob shape "
                "is a system-wide override that mirrors the prior dict "
                "constant in topic_ranking.py."
            ),
        ),
        # ------------------------------------------------------------------
        # topic_batch_service.py
        # ------------------------------------------------------------------
        (
            "niche_carry_forward_decay_factor",
            "0.7",
            "niche_pivot",
            (
                "Multiplicative decay applied to a candidate's decay_factor "
                "each time it survives a batch unpicked. Default 0.7 matches "
                "the prior hardcoded literal in TopicBatchService."
                "_load_carry_forward — by the third batch a candidate has "
                "decayed to 0.7^3 = 0.343 of its original score. Lower = "
                "more aggressive (older candidates fall off the rank list "
                "faster); higher = stickier (the same topic keeps "
                "resurfacing for many batches)."
            ),
        ),
        (
            "niche_internal_rag_per_kind_limit",
            "4",
            "niche_pivot",
            (
                "Per-source-kind limit passed to InternalRagSource.generate "
                "by TopicBatchService._discover_internal. Default 4 matches "
                "the prior hardcoded literal — pulls 4 most-recent items "
                "from each of {claude_session, brain_knowledge, audit_event, "
                "decision_log, memory_file, post_history} per discovery "
                "sweep. Higher = larger candidate pool, more LLM tokens "
                "spent on distillation; lower = faster sweep, cheaper."
            ),
        ),
        (
            "niche_top_n_per_pool",
            "5",
            "niche_pivot",
            (
                "Top N candidates per pool (external + internal) carried "
                "forward from the embedding pre-rank into the LLM final-"
                "score stage. Default 5 matches the prior hardcoded slice "
                "in TopicBatchService._embed_and_pre_rank and run_sweep "
                "(top10 = pool_external[:5] + pool_internal[:5]). Higher = "
                "wider LLM prompt, more candidates to rank; lower = tighter "
                "shortlist, cheaper LLM call."
            ),
        ),
        (
            "niche_batch_expires_days",
            "7",
            "niche_pivot",
            (
                "Number of days a topic_batch row stays open before its "
                "expires_at watermark trips. Default 7 matches the prior "
                "hardcoded timedelta(days=7) in TopicBatchService._write_"
                "batch. Higher = give the operator more time to rank/"
                "resolve a batch; lower = force timely operator action."
            ),
        ),
        # ------------------------------------------------------------------
        # internal_rag_source.py
        # ------------------------------------------------------------------
        (
            "niche_internal_rag_snippet_max_chars",
            "600",
            "niche_pivot",
            (
                "Per-snippet character cap when joining raw snippets into "
                "the topic/angle distillation prompt in InternalRagSource."
                "_distill_topic_angle. Default 600 matches the prior "
                "hardcoded slice (s[:600]). Higher = more context per "
                "snippet, larger prompt; lower = tighter prompt, faster + "
                "cheaper distillation."
            ),
        ),
        # ------------------------------------------------------------------
        # writer_rag_modes/topic_only.py
        # ------------------------------------------------------------------
        (
            "writer_rag_topic_only_snippet_limit",
            "8",
            "writer_rag",
            (
                "Top-N pgvector snippets fetched and dumped into the "
                "TOPIC_ONLY writer prompt as background context. Default 8 "
                "matches the prior hardcoded LIMIT 8 in topic_only.run. "
                "Higher = more context in the writer prompt, longer + "
                "potentially better-grounded drafts but slower generation; "
                "lower = leaner prompt, faster but more terse."
            ),
        ),
        # ------------------------------------------------------------------
        # writer_rag_modes/citation_budget.py
        # ------------------------------------------------------------------
        (
            "writer_rag_citation_budget_snippet_limit",
            "12",
            "writer_rag",
            (
                "Top-N pgvector snippets fetched in the CITATION_BUDGET "
                "writer mode. Default 12 matches the prior hardcoded "
                "LIMIT 12 in citation_budget.run. Higher than TOPIC_ONLY "
                "by design — the writer is required to cite at least N of "
                "them so it needs more candidates to choose from. Higher = "
                "more citation choices for the writer; lower = forces "
                "tighter relevance match per citation."
            ),
        ),
        (
            "writer_rag_citation_budget_min_citations",
            "3",
            "writer_rag",
            (
                "Minimum number of internal sources the CITATION_BUDGET "
                "writer must cite by [source/ref] tag. Default 3 matches "
                "the prior DEFAULT_MIN_CITATIONS constant. The "
                "content_validator extension (follow-up) enforces this "
                "post-write — drafts under the threshold are rejected. "
                "Higher = more groundedness, harder for the writer to "
                "satisfy; lower = looser citation requirement."
            ),
        ),
        # ------------------------------------------------------------------
        # writer_rag_modes/story_spine.py
        # ------------------------------------------------------------------
        (
            "writer_rag_story_spine_snippet_limit",
            "15",
            "writer_rag",
            (
                "Top-N pgvector snippets fed into the STORY_SPINE outline "
                "preprocessing pass. Default 15 matches the prior "
                "hardcoded LIMIT 15 in story_spine.run. Larger than "
                "TOPIC_ONLY/CITATION_BUDGET by design — STORY_SPINE needs "
                "enough raw material to extract a 5-beat outline (hook / "
                "what_happened / why_it_matters / what_we_learned / close). "
                "Higher = richer outline source material; lower = leaner "
                "outline pass."
            ),
        ),
        (
            "writer_rag_story_spine_snippet_max_chars",
            "600",
            "writer_rag",
            (
                "Per-snippet character cap when assembling the snippet "
                "block for the STORY_SPINE outline prompt. Default 600 "
                "matches the prior hardcoded slice (s['snippet'][:600]). "
                "Higher = more context per snippet at the cost of prompt "
                "length; lower = tighter prompt."
            ),
        ),
        # ------------------------------------------------------------------
        # writer_rag_modes/two_pass.py
        # ------------------------------------------------------------------
        (
            "writer_rag_two_pass_snippet_limit",
            "20",
            "writer_rag",
            (
                "Top-N pgvector snippets fetched up-front in the TWO_PASS "
                "internal-first draft. Default 20 matches the prior "
                "hardcoded LIMIT 20 in _embed_and_fetch_snippets. Largest "
                "of the four writer modes by design — TWO_PASS needs a "
                "broad internal context base before the conditional "
                "external-augment loop kicks in. Higher = stronger "
                "internal grounding, longer initial prompt; lower = "
                "skinnier first draft, more reliance on the external "
                "research pass."
            ),
        ),
        (
            "writer_rag_two_pass_max_revision_loops",
            "3",
            "writer_rag",
            (
                "Hard cap on revise → detect_needs → research_each → "
                "revise loops in the TWO_PASS LangGraph state machine. "
                "Default 3 matches the prior _MAX_REVISION_LOOPS module "
                "constant. Higher = give the writer more chances to fill "
                "[EXTERNAL_NEEDED:...] markers but increased risk of "
                "runaway / cost; lower = fail-fast on persistent gaps."
            ),
        ),
        (
            "writer_rag_two_pass_research_max_sources",
            "2",
            "writer_rag",
            (
                "max_sources passed to research_topic for each "
                "[EXTERNAL_NEEDED: ...] marker the TWO_PASS draft surfaces. "
                "Default 2 matches the prior hardcoded literal in "
                "two_pass._research_each. Higher = more thorough research "
                "per gap, slower revise loop; lower = faster but flimsier "
                "external augmentation."
            ),
        ),
        # ------------------------------------------------------------------
        # ai_content_generator.py — generate_with_context / _outline helpers
        # ------------------------------------------------------------------
        (
            "writer_rag_context_snippet_max_chars",
            "500",
            "writer_rag",
            (
                "Per-snippet character cap when building the snippet block "
                "for generate_with_context and generate_with_outline (the "
                "two helpers used by all writer RAG modes). Default 500 "
                "matches the prior hardcoded slice (s['snippet'][:500]) in "
                "ai_content_generator.py. Higher = richer per-snippet "
                "context inside the writer prompt; lower = tighter prompt "
                "to fit more snippets / leave room for instructions."
            ),
        ),
        # ------------------------------------------------------------------
        # research_service.py — research_topic shim for TWO_PASS
        # ------------------------------------------------------------------
        (
            "writer_rag_research_topic_max_sources",
            "2",
            "writer_rag",
            (
                "Default max_sources for the module-level research_topic() "
                "shim used by the TWO_PASS writer mode. Default 2 matches "
                "the prior hardcoded default. Note: per the docstring on "
                "research_topic, this value is currently advisory — "
                "ResearchService.build_context caps internally (5 web "
                "results, 8 references). When that internal cap is "
                "plumbed through, this setting becomes the actual cap."
            ),
        ),
    ]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration 0119"
            )
            return

        rows = _seed_settings()
        inserted = 0
        for key, value, category, description in rows:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 0119: seeded %d/%d niche-pivot + writer_rag settings "
            "(remainder were operator-set already)",
            inserted, len(rows),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        keys = [k for k, *_ in _seed_settings()]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            keys,
        )
        logger.info(
            "Migration 0119 rolled back: removed %d settings", len(keys),
        )

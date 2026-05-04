"""Migration 0141: wire up the dormant premium-prompt + niche-override system.

Two parts, both data-only — no schema changes:

1. **Seed ``premium_active=false`` in app_settings** so operators can see
   the toggle in ``poindexter settings list``. The companion code change
   makes ``UnifiedPromptManager.load_from_db`` honour this flag — when
   true, the loader merges ``prompt_templates`` rows with
   ``source = 'premium'`` on top of the ``'default'`` rows; when false
   (the OSS default), only ``'default'`` rows load. The ``source`` column
   was added by migration 0092 specifically for this gating but the
   loader never filtered on it — fixing that now.

2. **Seed a ``writer_prompt_override`` for the ``glad-labs`` niche.** The
   ``niches.writer_prompt_override`` column has been dormant since
   migration 0113 (column added) / 0134 (dev_diary seeded) — the writer
   pipeline never read it. The companion change wires it through
   ``stages/generate_content.py::_generate_via_writer_mode`` →
   ``dispatch_writer_mode`` → ``two_pass.run`` →
   ``generate_with_context(extra_instructions=...)``.

   The glad-labs override here is intentionally a **generic free
   default** — anti-hallucination guardrails copied from dev_diary's
   migration 0140 (which forbids the "as noted in"/"according to"
   patterns the validator vetoes). It's a meaningful upgrade over the
   current zero-instruction state but leaves real headroom for the
   eventual premium glad-labs prompt to add brand voice, structural
   scaffolding, citation-density targets, etc.

   The OSS mirror gets this generic override; the premium pack
   (delivered via Lemon Squeezy file drop, never in the public repo)
   would arrive as a ``prompt_templates`` row with key
   ``glad_labs.writer_system`` and ``source='premium'``, which the
   prompt manager loader would then prefer over this column when
   ``premium_active=true``. Niche-column override is the
   second-fallback — a single editable place an operator can hand-tune
   without touching code or buying premium.

Idempotent — both upserts use ON CONFLICT DO UPDATE / WHERE clauses so
re-runs simply re-apply the same content. Operator-edited values for
either row WILL be overwritten on re-run; preserve manual edits in the
migration text rather than the DB if you want them sticky.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_PREMIUM_ACTIVE_KEY = "premium_active"
_PREMIUM_ACTIVE_VALUE = "false"
_PREMIUM_ACTIVE_CATEGORY = "experiments"
_PREMIUM_ACTIVE_DESCRIPTION = (
    "When 'true', UnifiedPromptManager loads prompt_templates rows where "
    "source='premium' on top of source='default'. When 'false' (OSS default), "
    "only 'default' rows load. Set to 'true' after applying a Glad Labs "
    "Premium Prompts pack (Pro tier, delivered via Lemon Squeezy)."
)


_GLAD_LABS_WRITER_PROMPT = """\
You are writing a blog post for Glad Labs — an AI-operated content business
covering AI/ML, gaming, and PC hardware for indie developers and tinkerers.

Hard rules:

1. CITATIONS: when you reference a study, source, expert, library, or external
   fact, use FULL MARKDOWN LINKS pointing at a real URL. The pipeline's
   content validator rejects bare "as noted in...", "according to...",
   "[1]"-style markers because they're indistinguishable from hallucinated
   references.

   FORBIDDEN phrasings (validator vetoes the post if these appear without
   an accompanying URL within ~100 chars):
   - "as noted in <Title>"
   - "according to <Source>"
   - "as documented in..."
   - "per the <X> guide"
   - "research shows..." (when no URL follows)
   - bare "[1]" / "[2]" footnote markers

   When in doubt: don't cite. Describe in your own voice. Drop a link if
   and only if you can produce a real URL.

2. NO INVENTED SPECIFICS: do not fabricate statistics, named experts,
   quotes, study titles, or product names. Round numbers and named
   examples must be either checkable or omitted. "A 2024 study by MIT"
   is forbidden unless you have the URL.

3. STAY ON TOPIC: the article must deliver on the headline. If the topic
   is "X", write about X. Do not pivot to a tangentially related theme
   the writer prefers.

4. INTERNAL CONSISTENCY: don't claim X in one section and not-X in
   another. The internal_consistency reviewer rejects posts that
   contradict themselves.

5. SCOPE: only describe Glad Labs's own work in first person ("we",
   "our system", "we adopted"). External projects and tools are third
   person ("Project X published", "the Y library does Z"). Do not claim
   external projects as our own.

Style: short paragraphs, plain language, no marketing voice. Treat the
reader as a fellow developer.

This is the OSS default prompt — Glad Labs Premium Prompts (Pro tier)
unlock a tuned version with brand voice, structural scaffolding, and
citation-density targets. See https://gladlabs.io/pricing"""


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
        # Part 1: seed premium_active toggle
        if await _table_exists(conn, "app_settings"):
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_active)
                VALUES ($1, $2, $3, $4, true)
                ON CONFLICT (key) DO UPDATE SET
                    description = EXCLUDED.description,
                    is_active   = true,
                    updated_at  = NOW()
                """,
                _PREMIUM_ACTIVE_KEY,
                _PREMIUM_ACTIVE_VALUE,
                _PREMIUM_ACTIVE_CATEGORY,
                _PREMIUM_ACTIVE_DESCRIPTION,
            )
            logger.info(
                "Migration 0141: seeded %s=%s (description-only update on re-run)",
                _PREMIUM_ACTIVE_KEY,
                _PREMIUM_ACTIVE_VALUE,
            )
        else:
            logger.info(
                "Migration 0141: app_settings missing — skipping premium_active seed"
            )

        # Part 2: seed glad-labs writer_prompt_override
        if await _table_exists(conn, "niches"):
            result = await conn.execute(
                """
                UPDATE niches
                   SET writer_prompt_override = $1, updated_at = NOW()
                 WHERE slug = 'glad-labs'
                """,
                _GLAD_LABS_WRITER_PROMPT,
            )
            logger.info(
                "Migration 0141: applied writer_prompt_override to glad-labs niche (%s)",
                result,
            )
        else:
            logger.info(
                "Migration 0141: niches table missing — skipping glad-labs override seed"
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if await _table_exists(conn, "app_settings"):
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", _PREMIUM_ACTIVE_KEY,
            )
        if await _table_exists(conn, "niches"):
            await conn.execute(
                "UPDATE niches SET writer_prompt_override = NULL, updated_at = NOW() "
                "WHERE slug = 'glad-labs'"
            )
        logger.info("Migration 0141 down: removed premium_active + glad-labs override")

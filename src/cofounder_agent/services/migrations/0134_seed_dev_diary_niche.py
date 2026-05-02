"""Migration 0134: seed the ``dev_diary`` niche for the daily dev-blog feature.

Adds a new niche row distinct from the existing AI/ML/gaming/PC niches.
The dev_diary niche is the home for the daily "what we shipped today"
build-in-public posts — synthesised from PRs, commits, brain decisions,
audit warnings, recent posts, and cost data.

Voice rule for this niche
-------------------------

First-person ("we", "today", "this morning") is explicitly ALLOWED here
per Matt's voice-policy update on 2026-05-02. The body-side
``first_person_claims`` validator in ``services/quality_scorers.py``
no-ops for any post whose niche is ``dev_diary`` (or, more generally,
any niche tagged ``allow_first_person`` via app_settings). See the
``feedback_content_voice.md`` memory note for the full rationale.

Bot attribution is the safety mechanism: every dev_diary post must
carry a footer line "Auto-compiled by Poindexter from today's commits
and system decisions." (built into the writer-prompt override below).

Approval gate
-------------

Defaults to ``draft,final`` via the GH#24 gate engine (PR #156). The
gate engine is the operator's review surface — Matt approves the
draft before publish, no auto-publish.

Scope guard
-----------

The writer prompt explicitly tells the model NOT to claim external
work as Glad Labs' own. Glad Labs only — that's the original
"taken down 9 posts" rule preserved as a soft prompt-level guard
even though the hard third-person validator is relaxed.

Idempotent: ``ON CONFLICT (slug) DO NOTHING`` so re-running is safe.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_NICHE_SLUG = "dev_diary"
_NICHE_NAME = "Dev Diary"

# Writer-prompt override. Lives on the niches row so the content
# pipeline picks it up automatically when ``writer_prompt_override``
# is non-null. Frame the section concisely — model receives this
# verbatim as the system prompt for the draft stage.
_WRITER_PROMPT = """\
You are writing a daily dev diary post for Glad Labs — an AI-operated
content business. The voice is FIRST-PERSON ("we", "today", "this morning",
"just landed"). You are speaking as the Glad Labs team, not as a journalist.

Use the structured context bundle (merged PRs, notable commits, brain
decisions, resolved audit events, recent posts, cost summary) to
synthesise a short "what we shipped today" post.

Hard rules:
1. Cite specific PRs, commits, decisions, and audit events by their ID
   (e.g., "PR #156", "commit 20b89a71", "decision abc123"). This anchors
   trust and lets readers verify.
2. Only describe Glad Labs' own work. Do NOT claim external projects,
   libraries, or products as our own. If a PR uses a third-party tool,
   describe it as "we adopted X" or "we wired in Y", never "we built X".
3. End with this exact footer on its own line, with no surrounding
   commentary:

   _Auto-compiled by Poindexter from today's commits and system decisions._

4. Skip-if-empty: if the context bundle has zero merged PRs, zero
   notable commits, AND zero brain decisions, do not invent activity.
   Return a one-sentence "quiet day" post and the footer.

Style: short paragraphs, plain language, no marketing voice. Treat the
reader as a fellow developer who wants to know what changed.
"""


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
        if not await _table_exists(conn, "niches"):
            logger.info("Table 'niches' missing — skipping migration 0134")
            return

        existing = await conn.fetchval(
            "SELECT id FROM niches WHERE slug = $1", _NICHE_SLUG,
        )
        if existing is not None:
            logger.info(
                "dev_diary niche already exists (id=%s) — skipping seed",
                existing,
            )
            return

        niche_id = await conn.fetchval(
            """
            INSERT INTO niches (
                slug, name, target_audience_tags,
                writer_prompt_override, writer_rag_mode,
                batch_size, discovery_cadence_minute_floor
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            _NICHE_SLUG,
            _NICHE_NAME,
            ["indie-devs", "ai-curious", "future-matt", "build-in-public"],
            _WRITER_PROMPT,
            "TWO_PASS",
            1,    # one post per day, no batching
            1440, # 24h discovery floor — daily cadence
        )

        # Goals (sum to 100). Authority + brand are the primary drivers
        # for build-in-public content; education matters less than trust.
        goals = [
            ("AUTHORITY", 40),
            ("BRAND",     30),
            ("COMMUNITY", 15),
            ("EDUCATION", 10),
            ("TRAFFIC",    5),
        ]
        for goal_type, weight in goals:
            await conn.execute(
                "INSERT INTO niche_goals (niche_id, goal_type, weight_pct) "
                "VALUES ($1, $2, $3)",
                niche_id, goal_type, weight,
            )

        # Sources — internal_rag is the only one that makes sense here;
        # the dev_diary topic source is the actual driver, but registering
        # internal_rag at high weight keeps the source-rebalance machinery
        # happy if an operator runs the niche through the standard sweep.
        await conn.execute(
            "INSERT INTO niche_sources (niche_id, source_name, enabled, weight_pct) "
            "VALUES ($1, $2, $3, $4)",
            niche_id, "internal_rag", True, 100,
        )

        logger.info(
            "Seeded dev_diary niche (id=%s) with first-person writer prompt + "
            "AUTHORITY/BRAND-weighted goals",
            niche_id,
        )

        # ---- Seed the validator-bypass flag in app_settings ----
        # Niche-level escape hatch for the first_person_claims validator
        # in services/quality_scorers.py. Stored as a CSV of niche slugs
        # so a single key can cover dev_diary today + future niches that
        # want the same relaxation (no schema migration required).
        if await _table_exists(conn, "app_settings"):
            current = await conn.fetchval(
                "SELECT value FROM app_settings "
                "WHERE key = 'qa_allow_first_person_niches'",
            )
            if current is None:
                await conn.execute(
                    """
                    INSERT INTO app_settings (key, value, category, description, is_secret)
                    VALUES ($1, $2, $3, $4, FALSE)
                    """,
                    "qa_allow_first_person_niches",
                    _NICHE_SLUG,
                    "quality",
                    "Comma-separated list of niche slugs that bypass the "
                    "first_person_claims validator in quality_scorers.py. "
                    "Per Matt's voice-policy update on 2026-05-02, the "
                    "validator should not penalise legitimate first-person "
                    "content in the right niche. Default: 'dev_diary'.",
                )
                logger.info(
                    "Seeded qa_allow_first_person_niches=%s",
                    _NICHE_SLUG,
                )
            else:
                # If the operator has already set the value, only append
                # dev_diary if it's missing — never overwrite.
                slugs = {s.strip() for s in str(current).split(",") if s.strip()}
                if _NICHE_SLUG not in slugs:
                    slugs.add(_NICHE_SLUG)
                    await conn.execute(
                        "UPDATE app_settings SET value = $1, updated_at = NOW() "
                        "WHERE key = 'qa_allow_first_person_niches'",
                        ",".join(sorted(slugs)),
                    )
                    logger.info(
                        "Appended dev_diary to existing "
                        "qa_allow_first_person_niches list (now: %s)",
                        ",".join(sorted(slugs)),
                    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if await _table_exists(conn, "niches"):
            await conn.execute(
                "DELETE FROM niches WHERE slug = $1", _NICHE_SLUG,
            )
        if await _table_exists(conn, "app_settings"):
            await conn.execute(
                "DELETE FROM app_settings WHERE key = 'qa_allow_first_person_niches'",
            )
        logger.info("Migration 0134 rolled back: removed dev_diary niche + setting")

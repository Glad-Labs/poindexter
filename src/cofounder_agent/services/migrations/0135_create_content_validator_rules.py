"""Migration 0135: fine-grained content validator rules table (Validators CRUD V1).

Operationalises Matt's directive that every individual content-validator
rule (``first_person_claims``, ``_check_patterns``, ``_check_code_block_density``,
``min_word_count``, ``forbidden_phrases``, ...) be toggleable from the
database with optional niche scoping.

Background
----------

The QA pipeline currently has three layers of guardrails:

1. **Per-LLM-reviewer gates** — ``qa_gates`` table (migrations 0093/0094),
   coarse rows like ``consistency`` / ``llm_critic`` / ``programmatic_validator``.
2. **Per-validator rules** (this migration) — fine-grained rules baked
   into ``services/quality_scorers.py`` and ``services/content_validator.py``
   that fire INSIDE the ``programmatic_validator`` reviewer.
3. **Tunable thresholds** — ``app_settings`` rows like
   ``qa_completeness_word_2000_score``.

V0.5 of this work was PR #160's ``qa_allow_first_person_niches`` CSV
in ``app_settings`` (a niche-level escape hatch for one specific rule).
V1 generalises that pattern: every fine-grained validator gets its own
row with ``enabled``, ``severity``, ``threshold`` (JSONB), and
``applies_to_niches`` (TEXT[] — NULL = all niches).

Schema
------

::

    content_validator_rules (
        id                 UUID PK,
        name               TEXT UNIQUE,         -- 'first_person_claims', etc.
        enabled            BOOLEAN DEFAULT TRUE,
        severity           TEXT DEFAULT 'warning',  -- 'info'|'warning'|'error'
        threshold          JSONB DEFAULT '{}',
        applies_to_niches  TEXT[] DEFAULT NULL, -- NULL => all niches
        description        TEXT,
        created_at         TIMESTAMPTZ DEFAULT NOW(),
        updated_at         TIMESTAMPTZ DEFAULT NOW()
    )

Plus a CHECK constraint on ``severity`` (the writer-side helper enforces
the same set, but the DB rejects bad rows directly so a stray ``poindexter
validators`` set-severity ``critical`` doesn't silently land an invalid row).

Seeding
-------

Every existing hardcoded rule found in
``services/quality_scorers.py`` and ``services/content_validator.py``
gets a row with sensible defaults. The full list is committed in
``_SEED_RULES`` below — adding new rules later means appending to this
list AND wiring a ``is_validator_enabled()`` check at the call site
(see ``services/validator_config.py``).

Migration of the V0.5 setting
-----------------------------

PR #160 introduced ``app_settings.qa_allow_first_person_niches`` as a
CSV of niche slugs that bypass the ``first_person_claims`` validator.
We invert that here: ``first_person_claims`` is registered with no
``applies_to_niches`` filter (so it applies to all niches by default),
but the per-niche bypass is preserved via the existing CSV setting,
which the new ``validator_config`` helper continues to consult for
backwards compatibility. We do NOT delete the old setting in this
migration — operators that disabled the validator entirely via the CSV
should still see it disabled, and the dual-source check in
``is_validator_enabled()`` handles both paths until the legacy CSV is
deprecated in a future migration.

Idempotent: ``CREATE TABLE IF NOT EXISTS`` and ``INSERT ... ON CONFLICT
(name) DO NOTHING`` so re-running is safe and won't overturn operator
edits.
"""

from __future__ import annotations

from services.logger_config import get_logger

logger = get_logger(__name__)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS content_validator_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    severity TEXT NOT NULL DEFAULT 'warning'
        CHECK (severity IN ('info', 'warning', 'error')),
    threshold JSONB NOT NULL DEFAULT '{}'::jsonb,
    applies_to_niches TEXT[],
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_CREATE_INDEX_ENABLED = """
CREATE INDEX IF NOT EXISTS idx_content_validator_rules_enabled
ON content_validator_rules(enabled)
WHERE enabled = TRUE
"""


# Each tuple: (name, severity, threshold_dict, description)
#
# Names match the call-site identifier used in `is_validator_enabled()` —
# kept lowercase + underscored to mirror the existing Python identifiers
# the validator code already uses internally.
#
# Severity defaults are conservative: anything that previously promoted to
# critical/error in the validator stays that way here. Operators flipping
# `severity` only changes the DB row — the call sites still decide whether
# to enforce or merely log based on the rule's current severity.
_SEED_RULES: list[tuple[str, str, dict, str]] = [
    # ---- quality_scorers.py rules ---------------------------------------
    (
        "first_person_claims",
        "warning",
        {"penalty_per": 1.0, "max_penalty": 3.0},
        "Penalises 'I/we built/created/...' phrasing in the post body. "
        "Bypassed for niches in `qa_allow_first_person_niches` (CSV) for "
        "backwards compatibility with PR #160. Tune `applies_to_niches` "
        "to scope this rule to specific niches.",
    ),
    (
        "meta_commentary",
        "warning",
        {"penalty_per": 0.5, "max_penalty": 2.0},
        "Penalises 'in this post', 'we'll explore', 'this article discusses' "
        "and similar self-referential phrasing in the body.",
    ),
    # ---- content_validator.py: pattern-based rules ----------------------
    (
        "fake_person",
        "error",
        {},
        "Detects fabricated person names + titles ('Sarah Smith, CEO at ...'). "
        "Critical — fabricated humans are a publishing-blocker fail.",
    ),
    (
        "fake_stat",
        "error",
        {},
        "Detects fabricated statistics ('43% reduction', 'McKinsey study found ...') "
        "without citation backing. Critical fail per Matt 2026-04-11.",
    ),
    (
        "glad_labs_claim",
        "error",
        {},
        "Detects impossible claims about the company (decade-long history, "
        "thousands of employees, named clients) — uses configured "
        "`company_name` + `company_age_months` settings.",
    ),
    (
        "fake_quote",
        "error",
        {},
        "Detects fabricated quotes attributed to named people. Critical "
        "fail — same class of lie as fake stats.",
    ),
    (
        "fabricated_experience",
        "error",
        {},
        "Detects AI pretending to be a person ('I was on a call with ...', "
        "'a client of mine ...'). Critical fail.",
    ),
    (
        "hallucinated_link",
        "error",
        {},
        "Detects 'see our guide on X' phrasing that points at internal "
        "content that doesn't exist. Critical fail.",
    ),
    (
        "unlinked_citation",
        "warning",
        {"warning_reject_threshold": 3},
        "Detects 'introduced in <Paper Title>', 'according to <Source>', "
        "'arXiv:2401.12345' without an accompanying URL. Promoted to "
        "critical when the matched text names a source type (Medium, "
        "article, paper, study) without a URL within ~100 chars.",
    ),
    (
        "hallucinated_reference",
        "warning",
        {},
        "Detects backtick-quoted library/API references that don't match "
        "Python stdlib, top-500 PyPI, or known Ollama models. Also flags "
        "topic-orthogonal library mentions (CadQuery in an ai-ml post).",
    ),
    (
        "code_block_density",
        "warning",
        {
            "min_blocks_per_700w": 1,
            "min_line_ratio_pct": 20,
            "long_post_floor_words": 300,
        },
        "Warns when tech-tagged posts ship without enough fenced code "
        "blocks. Tag list comes from `code_density_tag_filter`. Soft "
        "signal — never critical. Disabled via `code_density_check_enabled` "
        "OR by setting this row's `enabled=false`.",
    ),
    (
        "brand_contradiction",
        "warning",
        {},
        "Detects content promoting paid cloud APIs ('OpenAI pricing', "
        "'pay per token to Anthropic'). Brand-specific to local-first "
        "Ollama positioning.",
    ),
    (
        "leaked_image_prompt",
        "warning",
        {},
        "Detects image-generation prompts left inline in the markdown "
        "after image rendering ('*A split-screen comparison of ...*').",
    ),
    (
        "image_placeholder",
        "error",
        {},
        "Detects [IMAGE-1: ...], [FIGURE: ...], [DIAGRAM: ...] placeholder "
        "tokens that the image pipeline failed to replace. Critical — these "
        "leak into the rendered post and look broken.",
    ),
    (
        "known_wrong_fact",
        "error",
        {},
        "Detects facts overridden via the `fact_overrides` DB table. "
        "Severity is set per-row in fact_overrides, but this rule must "
        "be enabled for the override gate to fire at all.",
    ),
    (
        "filler_phrase",
        "warning",
        {},
        "Detects LLM filler phrases ('many organizations have found', "
        "'the landscape is constantly evolving', 'in today's fast-paced "
        "world'). Score-penalty only.",
    ),
    (
        "filler_intro",
        "warning",
        {},
        "Detects 'In this post/article/guide/tutorial' opening lines "
        "in the first 500 chars. Score-penalty only.",
    ),
    (
        "banned_header",
        "warning",
        {},
        "Detects generic section headings ('## Introduction', "
        "'## Conclusion', '## Summary', etc.). Score-penalty only.",
    ),
    (
        "late_acronym_expansion",
        "warning",
        {"min_prior_uses": 2},
        "Detects acronyms expanded after they've already been used 2+ "
        "times unexpanded ('use the CRM ... CRM (Customer Relationship "
        "Management)').",
    ),
    (
        "truncated_content",
        "error",
        {},
        "Detects content that ends mid-sentence — strong signal the LLM "
        "hit its output token limit. Critical — readers get an incomplete "
        "article.",
    ),
    (
        "title_diversity",
        "warning",
        {},
        "Detects titles starting with overused openers ('Beyond the ...', "
        "'Unlocking ...', 'The Ultimate Guide to ...'). Score-penalty only.",
    ),
    (
        "title_year_claim",
        "error",
        {},
        "Detects titles claiming multi-year history ('Five Years of ...', "
        "'10 Years Building ...') that contradict configured "
        "`company_age_months`.",
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
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.execute(_CREATE_INDEX_ENABLED)

        # Seed every known rule. ON CONFLICT (name) DO NOTHING preserves
        # operator edits across re-runs (the canonical pattern across
        # every other seed migration in this directory).
        import json
        inserted = 0
        for name, severity, threshold, description in _SEED_RULES:
            result = await conn.execute(
                """
                INSERT INTO content_validator_rules
                    (name, severity, threshold, description)
                VALUES ($1, $2, $3::jsonb, $4)
                ON CONFLICT (name) DO NOTHING
                """,
                name, severity, json.dumps(threshold), description,
            )
            # asyncpg returns "INSERT 0 1" on success, "INSERT 0 0" on conflict.
            if result.endswith(" 1"):
                inserted += 1
        logger.info(
            "0135: created content_validator_rules + seeded %d/%d rules "
            "(rest already present)",
            inserted, len(_SEED_RULES),
        )

        # The PR #160 setting `qa_allow_first_person_niches` (CSV of niche
        # slugs) is preserved as-is. validator_config.is_validator_enabled()
        # consults BOTH the new table AND that setting so operators on the
        # old path keep their bypass. No data move needed — both paths
        # converge in the helper.


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS content_validator_rules")
        logger.info("0135: dropped content_validator_rules")

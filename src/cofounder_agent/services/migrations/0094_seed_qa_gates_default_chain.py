"""Migration 0094: Seed ``qa_gates`` rows for the existing QA chain (GH-115).

Reads the legacy per-gate enable settings from ``app_settings`` so this
migration is fully data-preserving — operators who flipped a gate off
yesterday still see it off today.

Legacy → new mapping:

| Legacy ``app_settings`` key            | New ``qa_gates`` row name | Default if unset |
| -------------------------------------- | ------------------------- | ---------------- |
| (always on — programmatic, no flag)    | ``programmatic_validator``| enabled          |
| (always on — main critic, no flag)     | ``llm_critic``            | enabled          |
| (always on — URL verifier, no flag)    | ``url_verifier``          | enabled          |
| (always on — internal_consistency)     | ``consistency``           | enabled          |
| ``qa_web_factcheck_enabled``           | ``web_factcheck``         | enabled          |
| ``qa_vision_check_enabled``            | ``vision_gate``           | disabled (opt-in)|

Seed is idempotent — ``ON CONFLICT (name) DO NOTHING`` skips rows
already inserted by a prior run.
"""

import json

from services.logger_config import get_logger

logger = get_logger(__name__)


# (name, execution_order, reviewer, required_to_pass, default_enabled,
#  legacy_setting_key, description)
# legacy_setting_key=None => no legacy flag, always enabled by default.
_SEEDS: list[tuple[str, int, str, bool, bool, str | None, str]] = [
    (
        "programmatic_validator",
        100,
        "programmatic_validator",
        True,
        True,
        None,
        "Anti-hallucination regex + heuristic checks. Catches fabricated "
        "people, fake stats, invented quotes. Always-on safety net.",
    ),
    (
        "llm_critic",
        200,
        "llm_critic",
        True,
        True,
        None,
        "Cross-model review by a different LLM than the writer. Style, "
        "logic, coherence, suspiciously specific claims.",
    ),
    (
        "url_verifier",
        300,
        "url_verifier",
        True,
        True,
        None,
        "HEAD-checks every external URL in the content. Dead links "
        "block publish; healthy citations earn a small score bonus.",
    ),
    (
        "consistency",
        400,
        "consistency",
        False,  # advisory unless score < qa_consistency_veto_threshold
        True,
        None,
        "Internal-consistency gate — catches sections that contradict "
        "each other. Advisory unless score is unambiguously low.",
    ),
    (
        "web_factcheck",
        500,
        "web_factcheck",
        False,
        True,  # default if legacy setting unset
        "qa_web_factcheck_enabled",
        "DuckDuckGo lookup for product/spec claims that the LLM critic "
        "cannot verify (training-cutoff override). Advisory.",
    ),
    (
        "vision_gate",
        600,
        "vision_gate",
        False,
        False,  # opt-in by default; vision inference is ~10s/image
        "qa_vision_check_enabled",
        "Vision-model inline-image relevance check. Catches the 'stock "
        "photo of a server room on a FastAPI post' pattern.",
    ),
]


_TRUTHY = {"true", "1", "yes", "on"}
_FALSY = {"false", "0", "no", "off"}


def _resolve_enabled(legacy_value: str | None, default_enabled: bool) -> bool:
    """Coerce a legacy setting value into a bool with the documented default.

    None / unset / empty string → fall back to ``default_enabled``.
    Recognized truthy/falsy strings → respect the operator's choice.
    Anything else → fall back to default (we don't fail-loud here because
    the migration is best-effort data migration, not config validation).
    """
    if legacy_value is None:
        return default_enabled
    s = str(legacy_value).strip().lower()
    if not s:
        return default_enabled
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return default_enabled


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # Pull the legacy flags in one query so we don't hammer
        # app_settings with N round-trips.
        legacy_keys = [s[5] for s in _SEEDS if s[5]]
        legacy_values: dict[str, str | None] = {k: None for k in legacy_keys}
        if legacy_keys:
            try:
                rows = await conn.fetch(
                    "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
                    legacy_keys,
                )
                for r in rows:
                    legacy_values[r["key"]] = r["value"]
            except Exception as exc:  # noqa: BLE001
                # app_settings might not exist yet on a brand-new DB.
                # Fall back to defaults — same as if no key were set.
                logger.info(
                    "0094: legacy app_settings lookup skipped (%s) — using defaults",
                    exc,
                )

        inserted = 0
        for (
            name, order, reviewer, required, default_enabled, legacy_key, desc
        ) in _SEEDS:
            legacy_val = legacy_values.get(legacy_key) if legacy_key else None
            enabled = _resolve_enabled(legacy_val, default_enabled)
            metadata = json.dumps({
                "description": desc,
                "legacy_setting_key": legacy_key,
                "legacy_setting_value_at_seed": legacy_val,
                "seeded_by_migration": "0094",
            })
            result = await conn.execute(
                """
                INSERT INTO qa_gates
                    (name, stage_name, execution_order, reviewer,
                     required_to_pass, enabled, config, metadata)
                VALUES ($1, 'qa', $2, $3, $4, $5, '{}'::jsonb, $6::jsonb)
                ON CONFLICT (name) DO NOTHING
                """,
                name, order, reviewer, required, enabled, metadata,
            )
            # asyncpg returns 'INSERT 0 1' on success, 'INSERT 0 0' on conflict.
            if result.endswith(" 1"):
                inserted += 1

        logger.info(
            "0094: seeded %d qa_gates rows (%d already present)",
            inserted, len(_SEEDS) - inserted,
        )


async def down(pool) -> None:
    names = [s[0] for s in _SEEDS]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM qa_gates WHERE name = ANY($1::text[])", names,
        )
        logger.info("0094: removed %d seeded qa_gates rows", len(names))

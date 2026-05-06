"""Migration 20260506_052451: seed firefighter ops_triage app_settings.

ISSUE: Glad-Labs/poindexter#347 (firefighter ops LLM v1 — diagnosis-only
enrichment of the alert chain).

Step 1 of the v1 rollout. Adds the eight ``ops_triage_*`` knobs the new
``services/firefighter_service.py`` reads when assembling triage context
and invoking the LLM. The brain alert_dispatcher gains a parallel triage
task in a later step (#347 step 4); this migration just plants the
configuration so the service module + tests can land independently.

Seeded keys (defaults chosen so the system works out of the box; every
value is operator-tunable at runtime):

- ``ops_triage_enabled`` (``true``) — master kill-switch. Flip to false
  to turn enrichment off without redeploy.
- ``ops_triage_model_class`` (``ops_triage``) — which model_router tier
  to use. Defaults to a dedicated ``ops_triage`` class which maps to
  free / glm-4.7-5090 by default.
- ``ops_triage_system_prompt`` — operator-persona system prompt the
  triage LLM sees. Iterable without redeploy.
- ``ops_triage_max_context_tokens`` (``4000``) — cap on pre-fetched
  context size. Service truncates oldest audit_log rows first when
  the assembled context would exceed this budget.
- ``ops_triage_max_diagnosis_tokens`` (``400``) — cap on diagnosis
  output length (Telegram-friendly). Truncated with ``[...]`` marker.
- ``ops_triage_retry_max`` (``3``) — maximum retry attempts when the
  worker is unreachable from the brain.
- ``ops_triage_retry_backoff_seconds`` (``[10, 30, 90]``) — per-attempt
  sleep before retry. JSON list.
- ``ops_triage_audit_logged`` (``true``) — when true, every triage
  call writes an audit_log row tagged ``source='ops_triage'``.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — operator-set values
preserved on re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_DEFAULT_SYSTEM_PROMPT = (
    "You are the Poindexter operator. The system you are diagnosing is "
    "the Poindexter content pipeline -- a self-hosted FastAPI worker, "
    "brain daemon, Postgres + pgvector, Ollama for LLM inference. You "
    "will be shown an alert + curated database state. Your job is to "
    "write ONE SHORT PARAGRAPH (<=400 tokens) explaining: what likely "
    "happened, why you think so (cite the rows you saw), and one "
    "suggested next step the operator could take. Do NOT propose code "
    "changes -- those go to a different escalation path. Do NOT suggest "
    "ALL POSSIBLE causes -- commit to your most likely diagnosis. If "
    "the context is genuinely ambiguous, say so plainly and stop."
)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "ops_triage_enabled",
        "true",
        "Master kill-switch for the firefighter ops LLM (#347). When "
        "false, alert_dispatcher skips the parallel triage task and "
        "operators only see the raw alert. Flip without redeploy.",
    ),
    (
        "ops_triage_model_class",
        "ops_triage",
        "model_router tier the firefighter_service uses for triage "
        "(#347). Defaults to a dedicated 'ops_triage' class which maps "
        "to free / glm-4.7-5090 in the model_router tier table. Change "
        "to 'standard' / 'budget' / etc. to A/B alternative writers.",
    ),
    (
        "ops_triage_system_prompt",
        _DEFAULT_SYSTEM_PROMPT,
        "Operator-persona system prompt the triage LLM sees. Iterable "
        "without redeploy. Keep <=400 tokens; the prompt sets the "
        "voice (one paragraph, commit to most-likely diagnosis, no "
        "code suggestions). #347.",
    ),
    (
        "ops_triage_max_context_tokens",
        "4000",
        "Cap on the pre-fetched context size handed to the LLM. When "
        "the assembled context (alert + history + audit_log + "
        "pipeline_tasks + app_settings snapshot) would exceed this, "
        "firefighter_service truncates oldest audit_log rows first. "
        "Rough 4-chars-per-token approximation. #347.",
    ),
    (
        "ops_triage_max_diagnosis_tokens",
        "400",
        "Cap on the diagnosis output length (Telegram-friendly). The "
        "service truncates with a '[...]' marker if the LLM exceeds "
        "this. Rough 4-chars-per-token approximation. #347.",
    ),
    (
        "ops_triage_retry_max",
        "3",
        "Maximum retry attempts when the brain can't reach the worker "
        "/api/triage endpoint. Retries are scheduled with the backoff "
        "from ops_triage_retry_backoff_seconds. #347.",
    ),
    (
        "ops_triage_retry_backoff_seconds",
        "[10, 30, 90]",
        "JSON list of per-attempt sleep durations (seconds) the brain "
        "uses between retries when worker /api/triage is unreachable. "
        "Length should equal ops_triage_retry_max. #347.",
    ),
    (
        "ops_triage_audit_logged",
        "true",
        "When true, every triage call writes an audit_log row tagged "
        "source='ops_triage' (success or failure). When false, only "
        "successful follow-up posts are observable. Default true so "
        "operators can grep audit_log for triage history. #347.",
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
    """Apply the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing -- skipping migration "
                "20260506_052451 (ops_triage_* seed)"
            )
            return

        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, 'firefighter', $3, FALSE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 20260506_052451: seeded %d/%d ops_triage_* "
            "settings (remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "Migration 20260506_052451 rolled back: removed %d "
            "ops_triage_* seeds",
            len(_SEEDS),
        )

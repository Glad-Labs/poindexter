"""
Decision Service — standard interface for ML decision logging.

Every AI/ML decision point in the pipeline uses this service to:
  1. Log what decision was made and why
  2. Record the outcome after execution
  3. Query past decisions for learning

This is the foundation for self-improving AI. Outcomes feed back
into future decisions — the system gets smarter over time.

Usage:
    from services.decision_service import log_decision, record_outcome, get_past_decisions

    # Log a decision
    decision_id = await log_decision(
        pool=pool,
        decision_type="image_source",
        decision_point="image_decision_agent",
        context={"section": "Schema Design", "category": "technology"},
        decision={"source": "sdxl", "style": "blueprint", "prompt": "..."},
        task_id=task_id,
        model_used="qwen3:8b",
        duration_ms=1200,
    )

    # Later, record the outcome
    await record_outcome(
        pool=pool,
        decision_id=decision_id,
        outcome={"success": True, "image_url": "...", "user_approved": True},
    )

    # Query past decisions for learning
    past = await get_past_decisions(
        pool=pool,
        decision_type="image_source",
        limit=50,
    )
"""

import json
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


async def log_decision(
    pool,
    decision_type: str,
    decision_point: str,
    context: dict[str, Any],
    decision: dict[str, Any],
    task_id: str | None = None,
    post_id: str | None = None,
    model_used: str | None = None,
    duration_ms: int | None = None,
    cost_usd: float = 0.0,
) -> str | None:
    """Log an ML decision to the decision_log table.

    Returns the decision_id (UUID) for later outcome recording.
    """
    try:
        row = await pool.fetchrow("""
            INSERT INTO decision_log
                (decision_type, decision_point, context, decision,
                 task_id, post_id, model_used, duration_ms, cost_usd)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
        """,
            decision_type,
            decision_point,
            json.dumps(context),
            json.dumps(decision),
            task_id,
            post_id,
            model_used,
            duration_ms,
            cost_usd,
        )
        decision_id = str(row["id"])
        logger.debug(
            "[DECISION] Logged %s decision from %s (id=%s)",
            decision_type, decision_point, decision_id[:8],
        )
        return decision_id
    except Exception as e:
        logger.warning("[DECISION] Failed to log decision: %s", e)
        return None


async def record_outcome(
    pool,
    decision_id: str,
    outcome: dict[str, Any],
) -> bool:
    """Record the outcome of a previously logged decision.

    Call this after executing the decision to close the feedback loop.
    """
    try:
        await pool.execute("""
            UPDATE decision_log
            SET outcome = $1, outcome_recorded_at = NOW()
            WHERE id = $2
        """, json.dumps(outcome), decision_id)
        logger.debug("[DECISION] Recorded outcome for %s", decision_id[:8])
        return True
    except Exception as e:
        logger.warning("[DECISION] Failed to record outcome: %s", e)
        return False


async def get_past_decisions(
    pool,
    decision_type: str,
    limit: int = 50,
    with_outcomes_only: bool = False,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query past decisions for learning.

    Returns decisions with their outcomes so agents can learn
    from what worked and what didn't.
    """
    try:
        conditions = ["decision_type = $1"]
        params: list = [decision_type]
        idx = 2

        if with_outcomes_only:
            conditions.append("outcome IS NOT NULL")

        if task_id:
            conditions.append(f"task_id = ${idx}")
            params.append(task_id)
            idx += 1

        params.append(limit)
        where = " AND ".join(conditions)

        rows = await pool.fetch(f"""
            SELECT id, decision_type, decision_point, context, decision,
                   outcome, task_id, model_used, duration_ms, cost_usd, created_at
            FROM decision_log
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ${idx}
        """, *params)  # nosec B608  # where is built from local literals; values use $N params

        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("[DECISION] Failed to query past decisions: %s", e)
        return []


async def get_decision_stats(
    pool,
    decision_type: str,
    days: int = 30,
) -> dict[str, Any]:
    """Get aggregate stats for a decision type.

    Useful for dashboards and understanding decision patterns.
    """
    try:
        row = await pool.fetchrow("""
            SELECT
                COUNT(*) as total_decisions,
                COUNT(outcome) as decisions_with_outcomes,
                AVG(duration_ms) as avg_duration_ms,
                SUM(cost_usd) as total_cost_usd,
                MIN(created_at) as earliest,
                MAX(created_at) as latest
            FROM decision_log
            WHERE decision_type = $1
            AND created_at > NOW() - ($2 || ' days')::interval
        """, decision_type, str(days))

        return dict(row) if row else {}
    except Exception as e:
        logger.warning("[DECISION] Failed to get stats: %s", e)
        return {}

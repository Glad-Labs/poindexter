"""Shared DB-pool resolution for content atoms.

Content atoms cost-log and capture ``atom_runs`` through a live asyncpg pool
they read from the pipeline state's ``database_service.pool``. On some runs a
threaded ``database_service`` can present a ``None`` ``.pool`` (the value is
None though the key is present); ``site_config._pool`` — the same handle
``caption_images``/``qa.vision`` source — stays live. Prefer
``database_service.pool``; fall back to ``site_config._pool``; and log a loud
``[POOL]`` WARNING when the fallback engages so a genuine missing-pool
condition surfaces in Loki instead of silently dropping cost-logging.

Named ``_pool`` (underscore-prefixed) so the atom registry skips it — it is a
shared library, not a graph node, and atoms may import it downward without
tripping the atom-independence lint (vision_scorer_unavailable follow-up,
2026-07-12).
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


def resolve_pool(state: dict[str, Any], *, atom: str) -> Any | None:
    """Return a live DB pool from ``state`` for cost-logging / atom_runs.

    Prefers ``state['database_service'].pool``. When that is ``None`` (present
    key, dead value) falls back to ``state['site_config']._pool`` and logs a
    loud ``[POOL]`` WARNING naming ``atom`` — the fallback engaging is a signal
    worth catching in Loki, not a silent heal. Returns ``None`` only when
    neither handle is live (tests / ad-hoc CLI without a pool), matching the
    pool-optional behaviour every atom already tolerates (cost-logging is then
    skipped, never fatal).

    Args:
        state: the LangGraph pipeline state / atom input dict.
        atom: the atom's dotted name (e.g. ``"qa.vision"``), used only to make
            the fallback WARNING self-identifying.
    """
    db_pool = getattr(state.get("database_service"), "pool", None)
    if db_pool is not None:
        return db_pool
    fallback = getattr(state.get("site_config"), "_pool", None)
    if fallback is not None:
        logger.warning(
            "[POOL] %s: threaded database_service has no live .pool — using "
            "site_config._pool fallback so cost-logging/atom_runs stay live "
            "(task %s)",
            atom,
            str(state.get("task_id") or "?")[:8],
        )
    return fallback


__all__ = ["resolve_pool"]

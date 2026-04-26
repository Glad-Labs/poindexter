"""Read access to the ``qa_gates`` table — declarative QA chain (GH-115).

The runtime side of migration 0093/0094: this module loads the gate
chain from PostgreSQL and exposes it as ordered :class:`QAGateSpec`
records that ``MultiModelQA`` walks.

Design notes
------------

- **Read-only here.** Mutations live in the ``poindexter qa-gates ...``
  CLI (see ``poindexter/cli/qa_gates.py``). The runtime never writes
  back to the table — the counter columns get updated through the
  audit pipeline instead.

- **Stage scoping.** v1 only ships the ``qa`` stage; the column exists
  so ``post_publish`` / ``pre_research`` chains can reuse the same
  table later without a schema bump.

- **Graceful fallback.** When ``pool`` is ``None`` (unit tests, callers
  without a DB) or the table doesn't exist yet (fresh checkout that
  hasn't migrated), :func:`load_qa_gate_chain` returns an empty list.
  ``MultiModelQA`` interprets that as "fall back to the hardcoded
  legacy chain" so existing tests keep working without setup.

- **Style scoping.** ``config.applies_to_styles`` is a list of
  ``writing_style_id`` values; an empty list / missing key applies to
  all styles. Filtering happens in the consumer (so the same loaded
  chain can be reused across requests with different styles).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class QAGateSpec:
    """One row of the ``qa_gates`` table, materialized for the runtime.

    ``frozen=True`` keeps the chain immutable once loaded — callers can
    safely cache it across requests without worrying about another
    request mutating an entry.
    """

    name: str
    stage_name: str
    execution_order: int
    reviewer: str
    required_to_pass: bool
    enabled: bool
    config: dict[str, Any] = field(default_factory=dict)

    def applies_to_style(self, writing_style_id: str | None) -> bool:
        """Filter helper for the per-niche QA variation feature.

        Empty / missing ``applies_to_styles`` config = applies to all
        styles. Otherwise the row only fires when ``writing_style_id``
        is in the list.
        """
        styles = self.config.get("applies_to_styles") or []
        if not styles:
            return True
        if not writing_style_id:
            return False
        return str(writing_style_id) in {str(s) for s in styles}


async def load_qa_gate_chain(
    pool: Any,
    *,
    stage_name: str = "qa",
    only_enabled: bool = True,
) -> list[QAGateSpec]:
    """Return the ordered gate chain for ``stage_name`` from the DB.

    Args:
        pool: asyncpg pool. ``None`` is tolerated and yields ``[]`` —
            the caller falls back to its hardcoded legacy chain.
        stage_name: Pipeline stage to query. v1 ships ``qa`` only.
        only_enabled: When True (default), filter to ``enabled = TRUE``
            rows. CLI commands that want to list disabled gates pass
            ``False``.

    Returns:
        List of :class:`QAGateSpec`, ordered by ``execution_order``.
        Empty list on any failure — the runtime treats that as the
        signal to use the legacy hardcoded order.
    """
    if pool is None:
        return []

    where = ["stage_name = $1"]
    args: list[Any] = [stage_name]
    if only_enabled:
        where.append("enabled = TRUE")
    where_sql = " AND ".join(where)

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT name, stage_name, execution_order, reviewer,
                       required_to_pass, enabled, config
                  FROM qa_gates
                 WHERE {where_sql}
              ORDER BY execution_order ASC, name ASC
                """,
                *args,
            )
    except Exception as exc:  # noqa: BLE001
        # Table missing on fresh-clone test runs, or a transient
        # connection blip during startup. Falling back to the legacy
        # hardcoded chain is strictly better than failing the whole
        # pipeline because the catalog table isn't there yet.
        logger.debug(
            "qa_gates lookup failed (%s) — runtime will use legacy chain",
            exc,
        )
        return []

    chain: list[QAGateSpec] = []
    for r in rows:
        cfg = r["config"]
        if isinstance(cfg, str):
            # Some asyncpg versions / typecodecs return jsonb as text.
            import json as _json
            try:
                cfg = _json.loads(cfg)
            except Exception:
                cfg = {}
        chain.append(QAGateSpec(
            name=r["name"],
            stage_name=r["stage_name"],
            execution_order=int(r["execution_order"]),
            reviewer=r["reviewer"],
            required_to_pass=bool(r["required_to_pass"]),
            enabled=bool(r["enabled"]),
            config=dict(cfg) if cfg else {},
        ))
    return chain


__all__ = ["QAGateSpec", "load_qa_gate_chain"]

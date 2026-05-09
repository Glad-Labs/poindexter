"""Read access to the ``publishing_adapters`` table (poindexter#112).

Runtime side of migration ``20260509_175447_add_publishing_adapters``:
this module loads enabled publisher rows from PostgreSQL and exposes
them as frozen :class:`PublishingAdapterRow` records that
:func:`services.social_poster._distribute_to_adapters` walks.

Design notes
------------

- **Read-only here.** Mutations live in the
  ``poindexter publishers ...`` CLI (see ``poindexter/cli/publishers.py``)
  and the per-row counter writes in the dispatch loop (mirrors what
  ``services.integrations.tap_runner`` does — counter writes inline in
  the runner, not in a separate writer module).

- **Graceful fallback.** When ``pool`` is ``None`` (unit tests, callers
  without a DB) or the table doesn't exist yet (fresh checkout that
  hasn't migrated), :func:`load_enabled_publishers` returns an empty list.
  ``social_poster`` interprets that as "no platforms wired — skip
  distribution" and logs an INFO line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PublishingAdapterRow:
    """One row of the ``publishing_adapters`` table, materialized.

    ``frozen=True`` keeps the loaded list immutable so callers can pass
    it around without worrying about another caller mutating it.
    """

    id: UUID
    name: str
    platform: str
    handler_name: str
    credentials_ref: str | None
    enabled: bool
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the row as a plain dict for the registry's ``row=`` kwarg."""
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "handler_name": self.handler_name,
            "credentials_ref": self.credentials_ref,
            "enabled": self.enabled,
            "config": dict(self.config),
            "metadata": dict(self.metadata),
        }


async def load_enabled_publishers(pool: Any) -> list[PublishingAdapterRow]:
    """Return the ordered enabled publisher chain from the DB.

    Args:
        pool: asyncpg pool. ``None`` is tolerated and yields ``[]`` —
            the caller falls back to skipping distribution entirely.

    Returns:
        List of :class:`PublishingAdapterRow`, ordered by ``name``.
        Empty list on any failure (table missing, transient DB error).
    """
    if pool is None:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, platform, handler_name, credentials_ref,
                       enabled, config, metadata
                  FROM publishing_adapters
                 WHERE enabled = TRUE
              ORDER BY name ASC
                """
            )
    except Exception as exc:  # noqa: BLE001
        # Same fallback semantics as qa_gates_db.load_qa_gate_chain —
        # missing table on a fresh checkout shouldn't fail the loop.
        logger.debug(
            "publishing_adapters lookup failed (%s) — distribution disabled", exc,
        )
        return []

    out: list[PublishingAdapterRow] = []
    for r in rows:
        out.append(
            PublishingAdapterRow(
                id=r["id"],
                name=r["name"],
                platform=r["platform"],
                handler_name=r["handler_name"],
                credentials_ref=r["credentials_ref"],
                enabled=bool(r["enabled"]),
                config=_parse_jsonb(r["config"]),
                metadata=_parse_jsonb(r["metadata"]),
            )
        )
    return out


def _parse_jsonb(value: Any) -> dict[str, Any]:
    """Return ``value`` as a dict, parsing JSON if asyncpg handed back a string.

    Some asyncpg/typecodec setups return jsonb columns as already-decoded
    dicts; others return raw JSON strings. Tolerate both.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


__all__ = ["PublishingAdapterRow", "load_enabled_publishers"]

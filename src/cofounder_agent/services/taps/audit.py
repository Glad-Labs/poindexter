"""AuditTap — ingest recent audit_log entries (errors + warnings).

Replaces Phase 4 of ``scripts/auto-embed.py``. Pulls recent
error/warning audit rows and yields one Document per entry. The
pipeline uses this to give the brain daemon context about recent
incidents during alert triage.

Config (``plugin.tap.audit``):
- ``limit`` (default ``200``) — how many recent rows to consider
- ``severities`` (default ``["error", "warning"]``) — which severity
  levels to ingest
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from plugins.tap import Document

logger = logging.getLogger(__name__)


class AuditTap:
    """One Document per audit_log row."""

    name = "audit"
    interval_seconds = 1800  # every 30 minutes; audit rows move faster than posts

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        limit = int(config.get("limit", 200))
        severities = list(config.get("severities", ["error", "warning"]))

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, source, task_id, details, severity, timestamp
                  FROM audit_log
                 WHERE severity = ANY($1)
                 ORDER BY timestamp DESC
                 LIMIT $2
                """,
                severities,
                limit,
            )

        logger.info("AuditTap: %d audit rows (severities=%s)", len(rows), severities)

        for row in rows:
            try:
                details = json.loads(row["details"]) if row["details"] else {}
            except (ValueError, TypeError):
                details = {}
            text = (
                f"Audit: {row['event_type']} [{row['severity']}]\n"
                f"Source: {row['source']}\n"
                f"Task: {row['task_id'] or 'N/A'}\n"
                f"Time: {row['timestamp']}\n"
                f"Details: {json.dumps(details)[:2000]}"
            )
            yield Document(
                source_id=str(row["id"]),
                source_table="audit",
                text=text,
                metadata={
                    "event_type": row["event_type"],
                    "severity": row["severity"],
                    "source": row["source"],
                    "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                },
                writer="auto-embed",
            )

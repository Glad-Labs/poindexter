"""Migration 20260924_190722: unwrap double-encoded jsonb in the seeded declarative tables.

ISSUE: Glad-Labs/poindexter#1061.

The Phase G baseline seeded 58 jsonb values DOUBLE-ENCODED — a jsonb *string*
holding JSON text (``'"{...}"'::jsonb``) instead of the object itself — across
seven tables. Prod never saw it (its rows predate the squash) except one:
``external_taps.corsair_csv.config`` is a string there too (the tap is
disabled). Every install built from that baseline carries all of them, and a
consumer that decodes once and treats the result as a mapping gets a ``str``:
``dict()`` raises, ``.get`` raises, and ``load_active_graph_def`` reads the
seeded ``dev_diary`` graph_def as "no graph". The seed file is fixed in the
same change; this converges installs that already ran the old one.

Only a string whose content parses as a JSON object or array is unwrapped — a
jsonb value that is legitimately a string is left alone. Columns are
discovered from ``information_schema`` rather than listed, so a jsonb column
added to one of these tables later is covered too. Idempotent: a second run
finds no string-typed containers.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# The tables the baseline seeds with jsonb literals. Identifiers are
# interpolated below, so they come only from this constant and from
# information_schema for these names.
_TABLES = (
    "content_validator_rules",
    "external_taps",
    "pipeline_templates",
    "publishing_adapters",
    "qa_gates",
    "retention_policies",
    "webhook_endpoints",
)


def _container(text: str | None):
    try:
        value = json.loads(text) if text is not None else None
    except ValueError:
        return None
    return value if isinstance(value, (dict, list)) else None


async def up(pool) -> None:
    total = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            cols = await conn.fetch(
                """
                SELECT table_name, column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND data_type = 'jsonb'
                   AND table_name = ANY($1::text[])
                 ORDER BY table_name, column_name
                """,
                list(_TABLES),
            )
            for c in cols:
                table = '"' + c["table_name"] + '"'
                col = '"' + c["column_name"] + '"'
                rows = await conn.fetch(
                    f"SELECT ctid::text AS ctid, {col} #>> '{{}}' AS inner_text "  # nosec B608 - identifiers from _TABLES + information_schema
                    f"FROM {table} WHERE jsonb_typeof({col}) = 'string'"
                )
                for r in rows:
                    value = _container(r["inner_text"])
                    if value is None:
                        continue
                    await conn.execute(
                        f"UPDATE {table} SET {col} = $1::jsonb "  # nosec B608 - identifiers from _TABLES + information_schema
                        f"WHERE ctid = ($2::text)::tid AND jsonb_typeof({col}) = 'string'",
                        json.dumps(value), r["ctid"],
                    )
                    total += 1
                    logger.info(
                        "unwrapped double-encoded %s.%s", c["table_name"], c["column_name"],
                    )
    logger.info("unwrap double-encoded jsonb: %d value(s) fixed", total)


async def down(pool) -> None:
    # Re-wrapping objects as strings would re-break every consumer; no-op.
    return None

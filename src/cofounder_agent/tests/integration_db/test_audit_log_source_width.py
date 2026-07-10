"""integration_db: audit_log.source holds long dotted atom paths (GlitchTip #863).

Root cause B of the "WARN finding lost on audit write" alert: ``audit_log.source``
was ``character varying(50)``, but it holds the arbitrary dotted module path of
whatever raised a finding. The 53-char source
``modules.content.atoms.content_llm_reconcile_citations`` overran it, so every
finding from that atom failed the INSERT with
``StringDataRightTruncationError`` and was lost. Migration 20260710_060100 widens
the column to ``text``.

The harness (`schema_loaded`) applies the full migration chain — baseline + every
timestamped migration, including the widen — so this asserts the end state: the
column is ``text`` and a >50-char source inserts and reads back verbatim.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

# The exact production source that overran varchar(50) — see the GlitchTip #863
# traceback. 53 chars.
_LONG_SOURCE = "modules.content.atoms.content_llm_reconcile_citations"


async def test_source_column_widened_to_text(test_pool) -> None:
    async with test_pool.acquire() as conn:
        data_type = await conn.fetchval(
            """
            SELECT data_type
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'audit_log'
               AND column_name = 'source'
            """
        )
    assert data_type == "text", (
        "audit_log.source must be widened to text by migration 20260710_060100 "
        f"so long atom sources cannot truncate; got {data_type!r}"
    )


async def test_long_atom_source_inserts_untruncated(test_txn) -> None:
    # Guard the premise: this source is longer than the old varchar(50) ceiling,
    # so the pre-migration column would have raised StringDataRightTruncationError.
    assert len(_LONG_SOURCE) > 50

    row_id = await test_txn.fetchval(
        """
        INSERT INTO audit_log (event_type, source, details, severity)
        VALUES ('finding', $1, '{}'::jsonb, 'warn')
        RETURNING id
        """,
        _LONG_SOURCE,
    )
    stored = await test_txn.fetchval(
        "SELECT source FROM audit_log WHERE id = $1", row_id
    )
    assert stored == _LONG_SOURCE  # full 53 chars, not truncated to 50

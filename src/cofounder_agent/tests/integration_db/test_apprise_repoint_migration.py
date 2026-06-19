"""integration_db: the ops webhook rows end up on apprise_notify after migrate.

The harness (`schema_loaded` -> `fixtures_loaded` -> `test_pool`) runs the full
migration chain — baseline (incl. seeds) + every timestamped migration. On a
fresh DB the baseline seed inserts both rows already pointing at
``apprise_notify``; the 20260619 re-point migration then no-ops (its WHERE
clause matches only the legacy handler). Either way the end state asserted
here holds, and the seed's config must be a proper JSONB object (not the
legacy double-encoded string) for ``apprise_notify`` to read ``apprise_url``.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_ops_rows_repointed_to_apprise(test_pool):
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT name, handler_name, jsonb_typeof(config) AS cfg_type,
                   config->>'apprise_url' AS apprise_url
              FROM webhook_endpoints
             WHERE name IN ('discord_ops', 'telegram_ops')
             ORDER BY name
            """
        )
    by_name = {r["name"]: r for r in rows}

    assert by_name["discord_ops"]["handler_name"] == "apprise_notify"
    assert by_name["telegram_ops"]["handler_name"] == "apprise_notify"

    assert by_name["discord_ops"]["cfg_type"] == "object"
    assert by_name["telegram_ops"]["cfg_type"] == "object"

    assert by_name["discord_ops"]["apprise_url"] == "{secret}"
    assert by_name["telegram_ops"]["apprise_url"] == "tgram://{secret}/{chat_id}/"

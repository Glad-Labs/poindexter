"""Redact leaked app_settings rows + flip them to is_secret=true.

Security incident 2026-05-12: a comprehensive audit found three rows
in ``0000_baseline.seeds.sql`` were committed with live secret values
AND ``is_secret='f'``, so they shipped to the public Glad-Labs/poindexter
mirror in plaintext.

Affected rows:
  - ``discord_ops_webhook_url`` — the Discord webhook URL embeds a
    bearer token. Anyone reading the public mirror could post to the
    operator's #ops channel until rotated upstream.
  - ``indexnow_key`` — the IndexNow site-ownership key. Leaking it
    lets a third party submit URLs to the operator's IndexNow account
    if they also control the verification file at the site root.
  - ``langfuse_public_key`` — identifies the operator's Langfuse
    project. Public key alone can't authenticate writes (those need
    the paired secret_key, already is_secret=true), but it's still
    operator-identifying metadata that doesn't belong on a public
    OSS mirror.

The seed file has been patched in the same PR — fresh installs after
2026-05-12 don't see the leaked values. This migration handles the
existing-install case: any DB that ran an earlier baseline (before
2026-05-12) still has the plaintext leaked values + ``is_secret='f'``.

Value-aware redaction — only clears + flips when the row STILL matches
the known-leaked value. If the operator has already rotated upstream
and reseated via ``poindexter settings set <key> <fresh>``, the row's
value won't match the leaked one and the migration leaves the value
intact while still promoting ``is_secret`` to ``true``. This lets us
ship + run the migration even while operators are mid-rotation:
already-rotated rows keep their fresh value, only-leaked rows get
wiped to ``''`` to force a re-seat.

Operator action: rotate each secret upstream (regenerate the Discord
webhook in the Discord UI, generate a new IndexNow key + rehost the
verification file, rotate the Langfuse keys via the Langfuse UI),
then ``poindexter settings set <key> <new_value>`` to reseat. After
this migration runs, ``is_secret`` will be ``true`` on every row
regardless of which branch ran.

Idempotent — re-running on a clean row is a no-op.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# (key, leaked_value) pairs. Match exactly — partial matches don't
# trigger the redaction. The leaked values are inlined here because
# they were already public (this is part of the remediation, not new
# leakage) and this file ships to the same public mirror in the same
# state.
_LEAKED_ROWS: tuple[tuple[str, str], ...] = (
    (
        "discord_ops_webhook_url",
        (
            "https://discord.com/api/webhooks/1494521160728055889/"
            "HRlxTpHRztfkvB1RAxoa8J8tR0sth5HEJgYAR-o7Af0n-mmu1W6Z5AGmneLn9ZaK147b"
        ),
    ),
    ("indexnow_key", "34352c4f981b45698941c47eefef2fb4"),
    ("langfuse_public_key", "pk-lf-KCi8EdrTVh6kg4_YNmB63UD6N7E"),
)


async def run_migration(conn) -> None:
    rows_cleared = 0
    rows_already_rotated = 0
    for key, leaked_value in _LEAKED_ROWS:
        # Branch 1: row still on the leaked value → clear + flip.
        result = await conn.execute(
            """
            UPDATE app_settings
               SET value      = '',
                   is_secret  = TRUE,
                   updated_at = NOW()
             WHERE key        = $1
               AND value      = $2
            """,
            key, leaked_value,
        )
        if result.startswith("UPDATE 1"):
            rows_cleared += 1
            logger.info(
                "20260512_032900: cleared + is_secret=true on %s "
                "(value still matched the leaked one)", key,
            )
            continue

        # Branch 2: operator already rotated upstream + reseated. The
        # row carries a fresh value — leave it alone, but still promote
        # is_secret so the cache hygiene is correct.
        promote = await conn.execute(
            """
            UPDATE app_settings
               SET is_secret  = TRUE,
                   updated_at = NOW()
             WHERE key        = $1
               AND is_secret  = FALSE
            """,
            key,
        )
        if promote.startswith("UPDATE 1"):
            rows_already_rotated += 1
            logger.info(
                "20260512_032900: %s already rotated upstream — "
                "leaving fresh value intact, promoting is_secret=true",
                key,
            )
        else:
            logger.info(
                "20260512_032900: %s already in target state "
                "(no-op)", key,
            )

    logger.info(
        "20260512_032900: %d/%d rows redacted (still on leaked value), "
        "%d/%d rows kept their rotated value but promoted to is_secret. "
        "For any redacted key, operator must run `poindexter settings "
        "set <key> <fresh>` after rotating upstream.",
        rows_cleared, len(_LEAKED_ROWS),
        rows_already_rotated, len(_LEAKED_ROWS),
    )

"""Migration 20260619_141719: re-point existing telegram_ops row to the
{telegram_chat_id} placeholder (drop the chat_id literal).

Follow-up to the 2026-06-19 telegram_ops leak fix. That change made the
*baseline seed* carry no operator identity: a fresh DB now seeds
``telegram_ops`` with ``apprise_url = "tgram://{secret}/{telegram_chat_id}/"``,
where the chat_id is resolved from ``app_settings.telegram_chat_id`` at send
time (symmetric with ``discord_ops``). The seed was left ``ON CONFLICT DO
NOTHING`` so it could not disturb a live, working notification row.

That leaves *pre-existing installs* on the legacy shape that the earlier
``20260619_120000`` apprise re-point produced::

    config = {"chat_id": "<operator-id>",
              "apprise_url": "tgram://{secret}/{chat_id}/"}

It still delivers (the handler resolves ``{chat_id}`` from ``config``), but it
(a) keeps the operator's chat_id as a literal in the row and (b) diverges from
fresh installs. This migration converges those rows to the clean template and
drops the literal — leaving routing functionally unchanged, because the handler
then resolves the *same* value from ``app_settings.telegram_chat_id``.

GUARDED so it can never strand a working row: the join onto
``app_settings.telegram_chat_id`` with ``COALESCE(s.value,'') <> ''`` means the
re-point only fires when the canonical value is populated. And the
``apprise_url = 'tgram://{secret}/{chat_id}/'`` predicate scopes it to the
legacy shape only, so it is idempotent and a no-op on a fresh DB (the seed
already wrote the ``{telegram_chat_id}`` form). ``down()`` reverses it: restore
the ``{chat_id}`` template and copy ``app_settings.telegram_chat_id`` back into
``config.chat_id``.

This is defense-in-depth + consistency cleanup, not a live leak.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Re-point the legacy row to the app_settings-resolved placeholder and drop the
# chat_id literal. Scoped to the legacy shape (idempotent / no-op on fresh DB);
# fires only when app_settings.telegram_chat_id is populated.
_REPOINT_SQL = """
UPDATE webhook_endpoints w
   SET config = (w.config - 'chat_id')
                || '{"apprise_url": "tgram://{secret}/{telegram_chat_id}/"}'::jsonb
  FROM app_settings s
 WHERE w.name = 'telegram_ops'
   AND w.config->>'apprise_url' = 'tgram://{secret}/{chat_id}/'
   AND s.key = 'telegram_chat_id'
   AND COALESCE(s.value, '') <> ''
"""

# Reverse: restore the {chat_id}-from-config template and copy the operator's
# telegram_chat_id back into config.chat_id. Scoped to the clean shape so it
# only undoes what up() produced.
_REVERT_SQL = """
UPDATE webhook_endpoints w
   SET config = w.config
                || jsonb_build_object(
                       'chat_id', s.value,
                       'apprise_url', 'tgram://{secret}/{chat_id}/'
                   )
  FROM app_settings s
 WHERE w.name = 'telegram_ops'
   AND w.config->>'apprise_url' = 'tgram://{secret}/{telegram_chat_id}/'
   AND s.key = 'telegram_chat_id'
   AND COALESCE(s.value, '') <> ''
"""


async def up(pool) -> None:
    """Converge a legacy telegram_ops row to the {telegram_chat_id} placeholder.

    Safe to re-run: the WHERE clause matches only the legacy ``{chat_id}`` shape,
    so once converged (or on a fresh DB whose seed already wrote the clean form)
    this is a no-op.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(_REPOINT_SQL)
    logger.info("telegram_ops chat_id-placeholder re-point: %s", result)


async def down(pool) -> None:
    """Restore the legacy {chat_id} template and copy telegram_chat_id back.

    Reverses :func:`up`. Only touches rows currently on the clean
    ``{telegram_chat_id}`` shape, and only when ``app_settings.telegram_chat_id``
    is populated (so it can reconstruct ``config.chat_id``).
    """
    async with pool.acquire() as conn:
        result = await conn.execute(_REVERT_SQL)
    logger.info("telegram_ops chat_id-placeholder re-point down: %s", result)

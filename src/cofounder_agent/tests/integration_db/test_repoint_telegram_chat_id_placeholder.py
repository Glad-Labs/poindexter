"""integration_db: the 20260619_141719 telegram_ops chat_id-placeholder re-point.

PR #1714 made the *seed* carry no operator identity — a fresh DB seeds
``telegram_ops`` with ``apprise_url = "tgram://{secret}/{telegram_chat_id}/"``
(chat_id resolved from ``app_settings.telegram_chat_id`` at send time). But
pre-existing installs were left on the legacy shape by the earlier
20260619_120000 re-point: ``config = {"chat_id": "<id>", "apprise_url":
"tgram://{secret}/{chat_id}/"}`` — a chat_id literal living in the row. This
migration converges those existing rows to the clean template and drops the
literal, GUARDED on a populated ``app_settings.telegram_chat_id`` so it never
strands a working notification row.

These tests drive the migration's SQL (exposed as ``_REPOINT_SQL`` /
``_REVERT_SQL`` module constants — same pattern as
``test_dedup_collapse_video_long``) on a rolled-back ``test_txn`` connection:
the shared session DB already seeds the *converged* form, so we reconstruct the
legacy shape in-txn to exercise the conversion the migration performs on an
existing install. The rollback keeps the shared ``telegram_ops`` row pristine
for the sibling ``test_apprise_repoint_migration`` test.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_LEGACY_URL = "tgram://{secret}/{chat_id}/"
_CLEAN_URL = "tgram://{secret}/{telegram_chat_id}/"
_SUFFIX = "_repoint_existing_telegram_ops_row_to_telegram_chat_id_placeholder"


def _mig():
    """Resolve the timestamped migration module by suffix (not pinned to HHMMSS)."""
    import services.migrations as m

    name = next(
        n for _, n, _ in pkgutil.iter_modules(m.__path__) if n.endswith(_SUFFIX)
    )
    return importlib.import_module(f"services.migrations.{name}")


async def _set_chat_id_setting(conn, value: str) -> None:
    """Set app_settings.telegram_chat_id (baseline-seeded, so this UPDATEs)."""
    await conn.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('telegram_chat_id', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        value,
    )


async def _set_legacy_telegram_ops(conn, chat_id_literal: str) -> None:
    """Reconstruct the pre-existing-install legacy shape: a chat_id literal in
    config + the {chat_id}-from-config apprise_url template.
    """
    await conn.execute(
        """
        UPDATE webhook_endpoints
           SET handler_name = 'apprise_notify',
               config = jsonb_build_object('chat_id', $1::text, 'apprise_url', $2::text)
         WHERE name = 'telegram_ops'
        """,
        chat_id_literal,
        _LEGACY_URL,
    )


async def _telegram_ops(conn):
    return await conn.fetchrow(
        """
        SELECT config->>'apprise_url'          AS apprise_url,
               config->>'chat_id'              AS chat_id,
               jsonb_exists(config, 'chat_id') AS has_chat_id
          FROM webhook_endpoints
         WHERE name = 'telegram_ops'
        """
    )


async def test_repoints_legacy_row_and_drops_chat_id_literal(test_txn) -> None:
    """A populated telegram_chat_id converges the legacy row to the clean
    template and removes the chat_id literal."""
    mig = _mig()
    await _set_chat_id_setting(test_txn, "987654321")
    await _set_legacy_telegram_ops(test_txn, "111222333")

    await test_txn.execute(mig._REPOINT_SQL)

    row = await _telegram_ops(test_txn)
    assert row["apprise_url"] == _CLEAN_URL  # re-pointed to app_settings placeholder
    assert row["has_chat_id"] is False  # literal dropped
    assert row["chat_id"] is None


async def test_empty_telegram_chat_id_leaves_row_untouched(test_txn) -> None:
    """The COALESCE(s.value,'')<>'' guard: an empty telegram_chat_id must not
    strand the working row — it stays on the legacy template with its literal."""
    mig = _mig()
    await _set_chat_id_setting(test_txn, "")  # unset sentinel
    await _set_legacy_telegram_ops(test_txn, "111222333")

    await test_txn.execute(mig._REPOINT_SQL)

    row = await _telegram_ops(test_txn)
    assert row["apprise_url"] == _LEGACY_URL  # unchanged
    assert row["chat_id"] == "111222333"  # literal preserved


async def test_down_restores_legacy_template_from_app_settings(test_txn) -> None:
    """down() reverses up(): clean template -> legacy {chat_id} template, with
    chat_id copied back from app_settings.telegram_chat_id."""
    mig = _mig()
    await _set_chat_id_setting(test_txn, "987654321")
    await _set_legacy_telegram_ops(test_txn, "111222333")

    await test_txn.execute(mig._REPOINT_SQL)  # up: -> clean form, literal gone
    await test_txn.execute(mig._REVERT_SQL)  # down: -> legacy form, literal restored

    row = await _telegram_ops(test_txn)
    assert row["apprise_url"] == _LEGACY_URL
    assert row["chat_id"] == "987654321"  # restored from app_settings.telegram_chat_id

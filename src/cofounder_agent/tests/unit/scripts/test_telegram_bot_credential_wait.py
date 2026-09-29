"""``scripts/telegram-bot.py`` idles until Telegram is configured. It never loops.

Telegram is optional. A default install has neither ``telegram_bot_token`` nor
``telegram_chat_id`` (``poindexter setup`` writes neither), and the pipeline-bot
container used to ``sys.exit(1)`` on that. The compose files give it
``restart: unless-stopped``, which restarts on ANY exit code, so a fresh
consumer stack restart-looped the bot for as long as it ran: ``Restarting (1)``
in ``docker ps`` and ``ERROR: telegram_bot_token not in app_settings.`` every few
seconds. The public quick-start CI run of 2026-09-28 tailed 41 of them.

The fix lives in the script, not the restart policy, because each alternative
fails somewhere:

* Exiting 0 changes nothing under ``unless-stopped``. It restarts on a clean
  exit too.
* ``restart: on-failure`` ends the loop but leaves a stopped container. The
  brain's compose-drift probe reads that as drift: it warns, and where
  auto-recover is on (the baseline seeds it on) it brings the container back
  up, to exit again. ``on-failure`` also does not bring a container back after
  a Docker daemon restart.
* A compose profile keeps a second copy of "is Telegram configured?" outside
  ``app_settings``, and nothing in ``poindexter setup`` could set it: the token
  is provisioned later, by ``poindexter settings set``.

So the bot waits. The process stays up and healthy, and setting the two rows is
the whole enable step. That is the contract pinned here.

These tests execute the real script top to bottom, because its import-time work
is the code under test: they fake ``asyncpg`` (an in-memory ``app_settings``) and
``time.sleep`` and nothing else. The one ordering that matters most is asserted
by behaviour rather than by reading the source: the wait must finish BEFORE the
script snapshots the ``/cli`` settings, or a bot that started unconfigured would
keep a snapshot with no ``telegram_chat_id`` and the ``/cli`` passthrough, which
authorises against that value, would reject every message until a restart.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import asyncpg
import pytest


def _bot_script() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "scripts" / "telegram-bot.py"
        if candidate.is_file():
            return candidate
    pytest.skip("scripts/telegram-bot.py not reachable from the test location")


# The two states that matter on a real install. A fresh stack has the chat id
# seeded EMPTY and no token row at all; an operator's stack has both set.
FRESH_INSTALL = {"telegram_chat_id": ("", False)}
CONFIGURED = {
    "telegram_bot_token": ("123456:not-a-real-token", False),
    "telegram_chat_id": ("987654321", False),
}

# Fail a test that would otherwise wait forever instead of hanging the suite.
_MAX_POLLS = 25


class _FakeConn:
    """The slice of an asyncpg connection the bot's import-time code uses."""

    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def fetchrow(self, _sql: str, key: str):
        # ``plugins.secrets.get_secret``: ``SELECT value, is_secret ... WHERE key = $1``
        row = self._db.rows.get(key)
        return None if row is None else {"value": row[0], "is_secret": row[1]}

    async def fetch(self, _sql: str, keys: list[str]):
        # ``_load_passthrough_config``: ``SELECT key, value ... WHERE key = ANY($1)``
        return [
            {"key": key, "value": value}
            for key, (value, _is_secret) in self._db.rows.items()
            if key in keys
        ]

    async def fetchval(self, _sql: str, key: str):
        row = self._db.rows.get(key)
        return None if row is None else row[0]

    async def close(self) -> None:
        return None


class _Acquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db
        self.closed = False

    def acquire(self) -> _Acquire:
        return _Acquire(_FakeConn(self._db))

    async def close(self) -> None:
        self.closed = True


class _FakeDb:
    """An in-memory ``app_settings`` plus the clock the idle bot sleeps on.

    ``arrivals`` is the operator, acting between polls: each ``time.sleep`` call
    applies the next entry to ``rows``, so a test scripts exactly when the
    settings appear.
    """

    def __init__(
        self,
        rows: dict[str, tuple[str, bool]] | None = None,
        arrivals: list[dict[str, tuple[str, bool]]] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.rows = dict(rows or {})
        self.arrivals = list(arrivals or [])
        self.fail_with = fail_with
        self.pools: list[_FakePool] = []
        self.sleeps: list[float] = []
        self.pools_open_while_sleeping: list[int] = []

    async def create_pool(self, _dsn: str, min_size: int = 1, max_size: int = 2):
        if self.fail_with is not None:
            raise self.fail_with
        pool = _FakePool(self)
        self.pools.append(pool)
        return pool

    async def connect(self, _dsn: str, *_args: object, **_kwargs: object) -> _FakeConn:
        if self.fail_with is not None:
            raise self.fail_with
        return _FakeConn(self)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.pools_open_while_sleeping.append(sum(not p.closed for p in self.pools))
        assert len(self.sleeps) <= _MAX_POLLS, "the bot never stopped waiting"
        if self.arrivals:
            self.rows.update(self.arrivals.pop(0))


def _run_script(monkeypatch: pytest.MonkeyPatch, db: _FakeDb) -> ModuleType:
    """Execute ``scripts/telegram-bot.py`` as an import, against ``db``."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://bot:pw@db.invalid:5432/bot")
    # The script prepends ``scripts/`` and the backend root to sys.path.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(asyncpg, "create_pool", db.create_pool)
    monkeypatch.setattr(asyncpg, "connect", db.connect)
    monkeypatch.setattr(time, "sleep", db.sleep)
    spec = importlib.util.spec_from_file_location("telegram_bot_under_test", _bot_script())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# A configured bot is exactly what it was before
# ---------------------------------------------------------------------------


def test_configured_bot_starts_without_waiting_or_saying_anything(monkeypatch, capsys) -> None:
    """The operator's stack has both settings, so nothing about it may change."""
    db = _FakeDb(rows=CONFIGURED)

    _run_script(monkeypatch, db)

    out = capsys.readouterr().out
    assert db.sleeps == []
    assert "not configured" not in out and "found; starting" not in out
    assert "[INIT] Loading config + OAuth client..." in out


# ---------------------------------------------------------------------------
# An unconfigured bot idles, then starts on its own
# ---------------------------------------------------------------------------


def test_unconfigured_bot_idles_then_starts_when_both_settings_appear(
    monkeypatch, capsys
) -> None:
    db = _FakeDb(rows=FRESH_INSTALL, arrivals=[{}, CONFIGURED])

    _run_script(monkeypatch, db)  # returns only because the wait ended: no SystemExit

    out = capsys.readouterr().out
    assert db.sleeps == [30, 30], "one sleep per check that found it still unset"
    assert out.count("is not configured") == 1, "one message per state, not one per poll"
    assert "unset: telegram_bot_token, telegram_chat_id" in out
    assert "poindexter settings set telegram_bot_token <token> --secret" in out
    assert "poindexter settings set telegram_chat_id <chat id>" in out
    assert "Telegram settings found; starting." in out


def test_idle_bot_holds_no_database_connection_between_checks(monkeypatch) -> None:
    """A weeks-long idle bot must not pin a Postgres backend."""
    db = _FakeDb(rows=FRESH_INSTALL, arrivals=[{}, {}, CONFIGURED])

    _run_script(monkeypatch, db)

    assert db.pools_open_while_sleeping == [0, 0, 0]
    assert all(pool.closed for pool in db.pools)


def test_cli_snapshot_is_taken_after_the_wait_not_before(monkeypatch) -> None:
    """The ordering trap: the /cli settings are read ONCE, at import.

    ``_BOT_SITE_CONFIG`` is built from a snapshot of app_settings and the /cli
    passthrough authorises against its ``telegram_chat_id``. If the wait came
    after the snapshot, a bot that started unconfigured would hold a snapshot
    with no chat id for the rest of its life.
    """
    db = _FakeDb(rows=FRESH_INSTALL, arrivals=[CONFIGURED])

    module = _run_script(monkeypatch, db)

    assert module._passthrough_extra["telegram_chat_id"] == "987654321"
    assert module._BOT_SITE_CONFIG.get("telegram_chat_id") == "987654321"


# ---------------------------------------------------------------------------
# Partial and blank configuration
# ---------------------------------------------------------------------------


def test_partial_configuration_names_only_what_is_still_missing(monkeypatch, capsys) -> None:
    db = _FakeDb(
        rows={"telegram_bot_token": ("123456:not-a-real-token", False)},
        arrivals=[{}, {"telegram_chat_id": ("987654321", False)}],
    )

    _run_script(monkeypatch, db)

    out = capsys.readouterr().out
    assert out.count("is not configured") == 1
    assert "unset: telegram_chat_id)" in out
    assert "settings set telegram_chat_id" in out
    assert "settings set telegram_bot_token" not in out, "the token is set; don't ask for it"


def test_message_repeats_only_when_the_set_of_missing_keys_changes(monkeypatch, capsys) -> None:
    db = _FakeDb(
        rows=FRESH_INSTALL,
        arrivals=[
            {},  # still nothing: no new message
            {"telegram_bot_token": ("123456:not-a-real-token", False)},  # token arrives
            {},  # chat id still missing: no new message
            {"telegram_chat_id": ("987654321", False)},
        ],
    )

    _run_script(monkeypatch, db)

    out = capsys.readouterr().out
    assert out.count("is not configured") == 2
    assert out.index("unset: telegram_bot_token, telegram_chat_id") < out.index(
        "unset: telegram_chat_id)"
    )


@pytest.mark.parametrize(
    "blank_token",
    [("", False), ("   ", False), ("\n", False), ("", True)],
    ids=["empty", "spaces", "newline", "empty-secret-placeholder"],
)
def test_blank_values_count_as_unset(monkeypatch, capsys, blank_token) -> None:
    """``_setup()`` strips both values, so a blank one would fail ``_main()``'s
    ``not BOT_TOKEN`` guard and restart-loop the bot; the wait must catch it first."""
    db = _FakeDb(
        rows={"telegram_bot_token": blank_token, "telegram_chat_id": ("987654321", False)},
        arrivals=[{"telegram_bot_token": ("123456:not-a-real-token", False)}],
    )

    _run_script(monkeypatch, db)

    out = capsys.readouterr().out
    assert db.sleeps == [30]
    assert "unset: telegram_bot_token)" in out


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({}, ["telegram_bot_token", "telegram_chat_id"]),
        ({"telegram_bot_token": "t"}, ["telegram_chat_id"]),
        ({"telegram_chat_id": "c"}, ["telegram_bot_token"]),
        ({"telegram_bot_token": "t", "telegram_chat_id": "c"}, []),
        ({"telegram_bot_token": None, "telegram_chat_id": " c "}, ["telegram_bot_token"]),
        ({"telegram_bot_token": "  ", "telegram_chat_id": ""}, ["telegram_bot_token", "telegram_chat_id"]),
    ],
)
def test_missing_telegram_keys(monkeypatch, cfg, expected) -> None:
    module = _run_script(monkeypatch, _FakeDb(rows=CONFIGURED))

    assert module._missing_telegram_keys(cfg) == expected


# ---------------------------------------------------------------------------
# A broken database is still a failure
# ---------------------------------------------------------------------------


def test_a_database_that_is_down_raises_instead_of_looping_quietly(monkeypatch) -> None:
    """The wait absorbs "not configured", not "cannot read the settings".

    A database error must reach the top of the process like it always did, with a
    traceback and the restart policy's backoff, not be swallowed into an
    indefinite quiet retry that reads as a healthy idle bot.
    """
    db = _FakeDb(fail_with=ConnectionRefusedError("database is down"))

    with pytest.raises(ConnectionRefusedError):
        _run_script(monkeypatch, db)

    assert db.sleeps == []

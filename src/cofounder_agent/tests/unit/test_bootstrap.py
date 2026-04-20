"""Unit tests for brain/bootstrap.py — the DB-URL resolver (#198).

Covers the resolution chain (explicit → bootstrap.toml → env vars → None)
and the loud-fail behavior of require_database_url.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# brain/ lives at the repo root; walk up until we find it.
_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "brain" / "bootstrap.py").is_file():
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
        break

from brain import bootstrap  # noqa: E402


@pytest.fixture
def isolated_bootstrap(monkeypatch, tmp_path):
    """Point the bootstrap module at an empty tmp dir + clear env vars."""
    tmp_bootstrap = tmp_path / "bootstrap.toml"
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_DIR", tmp_path)
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_FILE", tmp_bootstrap)
    for var in ("DATABASE_URL", "LOCAL_DATABASE_URL", "POINDEXTER_MEMORY_DSN"):
        monkeypatch.delenv(var, raising=False)
    return tmp_bootstrap


# ---------------------------------------------------------------------------
# resolve_database_url priority chain
# ---------------------------------------------------------------------------


class TestResolvePriority:
    def test_explicit_arg_wins(self, isolated_bootstrap, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
        url = bootstrap.resolve_database_url(explicit="postgresql://explicit/db")
        assert url == "postgresql://explicit/db"

    def test_bootstrap_toml_beats_env(self, isolated_bootstrap, monkeypatch):
        bootstrap.write_bootstrap_toml({"database_url": "postgresql://toml/db"})
        monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
        assert bootstrap.resolve_database_url() == "postgresql://toml/db"

    def test_database_url_env(self, isolated_bootstrap, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
        assert bootstrap.resolve_database_url() == "postgresql://env/db"

    def test_local_database_url_env(self, isolated_bootstrap, monkeypatch):
        monkeypatch.setenv("LOCAL_DATABASE_URL", "postgresql://local/db")
        assert bootstrap.resolve_database_url() == "postgresql://local/db"

    def test_legacy_memory_dsn_env(self, isolated_bootstrap, monkeypatch):
        monkeypatch.setenv("POINDEXTER_MEMORY_DSN", "postgresql://mem/db")
        assert bootstrap.resolve_database_url() == "postgresql://mem/db"

    def test_database_url_beats_local(self, isolated_bootstrap, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://primary/db")
        monkeypatch.setenv("LOCAL_DATABASE_URL", "postgresql://local/db")
        # DATABASE_URL is listed first in _DB_URL_ENV_VARS.
        assert bootstrap.resolve_database_url() == "postgresql://primary/db"

    def test_returns_none_when_nothing_set(self, isolated_bootstrap):
        assert bootstrap.resolve_database_url() is None


# ---------------------------------------------------------------------------
# bootstrap.toml round-trip
# ---------------------------------------------------------------------------


class TestBootstrapToml:
    def test_write_and_read_round_trip(self, isolated_bootstrap):
        path = bootstrap.write_bootstrap_toml(
            {
                "database_url": "postgresql://roundtrip/db",
                "telegram_bot_token": "abc:123",
                "telegram_chat_id": "999",
            }
        )
        assert path.is_file()
        assert bootstrap.bootstrap_file_exists() is True
        assert bootstrap.resolve_database_url() == "postgresql://roundtrip/db"
        assert bootstrap.get_bootstrap_value("telegram_bot_token") == "abc:123"

    def test_get_bootstrap_value_falls_back_to_env(self, isolated_bootstrap, monkeypatch):
        bootstrap.write_bootstrap_toml({"database_url": "x"})
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "from-env")
        assert bootstrap.get_bootstrap_value("telegram_chat_id") == "from-env"

    def test_get_bootstrap_value_default(self, isolated_bootstrap):
        assert bootstrap.get_bootstrap_value("nope", default="fallback") == "fallback"

    def test_write_skips_none_values(self, isolated_bootstrap):
        bootstrap.write_bootstrap_toml(
            {"database_url": "postgresql://x", "telegram_bot_token": None}
        )
        text = bootstrap.BOOTSTRAP_FILE.read_text(encoding="utf-8")
        assert "database_url" in text
        assert "telegram_bot_token" not in text

    def test_write_escapes_quotes(self, isolated_bootstrap):
        bootstrap.write_bootstrap_toml({"database_url": 'postgresql://"weird"/db'})
        # resolve_database_url round-trips through tomllib, so the escape
        # has to actually parse as TOML — not just survive as bytes.
        assert bootstrap.resolve_database_url() == 'postgresql://"weird"/db'


# ---------------------------------------------------------------------------
# require_database_url loud-fail path
# ---------------------------------------------------------------------------


class TestRequireDatabaseUrl:
    def test_returns_url_when_resolved(self, isolated_bootstrap, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://yes/db")
        url = bootstrap.require_database_url(source="test")
        assert url == "postgresql://yes/db"

    def test_exits_when_unresolved(self, isolated_bootstrap, monkeypatch):
        # Stub notify_operator so the test doesn't actually page anyone,
        # and capture the exit code.
        calls: list[dict] = []

        def _fake_notify(**kwargs):
            calls.append(kwargs)
            return {"telegram": "stubbed", "discord": "stubbed", "alerts_log": "stubbed"}

        import brain.operator_notifier as notifier

        monkeypatch.setattr(notifier, "notify_operator", _fake_notify)

        with pytest.raises(SystemExit) as exc:
            bootstrap.require_database_url(source="pytest")
        assert exc.value.code == 2
        assert calls
        assert calls[0]["source"] == "pytest"
        assert calls[0]["severity"] == "critical"

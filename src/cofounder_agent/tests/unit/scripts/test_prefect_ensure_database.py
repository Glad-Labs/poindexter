"""scripts/prefect_ensure_database.py — the Prefect server's database exists.

Nothing created the ``prefect`` database on a fresh ``postgres-local`` volume,
so the Prefect server died with ``InvalidCatalogNameError`` on every fresh
install and nothing was ever dispatched (quickstart-e2e, 2026-09-28). The
consumer stack's prefect-server now runs this script before it starts.
"""

from __future__ import annotations

import asyncio
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import yaml

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "start-stack.sh").is_file()
)


def _load():
    path = _REPO_ROOT / "scripts" / "prefect_ensure_database.py"
    spec = spec_from_file_location("prefect_ensure_database", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PED = _load()
URL = "postgresql+asyncpg://poindexter:s3cr%40t@postgres-local:5432/prefect"


class _Conn:
    def __init__(self, *, exists: bool, create_error: Exception | None = None):
        self.exists = exists
        self.create_error = create_error
        self.executed: list[str] = []
        self.closed = False

    async def fetchval(self, sql, *args):
        assert args == ("prefect",)
        return 1 if self.exists else None

    async def execute(self, sql):
        self.executed.append(sql)
        if self.create_error:
            raise self.create_error

    async def close(self):
        self.closed = True


def test_parses_the_server_url():
    cfg = PED.parse_database_url(URL)
    assert cfg == {
        "host": "postgres-local", "port": 5432, "user": "poindexter",
        "password": "s3cr@t", "database": "prefect",
    }


@pytest.mark.parametrize("bad", ["mysql://u:p@h/db", "postgresql://u:p@h:5432/"])
def test_rejects_urls_it_cannot_act_on(bad):
    with pytest.raises(ValueError):
        PED.parse_database_url(bad)


def test_quote_ident_escapes_quotes():
    assert PED.quote_ident('pre"fect') == '"pre""fect"'


def test_creates_a_missing_database_via_the_maintenance_db():
    conn = _Conn(exists=False)
    with patch("asyncpg.connect", AsyncMock(return_value=conn)) as connect:
        assert asyncio.run(PED.ensure_database(URL)) == "created"
    assert connect.await_args.kwargs["database"] == "postgres"
    assert conn.executed == ['CREATE DATABASE "prefect"']
    assert conn.closed


def test_existing_database_is_left_alone():
    conn = _Conn(exists=True)
    with patch("asyncpg.connect", AsyncMock(return_value=conn)):
        assert asyncio.run(PED.ensure_database(URL)) == "exists"
    assert conn.executed == []


def test_a_concurrent_create_counts_as_exists():
    conn = _Conn(exists=False, create_error=asyncpg.DuplicateDatabaseError("exists"))
    with patch("asyncpg.connect", AsyncMock(return_value=conn)):
        assert asyncio.run(PED.ensure_database(URL)) == "exists"


def test_unreachable_postgres_fails_loud():
    with patch("asyncpg.connect", AsyncMock(side_effect=ConnectionRefusedError("refused"))):
        with pytest.raises(RuntimeError, match="could not reach Postgres"):
            asyncio.run(PED.ensure_database(URL, attempts=2, delay_s=0))


def test_main_needs_the_url(monkeypatch):
    monkeypatch.delenv(PED.URL_ENV, raising=False)
    assert PED.main() == 1


def test_public_stack_ensures_the_database_before_starting_the_server():
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.consumer.yml").read_text(encoding="utf-8"))
    server = compose["services"]["prefect-server"]
    command = server["command"]
    script = command[-1] if isinstance(command, list) else command
    ensure = script.find("prefect_ensure_database.py")
    start = script.find("prefect server start")
    assert -1 < ensure < start, command
    assert "&&" in script[ensure:start], "a failed ensure must not fall through to the server"
    assert any(str(v).endswith("/scripts:/opt/scripts:ro") for v in server.get("volumes", []))
    # The script reads the very URL the server uses.
    assert PED.URL_ENV in server["environment"]

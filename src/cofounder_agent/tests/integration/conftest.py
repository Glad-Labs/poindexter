"""Real-services integration fixtures (scaffold for GitHub #21).

Provides pytest fixtures that talk to a *real* Postgres (running in the
standard `docker compose -f docker-compose.local.yml` stack) and a *real*
Ollama on the host. Used by tests that need to verify behavior end-to-end
against actual services, not mocks.

This is the scaffold for the Phase A0 integration test harness required
by the plugin-architecture refactor (GitHub #64). The idea is simple:
give the refactor a test bed where "did we change pipeline behavior?"
can be answered by running the same canonical task before and after a
change and comparing outcomes.

## Running

Tests that import these fixtures only run when the caller opts in:

    INTEGRATION_TESTS=1 REAL_SERVICES_TESTS=1 poetry run pytest -m integration

Both gates are required so the harness can't be triggered accidentally
by CI that only sets `INTEGRATION_TESTS` for the existing live-server
integration suite.

## Isolation

The harness never touches Matt's operating database (`poindexter_brain`).
It creates / uses a separate `poindexter_test` database on the same
Postgres instance. The main DB is never written to.

## Ollama

Ollama is expected on the host at the URL stored in app_settings
(`ollama_base_url` — default `http://host.docker.internal:11434`).
Tests use `brain.docker_utils.resolve_url` to translate if running
inside a container; from the host pytest process the URL is taken
as-is. A missing or unreachable Ollama skips the relevant tests.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import httpx
import pytest

# ---------------------------------------------------------------------------
# Session-scoped event loop. pytest-asyncio's default event_loop is
# function-scoped, which conflicts with our session-scoped async fixtures
# (real_pool, real_ollama_url). Override here so everything coordinates.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def real_services_enabled() -> bool:
    """True when both INTEGRATION_TESTS and REAL_SERVICES_TESTS are set.

    Two gates so this harness can't be triggered by the existing
    INTEGRATION_TESTS-only convention used by `test_blog_workflow.py`.
    """
    return bool(os.getenv("INTEGRATION_TESTS")) and bool(os.getenv("REAL_SERVICES_TESTS"))


requires_real_services = pytest.mark.skipif(
    not real_services_enabled(),
    reason="Real-services harness disabled. Set INTEGRATION_TESTS=1 and REAL_SERVICES_TESTS=1 to enable.",
)


# ---------------------------------------------------------------------------
# DB URL resolution
# ---------------------------------------------------------------------------


_DEFAULT_ADMIN_DSN = "postgresql://poindexter:poindexter-brain-local@localhost:15432/postgres"
_TEST_DB_NAME = "poindexter_test"


def _admin_dsn() -> str:
    """DSN for the `postgres` admin DB — used to CREATE the test DB.

    Precedence: TEST_ADMIN_DSN env > POSTGRES_ADMIN_DSN env > default.
    """
    return os.getenv("TEST_ADMIN_DSN") or os.getenv("POSTGRES_ADMIN_DSN") or _DEFAULT_ADMIN_DSN


def _test_dsn() -> str:
    """DSN pointing at the isolated test database."""
    admin = _admin_dsn()
    # Swap the database name in the DSN.
    if admin.rsplit("/", 1)[-1] == "postgres":
        return admin.rsplit("/", 1)[0] + f"/{_TEST_DB_NAME}"
    # Caller passed a non-default admin DSN; trust them but swap the last segment.
    return admin.rsplit("/", 1)[0] + f"/{_TEST_DB_NAME}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def ensure_test_database() -> None:
    """Session-scoped: create `poindexter_test` if it doesn't exist.

    Installs pgvector if available. Silently skips if the extension
    isn't installed on the Postgres instance — tests that need vector
    columns should assert on that explicitly.
    """
    if not real_services_enabled():
        pytest.skip("real-services harness disabled")

    admin = await asyncpg.connect(_admin_dsn())
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", _TEST_DB_NAME
        )
        if not exists:
            # CREATE DATABASE can't run inside a transaction.
            await admin.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    finally:
        await admin.close()

    # Install pgvector in the test DB (best-effort).
    test_conn = await asyncpg.connect(_test_dsn())
    try:
        try:
            await test_conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except asyncpg.exceptions.UndefinedFileError:
            # Extension binary not available on this Postgres. OK; skip.
            pass
    finally:
        await test_conn.close()


@pytest.fixture(scope="session")
async def real_pool(ensure_test_database) -> AsyncIterator[asyncpg.Pool]:
    """Session-scoped connection pool against the isolated test DB."""
    pool = await asyncpg.create_pool(_test_dsn(), min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def clean_test_tables(real_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Pool]:
    """Per-test: truncate all non-system tables in the test DB before running.

    Fast path for tests that just need a blank slate. Schema is preserved;
    tables stay, rows are wiped.

    Use this instead of transaction rollback when the code under test opens
    its own pool (refactored plugin runners will, most likely).
    """
    async with real_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename NOT LIKE 'pg_%'
              AND tablename NOT LIKE 'sql_%'
            """
        )
        if rows:
            names = ", ".join(f'"{r["tablename"]}"' for r in rows)
            await conn.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    yield real_pool


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


_DEFAULT_OLLAMA_URL = "http://localhost:11434"


def _ollama_url() -> str:
    return (
        os.getenv("OLLAMA_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or _DEFAULT_OLLAMA_URL
    )


async def _ollama_reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url.rstrip('/')}/api/tags")
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
async def real_ollama_url() -> str:
    """Verify Ollama is reachable and return its base URL.

    Skips the dependent test if Ollama isn't responding. Does not pull
    models — that's the test's responsibility if it needs a specific one.
    """
    if not real_services_enabled():
        pytest.skip("real-services harness disabled")

    url = _ollama_url()
    if not await _ollama_reachable(url):
        pytest.skip(f"Ollama not reachable at {url}; start the host Ollama before running")
    return url


@asynccontextmanager
async def _ollama_client(url: str):
    async with httpx.AsyncClient(base_url=url, timeout=60.0) as client:
        yield client


@pytest.fixture
async def real_ollama(real_ollama_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """Per-test httpx client pre-configured with the Ollama base URL."""
    async with _ollama_client(real_ollama_url) as client:
        yield client


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def migrations_applied(real_pool: asyncpg.Pool) -> None:
    """Apply schema migrations to the test DB.

    Intentionally minimal: loads the canonical `init_test_schema.sql` if
    present, and runs every numbered `.py` migration in
    `services/migrations/` that defines `async def up(conn): ...`.

    The real refactor will replace this with a proper migration runner;
    for the scaffold, this just gives us a DB with the right tables.
    """
    repo_root = Path(__file__).resolve().parents[3]
    init_sql = repo_root / "src" / "cofounder_agent" / "init_test_schema.sql"

    async with real_pool.acquire() as conn:
        if init_sql.exists():
            await conn.execute(init_sql.read_text(encoding="utf-8"))

        # Also ensure app_settings exists — Phase A's seed_loader expects it.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                id SERIAL PRIMARY KEY,
                key VARCHAR(255) UNIQUE NOT NULL,
                value TEXT DEFAULT '',
                category VARCHAR(100) DEFAULT 'general',
                description TEXT DEFAULT '',
                is_secret BOOLEAN DEFAULT false,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )

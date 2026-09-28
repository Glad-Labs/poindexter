"""``poindexter settings set`` against a real Postgres.

The unit tests (``tests/unit/cli/test_settings_cli.py``) pin what the CLI
*binds*. They cannot run the upsert's ``COALESCE``, and the bug this file
guards lived exactly there: ``--category`` defaulted to ``general`` and
``--description`` to ``""``, neither is NULL, so ``COALESCE(<new>,
app_settings.<col>)`` could never fall back to the row's own value. Every
``settings set`` on an existing key silently re-filed it under ``general``
(observed 2026-09-28: ``preview_base_url``, ``infrastructure`` -> ``general``)
and blanked its description. Only a real row shows whether the statement
keeps what it should.

Every key here shares ``_PREFIX``, which ``resolve_category`` files under
``quality`` -- so a test that seeds a row under some OTHER category proves
"the row keeps its own", not "the row was re-derived".
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import pytest_asyncio
from click.testing import CliRunner

from poindexter.cli.settings import settings_group
from poindexter.services.settings_categories import resolve_category

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_PREFIX = "qa_cli_upsert_probe_"


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def _clean_probe_rows(test_pool):
    async def wipe() -> None:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM app_settings WHERE key LIKE $1", f"{_PREFIX}%",
            )

    await wipe()
    yield
    await wipe()


async def _settings_set(test_dsn: str, *args: str):
    """Run ``poindexter settings set <args>`` against the test DB.

    The command drives its own event loop (``asyncio.run``), which cannot
    start inside this test's running loop, so it runs on a worker thread.
    """
    runner = CliRunner()
    with patch("poindexter.cli._bootstrap.resolve_dsn", return_value=test_dsn):
        return await asyncio.to_thread(
            runner.invoke, settings_group, ["set", *args],
        )


async def _seed(
    pool,
    key: str,
    *,
    category: str = "pipeline",
    description: str = "Seeded description",
    is_active: bool = True,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value, category, description, is_active) "
            "VALUES ($1, 'old', $2, $3, $4)",
            key, category, description, is_active,
        )


async def _row(pool, key: str):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT value, category, description, is_active, is_secret "
            "FROM app_settings WHERE key = $1",
            key,
        )


# ---------------------------------------------------------------------------
# Plain path
# ---------------------------------------------------------------------------


async def test_existing_row_keeps_category_and_description_without_flags(
    test_pool, test_dsn,
) -> None:
    """THE regression: a bare ``settings set`` changes the value and nothing
    else. The row's category is deliberately NOT the resolver's answer."""
    key = f"{_PREFIX}existing"
    await _seed(test_pool, key, category="pipeline", description="Base URL for previews")

    result = await _settings_set(test_dsn, key, "new-value")

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["value"] == "new-value"
    assert row["category"] == "pipeline"
    assert row["description"] == "Base URL for previews"


async def test_write_reactivates_a_disabled_row_and_still_keeps_its_metadata(
    test_pool, test_dsn,
) -> None:
    key = f"{_PREFIX}disabled"
    await _seed(test_pool, key, category="pipeline", is_active=False)

    result = await _settings_set(test_dsn, key, "back-on")

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["is_active"] is True
    assert row["category"] == "pipeline"
    assert row["description"] == "Seeded description"


async def test_explicit_flags_override_on_an_existing_row(test_pool, test_dsn) -> None:
    key = f"{_PREFIX}override"
    await _seed(test_pool, key, category="pipeline", description="Seeded description")

    result = await _settings_set(
        test_dsn, key, "v", "--category", "models", "--description", "New text",
    )

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["category"] == "models"
    assert row["description"] == "New text"


async def test_explicit_general_is_written_not_mistaken_for_the_omitted_flag(
    test_pool, test_dsn,
) -> None:
    """The old default was the literal string ``general``; an operator who
    really wants ``general`` must still get it."""
    key = f"{_PREFIX}general"
    await _seed(test_pool, key, category="pipeline")

    result = await _settings_set(test_dsn, key, "v", "--category", "general")

    assert result.exit_code == 0, result.output
    assert (await _row(test_pool, key))["category"] == "general"


async def test_empty_description_clears_it(test_pool, test_dsn) -> None:
    key = f"{_PREFIX}clear"
    await _seed(test_pool, key, description="Seeded description")

    result = await _settings_set(test_dsn, key, "v", "--description", "")

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["description"] == ""
    assert row["category"] == "pipeline"


async def test_allow_new_files_the_row_under_the_resolved_category(
    test_pool, test_dsn,
) -> None:
    """A new row gets the category the boot seeder would give it (not
    ``general``), and an empty -- not NULL -- description."""
    key = f"{_PREFIX}new"

    result = await _settings_set(test_dsn, key, "fresh", "--allow-new")

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["value"] == "fresh"
    assert row["category"] == resolve_category(key) == "quality"
    assert row["description"] == ""
    assert row["is_active"] is True


async def test_allow_new_explicit_category_and_description_win(
    test_pool, test_dsn,
) -> None:
    key = f"{_PREFIX}new_explicit"

    result = await _settings_set(
        test_dsn, key, "fresh", "--allow-new",
        "--category", "pipeline", "--description", "Made by hand",
    )

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["category"] == "pipeline"
    assert row["description"] == "Made by hand"


async def test_missing_key_without_allow_new_writes_nothing(test_pool, test_dsn) -> None:
    key = f"{_PREFIX}typo"

    result = await _settings_set(test_dsn, key, "v")

    assert result.exit_code == 2, result.output
    assert "--allow-new" in result.output
    assert await _row(test_pool, key) is None


# ---------------------------------------------------------------------------
# --secret path (real pgcrypto)
#
# The secret path was audited for the same trap. It does not have it:
# ``set_secret``'s conflict clause never writes ``category``. These pin that
# with a real row rather than by reading the SQL, and pin that the CLI says
# so instead of reporting a category that was never applied.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def _pgcrypto(test_pool, monkeypatch):
    monkeypatch.setenv("POINDEXTER_SECRET_KEY", "integration-db-test-key")
    try:
        async with test_pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    except Exception as exc:  # noqa: BLE001 -- privilege problem, skip with the reason
        pytest.skip(f"pgcrypto unavailable on the test DB: {exc}")


async def test_secret_on_existing_row_keeps_its_category(
    test_pool, test_dsn, _pgcrypto,
) -> None:
    key = f"{_PREFIX}secret_existing"
    await _seed(test_pool, key, category="pipeline", description="Seeded description")

    result = await _settings_set(test_dsn, key, "s3cr3t", "--secret")

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["is_secret"] is True
    assert row["value"].startswith("enc:v1:")
    assert row["category"] == "pipeline"
    assert row["description"] == "Seeded description"
    # The confirmation names the category the row holds, not the ``secrets``
    # default that only a new row gets.
    assert "category=pipeline" in result.output
    assert "s3cr3t" not in result.output


async def test_secret_explicit_category_is_not_applied_to_an_existing_row_and_says_so(
    test_pool, test_dsn, _pgcrypto,
) -> None:
    key = f"{_PREFIX}secret_recategorise"
    await _seed(test_pool, key, category="pipeline")

    result = await _settings_set(test_dsn, key, "s3cr3t", "--secret", "--category", "models")

    assert result.exit_code == 0, result.output
    assert (await _row(test_pool, key))["category"] == "pipeline"
    assert "not applied" in result.output
    assert "'pipeline'" in result.output and "'models'" in result.output
    assert "category=pipeline" in result.output


async def test_secret_new_row_is_filed_under_secrets_by_default(
    test_pool, test_dsn, _pgcrypto,
) -> None:
    key = f"{_PREFIX}secret_new"

    result = await _settings_set(test_dsn, key, "s3cr3t", "--secret")

    assert result.exit_code == 0, result.output
    row = await _row(test_pool, key)
    assert row["is_secret"] is True
    assert row["category"] == "secrets"
    assert "category=secrets" in result.output

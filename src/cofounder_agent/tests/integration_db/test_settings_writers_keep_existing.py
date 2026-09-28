"""The service-layer ``app_settings`` writers keep what the caller didn't supply.

``SettingsService.set`` and ``AdminDatabase.set_setting`` upsert with
``COALESCE(<new>, app_settings.<col>)`` arms meant to keep the row's own value
when the caller omits an optional argument. Both read ``EXCLUDED`` -- the row
proposed for insertion -- and the fallbacks in VALUES made it never NULL, so
the COALESCE could not fall back:

* ``SettingsService.set(key, value)`` re-filed an existing row under
  ``general`` (the console chat's ``set_setting`` tool does exactly that) and
  cleared ``is_secret`` on an existing secret row. Its own docstring and
  ``docs/architecture/services/settings_service.md`` promised "None means
  leave the existing column alone".
* ``AdminDatabase.set_setting(key, value)`` blanked an existing row's
  description.

Same class of bug as the ``poindexter settings set`` CLI's upsert, whose
``--category`` / ``--description`` defaults were never NULL: a mock can't run
a COALESCE, so these assert on real rows.

Every key shares ``_PREFIX``, which ``resolve_category`` files under
``quality`` -- so a row seeded under some OTHER category proves "the row kept
its own", not "the row was re-derived".

``AdminDatabase.set_setting`` swallows exceptions and returns False, so each
call here asserts ``is True``: a SQL or parameter-typing error must not read
as a pass.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from poindexter.services.admin_db import AdminDatabase
from poindexter.services.settings_categories import resolve_category
from poindexter.services.settings_service import SettingsService

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_PREFIX = "qa_writers_probe_"


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


async def _seed(
    pool,
    key: str,
    *,
    category: str = "pipeline",
    description: str = "Seeded description",
    is_secret: bool = False,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings "
            "(key, value, category, description, is_secret, is_active) "
            "VALUES ($1, 'old', $2, $3, $4, true)",
            key, category, description, is_secret,
        )


async def _row(pool, key: str):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT value, category, description, is_secret, is_active "
            "FROM app_settings WHERE key = $1",
            key,
        )


# ---------------------------------------------------------------------------
# SettingsService.set
# ---------------------------------------------------------------------------


async def test_set_without_optionals_leaves_an_existing_row_alone(test_pool) -> None:
    """The live path: the console chat's ``set_setting`` tool is
    ``SettingsService(pool).set(key, value)``."""
    key = f"{_PREFIX}plain"
    await _seed(test_pool, key, category="pipeline", description="Base URL for previews")

    await SettingsService(test_pool).set(key, "new-value")

    row = await _row(test_pool, key)
    assert row["value"] == "new-value"
    assert row["category"] == "pipeline"
    assert row["description"] == "Base URL for previews"
    assert row["is_secret"] is False


async def test_set_never_unsecrets_a_secret_row_when_is_secret_is_omitted(
    test_pool,
) -> None:
    """The security-relevant half: an omitted ``is_secret`` used to be
    COALESCEd to FALSE and written over the row's TRUE."""
    key = f"{_PREFIX}secret"
    await _seed(test_pool, key, category="pipeline", is_secret=True)

    await SettingsService(test_pool).set(key, "new-value")

    row = await _row(test_pool, key)
    assert row["value"] == "new-value"
    assert row["is_secret"] is True
    assert row["category"] == "pipeline"
    assert row["description"] == "Seeded description"


async def test_set_explicit_optionals_override(test_pool) -> None:
    key = f"{_PREFIX}override"
    await _seed(test_pool, key, category="pipeline", description="Seeded description")

    await SettingsService(test_pool).set(
        key, "v", category="models", description="New text", is_secret=True,
    )

    row = await _row(test_pool, key)
    assert row["category"] == "models"
    assert row["description"] == "New text"
    assert row["is_secret"] is True


async def test_set_explicit_false_is_secret_is_written(test_pool) -> None:
    """COALESCE only replaces NULL: an explicit False is a value, so a caller
    that really wants to demote a secret still can."""
    key = f"{_PREFIX}demote"
    await _seed(test_pool, key, is_secret=True)

    await SettingsService(test_pool).set(key, "v", is_secret=False)

    assert (await _row(test_pool, key))["is_secret"] is False


async def test_set_new_row_uses_the_resolvers_category_and_is_not_secret(
    test_pool,
) -> None:
    key = f"{_PREFIX}brand_new"
    assert resolve_category(key) == "quality"  # premise: not the 'general' fallback

    await SettingsService(test_pool).set(key, "fresh")

    row = await _row(test_pool, key)
    assert row["value"] == "fresh"
    assert row["category"] == "quality"
    assert row["is_secret"] is False


async def test_set_new_row_explicit_category_wins(test_pool) -> None:
    key = f"{_PREFIX}brand_new_explicit"

    await SettingsService(test_pool).set(key, "fresh", category="pipeline")

    assert (await _row(test_pool, key))["category"] == "pipeline"


# ---------------------------------------------------------------------------
# AdminDatabase.set_setting
# ---------------------------------------------------------------------------


async def test_set_setting_without_description_keeps_the_rows_description(
    test_pool,
) -> None:
    key = f"{_PREFIX}admin_keep"
    await _seed(test_pool, key, category="pipeline", description="A real description")

    # Category passed explicitly: the REST PUT route does this (it hands back
    # the row's own category), which is how a caller keeps one.
    ok = await AdminDatabase(test_pool).set_setting(key, "new-value", category="pipeline")

    assert ok is True
    row = await _row(test_pool, key)
    assert row["value"] == "new-value"
    assert row["description"] == "A real description"
    assert row["category"] == "pipeline"


async def test_set_setting_explicit_description_and_empty_string_are_written(
    test_pool,
) -> None:
    key = f"{_PREFIX}admin_write"
    await _seed(test_pool, key, description="A real description")
    db = AdminDatabase(test_pool)

    assert await db.set_setting(key, "v", description="New text") is True
    assert (await _row(test_pool, key))["description"] == "New text"

    assert await db.set_setting(key, "v", description="") is True
    assert (await _row(test_pool, key))["description"] == ""  # a deliberate clear


async def test_set_setting_new_row_starts_with_an_empty_description(test_pool) -> None:
    key = f"{_PREFIX}admin_new"

    assert await AdminDatabase(test_pool).set_setting(key, "fresh") is True

    row = await _row(test_pool, key)
    assert row["value"] == "fresh"
    assert row["description"] == ""
    assert row["category"] == "quality"  # resolver, not 'general'


async def test_set_setting_omitted_category_still_defers_to_the_resolver(
    test_pool,
) -> None:
    """Deliberate, and pinned so the description fix can't quietly change it:
    ``category=None`` resolves from the key (the "third category writer" fix),
    so it does NOT keep a row's own category. Callers that want to keep one pass
    it (see the PUT route)."""
    key = f"{_PREFIX}admin_category"
    await _seed(test_pool, key, category="pipeline")

    assert await AdminDatabase(test_pool).set_setting(key, "v") is True

    assert (await _row(test_pool, key))["category"] == "quality"

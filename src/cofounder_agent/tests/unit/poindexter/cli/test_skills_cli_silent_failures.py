"""Unit tests — `poindexter skills` DB-degradation visibility.

Silent-excepts OLD-debt burn-down (batch 25). Both ``skills list`` and
``skills remove`` build their own pool and swallow the failure:

    pool = None
    try:
        pool = await asyncpg.create_pool(_dsn(), ...)
    except Exception:
        pass

``skill_importer`` then treats ``pool=None`` as "disk-only mode", which is a
legitimate degraded mode — but the operator was never told they were in it.
The CLI is the primary operator UI, so the test for a swallow here is: does
the command silently return a WRONG answer?

* ``list`` — ``list_skills(pool=None)`` returns a **disk scan** instead of the
  DB catalog. A different answer than the command advertises, presented as if
  it were the catalog.
* ``remove`` — ``remove_skill(name, pool=None)`` unlinks the file but skips
  the catalog-row delete (it is guarded by ``if pool is not None``), then
  returns ``{"ok": True}``. The CLI prints "Removed:" while the DB row
  survives, now pointing at a file that no longer exists. A half-completed
  mutation reported as success.

Neither behaviour changes here — disk-only mode stays — but both must say so.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import poindexter.cli.skills as skills

pytestmark = pytest.mark.unit


@pytest.fixture
def no_db(monkeypatch):
    """Make pool construction fail the way a wedged/absent DB would."""
    def _boom():
        raise RuntimeError("no DSN configured")

    monkeypatch.setattr(skills, "_dsn", _boom)
    return CliRunner()


def test_import_warns_that_skill_is_not_cataloged(no_db, monkeypatch):
    """Same half-completed-mutation shape as remove: the file lands on disk
    but the catalog upsert is skipped, and license validation silently uses
    the built-in allow-list instead of the operator's configured one."""
    import services.skill_importer as importer

    async def _import(source, *, pack=None, pool=None, site_config=None, force=False,
                      **_kw):
        assert pool is None and site_config is None, "precondition: degraded mode"
        return {
            "ok": True, "updated": False, "name": "x", "pack": pack or "imported",
            "license": "MIT", "prompt_count": 1, "path": "/skills/x/SKILL.md",
        }

    monkeypatch.setattr(importer, "import_skill", _import)

    res = no_db.invoke(skills.skills_group, ["import", "/tmp/SKILL.md"])

    assert res.exit_code == 0, res.output
    combined = res.stderr.lower()
    assert "catalog" in combined or "database" in combined, (
        "the skill installs to disk but is NOT cataloged, and the license "
        f"allow-list falls back to defaults — must warn; got: {res.stderr!r}"
    )


def test_list_warns_when_falling_back_to_disk_scan(no_db, monkeypatch):
    import services.skill_importer as importer

    async def _list(*, pool=None):
        assert pool is None, "precondition: the swallowed pool leaves None"
        return []

    monkeypatch.setattr(importer, "list_skills", _list)

    res = no_db.invoke(skills.skills_group, ["list"])

    assert res.exit_code == 0, res.output
    # The warning belongs on stderr so `skills list --json` stdout stays
    # machine-parseable (click 8.3 keeps the streams separate).
    combined = res.stderr.lower()
    assert "disk" in combined or "catalog" in combined or "database" in combined, (
        "listing from a disk scan instead of the DB catalog must be stated — "
        f"got stderr: {res.stderr!r}"
    )


def test_remove_warns_that_catalog_row_was_not_deleted(no_db, monkeypatch):
    import services.skill_importer as importer

    async def _remove(name, *, pool=None):
        assert pool is None, "precondition: the swallowed pool leaves None"
        return {"ok": True, "name": name, "path": "/skills/x/SKILL.md"}

    monkeypatch.setattr(importer, "remove_skill", _remove)

    res = no_db.invoke(skills.skills_group, ["remove", "x"], input="y\n")

    assert res.exit_code == 0, res.output
    combined = res.stderr.lower()
    assert "catalog" in combined or "database" in combined or "partial" in combined, (
        "the file was deleted but the catalog row was NOT — a half-completed "
        f"removal reported as success must warn; got stderr: {res.stderr!r}"
    )

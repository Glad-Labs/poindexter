"""migrations-smoke runs both real install orders (poindexter#1097).

The smoke only ever ran the ``poindexter setup`` order (migrations on an empty
database), so it stayed green for months while every ``docker compose up`` on a
fresh volume crashed. On that install the brain seeds ``app_settings`` before
the worker migrates anything. ``--brain-first`` runs the brain's boot seed
first, and ``--compare-schema-to`` requires the result to match a
migrations-first database. These tests pin the script's pieces and, above all,
that the required workflow actually runs that order. A mode no job invokes has
not passed anything.
"""

from __future__ import annotations

import importlib
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "ci" / "migrations_smoke.py").is_file():
            return parent
    raise RuntimeError("could not locate scripts/ci/migrations_smoke.py")


@pytest.fixture
def smoke(monkeypatch):
    monkeypatch.delenv("POINDEXTER_BACKEND_ROOT", raising=False)
    monkeypatch.syspath_prepend(str(_repo_root() / "scripts" / "ci"))
    sys.modules.pop("migrations_smoke", None)
    return importlib.import_module("migrations_smoke")


def test_diff_names_what_is_missing_on_each_side(smoke):
    diff = smoke.diff_snapshots(
        {"column t.a #1 text", "index I2"},
        {"column t.a #1 text", "column t.b #2 boolean NOT NULL"},
        actual_label="brain-first",
        reference_label="migrations-first",
    )
    assert diff == [
        "only in brain-first: index I2",
        "only in migrations-first: column t.b #2 boolean NOT NULL",
    ]
    assert smoke.diff_snapshots({"x"}, {"x"}, actual_label="a", reference_label="b") == []


def test_snapshot_covers_every_kind_the_comparison_promises(smoke):
    assert set(smoke._SNAPSHOT_QUERIES) == {
        "column", "constraint", "index", "trigger", "sequence", "view", "function", "type",
    }


def _pool(conn):
    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    return _Pool()


class _Conn:
    def __init__(self, tables: int):
        self.tables = tables

    async def fetchval(self, query, *args):
        return self.tables


async def test_brain_first_refuses_a_database_that_is_not_empty(smoke):
    """It models a first boot; on a populated database the brain's CREATE is a
    no-op and the run would prove nothing about the compose-first order."""
    failures = await smoke._seed_like_the_brain(_pool(_Conn(tables=3)))
    assert failures and "empty database" in failures[0]


async def test_brain_first_fails_when_the_seed_did_not_insert_every_row(smoke, monkeypatch):
    from poindexter.brain import seed_loader

    async def _partial(conn):
        return {"inserted": 79, "refilled": 0, "skipped_existing": 1, "total_seed": 80}

    monkeypatch.setattr(seed_loader, "seed_app_settings", _partial)
    failures = await smoke._seed_like_the_brain(_pool(_Conn(tables=0)))
    assert failures == ["FAIL: the brain seed inserted 79 of 80 rows"]


def test_cli_passes_both_flags_through(smoke, monkeypatch):
    seen = {}

    async def _fake_run(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(smoke, "_run", _fake_run)
    monkeypatch.setattr(
        sys, "argv",
        ["migrations_smoke.py", "--brain-first", "--compare-schema-to", "postgres://ref"],
    )
    assert smoke.main() == 0
    assert seen == {
        "allow_historical": False,
        "brain_first": True,
        "compare_schema_to": "postgres://ref",
    }


def test_the_required_workflow_runs_the_compose_first_order():
    """migrations-smoke runs the brain-first order on a database of its own,
    after the default step, and compares it to the database that step
    migrated."""
    workflow = yaml.safe_load(
        (_repo_root() / ".github" / "workflows" / "migrations-smoke.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["migrations-smoke"]
    reference_db = job["env"]["DATABASE_URL"].rsplit("/", 1)[1]
    runs = [step.get("run", "") for step in job["steps"]]
    default = next(i for i, r in enumerate(runs) if r.strip() == "python scripts/ci/migrations_smoke.py")
    brain_first = next(i for i, r in enumerate(runs) if "--brain-first" in r)
    assert default < brain_first
    step = " ".join(runs[brain_first].replace("\\\n", " ").split())
    target = re.search(r"DATABASE_URL=\S+/(\w+) python scripts/ci/migrations_smoke\.py --brain-first", step)
    compare = re.search(r"--compare-schema-to \S+/(\w+)", step)
    assert target and compare, step
    assert compare.group(1) == reference_db
    assert target.group(1) != reference_db
    assert f"createdb -h localhost -U postgres {target.group(1)}" in step

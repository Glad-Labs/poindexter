"""Contract test for the regen-counters / pending-flags migration (preview_gate).

The component-scoped regen gate (``preview_gate``) needs four durable columns on
``pipeline_tasks``:

- ``regen_images_attempts`` / ``regen_text_attempts`` — monotonic counters for the
  per-component regen cap + Grafana observability (bumped by the operator surface).
- ``regen_images_pending`` / ``regen_text_pending`` — one-shot consume flags. The
  surface sets ``pending=true`` on a regen request; the ``approval_gate`` atom
  reads it, clears it, and routes ``_goto`` to the image/writer block — so the
  loop-back finds ``pending=false`` and re-pauses for a fresh review instead of
  re-looping. See ``docs/architecture/2026-06-21-component-scoped-regen-gate.md``.

String-level contract so it runs without a DB. The live schema change is verified
by ``scripts/ci/migrations_smoke.py``.
"""
from __future__ import annotations

import glob
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "services" / "migrations"


def _migration_src() -> str:
    matches = sorted(glob.glob(str(_MIGRATIONS_DIR / "*add_regen_counters*.py")))
    assert matches, "no *add_regen_counters*.py migration found"
    return Path(matches[-1]).read_text(encoding="utf-8")


def test_regen_counter_migration_adds_all_four_columns():
    src = _migration_src()
    for col in (
        "regen_images_attempts",
        "regen_images_pending",
        "regen_text_attempts",
        "regen_text_pending",
    ):
        assert col in src, f"migration missing column {col}"


def test_regen_counter_migration_is_idempotent_and_typed():
    src = _migration_src()
    # Additive + idempotent (no table rewrite, safe re-run on prod).
    assert "ADD COLUMN IF NOT EXISTS" in src
    # attempts are integers, pending are booleans, all NOT NULL with a default.
    assert "integer NOT NULL DEFAULT 0" in src
    assert "boolean NOT NULL DEFAULT false" in src


def test_regen_counter_migration_has_runner_interface():
    src = _migration_src()
    assert "async def up(pool)" in src
    assert "async def down(pool)" in src
    # down() must drop all four columns (reversible).
    assert "DROP COLUMN IF EXISTS regen_images_attempts" in src
    assert "DROP COLUMN IF EXISTS regen_text_pending" in src

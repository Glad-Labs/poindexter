"""Contract test for the demote-deepeval_faithfulness-to-advisory migration.

Self-heal-before-paging (#qa-self-heal §6): ``deepeval_faithfulness`` was
graduated to a hard gate by ``20260607_182804_graduate_eval_rails_to_required``
but it is a noisy judge (the originating 30-day data: faithfulness false
positives were a meaningful share of discarded 79-avg drafts). This migration
demotes it back to advisory (``required_to_pass = false``) on prod and — because
it carries the latest timestamp and runs last — on fresh DBs too, overriding the
#454 graduation. String-level, mirroring ``test_restamp_after_preview_gate.py``.
"""

from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = next(
    p / "src" / "cofounder_agent" / "services" / "migrations"
    for p in Path(__file__).resolve().parents
    if (p / "src" / "cofounder_agent" / "services" / "migrations").is_dir()
)


def _migration_text() -> str:
    matches = sorted(
        MIGRATIONS_DIR.glob("*demote_deepeval_faithfulness_to_advisory*.py")
    )
    assert matches, "expected a demote_deepeval_faithfulness_to_advisory migration file"
    return matches[-1].read_text(encoding="utf-8")


def test_up_demotes_faithfulness_to_advisory() -> None:
    text = _migration_text()
    assert "UPDATE qa_gates" in text
    assert "required_to_pass" in text
    assert "deepeval_faithfulness" in text
    # The forward direction sets it advisory (false), not required.
    assert "false" in text.lower()


def test_only_targets_faithfulness() -> None:
    """Scope guard: a faithfulness-only demotion, not a batch update.

    The #454 graduation used ``name = ANY($1::text[])`` over a list of four
    rails. This migration must target the single rail by literal name so the
    other three (brand_fabrication / g_eval / ragas_eval) are untouched."""
    text = _migration_text()
    assert "name = 'deepeval_faithfulness'" in text
    assert "ANY(" not in text  # not the multi-rail batch form


def test_runner_interface_present() -> None:
    text = _migration_text()
    assert "async def up(pool)" in text
    assert "async def down(pool)" in text

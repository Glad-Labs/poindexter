"""Guard: checkpoint_prune's terminal_statuses must cover the whole lifecycle.

``retention.checkpoint_prune`` is the only thing that ever deletes LangGraph
checkpoint rows, and it finds them by enumerating ``pipeline_tasks`` whose
status is in a configured ``terminal_statuses`` list. A status missing from
that list is therefore not "pruned late" — it is **never pruned at all**, and
nothing downstream notices, because the policy still reports success with
``deleted=0``.

That failure already happened. The original list held only
``completed``/``published``/``failed``/``cancelled`` and omitted ``rejected``
+ ``rejected_final`` — which are the two largest terminal buckets in practice
(1,187 and 281 tasks on prod 2026-07-27, versus 389 across the entire
configured list). A rejected run executed the whole graph before being
rejected, so it leaves a full-size checkpoint behind. Measured on prod:
125 threads / ~20k rows across checkpoints + checkpoint_writes +
checkpoint_blobs sat permanently unreachable.

The invariant is anchored to ``services/pipeline_db.py``, which stamps
``completed_at`` for exactly the statuses the codebase considers finished.
That makes this test fail when someone adds a terminal status there without
teaching the pruner about it — the drift that caused the original bug.

``rejected_retry`` is the deliberate counter-case: that run goes around again
and still needs its checkpoint, so pruning it would destroy live state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_SEEDS = _ROOT / "services" / "migrations" / "0000_baseline.seeds.sql"
_PIPELINE_DB = _ROOT / "services" / "pipeline_db.py"

# Statuses a run can still resume from — pruning these destroys live state.
_MUST_NOT_PRUNE = (
    "pending",
    "in_progress",
    "approved",
    "awaiting_approval",
    "awaiting_gate",
    "rejected_retry",
)


def _seed_terminal_statuses() -> list[str]:
    """The terminal_statuses list as seeded into retention_policies."""
    for line in _SEEDS.read_text(encoding="utf-8").splitlines():
        if "retention_policies" in line and "'checkpoint_prune'" in line:
            match = re.search(r'"terminal_statuses":\s*(\[[^\]]*\])', line)
            assert match, f"no terminal_statuses in seed row: {line[:200]}"
            return json.loads(match.group(1))
    raise AssertionError(
        "no retention_policies seed row for checkpoint_prune — nothing else "
        "prunes the LangGraph checkpoint tables"
    )


def _completed_at_statuses() -> set[str]:
    """Statuses services/pipeline_db.py stamps completed_at for."""
    text = _PIPELINE_DB.read_text(encoding="utf-8")
    match = re.search(r"completed_at = CASE WHEN \$2 IN \(([^)]*)\)", text)
    assert match, "could not locate the completed_at CASE in pipeline_db.py"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _handler_default() -> list[str]:
    from services.integrations.handlers.retention_checkpoint_prune import (
        _DEFAULT_TERMINAL_STATUSES,
    )

    return list(_DEFAULT_TERMINAL_STATUSES)


def test_seed_and_handler_default_agree():
    """Seed and code default must not drift — installs would prune differently."""
    assert sorted(_seed_terminal_statuses()) == sorted(_handler_default())


@pytest.mark.parametrize("source", ["seed", "handler"])
def test_covers_every_completed_at_status(source: str):
    """Anything the codebase stamps as finished must be prunable.

    This is the regression anchor: rejected/rejected_final live here.
    """
    configured = set(
        _seed_terminal_statuses() if source == "seed" else _handler_default()
    )
    missing = _completed_at_statuses() - configured
    assert not missing, (
        f"{source} terminal_statuses omits {sorted(missing)} — pipeline_db.py "
        "treats these as finished, so their checkpoints would never be pruned "
        "by anything, ever (they accumulate silently as deleted=0 successes)"
    )


@pytest.mark.parametrize("source", ["seed", "handler"])
def test_never_prunes_resumable_statuses(source: str):
    """A resumable run's checkpoint is live state, not garbage."""
    configured = set(
        _seed_terminal_statuses() if source == "seed" else _handler_default()
    )
    overreach = configured & set(_MUST_NOT_PRUNE)
    assert not overreach, (
        f"{source} terminal_statuses includes resumable status(es) "
        f"{sorted(overreach)} — pruning those deletes checkpoints for runs "
        "that still intend to resume from them"
    )


@pytest.mark.parametrize("source", ["seed", "handler"])
def test_statuses_are_real(source: str):
    """Catch typos: every entry must be a status the DB constraint allows."""
    from modules.content.atoms.set_task_status import _VALID_STATUSES

    configured = set(
        _seed_terminal_statuses() if source == "seed" else _handler_default()
    )
    bogus = configured - set(_VALID_STATUSES)
    assert not bogus, (
        f"{source} terminal_statuses contains non-existent status(es) "
        f"{sorted(bogus)} — these match no row and silently prune nothing"
    )

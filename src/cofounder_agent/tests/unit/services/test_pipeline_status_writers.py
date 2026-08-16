"""Guard: every literal status written to pipeline_tasks must actually exist.

The ``pipeline_tasks_status_check`` CHECK constraint is enforced only at
the database, so a writer targeting a status the constraint never learned
passes every mocked unit test and crashes in prod — exactly when it has
real work to do. That happened twice before this guard existed:

- ``ExpireStaleApprovalsJob`` wrote ``'expired'`` (poindexter#981; crashed
  2026-07-23 and 2026-08-06, so it never once relieved a full approval
  queue), and
- ``approval_service.reject_gate`` writes ``'dismissed'`` via
  ``status_override`` / per-gate settings — zero such rows existed in
  prod, meaning the path was a latent instance of the same crash.

The valid set is anchored through
``modules.content.atoms.set_task_status._VALID_STATUSES``, which
``test_set_task_status.py::test_valid_statuses_match_db_constraint`` pins
to the baseline schema + the 20260816_021929 constraint migration. This
module then sweeps two writer surfaces against it:

1. every ``status = '<literal>'`` / ``status IN (...)`` inside an
   ``UPDATE pipeline_tasks`` / ``UPDATE content_tasks`` statement in the
   source tree, and
2. the named status constants/sets consumers enumerate (terminal sets,
   CLI filters, styling maps) — a typo there silently matches nothing,
   which is the same bug wearing a quieter costume.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from modules.content.atoms.set_task_status import _VALID_STATUSES

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_SCAN_DIRS = ("services", "modules", "routes", "poindexter")

_UPDATE_RE = re.compile(
    r"UPDATE\s+(?:public\.)?(?:pipeline_tasks|content_tasks)\b", re.I
)
_LITERAL_RE = re.compile(r"\bstatus\s*=\s*'([a-z_]+)'")
_IN_RE = re.compile(r"\bstatus\s+IN\s*\(([^)]*)\)", re.I)

_MIGRATION = (
    "services.migrations."
    "20260816_021929_add_expired_and_dismissed_to_pipeline_tasks_status_check"
)


def _update_windows() -> list[tuple[str, str]]:
    """(source-file, SQL window) for every UPDATE on the task tables.

    The window runs from the UPDATE keyword to the end of that statement
    (``;``), the end of the enclosing Python string (``\"\"\"``), or 800
    chars — whichever comes first — so literals from unrelated statements
    can't bleed in.
    """
    windows: list[tuple[str, str]] = []
    for d in _SCAN_DIRS:
        for path in (_ROOT / d).rglob("*.py"):
            rel = path.relative_to(_ROOT).as_posix()
            if "migrations" in rel or "test" in rel:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in _UPDATE_RE.finditer(text):
                window = text[m.start() : m.start() + 800]
                for cut in ('"""', ";"):
                    idx = window.find(cut, 6)
                    if idx != -1:
                        window = window[:idx]
                windows.append((rel, window))
    return windows


def test_update_literals_are_valid_statuses():
    windows = _update_windows()
    assert windows, "scanner found no UPDATE pipeline_tasks/content_tasks at all"

    found: set[str] = set()
    bad: list[tuple[str, str]] = []
    for rel, window in windows:
        literals = set(_LITERAL_RE.findall(window))
        for group in _IN_RE.findall(window):
            literals.update(re.findall(r"'([a-z_]+)'", group))
        found |= literals
        for lit in literals - _VALID_STATUSES:
            bad.append((rel, lit))

    assert not bad, (
        f"status literal(s) outside pipeline_tasks_status_check: {sorted(set(bad))} "
        "— the DB rejects these at runtime (or, in a WHERE clause, they match "
        "no row, ever). Add the status via a constraint migration or fix the "
        "typo."
    )
    # Scanner sanity: the two known writers must be visible to it.
    assert "expired" in found, (
        "sweep no longer sees expire_stale_approvals' SET status='expired' — "
        "scanner regressed or the job moved; fix the scanner, don't delete this"
    )
    assert "awaiting_approval" in found


def test_named_status_sets_are_valid():
    from poindexter.cli._status_style import TASK_STATUS
    from poindexter.cli.tasks import _VALID_STATUSES as cli_choices
    from services.approval_service import (
        DEFAULT_REJECT_STATUS,
        DEFAULT_REJECT_STATUS_DISMISS,
    )
    from services.chat_plans import _TERMINAL_STATUSES as plan_terminal
    from services.chat_watch import TERMINAL_STATUSES as watch_terminal
    from services.social_drafts import _TERMINAL_REJECT_TASK_STATUSES

    named: dict[str, set[str]] = {
        "approval_service reject statuses": {
            DEFAULT_REJECT_STATUS,
            DEFAULT_REJECT_STATUS_DISMISS,
        },
        "chat_watch.TERMINAL_STATUSES": set(watch_terminal),
        "chat_plans._TERMINAL_STATUSES": set(plan_terminal),
        "social_drafts._TERMINAL_REJECT_TASK_STATUSES": set(
            _TERMINAL_REJECT_TASK_STATUSES
        ),
        "cli tasks --status choices": set(cli_choices) - {"all"},
        "cli _status_style.TASK_STATUS keys": set(TASK_STATUS),
    }
    for label, statuses in named.items():
        bogus = statuses - _VALID_STATUSES
        assert not bogus, (
            f"{label} contains status(es) {sorted(bogus)} the DB constraint "
            "does not allow — they can never match a real row"
        )


def test_completed_at_twins_agree():
    """services/pipeline_db.py's completed_at CASE and the content_tasks
    view trigger (owned by the constraint migration) must stamp the same
    statuses — drift here means completed_at depends on which write path
    a status change took."""
    mig = importlib.import_module(_MIGRATION)

    text = (_ROOT / "services" / "pipeline_db.py").read_text(encoding="utf-8")
    m = re.search(r"completed_at = CASE WHEN \$2 IN \(([^)]*)\)", text)
    assert m, "could not locate the completed_at CASE in pipeline_db.py"
    pipeline_db_set = set(re.findall(r"'([a-z_]+)'", m.group(1)))

    assert pipeline_db_set == set(mig.COMPLETED_AT_STATUSES)
    assert set(mig.COMPLETED_AT_STATUSES) <= set(mig.PIPELINE_TASK_STATUSES)

    rendered = mig._redirect_trigger_sql(mig.COMPLETED_AT_STATUSES)
    case = re.search(r"NEW\.status IN \(([^)]*)\)", rendered)
    assert case and set(re.findall(r"'([a-z_]+)'", case.group(1))) == set(
        mig.COMPLETED_AT_STATUSES
    )

"""Regression guard: cost_logs consumers classify local-vs-paid on the write
invariant, not the dead ``provider='ollama'`` tag.

Since the 2026-05-16 LiteLLM-router cutover, local inference logs
``provider='litellm'`` (the real Ollama model lands in the ``model`` column)
with ``cost_usd=0`` — the P1 write invariant enforced in
``dispatcher._record_dispatch_cost``. Every consumer that still keyed on
``provider='ollama'`` (or the exclusion list
``NOT IN ('electricity','ollama','ollama_native')`` that omits ``'litellm'``)
silently matched wrong/zero rows:

* ``cost_guard._sum_cost`` — the paid-spend cap left litellm-tagged local rows
  in the summed set (a phantom-regression landmine that false-tripped the cap
  on 2026-06-21).
* ``morning_brief`` — every local call was miscounted as cloud spend.
* ``admin_db`` — the ``model_performance.electricity_cost_usd`` mirror keyed on
  a tag that never matches, mis-attributing a stray cost as electricity.

This module forbids those stale predicates from returning to the three
consumers fixed alongside PR #2158, and pins the canonical "paid-API axis"
definition to the single ``cost_ledger`` seam so the cost_guard cap can't drift
from the ledger the operator dashboards read. Companion to
``tests/unit/infrastructure/test_grafana_ollama_alert_predicate.py`` (the
alert-side guard from PR #2158).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services import cost_ledger

# Resolve source via the installed module path (not __file__ walking) so this
# runs identically in-container and on-host.
_SERVICES = Path(cost_ledger.__file__).parent

# The stale provider-name locality heuristic in any spelling: the exclusion
# tuple, or a ``provider (=|<>|!=|==) 'ollama'`` predicate.
_OLLAMA_PREDICATE = re.compile(
    r"""provider["')\s]*(?:==|=|<>|!=)\s*["']ollama["']"""
)
_EXCLUSION_TUPLE = re.compile(
    r"""NOT\s+IN\s*\(\s*['"]electricity['"]"""
)


def _src(rel: str) -> str:
    return (_SERVICES / rel).read_text(encoding="utf-8")


def test_api_axis_predicate_is_the_electricity_axis() -> None:
    """The single owner of "what counts as paid-API spend" keys on cost_type,
    never on a provider name (which the router makes meaningless)."""
    p = cost_ledger.API_AXIS_PREDICATE
    assert "NOT LIKE 'electricity%'" in p
    assert "cost_type" in p
    assert "ollama" not in p
    assert "provider" not in p


def test_cost_guard_cap_rents_the_ledger_predicate() -> None:
    src = _src("cost_guard.py")
    # Rents the shared definition rather than re-deriving a filter that drifts.
    assert "API_AXIS_PREDICATE" in src
    # The stale provider-name denylist must never come back.
    assert not _EXCLUSION_TUPLE.search(src)
    assert "ollama_native" not in src


def test_morning_brief_cost_split_not_keyed_on_ollama() -> None:
    src = _src("jobs/morning_brief.py")
    # The provider='electricity' SUM is legitimate; only an 'ollama' predicate
    # is the drift, and it must be gone.
    assert not _OLLAMA_PREDICATE.search(src)


def test_admin_db_electricity_mirror_not_keyed_on_ollama() -> None:
    src = _src("admin_db.py")
    # The model_performance electricity mirror no longer special-cases the dead
    # tag. (An 'ollama' example in the docstring is fine — it isn't a predicate.)
    assert not _OLLAMA_PREDICATE.search(src)


@pytest.mark.parametrize(
    "sample",
    [
        "provider = 'ollama'",
        "provider='ollama'",
        "provider <> 'ollama'",
        'cost_log.get("provider") == "ollama"',
        "NOT IN ('electricity', 'ollama', 'ollama_native')",
    ],
)
def test_guards_actually_match_the_stale_patterns(sample: str) -> None:
    """Self-check: the guard regexes really do catch the patterns they forbid,
    so a green suite means the code is clean — not that the regex is toothless."""
    assert _OLLAMA_PREDICATE.search(sample) or _EXCLUSION_TUPLE.search(sample)

"""Tests for the retention correctness signal (poindexter#933).

The bug this exists for (Glad-Labs/glad-labs-stack#2871) was invisible because
every signal was a *liveness* signal: `last_error` NULL, `last_run_at` current,
`total_deleted` non-zero, panel green — for months, while pruning nothing.

So the tests that matter most here are the ones pinning what the probe must
NOT do: report a healthy zero for a policy it cannot measure, page on ordinary
inflow, or measure `checkpoint_prune` using the very config that was wrong.
"""

from __future__ import annotations

import pytest

# Importing the handler modules registers their backlog expressions.
import services.integrations.handlers.retention_checkpoint_prune  # noqa: F401
import services.integrations.handlers.retention_ttl_prune  # noqa: F401
from services.integrations.retention_backlog import (
    BacklogQuery,
    BacklogRegistrationError,
    BacklogResult,
    build_backlog_query,
    handlers_with_backlog,
    measure_all,
    measure_backlog,
    register_backlog,
)
from services.jobs.probe_retention_backlog import breaching_policies, build_body


class _Conn:
    def __init__(self, value=0, raises=None):
        self._value, self._raises = value, raises
        self.calls = []

    async def fetchval(self, sql, *params):
        if self._raises:
            raise self._raises
        self.calls.append((sql, params))
        return self._value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, value=0, raises=None):
        self.conn = _Conn(value, raises)

    def acquire(self):
        return self.conn


# --- the registry ------------------------------------------------------------


def test_ttl_prune_and_checkpoint_prune_declare_backlog():
    have = handlers_with_backlog()
    assert "ttl_prune" in have
    assert "checkpoint_prune" in have


def test_unregistered_handler_returns_none_not_a_zero():
    """The distinction the whole issue rests on: 'I cannot measure this' must
    never be expressible as 'this measured zero'."""
    assert build_backlog_query("summarize_to_table", {"ttl_days": 30}) is None


def test_duplicate_registration_is_a_hard_error():
    @register_backlog("__test_dup__")
    def _first(row):  # noqa: ANN001
        return None

    with pytest.raises(BacklogRegistrationError):
        @register_backlog("__test_dup__")
        def _second(row):  # noqa: ANN001
            return None


@pytest.mark.asyncio
async def test_unmeasurable_policy_is_reported_unmonitored():
    result = await measure_backlog(
        _Pool(), {"name": "p", "handler_name": "summarize_to_table", "ttl_days": 1},
    )
    assert result.status == "unmonitored"
    assert result.count is None


@pytest.mark.asyncio
async def test_failed_measurement_is_error_not_zero():
    pool = _Pool(raises=RuntimeError("relation does not exist"))
    result = await measure_backlog(
        pool,
        {"name": "p", "handler_name": "ttl_prune", "table_name": "t",
         "age_column": "created_at", "ttl_days": 7},
    )
    assert result.status == "error"
    assert result.count is None
    assert "does not exist" in result.detail


@pytest.mark.asyncio
async def test_measure_all_preserves_order():
    rows = [
        {"name": "a", "handler_name": "ttl_prune", "table_name": "t",
         "age_column": "created_at", "ttl_days": 1},
        {"name": "b", "handler_name": "summarize_to_table", "ttl_days": 1},
    ]
    out = await measure_all(_Pool(5), rows)
    assert [r.policy for r in out] == ["a", "b"]
    assert out[0].count == 5
    assert out[1].count is None


# --- ttl_prune's expression --------------------------------------------------


def test_ttl_prune_backlog_uses_the_policys_own_predicate():
    q = build_backlog_query(
        "ttl_prune",
        {"table_name": "page_views", "age_column": "viewed_at", "ttl_days": 180,
         "filter_sql": "is_bot = false"},
    )
    assert isinstance(q, BacklogQuery)
    assert "COUNT(*)" in q.sql
    assert "page_views" in q.sql
    assert "viewed_at < now() - make_interval(days => $1)" in q.sql
    assert "(is_bot = false)" in q.sql
    assert q.params == (180,)


def test_ttl_prune_backlog_rejects_a_bad_identifier():
    """The backlog query is still SQL built from row fields, so it gets the
    same identifier whitelist the handler applies."""
    with pytest.raises(ValueError, match="table_name"):
        build_backlog_query(
            "ttl_prune",
            {"table_name": "t; DROP TABLE posts", "age_column": "created_at",
             "ttl_days": 1},
        )


def test_dry_run_policy_declares_no_backlog():
    """A dry-run policy deletes nothing by design, so a backlog is meaningless
    as a fault signal — it would alarm forever."""
    q = build_backlog_query(
        "ttl_prune",
        {"table_name": "t", "age_column": "created_at", "ttl_days": 1,
         "config": {"dry_run": True}},
    )
    assert q is None


# --- checkpoint_prune's expression -------------------------------------------


def test_checkpoint_backlog_measures_the_invariant_not_the_policy_config():
    """The #2871 bug lived in this handler's own `terminal_statuses` config, so
    a backlog built from that config would have read 0 throughout the outage.

    The expression must therefore be phrased as 'NOT active', so a terminal
    status missing from the policy's list still counts as backlog.
    """
    from services.integrations.handlers.retention_checkpoint_prune import (
        _ACTIVE_STATUSES,
    )

    q = build_backlog_query(
        "checkpoint_prune",
        {"ttl_days": 30, "config": {"terminal_statuses": ["completed"]}},
    )
    assert q is not None
    # The narrowed config must NOT reach the query.
    assert "completed" not in q.sql
    assert "completed" not in str(q.params)
    # The two statuses whose omission caused the bug are treated as terminal,
    # because they are absent from the active list.
    assert "rejected" not in _ACTIVE_STATUSES
    assert "rejected_final" not in _ACTIVE_STATUSES
    # ...while a run going around again is still active.
    assert "rejected_retry" in _ACTIVE_STATUSES
    assert list(_ACTIVE_STATUSES) == list(q.params[2])


def test_checkpoint_backlog_matches_by_concatenation_not_regex():
    """Built the same way the handler builds thread_ids (prefix || task_id).
    The strip-and-match form measured 9.5s on the live table; this one 0.05s
    for the identical answer, and it keeps the two in step."""
    q = build_backlog_query("checkpoint_prune", {"ttl_days": 30})
    assert "p.prefix || pt.task_id" in q.sql
    assert "regexp_replace" not in q.sql


def test_checkpoint_backlog_carries_the_configured_prefixes():
    q = build_backlog_query(
        "checkpoint_prune",
        {"ttl_days": 30, "config": {"thread_prefixes": ["", "media-"]}},
    )
    assert q.params[1] == ["", "media-"]


# --- persistence, the actual alarm logic -------------------------------------


def _r(policy, count):
    return BacklogResult(policy, "ttl_prune", count, "measured")


def test_a_single_spike_does_not_page():
    """Measured live: `live_activity` held 1,699 overdue rows immediately after
    a run that deleted 2,953. That is inflow on a 2-day TTL, not a failure —
    and ~20 enabled policies sit at 0 most runs, so magnitude alone can never
    be the alarm."""
    out = breaching_policies(
        [_r("live_activity", 1699)], [{"live_activity": 0}, {"live_activity": 0}],
        threshold=100, consecutive=3,
    )
    assert out == []


def test_a_sustained_backlog_pages():
    out = breaching_policies(
        [_r("checkpoint_prune", 20000)],
        [{"checkpoint_prune": 19000}, {"checkpoint_prune": 18000}],
        threshold=100, consecutive=3,
    )
    assert [r.policy for r in out] == ["checkpoint_prune"]


def test_a_drained_run_between_breaches_breaks_the_streak():
    out = breaching_policies(
        [_r("p", 900)], [{"p": 0}, {"p": 900}], threshold=100, consecutive=3,
    )
    assert out == []


def test_insufficient_history_cannot_page():
    """A fresh install must not alarm before it has the readings to justify it."""
    out = breaching_policies(
        [_r("p", 5000)], [{"p": 5000}], threshold=100, consecutive=3,
    )
    assert out == []


def test_a_policy_absent_from_a_prior_sample_breaks_the_streak():
    """It was not measured then, and an unmeasured reading is not a breaching
    one — otherwise a newly-added policy inherits a streak it never had."""
    out = breaching_policies(
        [_r("new_policy", 5000)], [{"other": 9000}, {"other": 9000}],
        threshold=100, consecutive=3,
    )
    assert out == []


def test_unmonitored_and_errored_policies_never_breach():
    results = [
        BacklogResult("a", "summarize_to_table", None, "unmonitored"),
        BacklogResult("b", "ttl_prune", None, "error"),
    ]
    priors = [{"a": 9999, "b": 9999}, {"a": 9999, "b": 9999}]
    assert breaching_policies(results, priors, threshold=100, consecutive=3) == []


def test_consecutive_of_one_pages_on_the_first_reading():
    out = breaching_policies([_r("p", 500)], [], threshold=100, consecutive=1)
    assert [r.policy for r in out] == ["p"]


# --- the finding body --------------------------------------------------------


def test_body_names_unmonitored_policies_so_the_gap_is_visible():
    """A policy silently exempt from the check would recreate the blind spot
    the probe exists to close."""
    body = build_body(
        [_r("checkpoint_prune", 20000)],
        threshold=100,
        consecutive=3,
        unmonitored=[BacklogResult("sum", "summarize_to_table", None, "unmonitored")],
        errored=[],
    )
    assert "checkpoint_prune" in body
    assert "20000" in body
    assert "NOT covered" in body
    assert "`sum`" in body


def test_body_explains_why_the_existing_signals_missed_it():
    body = build_body([_r("p", 500)], threshold=100, consecutive=3,
                      unmonitored=[], errored=[])
    assert "last_error" in body
    assert "total_deleted" in body


def test_body_reports_failed_measurements_as_unknown():
    body = build_body(
        [_r("p", 500)], threshold=100, consecutive=3, unmonitored=[],
        errored=[BacklogResult("q", "ttl_prune", None, "error", "boom")],
    )
    assert "never as zero" in body
    assert "boom" in body

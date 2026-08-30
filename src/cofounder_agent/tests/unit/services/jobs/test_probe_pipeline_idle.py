"""Guards the pipeline-output watchdog (poindexter#1036).

On 2026-08-28 the content pipeline created its last task at 22:00 and produced
nothing for ~46 hours while every liveness signal stayed green — containers
up, scheduler firing, `run_niche_topic_sweep` returning ok=True with
`skipped=1` every 30 minutes because one unactioned topic batch was open and
its 7-day expiry kept the reaper away.

These tests pin the three judgements that make this probe useful rather than
noisy: it fires on real idleness, it stays quiet when idleness is correct, and
its message names the cause instead of just the symptom.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.jobs.probe_pipeline_idle import ProbePipelineIdleJob, build_idle_body


class _SC:
    def __init__(self, values: dict | None = None):
        self._v = values or {}

    def get_bool(self, key, default=None):
        return self._v.get(key, default)

    def get_int(self, key, default=None):
        return self._v.get(key, default)


class _Conn:
    def __init__(self, idle_hours, active_niches, batch=None, queue=0):
        self._idle = idle_hours
        self._active = active_niches
        self._batch = batch
        self._queue = queue
        self.queried_batch = False

    async def fetchrow(self, sql, *args):
        if "topic_batches" in sql:
            self.queried_batch = True
            return self._batch
        return {"newest_task": None, "idle_hours": self._idle,
                "active_niches": self._active, "approval_queue": self._queue}


class _Pool:
    def __init__(self, conn):
        self._c = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._c

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _batch(open_hours=46.0):
    return {
        "id": "0e2091bf-2907-42b0-84fa-4664d0e0d4db",
        "slug": "glad-labs",
        "open_hours": open_hours,
        "expires_at": "2026-09-04 22:16:10+00",
    }


async def _run(job, conn, cfg=None, emitted: list | None = None, monkeypatch=None):
    if emitted is not None:
        import services.jobs.probe_pipeline_idle as mod
        monkeypatch.setattr(mod, "emit_finding", lambda **kw: emitted.append(kw))
    return await job.run(_Pool(conn), {"_site_config": _SC(cfg or {})})


@pytest.mark.unit
@pytest.mark.asyncio
class TestFiresOnRealIdleness:
    async def test_the_actual_08_28_stall_emits_a_finding(self, monkeypatch) -> None:
        """46 hours idle with one active niche — the incident, reproduced."""
        emitted: list[dict[str, Any]] = []
        conn = _Conn(idle_hours=46.7, active_niches=1, batch=_batch())
        res = await _run(ProbePipelineIdleJob(), conn, emitted=emitted, monkeypatch=monkeypatch)
        assert res.ok and res.changes_made == 1
        assert len(emitted) == 1
        assert emitted[0]["kind"] == "pipeline_idle"

    async def test_dedup_key_is_stable_so_one_stall_pages_once(self, monkeypatch) -> None:
        """Hourly re-checks of the SAME stall must not page hourly."""
        emitted: list[dict[str, Any]] = []
        for _ in range(3):
            conn = _Conn(idle_hours=46.7, active_niches=1, batch=_batch())
            await _run(ProbePipelineIdleJob(), conn, emitted=emitted, monkeypatch=monkeypatch)
        assert len({e["dedup_key"] for e in emitted}) == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestStaysQuietWhenIdlenessIsCorrect:
    async def test_no_active_niche_is_expected_idleness(self, monkeypatch) -> None:
        """dev_diary is active=false today. Alerting on deliberate quiet is how
        a probe teaches operators to ignore it."""
        emitted: list[dict[str, Any]] = []
        conn = _Conn(idle_hours=500.0, active_niches=0)
        res = await _run(ProbePipelineIdleJob(), conn, emitted=emitted, monkeypatch=monkeypatch)
        assert res.changes_made == 0
        assert emitted == []
        assert not conn.queried_batch, "should not even look for a cause"

    async def test_a_normal_gap_does_not_fire(self, monkeypatch) -> None:
        """Tasks arrive ~4-hourly; a 5-hour gap is routine."""
        emitted: list[dict[str, Any]] = []
        conn = _Conn(idle_hours=5.0, active_niches=1)
        res = await _run(ProbePipelineIdleJob(), conn, emitted=emitted, monkeypatch=monkeypatch)
        assert res.changes_made == 0 and emitted == []

    async def test_threshold_is_operator_tunable(self, monkeypatch) -> None:
        emitted: list[dict[str, Any]] = []
        conn = _Conn(idle_hours=20.0, active_niches=1, batch=_batch())
        await _run(ProbePipelineIdleJob(), conn, {"pipeline_idle_max_hours": 24},
                   emitted=emitted, monkeypatch=monkeypatch)
        assert emitted == [], "20h must not fire when the limit is 24h"

    async def test_disabled_switch_short_circuits(self, monkeypatch) -> None:
        emitted: list[dict[str, Any]] = []
        conn = _Conn(idle_hours=99.0, active_niches=1)
        res = await _run(ProbePipelineIdleJob(), conn, {"pipeline_idle_probe_enabled": False},
                         emitted=emitted, monkeypatch=monkeypatch)
        assert res.ok and emitted == []


@pytest.mark.unit
class TestTheMessageNamesTheCause:
    """A notification that only says "idle" sends someone digging."""

    def test_names_the_blocking_batch_and_how_to_clear_it(self) -> None:
        body = build_idle_body(46.7, _batch())
        assert "0e2091bf-2907-42b0-84fa-4664d0e0d4db" in body
        assert "glad-labs" in body
        assert "46.7" in body, "how long the batch has been open is the actionable part"
        assert "poindexter topics" in body, "must carry the command that clears it"
        assert "expires_at" in body or "2026-09-04" in body, "say why the reaper won't help"

    def test_says_so_when_the_batch_path_is_not_the_cause(self) -> None:
        """Without this the operator chases a batch that isn't there."""
        body = build_idle_body(20.0, None)
        assert "No open topic batch" in body
        assert "run_niche_topic_sweep" in body, "point at the next thing to check"


@pytest.mark.unit
class TestCauseRanking:
    """The 08-28 stall had two visible facts and only one actionable one.

    The approval queue was full (5/5), so `topic_auto_resolve` correctly
    declined to resolve the open batch — the batch being open was a
    CONSEQUENCE of the full queue, not an independent problem. Telling the
    operator to run `resolve-batch` would have sent them at a downstream
    effect that re-stalls on the next cycle.
    """

    def test_a_full_queue_outranks_the_open_batch(self) -> None:
        body = build_idle_body(47.1, _batch(), queue_size=5, queue_limit=5)
        assert "approval queue is full (5/5)" in body
        assert "poindexter tasks approve" in body, "must give the action that actually unblocks"
        assert "Consequence, not cause" in body, "the batch must be demoted, not hidden"
        # The batch-clearing commands must NOT be offered as the fix here.
        assert "resolve-batch" not in body

    def test_an_open_batch_alone_still_names_the_batch(self) -> None:
        """Queue has room, so the batch really is the thing to clear."""
        body = build_idle_body(20.0, _batch(), queue_size=1, queue_limit=5)
        assert "resolve-batch" in body
        assert "approval queue is full" not in body

    def test_neither_cause_points_at_the_sweep(self) -> None:
        body = build_idle_body(20.0, None, queue_size=1, queue_limit=5)
        assert "No open topic batch" in body
        assert "run_niche_topic_sweep" in body

    def test_a_disabled_throttle_is_never_reported_as_full(self) -> None:
        """max_approval_queue=0 is the documented off switch; 5 >= 0 must not
        read as 'full' or the probe invents a cause that cannot exist."""
        body = build_idle_body(47.1, _batch(), queue_size=5, queue_limit=0)
        assert "approval queue is full" not in body

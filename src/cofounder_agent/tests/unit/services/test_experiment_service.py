"""Unit tests for ``services.experiment_service``.

Coverage:

- ``create()`` validation (variant count, weight sum, malformed shapes).
- ``assign()`` determinism — same subject always maps to the same variant.
- ``assign()`` distribution — over a large subject pool, the realized
  variant split lands within ±5% of the declared weights.
- ``assign()`` short-circuits for unknown / draft / paused / complete
  experiments (returns ``None``).
- ``assign()`` UNIQUE-constraint behavior — re-assigning a known subject
  is a no-op.
- ``record_outcome()`` JSONB-merge semantics (``||``).
- ``summary()`` — per-variant counts + averages of numeric metrics.
- ``conclude()`` — flips status to ``complete`` + records the winner.

Tests use an in-memory stub pool / connection pair that mimics asyncpg's
async-context-manager ``acquire()`` shape and tracks the SQL it
receives. No real Postgres needed.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from services.experiment_service import ExperimentService


# ---------------------------------------------------------------------------
# In-memory asyncpg stand-in
# ---------------------------------------------------------------------------


class _FakeRow(dict):
    """asyncpg.Record stand-in — supports both ``r["col"]`` and
    ``dict(r)`` patterns the service uses."""


class _FakeDB:
    """Minimal in-memory storage for the two tables under test.

    Implements ``fetchrow`` / ``fetch`` / ``execute`` against the
    queries the service emits. The matcher is regex-based on the SQL
    text so we can keep the SQL in the production module canonical
    without hand-wiring an ORM in the tests.
    """

    def __init__(self) -> None:
        self.experiments: list[dict[str, Any]] = []
        self.assignments: list[dict[str, Any]] = []
        self._next_assignment_id = 1
        self._uuid_counter = 1

    def _new_uuid(self) -> str:
        # Deterministic — just a counter formatted as a UUID-ish string.
        # The service treats the id as opaque text so this is fine.
        n = self._uuid_counter
        self._uuid_counter += 1
        return f"00000000-0000-0000-0000-{n:012d}"

    # -- experiments ------------------------------------------------------

    def insert_experiment(
        self,
        *,
        key: str,
        description: str,
        status: str,
        variants_json: str,
        assignment_field: str,
        started_at_now: bool,
    ) -> dict[str, Any]:
        if any(e["key"] == key for e in self.experiments):
            raise RuntimeError(f"duplicate experiment key: {key}")
        row = {
            "id": self._new_uuid(),
            "key": key,
            "description": description,
            "status": status,
            "variants": variants_json,
            "assignment_field": assignment_field,
            "created_at": "2026-04-26T00:00:00Z",
            "started_at": "2026-04-26T00:00:00Z" if started_at_now else None,
            "completed_at": None,
            "winner_variant": None,
        }
        self.experiments.append(row)
        return row

    def get_experiment_by_key(self, key: str) -> dict[str, Any] | None:
        for e in self.experiments:
            if e["key"] == key:
                return e
        return None

    # -- assignments ------------------------------------------------------

    def upsert_assignment(
        self, *, experiment_id: str, subject_id: str, variant_key: str,
    ) -> dict[str, Any] | None:
        """Returns the freshly-inserted row, or None on UNIQUE conflict."""
        for a in self.assignments:
            if a["experiment_id"] == experiment_id and a["subject_id"] == subject_id:
                return None
        row = {
            "id": self._next_assignment_id,
            "experiment_id": experiment_id,
            "subject_id": subject_id,
            "variant_key": variant_key,
            "metrics": {},
        }
        self._next_assignment_id += 1
        self.assignments.append(row)
        return row

    def find_assignment(
        self, *, experiment_id: str, subject_id: str
    ) -> dict[str, Any] | None:
        for a in self.assignments:
            if a["experiment_id"] == experiment_id and a["subject_id"] == subject_id:
                return a
        return None


class _FakeConn:
    """Translates the SQL the service emits into operations on _FakeDB."""

    _INSERT_EXPERIMENT_RE = re.compile(
        r"INSERT INTO experiments.*RETURNING id::text", re.S | re.I,
    )
    _SELECT_EXP_FOR_ASSIGN_RE = re.compile(
        r"SELECT id::text AS id, key, status, variants\s+FROM experiments\s+WHERE key = \$1",
        re.S | re.I,
    )
    _SELECT_EXP_BY_KEY_ID_ONLY_RE = re.compile(
        r"SELECT id::text AS id FROM experiments WHERE key = \$1",
        re.S | re.I,
    )
    _SELECT_EXP_FOR_CONCLUDE_RE = re.compile(
        r"SELECT id::text AS id, status, variants\s+FROM experiments\s+WHERE key = \$1",
        re.S | re.I,
    )
    _SELECT_RUNNING_RE = re.compile(
        r"SELECT id::text AS id.*FROM experiments\s+WHERE status = 'running'",
        re.S | re.I,
    )
    _INSERT_ASSIGNMENT_RE = re.compile(
        r"INSERT INTO experiment_assignments.*ON CONFLICT.*RETURNING variant_key",
        re.S | re.I,
    )
    _SELECT_EXISTING_ASSIGNMENT_RE = re.compile(
        r"SELECT variant_key\s+FROM experiment_assignments\s+WHERE experiment_id",
        re.S | re.I,
    )
    _SELECT_ASSIGNMENTS_FOR_SUMMARY_RE = re.compile(
        r"SELECT variant_key, metrics\s+FROM experiment_assignments",
        re.S | re.I,
    )
    _UPDATE_ASSIGNMENT_METRICS_RE = re.compile(
        r"UPDATE experiment_assignments AS a\s+SET metrics = a\.metrics \|\| \$3::jsonb",
        re.S | re.I,
    )
    _UPDATE_EXPERIMENT_COMPLETE_RE = re.compile(
        r"UPDATE experiments\s+SET status = 'complete'", re.S | re.I,
    )

    def __init__(self, db: _FakeDB) -> None:
        self._db = db
        self.queries: list[str] = []

    # -- public asyncpg surface ------------------------------------------

    async def fetchrow(self, query: str, *args: Any) -> _FakeRow | None:
        self.queries.append(query)

        if self._INSERT_EXPERIMENT_RE.search(query):
            key, description, status, variants_json, assignment_field = args
            # Match the production SQL: ``started_at_sql`` is inlined as
            # ``NOW()`` or ``NULL`` depending on status. The fake walks
            # the canonical text so we honor both branches.
            started_at_now = "NOW()" in query and "NULL" not in query.split(
                "started_at"
            )[-1].splitlines()[0]
            row = self._db.insert_experiment(
                key=key,
                description=description,
                status=status,
                variants_json=variants_json,
                assignment_field=assignment_field,
                started_at_now=(status == "running"),
            )
            return _FakeRow(id=row["id"])

        if self._SELECT_EXP_FOR_ASSIGN_RE.search(query):
            (key,) = args
            exp = self._db.get_experiment_by_key(key)
            if exp is None:
                return None
            return _FakeRow(
                id=exp["id"], key=exp["key"], status=exp["status"],
                variants=exp["variants"],
            )

        if self._SELECT_EXP_FOR_CONCLUDE_RE.search(query):
            (key,) = args
            exp = self._db.get_experiment_by_key(key)
            if exp is None:
                return None
            return _FakeRow(
                id=exp["id"], status=exp["status"], variants=exp["variants"],
            )

        if self._SELECT_EXP_BY_KEY_ID_ONLY_RE.search(query):
            (key,) = args
            exp = self._db.get_experiment_by_key(key)
            if exp is None:
                return None
            return _FakeRow(id=exp["id"])

        if self._INSERT_ASSIGNMENT_RE.search(query):
            experiment_id, subject_id, variant_key = args
            row = self._db.upsert_assignment(
                experiment_id=experiment_id,
                subject_id=subject_id,
                variant_key=variant_key,
            )
            if row is None:
                return None
            return _FakeRow(variant_key=row["variant_key"])

        if self._SELECT_EXISTING_ASSIGNMENT_RE.search(query):
            experiment_id, subject_id = args
            existing = self._db.find_assignment(
                experiment_id=experiment_id, subject_id=subject_id,
            )
            if existing is None:
                return None
            return _FakeRow(variant_key=existing["variant_key"])

        raise AssertionError(f"unexpected fetchrow SQL: {query!r}")

    async def fetch(self, query: str, *args: Any) -> list[_FakeRow]:
        self.queries.append(query)

        if self._SELECT_RUNNING_RE.search(query):
            running = [e for e in self._db.experiments if e["status"] == "running"]
            return [
                _FakeRow(
                    id=e["id"], key=e["key"], description=e["description"],
                    status=e["status"], variants=e["variants"],
                    assignment_field=e["assignment_field"],
                    created_at=e["created_at"], started_at=e["started_at"],
                )
                for e in running
            ]

        if self._SELECT_ASSIGNMENTS_FOR_SUMMARY_RE.search(query):
            (experiment_id,) = args
            rows = [
                a for a in self._db.assignments
                if a["experiment_id"] == experiment_id
            ]
            return [
                _FakeRow(variant_key=r["variant_key"], metrics=r["metrics"])
                for r in rows
            ]

        raise AssertionError(f"unexpected fetch SQL: {query!r}")

    async def execute(self, query: str, *args: Any) -> str:
        self.queries.append(query)

        if self._UPDATE_ASSIGNMENT_METRICS_RE.search(query):
            experiment_key, subject_id, metrics_json = args
            exp = self._db.get_experiment_by_key(experiment_key)
            if exp is None:
                return "UPDATE 0"
            existing = self._db.find_assignment(
                experiment_id=exp["id"], subject_id=subject_id,
            )
            if existing is None:
                return "UPDATE 0"
            patch = json.loads(metrics_json)
            existing["metrics"] = {**existing["metrics"], **patch}
            return "UPDATE 1"

        if self._UPDATE_EXPERIMENT_COMPLETE_RE.search(query):
            experiment_key, winner_variant = args
            exp = self._db.get_experiment_by_key(experiment_key)
            if exp is not None:
                exp["status"] = "complete"
                exp["completed_at"] = "2026-04-26T00:00:00Z"
                exp["winner_variant"] = winner_variant
                return "UPDATE 1"
            return "UPDATE 0"

        raise AssertionError(f"unexpected execute SQL: {query!r}")


class _FakePool:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db
        self._conn = _FakeConn(db)

    def acquire(self):
        pool_self = self

        class _Ctx:
            async def __aenter__(self_inner):
                return pool_self._conn

            async def __aexit__(self_inner, *_exc_info):
                return False

        return _Ctx()


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_db() -> _FakeDB:
    return _FakeDB()


@pytest.fixture
def service(fake_db: _FakeDB) -> ExperimentService:
    return ExperimentService(site_config=None, pool=_FakePool(fake_db))


def _two_arms(weight_a: int = 50, weight_b: int = 50) -> list[dict[str, Any]]:
    return [
        {"key": "control", "weight": weight_a, "config": {"writer": "glm-4.7"}},
        {"key": "treatment", "weight": weight_b, "config": {"writer": "gemma3:27b"}},
    ]


# ---------------------------------------------------------------------------
# create() validation
# ---------------------------------------------------------------------------


class TestCreateValidation:
    async def test_rejects_under_two_variants(self, service: ExperimentService) -> None:
        with pytest.raises(ValueError, match="at least 2 variants"):
            await service.create(
                key="solo", description="d",
                variants=[{"key": "only", "weight": 100, "config": {}}],
            )

    async def test_rejects_non_list_variants(self, service: ExperimentService) -> None:
        with pytest.raises(ValueError, match="must be a list"):
            await service.create(
                key="bad", description="d", variants={"not": "a list"},  # type: ignore[arg-type]
            )

    async def test_rejects_bad_weight_sum(self, service: ExperimentService) -> None:
        # 60 + 30 = 90 — outside the [98, 102] slack window.
        bad = [
            {"key": "a", "weight": 60, "config": {}},
            {"key": "b", "weight": 30, "config": {}},
        ]
        with pytest.raises(ValueError, match="weights must sum"):
            await service.create(key="bad", description="d", variants=bad)

    async def test_rejects_missing_key(self, service: ExperimentService) -> None:
        bad = [
            {"weight": 50, "config": {}},
            {"key": "b", "weight": 50, "config": {}},
        ]
        with pytest.raises(ValueError, match="missing required 'key'"):
            await service.create(key="bad", description="d", variants=bad)

    async def test_rejects_missing_weight(self, service: ExperimentService) -> None:
        bad = [
            {"key": "a", "config": {}},
            {"key": "b", "weight": 50, "config": {}},
        ]
        with pytest.raises(ValueError, match="missing required 'weight'"):
            await service.create(key="bad", description="d", variants=bad)

    async def test_rejects_missing_config(self, service: ExperimentService) -> None:
        bad = [
            {"key": "a", "weight": 50},
            {"key": "b", "weight": 50, "config": {}},
        ]
        with pytest.raises(ValueError, match="missing required 'config'"):
            await service.create(key="bad", description="d", variants=bad)

    async def test_rejects_duplicate_keys(self, service: ExperimentService) -> None:
        bad = [
            {"key": "same", "weight": 50, "config": {}},
            {"key": "same", "weight": 50, "config": {}},
        ]
        with pytest.raises(ValueError, match="duplicate key"):
            await service.create(key="bad", description="d", variants=bad)

    async def test_accepts_slack_in_weight_sum(
        self, service: ExperimentService,
    ) -> None:
        """98 and 102 are accepted (integer rounding for 33/33/34 etc.)."""
        await service.create(
            key="slack-low", description="d",
            variants=[
                {"key": "a", "weight": 49, "config": {}},
                {"key": "b", "weight": 49, "config": {}},
            ],
        )
        await service.create(
            key="slack-high", description="d",
            variants=[
                {"key": "a", "weight": 51, "config": {}},
                {"key": "b", "weight": 51, "config": {}},
            ],
        )

    async def test_create_returns_uuid_str(
        self, service: ExperimentService,
    ) -> None:
        new_id = await service.create(
            key="exp1", description="A/B writer model",
            variants=_two_arms(),
        )
        assert isinstance(new_id, str)
        assert len(new_id) > 0

    async def test_rejects_invalid_status(
        self, service: ExperimentService,
    ) -> None:
        with pytest.raises(ValueError, match="invalid status"):
            await service.create(
                key="bad-status", description="d",
                variants=_two_arms(), status="bogus",
            )


# ---------------------------------------------------------------------------
# assign() — sticky / deterministic / running-only
# ---------------------------------------------------------------------------


class TestAssign:
    async def _running_exp(
        self, service: ExperimentService, *, key: str = "exp",
        variants: list[dict[str, Any]] | None = None,
    ) -> None:
        await service.create(
            key=key, description="t", variants=variants or _two_arms(),
            status="running",
        )

    async def test_assign_is_deterministic(
        self, service: ExperimentService,
    ) -> None:
        """Same subject_id must always land on the same variant."""
        await self._running_exp(service)
        first = await service.assign(experiment_key="exp", subject_id="task-42")
        # Second call would hit the UNIQUE conflict path — must still
        # return the original variant.
        second = await service.assign(experiment_key="exp", subject_id="task-42")
        third = await service.assign(experiment_key="exp", subject_id="task-42")
        assert first is not None
        assert first == second == third

    async def test_assign_returns_none_for_unknown_experiment(
        self, service: ExperimentService,
    ) -> None:
        assert await service.assign(
            experiment_key="never-existed", subject_id="x",
        ) is None

    async def test_assign_returns_none_for_draft_experiment(
        self, service: ExperimentService,
    ) -> None:
        await service.create(
            key="drafty", description="d", variants=_two_arms(),
            status="draft",
        )
        assert await service.assign(
            experiment_key="drafty", subject_id="x",
        ) is None

    async def test_assign_returns_none_for_paused_experiment(
        self, service: ExperimentService, fake_db: _FakeDB,
    ) -> None:
        await service.create(
            key="pause", description="d", variants=_two_arms(),
            status="running",
        )
        # Flip to paused via the fake DB (the service has no
        # pause method by design — operator does it via UPDATE).
        fake_db.experiments[-1]["status"] = "paused"
        assert await service.assign(
            experiment_key="pause", subject_id="x",
        ) is None

    async def test_assign_returns_none_for_completed_experiment(
        self, service: ExperimentService,
    ) -> None:
        await service.create(
            key="done", description="d", variants=_two_arms(),
            status="running",
        )
        await service.conclude(experiment_key="done", winner_variant="control")
        assert await service.assign(
            experiment_key="done", subject_id="anyone",
        ) is None

    async def test_assignment_persists(
        self, service: ExperimentService, fake_db: _FakeDB,
    ) -> None:
        """A successful assign() writes a row to experiment_assignments."""
        await self._running_exp(service)
        variant = await service.assign(experiment_key="exp", subject_id="t1")
        assert len(fake_db.assignments) == 1
        row = fake_db.assignments[0]
        assert row["subject_id"] == "t1"
        assert row["variant_key"] == variant

    async def test_unique_constraint_enforced(
        self, service: ExperimentService, fake_db: _FakeDB,
    ) -> None:
        """Re-assigning the same subject doesn't create a second row."""
        await self._running_exp(service)
        await service.assign(experiment_key="exp", subject_id="t-unique")
        await service.assign(experiment_key="exp", subject_id="t-unique")
        await service.assign(experiment_key="exp", subject_id="t-unique")
        rows = [a for a in fake_db.assignments if a["subject_id"] == "t-unique"]
        assert len(rows) == 1

    async def test_assignment_distribution_within_5pct(
        self, service: ExperimentService, fake_db: _FakeDB,
    ) -> None:
        """Over 1000 hashed subjects, the realized split is within ±5%
        of the declared 50/50 weights.

        Tighter bounds aren't worth the flakiness budget — SHA-1 is
        uniform but 1000 samples have a ~3% standard deviation per
        bucket, so we land at 5% to leave room.
        """
        await self._running_exp(service)
        n = 1000
        for i in range(n):
            await service.assign(
                experiment_key="exp", subject_id=f"subject-{i}",
            )
        control = sum(
            1 for a in fake_db.assignments if a["variant_key"] == "control"
        )
        treatment = n - control
        assert abs(control / n - 0.5) <= 0.05, (
            f"control got {control}/{n} = {control / n:.3f} — "
            "expected ~0.5 ±0.05"
        )
        assert abs(treatment / n - 0.5) <= 0.05

    async def test_assignment_distribution_70_30(
        self, service: ExperimentService, fake_db: _FakeDB,
    ) -> None:
        """Weighted split (70/30) realized within ±5%."""
        await self._running_exp(
            service,
            key="weighted",
            variants=[
                {"key": "control", "weight": 70, "config": {}},
                {"key": "treatment", "weight": 30, "config": {}},
            ],
        )
        n = 1000
        for i in range(n):
            await service.assign(
                experiment_key="weighted",
                subject_id=f"weighted-subject-{i}",
            )
        control = sum(
            1 for a in fake_db.assignments if a["variant_key"] == "control"
        )
        treatment = n - control
        assert abs(control / n - 0.70) <= 0.05
        assert abs(treatment / n - 0.30) <= 0.05


# ---------------------------------------------------------------------------
# record_outcome()
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    async def test_record_merges_into_metrics_jsonb(
        self, service: ExperimentService, fake_db: _FakeDB,
    ) -> None:
        await service.create(
            key="outcome", description="d", variants=_two_arms(),
            status="running",
        )
        await service.assign(experiment_key="outcome", subject_id="task-1")
        await service.record_outcome(
            experiment_key="outcome", subject_id="task-1",
            metrics={"score": 78, "duration_ms": 1234},
        )
        # Second call merges — adds a new key, overwrites duration_ms.
        await service.record_outcome(
            experiment_key="outcome", subject_id="task-1",
            metrics={"duration_ms": 2222, "cost_usd": 0.012},
        )
        row = fake_db.assignments[0]
        assert row["metrics"] == {
            "score": 78,
            "duration_ms": 2222,
            "cost_usd": 0.012,
        }

    async def test_record_no_op_when_assignment_missing(
        self, service: ExperimentService,
    ) -> None:
        """record_outcome on a subject that was never assigned must
        warn-and-continue, not raise — production callers are pipeline
        stages that may have skipped assignment for a runtime reason."""
        await service.create(
            key="ghost", description="d", variants=_two_arms(),
            status="running",
        )
        # No assign() — directly try to record.
        await service.record_outcome(
            experiment_key="ghost", subject_id="never-assigned",
            metrics={"score": 50},
        )

    async def test_record_rejects_non_dict_metrics(
        self, service: ExperimentService,
    ) -> None:
        await service.create(
            key="bad-rec", description="d", variants=_two_arms(),
            status="running",
        )
        await service.assign(experiment_key="bad-rec", subject_id="t")
        with pytest.raises(ValueError, match="metrics must be a dict"):
            await service.record_outcome(
                experiment_key="bad-rec", subject_id="t",
                metrics=["not", "a", "dict"],  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------


class TestSummary:
    async def test_summary_counts_and_averages_numeric_metrics(
        self, service: ExperimentService,
    ) -> None:
        await service.create(
            key="sum", description="d", variants=_two_arms(),
            status="running",
        )
        # Spread 6 subjects across the two variants. Use IDs we know
        # the hash routes deterministically — the test just needs
        # non-zero rows in each bucket; we don't care which arm.
        for i in range(20):
            sid = f"sum-subject-{i}"
            variant = await service.assign(
                experiment_key="sum", subject_id=sid,
            )
            assert variant is not None
            # Give control rows score=80, treatment rows score=70 so
            # the per-variant average is meaningful.
            score = 80 if variant == "control" else 70
            await service.record_outcome(
                experiment_key="sum", subject_id=sid,
                metrics={
                    "score": score,
                    "duration_ms": 1000,
                    # Non-numeric value — must be skipped, not crash.
                    "model_used": "glm-4.7",
                    # Bool — must be skipped (bool is int-subclass).
                    "approved": True,
                },
            )
        summary = await service.summary("sum")

        assert set(summary.keys()).issubset({"control", "treatment"})
        for variant_key, bucket in summary.items():
            assert bucket["n"] > 0
            # score_avg matches the constant we wrote.
            expected = 80 if variant_key == "control" else 70
            assert bucket["metrics"]["score_avg"] == pytest.approx(expected)
            assert bucket["metrics"]["duration_ms_avg"] == pytest.approx(1000.0)
            # Non-numeric metrics never get an _avg key.
            assert "model_used_avg" not in bucket["metrics"]
            assert "approved_avg" not in bucket["metrics"]

    async def test_summary_empty_for_unknown_experiment(
        self, service: ExperimentService,
    ) -> None:
        result = await service.summary("never-was")
        assert result == {}

    async def test_summary_reports_zero_metric_avg_for_assignments_without_outcome(
        self, service: ExperimentService,
    ) -> None:
        """A variant with assignments but no recorded outcomes still
        appears in the summary with n>0 and an empty metrics dict."""
        await service.create(
            key="bare", description="d", variants=_two_arms(),
            status="running",
        )
        await service.assign(experiment_key="bare", subject_id="t1")
        await service.assign(experiment_key="bare", subject_id="t2")
        summary = await service.summary("bare")
        assert sum(b["n"] for b in summary.values()) == 2
        for bucket in summary.values():
            assert bucket["metrics"] == {}


# ---------------------------------------------------------------------------
# conclude()
# ---------------------------------------------------------------------------


class TestConclude:
    async def test_conclude_flips_status_and_records_winner(
        self, service: ExperimentService, fake_db: _FakeDB,
    ) -> None:
        await service.create(
            key="finale", description="d", variants=_two_arms(),
            status="running",
        )
        await service.conclude(
            experiment_key="finale", winner_variant="treatment",
        )
        exp = fake_db.get_experiment_by_key("finale")
        assert exp is not None
        assert exp["status"] == "complete"
        assert exp["winner_variant"] == "treatment"
        assert exp["completed_at"] is not None

    async def test_conclude_rejects_unknown_winner(
        self, service: ExperimentService,
    ) -> None:
        await service.create(
            key="bad-winner", description="d", variants=_two_arms(),
            status="running",
        )
        with pytest.raises(ValueError, match="not one of the declared variants"):
            await service.conclude(
                experiment_key="bad-winner", winner_variant="not-a-variant",
            )

    async def test_conclude_rejects_unknown_experiment(
        self, service: ExperimentService,
    ) -> None:
        with pytest.raises(ValueError, match="unknown experiment"):
            await service.conclude(
                experiment_key="never-was", winner_variant="control",
            )


# ---------------------------------------------------------------------------
# list_running()
# ---------------------------------------------------------------------------


class TestListRunning:
    async def test_only_running_experiments_returned(
        self, service: ExperimentService,
    ) -> None:
        await service.create(
            key="r1", description="d", variants=_two_arms(),
            status="running",
        )
        await service.create(
            key="r2", description="d", variants=_two_arms(),
            status="running",
        )
        await service.create(
            key="d1", description="d", variants=_two_arms(),
            status="draft",
        )
        running = await service.list_running()
        keys = {e["key"] for e in running}
        assert keys == {"r1", "r2"}


pytestmark = pytest.mark.asyncio

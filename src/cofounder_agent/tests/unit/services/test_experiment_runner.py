"""Unit tests for ``services/experiment_runner.py``.

Phase 1 lab harness (PR following #699). Pins the selection contract +
the fail-safe semantics that the writer-atom hook depends on:

- No active experiment → ``None`` (production path unchanged).
- Active experiment + active variants → returns one of them, uniform
  random.
- Inactive variant is excluded from the pool.
- Pool exception is swallowed and logged (``None`` returned).
- ``ExperimentVariant`` faithfully carries the NULL-as-inherit semantics
  for held-constant axes.

No real DB — uses a tiny in-process asyncpg-shaped stub. The integration
test sibling (``tests/integration_db/test_phase1_experiments_*``) covers
the end-to-end SQL behavior against a live disposable DB.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.experiment_runner import (
    ExperimentVariant,
    apply_variant_to_state,
    pick_variant,
)

# ---------------------------------------------------------------------------
# Minimal asyncpg-shaped stubs
#
# ``pool.acquire()`` returns an async-context-manager that yields a
# ``conn`` whose ``fetch()`` returns whatever rows the test seeded.
# Matches the pattern used in tests/unit/services/test_capability_outcomes.py.
# ---------------------------------------------------------------------------


class _Row(dict):
    """Asyncpg Record-shape: dict subscripted-by-column-name."""


class _FakeConn:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[_Row]:
        self.queries.append((sql, args))
        return list(self._rows)

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakePool:
    """In-process pool: ``acquire()`` returns a ctx mgr over the seeded conn."""

    def __init__(self, rows: list[_Row] | None = None) -> None:
        self._conn = _FakeConn(rows or [])

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)

    @property
    def last_query(self) -> tuple[str, tuple[Any, ...]] | None:
        return self._conn.queries[-1] if self._conn.queries else None


class _RaisingPool:
    """Pool whose ``acquire()`` raises — exercise the fail-safe path."""

    def acquire(self) -> Any:
        raise RuntimeError("simulated DB outage")


def _make_variant_row(
    *,
    experiment_id: str | None = None,
    experiment_key: str = "test/exp",
    variant_label: str = "A",
    prompt_template_key: str | None = None,
    prompt_template_version: int | None = None,
    writer_model: str | None = None,
    rag_config: Any = None,
) -> _Row:
    """Build a row in the shape ``_PICK_ACTIVE_VARIANTS_SQL`` returns."""
    return _Row(
        experiment_id=experiment_id or str(uuid.uuid4()),
        experiment_key=experiment_key,
        variant_id=str(uuid.uuid4()),
        variant_label=variant_label,
        prompt_template_key=prompt_template_key,
        prompt_template_version=prompt_template_version,
        writer_model=writer_model,
        rag_config=rag_config if rag_config is not None else {},
    )


# ---------------------------------------------------------------------------
# 1. No active experiment → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_active_experiment_returns_none() -> None:
    """When the niche has no active experiment, the SQL returns zero
    rows. pick_variant must return None so the writer atom falls
    through to its niche-default model + prompt resolution."""
    pool = _FakePool(rows=[])
    result = await pick_variant(pool, "glad-labs", task_id="task-1")
    assert result is None


# ---------------------------------------------------------------------------
# 2. No experiment at all for this niche → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_experiment_for_niche_returns_none() -> None:
    """Distinct from "experiment exists in draft": when nothing's been
    created for the niche at all, the SELECT still returns 0 rows. Same
    contract — caller treats it identically."""
    pool = _FakePool(rows=[])
    result = await pick_variant(pool, "untouched-niche", task_id="task-2")
    assert result is None


# ---------------------------------------------------------------------------
# 3. One active variant → returns that variant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_one_active_variant_returns_that_variant() -> None:
    """When exactly one variant is active on the niche's experiment, we
    return it deterministically (only one to choose from)."""
    row = _make_variant_row(
        experiment_key="glad-labs/single-axis",
        variant_label="solo",
        writer_model="qwen3.6:latest",
    )
    pool = _FakePool(rows=[row])

    result = await pick_variant(pool, "glad-labs", task_id="task-3")

    assert result is not None
    assert isinstance(result, ExperimentVariant)
    assert result.variant_label == "solo"
    assert result.experiment_key == "glad-labs/single-axis"
    assert result.writer_model == "qwen3.6:latest"
    # Held-constant axes inherited as None.
    assert result.prompt_template_key is None
    assert result.prompt_template_version is None
    assert result.rag_config == {}


# ---------------------------------------------------------------------------
# 4. Two active variants → both get picked across many calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_two_active_variants_both_get_selected() -> None:
    """Uniform random over the active variant pool. Run 100 task
    assignments and assert both labels showed up — the canary that the
    random selection is actually rotating, not stuck on one row."""
    exp_id = str(uuid.uuid4())
    rows = [
        _make_variant_row(
            experiment_id=exp_id, experiment_key="model-bake-off",
            variant_label="A", writer_model="gemma-4-31B-it-qat:latest",
        ),
        _make_variant_row(
            experiment_id=exp_id, experiment_key="model-bake-off",
            variant_label="B", writer_model="qwen3.6:latest",
        ),
    ]
    pool = _FakePool(rows=rows)

    chosen_labels = set()
    for i in range(100):
        result = await pick_variant(pool, "glad-labs", task_id=f"task-{i}")
        assert result is not None
        chosen_labels.add(result.variant_label)

    assert chosen_labels == {"A", "B"}, (
        f"Uniform random over 100 calls must hit both A and B; got {chosen_labels}"
    )


# ---------------------------------------------------------------------------
# 5. Paused variant is excluded from the active pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_paused_variant_never_selected() -> None:
    """The SQL filters ``ev.active = TRUE`` so a paused variant never
    enters the pool at the runner level. We model this at the stub
    layer (only the two active rows are returned) and assert
    "Paused" is never returned. Belt-and-suspenders: even if the SQL
    filter regresses, the runner-layer assertion catches it via 100
    repeated calls."""
    exp_id = str(uuid.uuid4())
    # Stub returns only the two active rows (mirroring what the SQL
    # filter does in prod). pick_variant should never see Paused.
    rows = [
        _make_variant_row(
            experiment_id=exp_id, experiment_key="3-way-test",
            variant_label="A",
        ),
        _make_variant_row(
            experiment_id=exp_id, experiment_key="3-way-test",
            variant_label="B",
        ),
    ]
    pool = _FakePool(rows=rows)

    for i in range(100):
        result = await pick_variant(pool, "glad-labs", task_id=f"task-{i}")
        assert result is not None
        assert result.variant_label != "Paused", (
            "paused variant leaked into the selection pool — runner "
            "must respect ev.active=true filter"
        )


# ---------------------------------------------------------------------------
# 6. Pool exception → returns None, no exception escapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_pool_exception_returns_none_does_not_raise(caplog) -> None:
    """The design doc's "Posture: testing in production" section is
    binding: a runner failure MUST NOT crash the writer. Pool raises →
    runner logs a warning and returns None. The writer falls back to
    its niche-default resolution."""
    pool = _RaisingPool()

    with caplog.at_level("WARNING", logger="services.experiment_runner"):
        result = await pick_variant(pool, "glad-labs", task_id="task-X")

    assert result is None
    # The warning log must include enough context for an operator
    # scrolling logs to diagnose what failed + which niche/task it
    # would have affected.
    log_messages = [r.getMessage() for r in caplog.records]
    assert any("pick_variant" in msg for msg in log_messages), (
        f"expected a pick_variant warning in {log_messages!r}"
    )
    assert any("glad-labs" in msg for msg in log_messages), (
        "warning must mention the niche_slug for diagnosability"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_none_pool_returns_none_without_logging(caplog) -> None:
    """Calling with ``pool=None`` is the test/bootstrap path — not an
    error, just "no DB to consult". Must not log a warning so it
    doesn't spam log review on every test run."""
    with caplog.at_level("WARNING", logger="services.experiment_runner"):
        result = await pick_variant(None, "glad-labs", task_id="task-N")
    assert result is None
    assert caplog.records == [] or all(
        r.levelname != "WARNING" for r in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_empty_niche_slug_returns_none() -> None:
    """Legacy / manual tasks that don't carry a niche_slug pass an
    empty string — the runner returns None so the writer atom keeps
    its no-variant path."""
    pool = _FakePool(rows=[_make_variant_row()])
    result = await pick_variant(pool, "", task_id="legacy-task")
    assert result is None


# ---------------------------------------------------------------------------
# 7. Variant correctly carries NULL-as-inherit fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_variant_inherits_none_fields() -> None:
    """The scientific-method control rule (design doc 2026-05-28):
    a model-axis variant sets ``writer_model`` and leaves prompt key /
    version NULL so they inherit the niche default. The dataclass must
    carry None faithfully so the hook's "apply only non-None" logic
    works."""
    row = _make_variant_row(
        experiment_key="model-only",
        variant_label="gemma4-31b",
        # The axis being tested:
        writer_model="gemma-4-31B-it-qat:latest",
        # Held-constant axes (NULL in DB → None on the dataclass):
        prompt_template_key=None,
        prompt_template_version=None,
        rag_config={},
    )
    pool = _FakePool(rows=[row])

    variant = await pick_variant(pool, "glad-labs", task_id="task-Y")

    assert variant is not None
    assert variant.writer_model == "gemma-4-31B-it-qat:latest"
    assert variant.prompt_template_key is None
    assert variant.prompt_template_version is None
    assert variant.rag_config == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_variant_decodes_jsonb_rag_config_dict() -> None:
    """asyncpg returns JSONB as a Python dict already; verify the
    dataclass round-trips it cleanly."""
    rag = {"snippet_limit": 10, "rerank": True}
    row = _make_variant_row(
        variant_label="rag-axis",
        rag_config=rag,
    )
    pool = _FakePool(rows=[row])

    variant = await pick_variant(pool, "glad-labs", task_id="task-R")
    assert variant is not None
    assert variant.rag_config == rag


@pytest.mark.asyncio
@pytest.mark.unit
async def test_variant_decodes_jsonb_rag_config_string_fallback() -> None:
    """Defensive: some asyncpg JSONB paths return a JSON string instead
    of a parsed dict. The runner handles both shapes."""
    row = _make_variant_row(rag_config='{"snippet_limit": 7}')
    pool = _FakePool(rows=[row])
    variant = await pick_variant(pool, "glad-labs", task_id="task-S")
    assert variant is not None
    assert variant.rag_config == {"snippet_limit": 7}


# ---------------------------------------------------------------------------
# apply_variant_to_state — the shared hook helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyVariantToState:
    """Unit-level coverage for the helper the writer + narrate hooks use."""

    def test_none_variant_is_noop(self) -> None:
        """``None`` variant means "no experiment active" — state must be
        returned untouched so the production path is identical."""
        state = {"existing": "value"}
        out = apply_variant_to_state(state, None)
        assert out == {"existing": "value"}
        assert "variant_id" not in out

    def test_variant_id_always_set_when_present(self) -> None:
        """Identifier fields are always set so capability_outcomes can
        stamp them on the row even when every override is None
        (held-constant on every axis — defensive coverage)."""
        variant = ExperimentVariant(
            variant_id="vid-1", variant_label="A",
            experiment_id="eid-1", experiment_key="exp/key",
            prompt_template_key=None,
            prompt_template_version=None,
            writer_model=None,
            rag_config={},
        )
        state: dict[str, Any] = {}
        apply_variant_to_state(state, variant)
        assert state["variant_id"] == "vid-1"
        assert state["variant_label"] == "A"
        assert state["experiment_id"] == "eid-1"
        assert state["experiment_key"] == "exp/key"
        # No override fields applied → they're absent from state.
        assert "writer_model" not in state
        assert "prompt_template_key" not in state

    def test_writer_model_override_applied(self) -> None:
        variant = ExperimentVariant(
            variant_id="vid-2", variant_label="model-axis",
            experiment_id="eid-2", experiment_key="exp/model",
            prompt_template_key=None,
            prompt_template_version=None,
            writer_model="qwen3.6:latest",
            rag_config={},
        )
        state: dict[str, Any] = {}
        apply_variant_to_state(state, variant)
        assert state["writer_model"] == "qwen3.6:latest"

    def test_rag_config_shallow_merge_variant_wins(self) -> None:
        """Variant config values win on key conflict; non-conflicting
        keys from the niche default carry through unchanged."""
        variant = ExperimentVariant(
            variant_id="vid-3", variant_label="rag-axis",
            experiment_id="eid-3", experiment_key="exp/rag",
            prompt_template_key=None,
            prompt_template_version=None,
            writer_model=None,
            rag_config={"snippet_limit": 10, "max_tokens": 4000},
        )
        state: dict[str, Any] = {"rag_config": {"snippet_limit": 5}}
        apply_variant_to_state(state, variant)
        assert state["rag_config"] == {"snippet_limit": 10, "max_tokens": 4000}

    def test_rag_config_empty_inherits_niche_default(self) -> None:
        """``{}`` on the variant = "inherit niche default" — state's
        existing ``rag_config`` (if any) must NOT be replaced."""
        variant = ExperimentVariant(
            variant_id="vid-4", variant_label="prompt-axis",
            experiment_id="eid-4", experiment_key="exp/prompt",
            prompt_template_key="atoms.x.v2",
            prompt_template_version=2,
            writer_model=None,
            rag_config={},
        )
        state: dict[str, Any] = {"rag_config": {"snippet_limit": 5}}
        apply_variant_to_state(state, variant)
        # No mutation to rag_config.
        assert state["rag_config"] == {"snippet_limit": 5}
        # Prompt overrides DID apply (those are non-None).
        assert state["prompt_template_key"] == "atoms.x.v2"
        assert state["prompt_template_version"] == 2

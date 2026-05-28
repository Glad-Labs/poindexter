"""Unit tests for capability_outcomes.record_run / record_one.

Phase 0 lab observability (2026-05-28) — confirms ``record_run`` and
``record_one`` correctly extract + persist the new
``niche_slug`` / ``prompt_template_key`` / ``prompt_template_version``
fields. The downstream lab view assumes these three columns are
populated whenever the atom resolved a prompt + the task carried a
niche; these tests pin that contract at the writer boundary so a
later refactor can't silently drop them.

No DB hits — uses an asyncpg-pool stub that records execute() calls
into a list for assertion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from services.capability_outcomes import record_one, record_run


# ---------------------------------------------------------------------------
# Minimal asyncpg pool stub
# ---------------------------------------------------------------------------


class _Conn:
    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]]):
        self._sink = sink

    async def execute(self, sql: str, *args: Any) -> None:
        self._sink.append((sql, args))

    async def __aenter__(self):  # noqa: D401 - context manager dunder
        return self

    async def __aexit__(self, *_a):
        return False


class _Acquire:
    """Returned by Pool.acquire() — both an async context manager AND
    awaitable so it matches the asyncpg pool surface our writer uses."""

    def __init__(self, conn: _Conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _Pool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._conn = _Conn(self.executed)

    def acquire(self):
        return _Acquire(self._conn)


# ---------------------------------------------------------------------------
# Summary / record stubs
# ---------------------------------------------------------------------------


@dataclass
class _Record:
    name: str
    ok: bool = True
    detail: str = ""
    halted: bool = False
    elapsed_ms: int = 100
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Summary:
    template_slug: str
    records: list[_Record]


# ---------------------------------------------------------------------------
# record_run
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordRunLabFields:
    async def test_stamps_niche_slug_from_state_when_record_missing(self):
        """When the per-record metrics dict lacks niche_slug, the writer
        falls back to the initial_state-level niche_slug. Common path —
        only the writer atom stamps it on per-record metrics, but
        every node on the run should inherit it from state."""
        pool = _Pool()
        summary = _Summary(
            template_slug="canonical_blog",
            records=[_Record(name="verify_task")],
        )
        state = {
            "task_id": "task-123",
            "niche_slug": "glad-labs",
        }
        written = await record_run(pool, summary, state)
        assert written == 1
        sql, args = pool.executed[0]
        # niche_slug is the 13th positional in the INSERT — easier to
        # assert by looking at the tail of the args tuple (the SQL
        # column order is enforced by the writer).
        # Layout per record_run INSERT:
        #   ... metrics(jsonb), niche_slug, prompt_template_key,
        #   prompt_template_version
        assert args[-3] == "glad-labs"
        assert args[-2] is None  # prompt_template_key absent
        assert args[-1] is None  # prompt_template_version absent

    async def test_metrics_dict_overrides_state_niche(self):
        """A per-record metrics niche_slug takes precedence over the
        state-level niche_slug — supports future per-atom override."""
        pool = _Pool()
        summary = _Summary(
            template_slug="canonical_blog",
            records=[
                _Record(
                    name="atoms.two_pass_writer",
                    metrics={"niche_slug": "ai-engineering"},
                ),
            ],
        )
        state = {"task_id": "t1", "niche_slug": "glad-labs"}
        await record_run(pool, summary, state)
        _, args = pool.executed[0]
        assert args[-3] == "ai-engineering"

    async def test_stamps_prompt_template_key_and_version_from_metrics(self):
        """The atom returns prompt provenance in its metrics dict via the
        wrapper; record_run must copy those into the
        ``prompt_template_key`` + ``_version`` columns."""
        pool = _Pool()
        summary = _Summary(
            template_slug="canonical_blog",
            records=[
                _Record(
                    name="atoms.two_pass_writer",
                    metrics={
                        "model_used": "test-model:42b",
                        "prompt_template_key": "atoms.two_pass_writer.revise_prompt",
                        "prompt_template_version": 4,
                    },
                ),
            ],
        )
        state = {"task_id": "t2", "niche_slug": "tech"}
        await record_run(pool, summary, state)
        _, args = pool.executed[0]
        assert args[-3] == "tech"
        assert args[-2] == "atoms.two_pass_writer.revise_prompt"
        assert args[-1] == 4

    async def test_version_coerced_from_string_int(self):
        """Defensive coercion — if the atom sends a string version
        (e.g. "12"), record_run should still cast it to int rather
        than crash the asyncpg type binding."""
        pool = _Pool()
        summary = _Summary(
            template_slug="canonical_blog",
            records=[
                _Record(
                    name="x",
                    metrics={"prompt_template_version": "12"},
                ),
            ],
        )
        await record_run(pool, summary, {"task_id": "t3"})
        _, args = pool.executed[0]
        assert args[-1] == 12

    async def test_version_unparseable_becomes_null(self):
        """A non-numeric version string (rare — most prompts carry an
        int) is recorded as NULL so the column type doesn't blow up."""
        pool = _Pool()
        summary = _Summary(
            template_slug="canonical_blog",
            records=[
                _Record(
                    name="x",
                    metrics={"prompt_template_version": "draft"},
                ),
            ],
        )
        await record_run(pool, summary, {"task_id": "t4"})
        _, args = pool.executed[0]
        assert args[-1] is None

    async def test_all_fields_default_to_null_when_unset(self):
        """A barebones state + record (no niche, no prompt) writes NULLs
        — exercises the legacy / dev_diary-infra path that doesn't
        carry prompt provenance."""
        pool = _Pool()
        summary = _Summary(
            template_slug="canonical_blog",
            records=[_Record(name="verify_task")],
        )
        await record_run(pool, summary, {"task_id": "t5"})
        _, args = pool.executed[0]
        assert args[-3] is None
        assert args[-2] is None
        assert args[-1] is None


# ---------------------------------------------------------------------------
# record_one
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordOneLabFields:
    async def test_accepts_new_lab_kwargs(self):
        """The single-row writer also supports the new kwargs —
        backwards-compatible defaults so existing callers don't break."""
        pool = _Pool()
        ok = await record_one(
            pool,
            task_id="single-1",
            template_slug="canonical_blog",
            node_name="atoms.test",
            niche_slug="brand-niche",
            prompt_template_key="test.prompt",
            prompt_template_version=2,
        )
        assert ok is True
        _, args = pool.executed[0]
        assert args[-3] == "brand-niche"
        assert args[-2] == "test.prompt"
        assert args[-1] == 2

    async def test_existing_call_shape_unchanged(self):
        """Backwards-compat — the pre-Phase-0 signature (no lab kwargs)
        must keep working without producing errors."""
        pool = _Pool()
        ok = await record_one(
            pool,
            task_id="legacy-1",
            template_slug="canonical_blog",
            node_name="legacy_node",
            model_used="legacy-model",
        )
        assert ok is True
        _, args = pool.executed[0]
        # New columns land as None when not passed
        assert args[-3] is None
        assert args[-2] is None
        assert args[-1] is None

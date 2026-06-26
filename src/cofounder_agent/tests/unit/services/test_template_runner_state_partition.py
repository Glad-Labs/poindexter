"""Regression tests for the state-vs-services partition in
``services/template_runner.py`` (Glad-Labs/poindexter#382).

PR #243 wired a checkpointer into ``TemplateRunner.run()``. LangGraph's
checkpointers (MemorySaver, AsyncPostgresSaver) serialize the entire
StateGraph state via ``ormsgpack``. Pre-#382, ``content_router_service``
seeded live ``DatabaseService`` / ``ImageService`` handles onto the
state dict — those aren't msgpack-serializable, so every checkpoint
write raised

    TypeError: Type is not msgpack serializable: DatabaseService

The error was non-fatal (LangGraph's encoder logged + continued) but
spammed every dev_diary run with a noisy template-level error.

This test suite locks down the partition fix:

- service handles ride in ``RunnableConfig.configurable["__services__"]``
- the StateGraph state stays msgpack-clean
- node functions still see the live handles when reading the legacy
  ``context.get("database_service")`` shape
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import ormsgpack
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from services.template_runner import (
    _CONFIG_SERVICES_KEY,
    PipelineState,
    TemplateRunner,
    _is_msgpack_serializable,
    _partition_state_and_services,
    _services_from_config,
)

# ---------------------------------------------------------------------------
# Test doubles — small unserializable handle that mimics DatabaseService
# ---------------------------------------------------------------------------


class _FakeDatabaseService:
    """Stand-in for DatabaseService — has methods + a ``pool`` attribute,
    not msgpack-encodable."""

    def __init__(self) -> None:
        self.pool = MagicMock()
        self.calls: list[str] = []

    async def get_task(self, task_id: str) -> dict[str, Any]:
        self.calls.append(f"get_task:{task_id}")
        return {"task_id": task_id}


class _FakeImageService:
    """Stand-in for ImageService — also not msgpack-encodable."""

    def __init__(self) -> None:
        self.calls: list[str] = []


# ---------------------------------------------------------------------------
# Group 1: pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPartitionHelpers:
    """Unit tests for the partition + serializability helpers."""

    def test_is_msgpack_serializable_accepts_data(self) -> None:
        assert _is_msgpack_serializable("a string")
        assert _is_msgpack_serializable(123)
        assert _is_msgpack_serializable({"a": 1, "b": [1, 2, 3]})
        assert _is_msgpack_serializable([1, "two", 3.0])
        assert _is_msgpack_serializable(None)

    def test_is_msgpack_serializable_rejects_handles(self) -> None:
        assert not _is_msgpack_serializable(_FakeDatabaseService())
        assert not _is_msgpack_serializable(_FakeImageService())
        assert not _is_msgpack_serializable(MagicMock())

    def test_partition_keeps_data_on_state(self) -> None:
        initial = {
            "task_id": "abc",
            "topic": "Hello",
            "tags": ["a", "b"],
            "models_by_phase": {"writer": "m1"},
        }
        data, services = _partition_state_and_services(initial)
        assert data == initial
        assert services == {}

    def test_partition_routes_known_service_keys(self) -> None:
        """Known service keys ALWAYS partition out, even when None.

        Deterministic partitioning: a stage that asks for
        ``context.get('database_service')`` gets either the real handle
        or None, never a surprise based on whether the value happens to
        round-trip through ormsgpack.
        """
        db = _FakeDatabaseService()
        img = _FakeImageService()
        initial = {
            "task_id": "abc",
            "topic": "Hello",
            "database_service": db,
            "image_service": img,
            "settings_service": None,  # known key, None value
        }
        data, services = _partition_state_and_services(initial)
        assert "database_service" not in data
        assert "image_service" not in data
        assert "settings_service" not in data
        assert services["database_service"] is db
        assert services["image_service"] is img
        assert services["settings_service"] is None
        # Pure data still on state.
        assert data["task_id"] == "abc"
        assert data["topic"] == "Hello"

    def test_partition_routes_platform_handle_to_services(self) -> None:
        """The ``platform`` capability handle (#667 Seam 1) is a KNOWN
        service key — partitioned to services deterministically, even when
        ``None``, so every node (writer, inline-image atom, AND the
        featured-image stage) receives it via ``__services__`` rather than
        via the checkpointed LangGraph state.

        Pre-fix ``platform`` was NOT in ``_KNOWN_SERVICE_KEYS``: a real
        (unserializable) handle reached services only by the msgpack
        heuristic, while a ``None`` handle — and any future serializable
        handle — routed to the data state. There, being undeclared in
        ``PipelineState``, LangGraph silently dropped it, so the image
        stages' LLM prompt build raised "platform handle required for
        dispatch" and fell back to a generic style-only prompt (#667
        Wave 3f, observed live 2026-06-18).
        """
        handle = MagicMock()
        data, services = _partition_state_and_services(
            {"task_id": "abc", "topic": "Hello", "platform": handle}
        )
        assert "platform" not in data
        assert services["platform"] is handle

        # Deterministic even when None — the KEY decides the routing, not
        # whether the value happens to round-trip through ormsgpack.
        data_none, services_none = _partition_state_and_services(
            {"task_id": "abc", "platform": None}
        )
        assert "platform" not in data_none
        assert "platform" in services_none
        assert services_none["platform"] is None

    def test_partition_routes_unknown_unserializable_to_services(self) -> None:
        """Unknown keys with unserializable values still get partitioned.

        Heuristic fallback for one-off custom objects (e.g. an experiment
        tracker, a DI container) that aren't on the well-known list.
        """
        custom_handle = MagicMock()
        initial = {
            "task_id": "abc",
            "weird_handle": custom_handle,
        }
        data, services = _partition_state_and_services(initial)
        assert "weird_handle" not in data
        assert services["weird_handle"] is custom_handle

    def test_partitioned_state_round_trips_through_ormsgpack(self) -> None:
        """The data half of the partition MUST be msgpack-encodable.

        This is the regression assertion for the #382 TypeError — the
        whole point of the partition is that the data the checkpointer
        sees serializes cleanly.
        """
        initial = {
            "task_id": "abc",
            "topic": "Hello",
            "tags": ["a", "b"],
            "database_service": _FakeDatabaseService(),
            "image_service": _FakeImageService(),
            "models_by_phase": {"writer": "m1"},
        }
        data, _services = _partition_state_and_services(initial)
        # Encode succeeds — no TypeError.
        packed = ormsgpack.packb(data)
        unpacked = ormsgpack.unpackb(packed)
        assert unpacked["task_id"] == "abc"
        assert unpacked["topic"] == "Hello"

    def test_services_from_config_extracts_dict(self) -> None:
        config = {
            "configurable": {
                "thread_id": "t1",
                _CONFIG_SERVICES_KEY: {"database_service": "x"},
            },
        }
        assert _services_from_config(config) == {"database_service": "x"}

    def test_services_from_config_handles_missing(self) -> None:
        assert _services_from_config(None) == {}
        assert _services_from_config({}) == {}
        assert _services_from_config({"configurable": {}}) == {}


# ---------------------------------------------------------------------------
# Group 2: end-to-end partition through TemplateRunner.run()
# ---------------------------------------------------------------------------


def _service_aware_factory_with_capture(
    capture: dict[str, Any],
) -> Any:
    """Build a 1-node StateGraph factory whose node:

    1. Reads ``database_service`` / ``image_service`` via the legacy
       ``state.get`` lookup pattern that real stages use.
    2. Records what it saw into ``capture`` so the test can assert on it.
    3. Returns a content update.

    The factory has the same signature TemplateRunner expects from
    ``services.pipeline_templates.TEMPLATES`` entries.
    """

    from services.template_runner import _services_from_config

    def _factory(*, pool: Any, record_sink: list | None = None) -> StateGraph:
        g: StateGraph = StateGraph(PipelineState)

        async def _service_aware_node(
            state: PipelineState,
            config: RunnableConfig = None,  # type: ignore[assignment]
        ) -> dict[str, Any]:
            # Mirror the make_stage_node merge pattern — services come
            # from config, data comes from state.
            context: dict[str, Any] = dict(state)
            svcs = _services_from_config(config)
            for k, v in svcs.items():
                context.setdefault(k, v)
            capture["topic_seen"] = context.get("topic")
            capture["db_seen"] = context.get("database_service")
            capture["img_seen"] = context.get("image_service")
            capture["platform_seen"] = context.get("platform")
            capture["state_keys_at_node_entry"] = sorted(state.keys())
            return {"content": f"ran with topic={context.get('topic')}"}

        g.add_node("svc_node", _service_aware_node)
        g.set_entry_point("svc_node")
        g.add_edge("svc_node", END)
        return g

    return _factory


@pytest.fixture
def flag_off_for_partition():
    """Return a SiteConfig with the postgres-checkpointer flag = false.

    The partition fix matters for BOTH MemorySaver and AsyncPostgresSaver
    — both serialize state via ormsgpack. We test on MemorySaver here
    because it's hermetic; the Postgres path is covered by the existing
    smoke test in test_template_runner_postgres_checkpointer.py.

    #272 Phase-2f deleted the template_runner module-global site_config;
    tests now construct a SiteConfig and thread it into TemplateRunner.
    """
    from services.site_config import SiteConfig
    return SiteConfig(initial_config={
        "template_runner_use_postgres_checkpointer": "false",
    })


@pytest.mark.unit
class TestRunnerPartitionsServicesFromState:
    """End-to-end: TemplateRunner.run() with a service-handle-bearing
    initial_state must NOT raise a msgpack TypeError, and the node must
    still receive the live handles."""

    @pytest.mark.asyncio
    async def test_node_config_annotation_must_match_runnableconfig(
        self,
    ):
        """Regression guard: LangGraph's KWARGS_CONFIG_KEYS injection
        only fires when the node's ``config`` parameter is annotated as
        ``RunnableConfig`` (or ``Optional[RunnableConfig]``, or no
        annotation). Annotating it as ``dict[str, Any] | None`` (the
        intuitive choice) silently gets the node called with
        ``config=None`` — no error, just dropped services.

        This test pins the contract so a future "make annotations more
        precise" refactor doesn't regress the partition.
        """
        g: StateGraph = StateGraph(PipelineState)
        seen: dict[str, Any] = {}

        async def _node(
            state: PipelineState,
            config: RunnableConfig = None,  # type: ignore[assignment]
        ) -> dict[str, Any]:
            seen["config"] = config
            return {"content": "ok"}

        g.add_node("n", _node)
        g.set_entry_point("n")
        g.add_edge("n", END)
        c = g.compile()
        await c.ainvoke(
            {"topic": "hi"},
            {"configurable": {
                "thread_id": "t",
                _CONFIG_SERVICES_KEY: {"db": "FAKE"},
            }},
        )
        assert seen["config"] is not None, (
            "LangGraph did not inject config — annotation regression. "
            "Check that the node's `config` param is typed as "
            "RunnableConfig | None, not dict[str, Any] | None."
        )
        assert (
            seen["config"].get("configurable", {}).get(_CONFIG_SERVICES_KEY)
            == {"db": "FAKE"}
        )

    @pytest.mark.asyncio
    async def test_runner_does_not_emit_msgpack_typeerror(
        self, flag_off_for_partition, caplog, monkeypatch,
    ):
        """Run a template with live service handles in the initial state.

        Before #382: MemorySaver's encoder raised
        ``TypeError: Type is not msgpack serializable: DatabaseService``
        on every node transition (LangGraph caught + logged each one).

        After #382: state-vs-services partition keeps handles out of
        the checkpointed state — zero msgpack errors in the log.
        """
        capture: dict[str, Any] = {}
        factory = _service_aware_factory_with_capture(capture)

        # Inject our factory into the templates registry.
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "TEMPLATES", {"svc_partition": factory})

        runner = TemplateRunner(
            pool=None, checkpointer_dsn=None,
            site_config=flag_off_for_partition,
        )

        db = _FakeDatabaseService()
        img = _FakeImageService()
        initial_state: dict[str, Any] = {
            "task_id": "partition-test-1",
            "topic": "the partition fix",
            "database_service": db,
            "image_service": img,
            "tags": ["a", "b"],
        }

        with caplog.at_level(logging.ERROR):
            summary = await runner.run(
                "svc_partition",
                initial_state,
                thread_id="partition-test-thread-1",
            )

        # Sanity: the run completed.
        assert summary.ok, f"run failed: {summary.to_dict()}"
        assert summary.final_state.get("content") == "ran with topic=the partition fix"

        # The node received both the data AND the live service handles.
        assert capture["topic_seen"] == "the partition fix"
        assert capture["db_seen"] is db, (
            "node didn't get the live DatabaseService handle — partition "
            "broke the configurable threading"
        )
        assert capture["img_seen"] is img, (
            "node didn't get the live ImageService handle"
        )

        # The state at node entry was msgpack-clean — service handles
        # were partitioned out before LangGraph saw the state.
        keys_on_state = capture["state_keys_at_node_entry"]
        assert "database_service" not in keys_on_state, (
            f"database_service leaked into LangGraph state: "
            f"{keys_on_state}"
        )
        assert "image_service" not in keys_on_state, (
            f"image_service leaked into LangGraph state: "
            f"{keys_on_state}"
        )
        assert "task_id" in keys_on_state
        assert "topic" in keys_on_state

        # The regression assertion: no msgpack TypeError errors
        # surfaced in the log during the run. Pre-fix this test would
        # see one error per checkpoint write.
        msgpack_errors = [
            rec for rec in caplog.records
            if "msgpack" in rec.message.lower()
            or "DatabaseService" in rec.message
            or "Type is not msgpack serializable" in rec.message
        ]
        assert not msgpack_errors, (
            f"saw msgpack-serializability errors after partition fix: "
            f"{[r.message for r in msgpack_errors]}"
        )

    @pytest.mark.asyncio
    async def test_runner_threads_platform_handle_to_node(
        self, flag_off_for_partition, monkeypatch,
    ):
        """Contract: an image-stage-shaped node receives a non-None
        ``platform`` handle on the graph_def path.

        The featured-image stage + the inline-image atom both reach the
        capability kernel through ``context.get("platform")`` to build
        LLM-crafted image-gen prompts (``platform.dispatch.complete``). This
        pins the runner guarantee that a platform handle seeded into the
        initial state survives the state/services partition and lands in
        the node's merged context — i.e. the image stages DO receive it,
        and it never leaks onto the checkpointed LangGraph state.
        """
        capture: dict[str, Any] = {}
        factory = _service_aware_factory_with_capture(capture)
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "TEMPLATES", {"svc_platform": factory})

        runner = TemplateRunner(
            pool=None, checkpointer_dsn=None,
            site_config=flag_off_for_partition,
        )
        platform = MagicMock(name="capability_handle")
        summary = await runner.run(
            "svc_platform",
            {
                "task_id": "platform-thread-1",
                "topic": "platform threading",
                "platform": platform,
            },
            thread_id="platform-thread-thread-1",
        )
        assert summary.ok, f"run failed: {summary.to_dict()}"
        assert capture["platform_seen"] is platform, (
            "the node did not receive the seeded platform handle via "
            "__services__ — image stages would fall back to generic prompts"
        )
        # It's a service handle, not data — must stay off the checkpointed
        # LangGraph state (undeclared in PipelineState → would be dropped).
        assert "platform" not in capture["state_keys_at_node_entry"]

    @pytest.mark.asyncio
    async def test_summary_final_state_is_msgpack_clean(
        self, flag_off_for_partition, monkeypatch,
    ):
        """``TemplateRunSummary.final_state`` should also be msgpack-
        clean — it's what we hand back to capability_outcomes and the
        audit_log writer, both of which JSON-serialize their inputs."""
        capture: dict[str, Any] = {}
        factory = _service_aware_factory_with_capture(capture)
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "TEMPLATES", {"svc_partition_2": factory})

        runner = TemplateRunner(
            pool=None, checkpointer_dsn=None,
            site_config=flag_off_for_partition,
        )
        summary = await runner.run(
            "svc_partition_2",
            {
                "task_id": "partition-test-2",
                "topic": "clean output",
                "database_service": _FakeDatabaseService(),
            },
            thread_id="partition-test-thread-2",
        )

        # The returned final_state is what callers like
        # content_router_service merge into their own result dict —
        # so it must encode cleanly.
        ormsgpack.packb(summary.final_state)
        assert "database_service" not in summary.final_state, (
            "service handles leaked back into final_state — caller's "
            "downstream serialization will TypeError"
        )

    @pytest.mark.asyncio
    async def test_node_with_no_services_in_config_still_runs(
        self, flag_off_for_partition, monkeypatch,
    ):
        """Backward-compat: a runner invocation with NO service handles
        in initial_state should still work — empty services dict
        threads through, node sees an empty services merge.

        This guards the existing trivial-graph tests in
        test_template_runner_postgres_checkpointer.py from regression.
        """
        capture: dict[str, Any] = {}
        factory = _service_aware_factory_with_capture(capture)
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "TEMPLATES", {"svc_partition_3": factory})

        runner = TemplateRunner(
            pool=None, checkpointer_dsn=None,
            site_config=flag_off_for_partition,
        )
        summary = await runner.run(
            "svc_partition_3",
            {"task_id": "no-svcs", "topic": "no services here"},
            thread_id="no-svcs-thread",
        )
        assert summary.ok
        assert capture["db_seen"] is None
        assert capture["img_seen"] is None
        assert capture["topic_seen"] == "no services here"

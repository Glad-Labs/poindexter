"""Unit tests for the Phase 1 variant hook on the writer atoms.

The hook lives in two places (per the design doc's
"scientific-method control" + "Posture: testing in production" sections):

- ``services/stages/generate_content.py::_generate_via_two_pass_atom``
  — calls ``experiment_runner.pick_variant`` for canonical_blog niches.
- ``services/pipeline_templates/__init__.py::narrate_node`` —
  the same hook for the dev_diary template's narrate atom.

Both hooks share the same shape: pick a variant once per task at atom
entry, stamp variant fields onto state, override model + prompt where
the variant specifies, fall back to no-variant when nothing's running.

We don't run a full LangGraph end-to-end here — that path is covered by
the broader integration tests. We assert the hook's behavior directly
at its function boundary by exercising ``_generate_via_two_pass_atom``
with the writer atom stubbed, and by directly invoking
``narrate_node`` (which is a closure inside the dev_diary factory) is
not feasible without exposing it; instead we cover the dev_diary path
by asserting the apply_variant_to_state helper does the right thing
under the shapes the narrate hook will produce. End-to-end is covered
in the integration_db tier.

Scope kept to "did the hook fire, and did the right fields land on
state + metrics", not "did the atom produce great content" — the
atom's own tests cover content quality.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.experiment_runner import ExperimentVariant

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _fake_site_config() -> Any:
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default="": {
        "pipeline_writer_model": "default-writer:1b",
        "cost_tier.standard.model": "",
    }.get(key, default))
    sc.get_int = MagicMock(side_effect=lambda key, default=0: default)
    sc.get_float = MagicMock(side_effect=lambda key, default=0.0: default)
    sc.get_bool = MagicMock(side_effect=lambda key, default=False: default)
    return sc


def _fake_database_service() -> Any:
    """Stub DatabaseService — pool is a MagicMock (won't be touched
    because we stub pick_variant + two_pass_writer.run + the
    helper-method DB reads at the test boundary)."""
    db = MagicMock()
    db.pool = MagicMock()
    db.get_task = AsyncMock(return_value=None)
    db.update_task = AsyncMock(return_value=None)
    db.log_cost = AsyncMock(return_value=None)
    return db


# ---------------------------------------------------------------------------
# Test 1: No active experiment → state has no variant_id key
#
# pick_variant returns None → the atom dispatch goes through the
# normal path; no variant_* keys land in the returned metrics dict.
# ---------------------------------------------------------------------------


async def test_no_active_experiment_state_unchanged() -> None:
    """When pick_variant returns None, the writer's metrics dict must
    NOT contain variant_id / variant_label / experiment_*. The
    production path is byte-equivalent to pre-PR behavior."""
    from modules.content.stages.generate_content import GenerateContentStage

    stage = GenerateContentStage()
    db = _fake_database_service()

    # Stub the two_pass writer atom to return a synthetic draft.
    fake_atom_result = {
        "draft": "draft body",
        "model_used": "default-writer:1b",
        "snippets_used": [],
    }

    # Stub the helper DB reads inside _generate_via_two_pass_atom so
    # they don't go to a real DB.
    with patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_writer_prompt_override",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_context_bundle",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms.two_pass_writer.run",
        new=AsyncMock(return_value=fake_atom_result),
    ), patch(
        "services.experiment_runner.pick_variant",
        new=AsyncMock(return_value=None),
    ), patch(
        # gpu.lock is an async context manager; replace it with a
        # passthrough that does nothing.
        "services.gpu_scheduler.gpu.lock",
        new=_passthrough_lock,
    ):
        content, model_used, metrics = await stage._generate_via_two_pass_atom(
            topic="t", style="", tone="", tags=[],
            database_service=db, task_id="task-no-exp",
            niche_slug="glad-labs",
            site_config=_fake_site_config(),
        )

    assert content == "draft body"
    assert model_used == "default-writer:1b"
    # No variant fields stamped on metrics — the recorder will write
    # variant_id NULL on the row, which is the right signal.
    assert "variant_id" not in metrics
    assert "variant_label" not in metrics
    assert "experiment_id" not in metrics
    assert "experiment_key" not in metrics


async def test_research_context_forwarded_to_writer_atom() -> None:
    """``research_context`` passed into ``_generate_via_two_pass_atom`` must
    thread through to ``two_pass_writer.run`` so the niche writer can ground
    + cite the same corpus the QA critic grades against.

    Pins the 2026-06-09 disconnect: ``_collect_research_context`` handed the
    external sources to the critic/ragas/deepeval rails, but the writer was
    never given them — so every ``glad-labs`` post was rejected for
    "completely ignores the provided SOURCES corpus".
    """
    from modules.content.stages.generate_content import GenerateContentStage

    stage = GenerateContentStage()
    db = _fake_database_service()

    atom_call_kwargs: dict[str, Any] = {}

    async def fake_run(*, topic, angle, niche_id, pool, **kw):
        atom_call_kwargs.update(kw)
        return {
            "draft": "grounded draft",
            "model_used": "default-writer:1b",
            "snippets_used": [],
        }

    research = "Source A: 2026 survey (https://example.com/s) — 60% cite the gap."

    with patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_writer_prompt_override",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_context_bundle",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms.two_pass_writer.run", new=fake_run,
    ), patch(
        "services.experiment_runner.pick_variant",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.gpu_scheduler.gpu.lock", new=_passthrough_lock,
    ):
        await stage._generate_via_two_pass_atom(
            topic="t", style="", tone="", tags=[],
            database_service=db, task_id="task-research-ctx",
            niche_slug="glad-labs",
            site_config=_fake_site_config(),
            research_context=research,
        )

    assert atom_call_kwargs.get("research_context") == research


# ---------------------------------------------------------------------------
# Test 2: Active experiment with writer_model override → model overridden
# ---------------------------------------------------------------------------


async def test_active_experiment_writer_model_override_flows_through() -> None:
    """Variant assigns ``writer_model='qwen3.6:latest'``. The override
    threads via ``writer_model_override=...`` kwarg into
    ``two_pass_writer.run``. The metrics dict carries the variant ids
    so capability_outcomes can stamp them."""
    from modules.content.stages.generate_content import GenerateContentStage

    stage = GenerateContentStage()
    db = _fake_database_service()

    variant = ExperimentVariant(
        variant_id="00000000-0000-0000-0000-000000000001",
        variant_label="qwen36",
        experiment_id="00000000-0000-0000-0000-00000000000a",
        experiment_key="glad-labs/model-bake-off",
        prompt_template_key=None,
        prompt_template_version=None,
        writer_model="qwen3.6:latest",
        rag_config={},
    )

    # Capture the kwargs the writer atom was invoked with so we can
    # assert writer_model_override propagated.
    atom_call_kwargs: dict[str, Any] = {}

    async def fake_run(*, topic, angle, niche_id, pool, **kw):
        atom_call_kwargs.update(kw)
        return {
            "draft": "variant-driven draft",
            "model_used": "qwen3.6:latest",
            "snippets_used": [],
        }

    with patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_writer_prompt_override",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_context_bundle",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms.two_pass_writer.run", new=fake_run,
    ), patch(
        "services.experiment_runner.pick_variant",
        new=AsyncMock(return_value=variant),
    ), patch(
        "services.gpu_scheduler.gpu.lock", new=_passthrough_lock,
    ):
        content, model_used, metrics = await stage._generate_via_two_pass_atom(
            topic="t", style="", tone="", tags=[],
            database_service=db, task_id="task-model-override",
            niche_slug="glad-labs",
            site_config=_fake_site_config(),
        )

    # Writer model override propagated into the atom kwargs.
    assert atom_call_kwargs.get("writer_model_override") == "qwen3.6:latest"
    # Variant ids stamped on metrics so record_run picks them up.
    assert metrics["variant_id"] == "00000000-0000-0000-0000-000000000001"
    assert metrics["variant_label"] == "qwen36"
    assert metrics["experiment_id"] == "00000000-0000-0000-0000-00000000000a"
    assert metrics["experiment_key"] == "glad-labs/model-bake-off"
    # The model_selection_log gains a variant attribution so the
    # operator can trace WHY the writer picked this model from the
    # metrics blob alone.
    assert metrics["model_selection_log"]["generate_content"]["variant"] == (
        "glad-labs/model-bake-off/qwen36"
    )


# ---------------------------------------------------------------------------
# Test 3: Active experiment with no overrides → variant_id set, model unchanged
#
# A "scientific-method control" variant that holds every override
# field None should still stamp the variant id (so the scorecard counts
# it) but must NOT shift the writer model away from the niche default.
# ---------------------------------------------------------------------------


async def test_active_experiment_no_overrides_preserves_defaults() -> None:
    from modules.content.stages.generate_content import GenerateContentStage

    stage = GenerateContentStage()
    db = _fake_database_service()

    variant = ExperimentVariant(
        variant_id="00000000-0000-0000-0000-000000000002",
        variant_label="baseline",
        experiment_id="00000000-0000-0000-0000-00000000000b",
        experiment_key="glad-labs/no-axis",
        prompt_template_key=None,
        prompt_template_version=None,
        writer_model=None,  # held constant
        rag_config={},
    )

    atom_call_kwargs: dict[str, Any] = {}

    async def fake_run(*, topic, angle, niche_id, pool, **kw):
        atom_call_kwargs.update(kw)
        return {
            "draft": "no-override draft",
            "model_used": "default-writer:1b",
            "snippets_used": [],
        }

    with patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_writer_prompt_override",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_context_bundle",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms.two_pass_writer.run", new=fake_run,
    ), patch(
        "services.experiment_runner.pick_variant",
        new=AsyncMock(return_value=variant),
    ), patch(
        "services.gpu_scheduler.gpu.lock", new=_passthrough_lock,
    ):
        content, model_used, metrics = await stage._generate_via_two_pass_atom(
            topic="t", style="", tone="", tags=[],
            database_service=db, task_id="task-no-override",
            niche_slug="glad-labs",
            site_config=_fake_site_config(),
        )

    # No model override → atom kwargs see writer_model_override=None.
    # The atom falls back to site_config resolution as before.
    assert atom_call_kwargs.get("writer_model_override") is None
    # But variant_id IS stamped on metrics so the scorecard counts the
    # post under this variant.
    assert metrics["variant_id"] == "00000000-0000-0000-0000-000000000002"
    assert metrics["variant_label"] == "baseline"


# ---------------------------------------------------------------------------
# Test 4: Variant metadata flows into capability_outcomes via record_run
# ---------------------------------------------------------------------------


async def test_variant_id_propagates_to_record_run() -> None:
    """End-to-end at the recorder boundary: when the writer atom
    returns a metrics dict carrying ``variant_id``, ``record_run`` must
    INSERT it into the variant_id column."""
    from services.capability_outcomes import record_run

    # Reuse the test_capability_outcomes pool stub shape inline.
    executed: list[tuple[str, tuple[Any, ...]]] = []

    class _Conn:
        async def execute(self, sql: str, *args: Any) -> None:
            executed.append((sql, args))

        async def __aenter__(self): return self

        async def __aexit__(self, *_a): return False

    class _Acq:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *_a):
            return False

    class _Pool:
        def acquire(self): return _Acq()

    # Minimal TemplateRunSummary-shape with one record carrying
    # variant_id on its metrics.
    from dataclasses import dataclass, field

    @dataclass
    class _Rec:
        name: str = "atoms.two_pass_writer"
        ok: bool = True
        detail: str = ""
        halted: bool = False
        elapsed_ms: int = 1
        metrics: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class _Sum:
        template_slug: str
        records: list[Any] = field(default_factory=list)

    summary = _Sum(
        template_slug="canonical_blog",
        records=[
            _Rec(metrics={
                "model_used": "qwen3.6:latest",
                "niche_slug": "glad-labs",
                "variant_id": "00000000-0000-0000-0000-000000000003",
                "prompt_template_key": "atoms.two_pass_writer.revise_prompt",
                "prompt_template_version": 4,
            }),
        ],
    )

    written = await record_run(
        _Pool(), summary, {"task_id": "task-variant-rec"},
    )
    assert written == 1

    _, args = executed[-1]
    # INSERT column order per record_run:
    #   ... niche_slug(13), prompt_template_key(14),
    #   prompt_template_version(15), variant_id(16).
    # Asserting by tail offset is the same pattern used in
    # test_capability_outcomes.py.
    assert args[-1] == "00000000-0000-0000-0000-000000000003"
    assert args[-2] == 4  # prompt version
    assert args[-3] == "atoms.two_pass_writer.revise_prompt"
    assert args[-4] == "glad-labs"  # niche_slug


# ---------------------------------------------------------------------------
# Test 5: rag_config shallow merge — niche default + variant overrides
#
# Pure helper-level test (apply_variant_to_state has its own coverage in
# test_experiment_runner.py — this one pins the contract specifically
# for the rag_config + writer/narrate hook collaboration).
# ---------------------------------------------------------------------------


async def test_rag_config_shallow_merge_via_hook_helper() -> None:
    """When the niche default has ``snippet_limit=5`` and the variant
    sets ``snippet_limit=10`` + ``max_tokens=4000``, the resulting
    state['rag_config'] must be {snippet_limit:10, max_tokens:4000}
    — variant wins on conflict, non-conflicting keys carry through."""
    from services.experiment_runner import apply_variant_to_state

    variant = ExperimentVariant(
        variant_id="00000000-0000-0000-0000-000000000004",
        variant_label="rag-axis",
        experiment_id="00000000-0000-0000-0000-00000000000c",
        experiment_key="glad-labs/rag-tune",
        prompt_template_key=None,
        prompt_template_version=None,
        writer_model=None,
        rag_config={"snippet_limit": 10, "max_tokens": 4000},
    )
    state: dict[str, Any] = {"rag_config": {"snippet_limit": 5}}
    apply_variant_to_state(state, variant)
    assert state["rag_config"] == {"snippet_limit": 10, "max_tokens": 4000}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PassthroughLock:
    """``gpu.lock(...)`` returns an async context manager — replace
    with one that does nothing so tests don't need the GPU scheduler
    fixture."""

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _passthrough_lock(*_a: Any, **_kw: Any) -> _PassthroughLock:
    """Drop-in replacement for ``gpu.lock`` — the production lock is a
    sync method returning an async ctx mgr; this matches that shape."""
    return _PassthroughLock()

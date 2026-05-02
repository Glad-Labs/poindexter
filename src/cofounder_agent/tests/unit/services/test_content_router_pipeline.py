"""
Unit tests for services/content_router_service.process_content_generation_task

Despite the existence of test_content_router_service.py, that file actually
exercises sibling helpers (ContentTaskStore, category_resolver, etc) — NOT
the load-bearing `process_content_generation_task` orchestrator that
content_router_service.py exports. This file fills that gap.

Strategy: the orchestrator is one large async function that delegates
every stage to a `StageRunner`. We patch `StageRunner` and the
fire-and-forget helpers so we can drive the orchestrator down each
branch without spinning up Ollama/the full plugin registry.

Branches covered:
- Happy path — all chunks complete, returns result with status set by stages
- ValueError when database_service is None
- generate_content halt → RuntimeError
- quality_evaluation halt → RuntimeError
- cross_model_qa rejects → early-return with status='rejected'
- post-QA halt at finalize → RuntimeError caught and surfaces as 'failed'
- Generic stage exception → caught, task updated to 'failed', webhook fired
- task_id auto-generated when not provided
- _models_by_phase is mutated by experiment hook (best-effort)
- Dry-run halt is logged at severity='info' not 'error'
- Update-task failure during error path is caught (no double-raise)
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.content_router_service import process_content_generation_task

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _make_db():
    """Build a mock DatabaseService that satisfies process_content_generation_task."""
    db = AsyncMock()
    db.pool = MagicMock()
    db.update_task = AsyncMock()
    return db


def _make_quality_result(score: float = 75.0):
    """A minimal quality_result stand-in — only `.overall_score` is read."""
    return SimpleNamespace(overall_score=score)


def _stub_summary(halted_at: str | None = None, detail: str = "ok"):
    """A StageRunSummary-shaped stub with optional halt."""
    record = SimpleNamespace(name=halted_at or "stage", detail=detail)
    return SimpleNamespace(
        ok=halted_at is None,
        halted_at=halted_at,
        records=[record],
    )


class _StageRunnerStub:
    """Drop-in StageRunner that returns scripted summaries per call.

    Pass a list of summaries — they are returned in order. After the list
    is exhausted, returns a successful empty summary. Each `run_all` call
    optionally mutates the context dict via `context_updates`.
    """

    def __init__(self, summaries: list, context_updates: list[dict] | None = None):
        self._summaries = list(summaries)
        self._updates = list(context_updates or [])
        self.calls: list[list[str]] = []  # captured `order=` arg per call

    def __call__(self, *_args, **_kwargs):  # constructed as StageRunner(pool, stages)
        return self

    async def run_all(self, context, order=None):
        self.calls.append(list(order or []))
        if self._updates:
            context.update(self._updates.pop(0))
        if self._summaries:
            return self._summaries.pop(0)
        return _stub_summary()


def _patch_orchestrator_internals(stage_runner_stub: _StageRunnerStub | None = None):
    """Patch every external dependency the orchestrator imports lazily.

    Returns a context-manager-like object (a list of patcher contexts) that
    callers `with` to enter all patches at once.
    """
    runner = stage_runner_stub or _StageRunnerStub([_stub_summary()])

    # plugins.registry.get_core_samples returns the stage list. The stub
    # ignores it — but the import must succeed.
    registry_mod = MagicMock()
    registry_mod.get_core_samples = MagicMock(return_value={"stages": []})

    # plugins.stage_runner.StageRunner is replaced by our stub.
    stage_runner_mod = MagicMock()
    stage_runner_mod.StageRunner = MagicMock(return_value=runner)

    # services.image_service.get_image_service — orchestrator instantiates it
    # at the top to seed the context.
    image_svc_mod = MagicMock()
    image_svc_mod.get_image_service = MagicMock(return_value=MagicMock())

    # services.audit_log.audit_log_bg — fire-and-forget logger; replace with no-op.
    # services.webhook_delivery_service.emit_webhook_event — same deal.
    audit_mod = MagicMock()
    audit_mod.audit_log_bg = MagicMock()
    webhook_mod = MagicMock()
    webhook_mod.emit_webhook_event = AsyncMock()

    # services.container.get_service — the lifespan-DI seam.
    container_mod = MagicMock()
    container_mod.get_service = MagicMock(side_effect=Exception("no container in test"))

    # services.image_style_rotation.ImageStyleTracker — instantiated.
    img_style_mod = MagicMock()
    img_style_mod.ImageStyleTracker = MagicMock(return_value=MagicMock())

    # services.pipeline_experiment_hook.assign_pipeline_variant +
    # record_pipeline_outcome — both best-effort, default no-op.
    exp_mod = MagicMock()
    exp_mod.assign_pipeline_variant = AsyncMock(
        return_value={"experiment_key": None, "variant_key": None},
    )
    exp_mod.record_pipeline_outcome = AsyncMock()

    # services.site_config.site_config — a MagicMock supports both .get + .require.
    site_config_obj = MagicMock()
    site_config_obj.get = MagicMock(return_value="")
    site_config_obj.require = MagicMock(return_value="https://gladlabs.io")
    site_config_mod = MagicMock()
    site_config_mod.site_config = site_config_obj

    # services.gpu_scheduler.gpu — prepare_mode is awaited in the SDXL chunk.
    gpu_obj = MagicMock()
    gpu_obj.prepare_mode = AsyncMock()
    gpu_mod = MagicMock()
    gpu_mod.gpu = gpu_obj

    sys_modules_overrides = {
        "plugins.registry": registry_mod,
        "plugins.stage_runner": stage_runner_mod,
        "services.image_service": image_svc_mod,
        "services.audit_log": audit_mod,
        "services.webhook_delivery_service": webhook_mod,
        "services.container": container_mod,
        "services.image_style_rotation": img_style_mod,
        "services.pipeline_experiment_hook": exp_mod,
        "services.site_config": site_config_mod,
        "services.gpu_scheduler": gpu_mod,
    }

    # Also patch the names already imported at module load time. The
    # function does some `from .audit_log import audit_log_bg` style imports
    # at the top — those bindings live on services.content_router_service.
    return sys_modules_overrides, runner


class _ImportPatchContext:
    """Context manager that overrides sys.modules entries for the duration."""

    def __init__(self, overrides: dict, also_patch_router_module: bool = True):
        self._overrides = overrides
        self._saved: dict = {}
        self._router_patches: list = []
        self._also_router = also_patch_router_module

    def __enter__(self):
        for name, mod in self._overrides.items():
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = mod

        if self._also_router:
            # The orchestrator does `from .audit_log import audit_log_bg` and
            # `from .webhook_delivery_service import emit_webhook_event` at
            # module load. Patch those bound names too.
            from services import content_router_service as crs
            self._router_patches = [
                patch.object(crs, "audit_log_bg", self._overrides["services.audit_log"].audit_log_bg),
                patch.object(crs, "emit_webhook_event", self._overrides["services.webhook_delivery_service"].emit_webhook_event),
                patch.object(crs, "get_image_service", self._overrides["services.image_service"].get_image_service),
            ]
            for p in self._router_patches:
                p.start()
        return self

    def __exit__(self, *exc):
        for p in self._router_patches:
            p.stop()
        for name, original in self._saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        return False


def _set_up_happy_path_stages():
    """Build a StageRunner stub that drives the orchestrator through every chunk
    successfully. Each `run_all` call returns ok and seeds the context with
    the values that the next chunk reads.
    """
    chunk1_summary = _stub_summary()  # verify+generate_content
    chunk2_summary = _stub_summary()  # writer_self_review+QA+url+inline_images
    chunk3_summary = _stub_summary()  # source_featured_image
    chunk4_summary = _stub_summary()  # cross_model_qa
    chunk5_summary = _stub_summary()  # SEO+media+training+finalize

    # After chunk 1: orchestrator reads result.content + result.model_used
    # After chunk 2: orchestrator reads result.quality_result + quality_score
    # After chunk 4: orchestrator checks result.status for 'rejected'
    return _StageRunnerStub(
        summaries=[
            chunk1_summary, chunk2_summary, chunk3_summary,
            chunk4_summary, chunk5_summary,
        ],
        context_updates=[
            {"content": "## Generated body\n\nA real article.", "model_used": "gemma3:27b"},
            {"quality_result": _make_quality_result(75.0), "quality_score": 75.0},
            {},
            {"status": "completed"},
            {"status": "completed", "qa_final_score": 80.0, "post_id": "post-xyz"},
        ],
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_happy_path_runs_all_chunks_and_returns_completed():
    """All 5 stage chunks complete; orchestrator returns the final result dict."""
    db = _make_db()
    overrides, runner = _patch_orchestrator_internals(_set_up_happy_path_stages())

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="Building Async Python Pipelines",
            style="technical",
            tone="informative",
            target_length=1200,
            tags=["python", "async"],
            generate_featured_image=True,
            database_service=db,
            task_id="11111111-2222-3333-4444-555555555555",
        )

    assert result["status"] == "completed"
    assert result["task_id"] == "11111111-2222-3333-4444-555555555555"
    assert result["topic"] == "Building Async Python Pipelines"
    assert result["category"] == "technology"  # default fallback
    # 5 chunks → 5 run_all() invocations
    assert len(runner.calls) == 5
    assert runner.calls[0] == ["verify_task", "generate_content"]
    assert "cross_model_qa" in runner.calls[3]
    assert runner.calls[-1] == [
        "generate_seo_metadata", "generate_media_scripts",
        "capture_training_data", "finalize_task",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_id_auto_generated_when_missing():
    """task_id defaults to a fresh UUID when caller omits it."""
    db = _make_db()
    overrides, _runner = _patch_orchestrator_internals(_set_up_happy_path_stages())

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="x", style="s", tone="t", target_length=500,
            database_service=db,
        )

    assert result["task_id"]
    # UUID4 is 36 chars with dashes
    assert len(result["task_id"]) == 36


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raises_when_database_service_is_none():
    """The orchestrator can't persist without a DatabaseService — raise hard."""
    overrides, _runner = _patch_orchestrator_internals()
    with _ImportPatchContext(overrides):
        with pytest.raises(ValueError, match="DatabaseService"):
            await process_content_generation_task(
                topic="topic", style="s", tone="t", target_length=500,
                database_service=None, task_id="abc",
            )


# ---------------------------------------------------------------------------
# Halt at generate_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_halt_at_generate_content_marks_task_failed():
    """When chunk-1 halts at generate_content the orchestrator surfaces failure
    via the global try/except and returns a 'failed' result (not raises)."""
    db = _make_db()
    runner = _StageRunnerStub(
        summaries=[_stub_summary(halted_at="generate_content", detail="model timeout")],
    )
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="halt-gen",
        )

    assert result["status"] == "failed"
    assert "generate_content" in result["error"]
    db.update_task.assert_awaited()


# ---------------------------------------------------------------------------
# Halt at quality_evaluation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_halt_at_quality_evaluation_marks_task_failed():
    """Chunk-2 halt at quality_evaluation surfaces the same way."""
    db = _make_db()
    runner = _StageRunnerStub(
        summaries=[
            _stub_summary(),  # chunk 1 ok
            _stub_summary(halted_at="quality_evaluation", detail="score N/A"),
        ],
        context_updates=[
            {"content": "body", "model_used": "gemma3:27b"},
            {},
        ],
    )
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="halt-qa",
        )

    assert result["status"] == "failed"
    assert "quality_evaluation" in result["error"]


# ---------------------------------------------------------------------------
# Cross-model QA rejection — early return, NOT raise
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_model_qa_rejection_short_circuits_with_rejected_status():
    """When the cross_model_qa stage halts AND sets status='rejected' the
    orchestrator returns the result without raising — this is the canonical
    'multi-model QA caught a hallucination' path."""
    db = _make_db()
    runner = _StageRunnerStub(
        summaries=[
            _stub_summary(),  # chunk 1
            _stub_summary(),  # chunk 2
            _stub_summary(),  # chunk 3 (image)
            _stub_summary(halted_at="cross_model_qa", detail="critic rejected"),
        ],
        context_updates=[
            {"content": "body", "model_used": "gemma3:27b"},
            {"quality_result": _make_quality_result(60.0), "quality_score": 60.0},
            {},
            {"status": "rejected"},
        ],
    )
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="rejected-by-qa",
        )

    assert result["status"] == "rejected"
    # SEO/media/finalize chunk should NOT have run
    # The runner has 5 scripted summaries; if it short-circuits we used 4
    assert len(runner.calls) == 4
    # update_task should NOT have been called (no error path)
    db.update_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# Post-QA halt
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_halt_after_qa_in_finalize_chunk_is_caught():
    """If finalize chunk halts the orchestrator's RuntimeError gets caught
    in the global try/except and result is marked 'failed'."""
    db = _make_db()
    runner = _StageRunnerStub(
        summaries=[
            _stub_summary(), _stub_summary(), _stub_summary(),
            _stub_summary(),  # cross_model_qa ok
            _stub_summary(halted_at="finalize_task", detail="DB write failed"),
        ],
        context_updates=[
            {"content": "body", "model_used": "gemma3:27b"},
            {"quality_result": _make_quality_result(75.0), "quality_score": 75.0},
            {}, {"status": "completed"}, {},
        ],
    )
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="halt-finalize",
        )

    assert result["status"] == "failed"
    assert "finalize_task" in result["error"]


# ---------------------------------------------------------------------------
# Generic stage exception → error path, task update, webhook
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unexpected_stage_exception_marks_task_failed_and_emits_webhook():
    """Any exception inside the try block lands in the error handler:
    audit_log_bg('error'), update_task(status='failed'), webhook task.failed."""
    db = _make_db()

    class _BoomRunner(_StageRunnerStub):
        async def run_all(self, context, order=None):
            self.calls.append(list(order or []))
            raise RuntimeError("ollama unreachable")

    runner = _BoomRunner([])
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="boom-task",
        )

    assert result["status"] == "failed"
    assert "ollama unreachable" in result["error"]
    db.update_task.assert_awaited()
    # The update payload preserves task_id and includes error_message
    update_call = db.update_task.call_args
    assert update_call.kwargs["task_id"] == "boom-task"
    assert update_call.kwargs["updates"]["status"] == "failed"
    # Webhook should have been attempted
    overrides["services.webhook_delivery_service"].emit_webhook_event.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_failure_during_error_path_does_not_raise():
    """Even if the cleanup `update_task` itself errors, the orchestrator
    swallows that secondary failure and still returns the failed-state dict."""
    db = _make_db()
    db.update_task = AsyncMock(side_effect=RuntimeError("DB also down"))

    class _BoomRunner(_StageRunnerStub):
        async def run_all(self, context, order=None):
            raise RuntimeError("primary boom")

    runner = _BoomRunner([])
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="double-fail",
        )

    # Original error is what we surface — secondary DB error is logged not raised
    assert result["status"] == "failed"
    assert "primary boom" in result["error"]


# ---------------------------------------------------------------------------
# Dry-run mode demotes severity (Glad-Labs/poindexter#260)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dry_run_halt_logs_at_info_severity_not_error():
    """When pipeline_dry_run_mode=true and the failure message is the
    'no attempts recorded' / AllModelsFailedError fingerprint, the audit
    log entry should be severity='info' (event='dry_run_halt'), NOT
    severity='error'. This prevents dry-run noise from drowning real
    error counts on dashboards."""
    db = _make_db()

    class _DryRunBoom(_StageRunnerStub):
        async def run_all(self, context, order=None):
            self.calls.append(list(order or []))
            raise RuntimeError("no attempts recorded — AllModelsFailedError")

    runner = _DryRunBoom([])
    overrides, _ = _patch_orchestrator_internals(runner)

    # Configure the site_config to report dry-run mode is on. The
    # orchestrator reads via _sc_dryrun_check.get("pipeline_dry_run_mode", "")
    overrides["services.site_config"].site_config.get = MagicMock(
        side_effect=lambda key, default="": "true" if key == "pipeline_dry_run_mode" else default,
    )

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="dry-run-task",
        )

    assert result["status"] == "failed"
    # audit_log_bg should have been called with severity='info' for the
    # dry-run-specific event_type 'dry_run_halt'.
    audit_calls = overrides["services.audit_log"].audit_log_bg.call_args_list
    dry_run_calls = [c for c in audit_calls if c.args and c.args[0] == "dry_run_halt"]
    assert len(dry_run_calls) == 1
    assert dry_run_calls[0].kwargs.get("severity") == "info"


# ---------------------------------------------------------------------------
# Experiment hook propagates models_by_phase mutation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_experiment_assignment_failure_is_swallowed():
    """If assign_pipeline_variant raises, the orchestrator continues with
    a no-op assignment dict — never raises."""
    db = _make_db()
    runner = _set_up_happy_path_stages()
    overrides, _ = _patch_orchestrator_internals(runner)
    overrides["services.pipeline_experiment_hook"].assign_pipeline_variant = AsyncMock(
        side_effect=RuntimeError("experiment table missing"),
    )

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="exp-fail",
        )

    # Pipeline still completed despite hook failure
    assert result["status"] == "completed"
    assert result["experiment_assignment"]["experiment_key"] is None


# ---------------------------------------------------------------------------
# Writer fallback detection (silent model substitution canary)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_fallback_emits_audit_when_model_used_differs_from_configured():
    """If site_config.pipeline_writer_model says qwen2.5:72b but the stage
    actually used gemma3:27b, the orchestrator emits a 'writer_fallback'
    audit event so dashboards/alerts notice the silent substitution."""
    db = _make_db()
    runner = _set_up_happy_path_stages()
    overrides, _ = _patch_orchestrator_internals(runner)

    # Configure pipeline_writer_model = qwen2.5:72b. The happy-path stub
    # seeds model_used = gemma3:27b → mismatch → fallback audit fires.
    overrides["services.site_config"].site_config.get = MagicMock(
        side_effect=lambda key, default="": (
            "qwen2.5:72b" if key == "pipeline_writer_model" else default
        ),
    )

    with _ImportPatchContext(overrides):
        await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="writer-fallback-test",
        )

    audit_calls = overrides["services.audit_log"].audit_log_bg.call_args_list
    fallback_calls = [c for c in audit_calls if c.args and c.args[0] == "writer_fallback"]
    assert len(fallback_calls) == 1
    payload = fallback_calls[0].args[2]
    assert payload["configured_writer"] == "qwen2.5:72b"
    assert payload["actual_writer"] == "gemma3:27b"
    assert fallback_calls[0].kwargs.get("severity") == "warning"


# ---------------------------------------------------------------------------
# Custom category accepted, defaults to 'technology' when None
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_custom_category_propagated_to_result():
    db = _make_db()
    overrides, _ = _patch_orchestrator_internals(_set_up_happy_path_stages())

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="cat-test", category="gaming",
        )

    assert result["category"] == "gaming"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_category_is_technology_when_none():
    db = _make_db()
    overrides, _ = _patch_orchestrator_internals(_set_up_happy_path_stages())

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="cat-default",
        )

    assert result["category"] == "technology"


# ---------------------------------------------------------------------------
# Models-by-phase + tags pass-through
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_models_by_phase_and_tags_seeded_in_context():
    db = _make_db()
    runner = _set_up_happy_path_stages()
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            tags=["a", "b"],
            models_by_phase={"writer": "qwen2.5:72b"},
            database_service=db, task_id="seed-test",
        )

    assert result["tags"] == ["a", "b"]
    # Mutated copy: result["models_by_phase"] should have writer key
    assert result["models_by_phase"].get("writer") == "qwen2.5:72b"


# ---------------------------------------------------------------------------
# update_task call shape: failure metadata preserves partial generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failure_metadata_preserves_partial_generation():
    """When a stage chunk fails AFTER content/SEO are populated, the cleanup
    update_task call must include those partial values so the operator can
    still review what got generated."""
    db = _make_db()

    class _PartialBoom(_StageRunnerStub):
        def __init__(self):
            super().__init__([])
            self._call_n = 0

        async def run_all(self, context, order=None):
            self.calls.append(list(order or []))
            self._call_n += 1
            if self._call_n == 1:
                # chunk 1 — populate content + image
                context["content"] = "Partial body"
                context["model_used"] = "gemma3:27b"
                context["featured_image_url"] = "https://img/1.png"
                context["seo_title"] = "Partial SEO"
                return _stub_summary()
            # chunk 2 boom
            raise RuntimeError("downstream boom")

    runner = _PartialBoom()
    overrides, _ = _patch_orchestrator_internals(runner)

    with _ImportPatchContext(overrides):
        await process_content_generation_task(
            topic="topic", style="s", tone="t", target_length=500,
            database_service=db, task_id="partial-test",
        )

    update_call = db.update_task.call_args
    metadata = update_call.kwargs["updates"]["task_metadata"]
    # Partial values preserved
    assert metadata["content"] == "Partial body"
    assert metadata["featured_image_url"] == "https://img/1.png"
    assert metadata["seo_title"] == "Partial SEO"
    # None values stripped (e.g. seo_description not set → not in dict)
    assert "seo_description" not in metadata
    # error_message captured
    assert "downstream boom" in metadata["error_message"]

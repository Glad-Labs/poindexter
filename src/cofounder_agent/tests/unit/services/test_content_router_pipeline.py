"""Unit tests for services.content_router_service.process_content_generation_task.

After the 2026-05-16 Lane C Stage 4 cleanup, this function is a thin
TemplateRunner dispatcher — no more legacy chunked StageRunner flow.
Tests pin:

- Happy path — TemplateRunner runs, result reflects final_state, audit
  ``task_started`` + ``template_completed`` events fire
- Missing ``database_service`` raises ValueError (can't persist)
- Missing ``template_slug`` on the pipeline_tasks row → fail-loud:
  task marked ``failed`` with diagnostic, no implicit pipeline runs.
  Enforces ``feedback_no_silent_defaults`` for the deleted legacy
  chunked path.
- TemplateRunner raises → caught, task marked ``failed``, partial
  state preserved, ``task.failed`` webhook fired
- Dry-run AllModelsFailedError demoted to severity='info'
- task_id auto-generated when caller omits
- Experiment hook is best-effort (failure swallowed, run continues)
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_db(template_slug: str | None = "canonical_blog"):
    """Mock DatabaseService with a pool whose fetchval returns ``template_slug``.

    ``template_slug=None`` simulates the no-slug-on-row case the new
    no-silent-default path is supposed to fail loud on.
    """
    db = AsyncMock()
    db.update_task = AsyncMock()

    # Build a pool.acquire() context manager that yields a conn whose
    # fetchval(template_slug-SELECT) returns ``template_slug``.
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=template_slug)
    pool_cm = MagicMock()
    pool_cm.__aenter__ = AsyncMock(return_value=conn)
    pool_cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=pool_cm)
    db.pool = pool
    return db


def _make_template_summary(
    *, ok: bool = True, halted_at: str | None = None,
    final_state: dict | None = None, records: list | None = None,
):
    """A TemplateRunSummary-shaped stub."""
    return SimpleNamespace(
        ok=ok,
        halted_at=halted_at,
        final_state=final_state or {"status": "awaiting_approval", "quality_score": 80.0},
        records=records or [SimpleNamespace(name="finalize_task")],
    )


def _patch_externals(template_summary=None, template_raises=None):
    """Override every external import the dispatcher reaches for.

    Returns (sys_modules_overrides, template_runner_stub).
    """
    # services.image_service.get_image_service
    image_svc_mod = MagicMock()
    image_svc_mod.get_image_service = MagicMock(return_value=MagicMock())

    # services.image_style_rotation.ImageStyleTracker
    img_style_mod = MagicMock()
    img_style_mod.ImageStyleTracker = MagicMock(return_value=MagicMock())

    # services.container.get_service — no settings service in tests
    container_mod = MagicMock()
    container_mod.get_service = MagicMock(side_effect=Exception("no container in test"))

    # services.pipeline_experiment_hook — best-effort, default no-op
    exp_mod = MagicMock()
    exp_mod.assign_pipeline_variant = AsyncMock(
        return_value={"experiment_key": None, "variant_key": None},
    )
    exp_mod.record_pipeline_outcome = AsyncMock()

    # services.template_runner.TemplateRunner — the actual dispatch target
    tmpl_runner_instance = MagicMock()
    if template_raises is not None:
        tmpl_runner_instance.run = AsyncMock(side_effect=template_raises)
    else:
        tmpl_runner_instance.run = AsyncMock(
            return_value=template_summary or _make_template_summary(),
        )
    tmpl_runner_mod = MagicMock()
    tmpl_runner_mod.TemplateRunner = MagicMock(return_value=tmpl_runner_instance)

    # services.site_config.site_config — minimal stub for dry-run check
    site_config_obj = MagicMock()
    site_config_obj.get = MagicMock(return_value="")
    site_config_mod = MagicMock()
    site_config_mod.site_config = site_config_obj
    site_config_mod.SiteConfig = MagicMock(return_value=site_config_obj)

    sys_modules_overrides = {
        "services.image_service": image_svc_mod,
        "services.image_style_rotation": img_style_mod,
        "services.container": container_mod,
        "services.pipeline_experiment_hook": exp_mod,
        "services.template_runner": tmpl_runner_mod,
    }
    return sys_modules_overrides, tmpl_runner_instance, site_config_obj


class _ImportPatchContext:
    """Override sys.modules entries + patch already-bound names on the router."""

    def __init__(self, overrides: dict, site_config_obj):
        self._overrides = overrides
        self._site_config_obj = site_config_obj
        self._saved: dict = {}
        self._router_patches: list = []

    def __enter__(self):
        for name, mod in self._overrides.items():
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = mod

        # Patch already-bound names on the content_router_service module.
        from services import content_router_service as crs
        audit_mock = MagicMock()
        webhook_mock = AsyncMock()
        self._audit_mock = audit_mock
        self._webhook_mock = webhook_mock
        self._router_patches = [
            patch.object(crs, "audit_log_bg", audit_mock),
            patch.object(crs, "emit_webhook_event", webhook_mock),
            patch.object(
                crs, "get_image_service",
                self._overrides["services.image_service"].get_image_service,
            ),
            patch.object(crs, "site_config", self._site_config_obj),
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_happy_path_dispatches_to_template_runner_and_returns_final_state():
    """With a non-NULL ``template_slug`` on the row, the dispatcher calls
    TemplateRunner.run and merges its final_state into the result dict."""
    from services.content_router_service import process_content_generation_task

    db = _make_db(template_slug="canonical_blog")
    summary = _make_template_summary(
        final_state={
            "status": "awaiting_approval",
            "quality_score": 82.0,
            "post_id": "post-xyz",
        },
    )
    overrides, tmpl_runner, site_config_obj = _patch_externals(template_summary=summary)

    with _ImportPatchContext(overrides, site_config_obj) as ctx:
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

    # TemplateRunner was invoked with the resolved slug + the context dict
    tmpl_runner.run.assert_awaited_once()
    args, kwargs = tmpl_runner.run.call_args
    assert args[0] == "canonical_blog"
    assert kwargs.get("thread_id") == "11111111-2222-3333-4444-555555555555"

    # Final state merged into result
    assert result["status"] == "awaiting_approval"
    assert result["quality_score"] == 82.0
    assert result["post_id"] == "post-xyz"
    assert result["task_id"] == "11111111-2222-3333-4444-555555555555"

    # task_started + template_completed audit events fired
    audit_events = [c.args[0] for c in ctx._audit_mock.call_args_list]
    assert "task_started" in audit_events
    assert "template_completed" in audit_events


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_id_auto_generated_when_missing():
    """task_id defaults to a fresh UUID when caller omits it."""
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    overrides, _runner, site_config_obj = _patch_externals()

    with _ImportPatchContext(overrides, site_config_obj):
        result = await process_content_generation_task(
            topic="x", style="s", tone="t", target_length=500,
            database_service=db,
        )

    assert result["task_id"]
    assert len(result["task_id"]) == 36  # UUID4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_custom_category_propagated_to_result():
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    overrides, _runner, site_config_obj = _patch_externals()

    with _ImportPatchContext(overrides, site_config_obj):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="cat-test", category="gaming",
        )

    assert result["category"] == "gaming"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_category_is_technology_when_none():
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    overrides, _runner, site_config_obj = _patch_externals()

    with _ImportPatchContext(overrides, site_config_obj):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="cat-default",
        )

    assert result["category"] == "technology"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raises_when_database_service_is_none():
    """The dispatcher can't persist or look up template_slug without a
    DatabaseService — raise hard."""
    from services.content_router_service import process_content_generation_task

    overrides, _runner, site_config_obj = _patch_externals()
    with _ImportPatchContext(overrides, site_config_obj):
        with pytest.raises(ValueError, match="DatabaseService"):
            await process_content_generation_task(
                topic="topic", style="s", tone="t", target_length=500,
                database_service=None, task_id="abc",
            )


# ---------------------------------------------------------------------------
# No silent defaults — missing template_slug must fail loud
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_template_slug_fails_loudly():
    """The legacy chunked StageRunner path was deleted 2026-05-16. A
    pipeline_tasks row with NULL template_slug is now a config error,
    not a fallback. Per feedback_no_silent_defaults, mark the task
    failed with a diagnostic instead of silently dispatching to an
    undefined pipeline."""
    from services.content_router_service import process_content_generation_task

    db = _make_db(template_slug=None)
    overrides, tmpl_runner, site_config_obj = _patch_externals()

    with _ImportPatchContext(overrides, site_config_obj) as ctx:
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="no-slug",
        )

    # TemplateRunner was NOT invoked — no fallback pipeline ran
    tmpl_runner.run.assert_not_awaited()

    # Task marked failed with a diagnostic error message
    assert result["status"] == "failed"
    assert "template_slug" in result["error"]
    db.update_task.assert_awaited_once()
    update_kwargs = db.update_task.call_args
    # update_task is called positionally (task_id, updates) here
    updates = update_kwargs.args[1]
    assert updates["status"] == "failed"
    assert "template_slug" in updates["error_message"]

    # Audit event fires at severity='error'
    audit_events = ctx._audit_mock.call_args_list
    slug_events = [c for c in audit_events if c.args and c.args[0] == "missing_template_slug"]
    assert len(slug_events) == 1
    assert slug_events[0].kwargs.get("severity") == "error"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_string_template_slug_treated_as_missing():
    """Whitespace-only or empty slug is the same as NULL — fail loud."""
    from services.content_router_service import process_content_generation_task

    db = _make_db(template_slug="   ")
    overrides, tmpl_runner, site_config_obj = _patch_externals()

    with _ImportPatchContext(overrides, site_config_obj):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="whitespace-slug",
        )

    tmpl_runner.run.assert_not_awaited()
    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# TemplateRunner raises → task failed, partial state preserved
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_template_runner_exception_marks_task_failed_and_emits_webhook():
    """Any exception out of TemplateRunner.run lands in the error handler:
    audit 'error', update_task(status='failed'), webhook task.failed."""
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    overrides, _runner, site_config_obj = _patch_externals(
        template_raises=RuntimeError("ollama unreachable"),
    )

    with _ImportPatchContext(overrides, site_config_obj) as ctx:
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="boom-task",
        )

    assert result["status"] == "failed"
    assert "ollama unreachable" in result["error"]
    db.update_task.assert_awaited()

    # Webhook fired
    ctx._webhook_mock.assert_awaited()
    webhook_call = ctx._webhook_mock.call_args
    assert webhook_call.args[1] == "task.failed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_failure_during_error_path_does_not_raise():
    """Even if the cleanup update_task itself errors, the dispatcher
    swallows the secondary failure and still returns the failed-state dict."""
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    db.update_task = AsyncMock(side_effect=RuntimeError("DB also down"))

    overrides, _runner, site_config_obj = _patch_externals(
        template_raises=RuntimeError("primary boom"),
    )

    with _ImportPatchContext(overrides, site_config_obj):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="double-fail",
        )

    assert result["status"] == "failed"
    assert "primary boom" in result["error"]


# ---------------------------------------------------------------------------
# Dry-run severity demote (Glad-Labs/poindexter#260)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dry_run_halt_logs_at_info_severity_not_error():
    """When pipeline_dry_run_mode=true AND the failure message matches
    the 'no attempts recorded' / AllModelsFailedError fingerprint, the
    audit_log entry should be severity='info' (event='dry_run_halt'),
    NOT severity='error'. Prevents dry-run noise from drowning real
    error counts on dashboards."""
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    overrides, _runner, site_config_obj = _patch_externals(
        template_raises=RuntimeError(
            "no attempts recorded - AllModelsFailedError",
        ),
    )
    # Configure dry-run mode on. The dispatcher reads via
    # site_config.get("pipeline_dry_run_mode", "")
    site_config_obj.get = MagicMock(
        side_effect=lambda key, default="": "true" if key == "pipeline_dry_run_mode" else default,
    )

    with _ImportPatchContext(overrides, site_config_obj) as ctx:
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="dry-run-task",
        )

    assert result["status"] == "failed"
    audit_calls = ctx._audit_mock.call_args_list
    dry_run_calls = [c for c in audit_calls if c.args and c.args[0] == "dry_run_halt"]
    assert len(dry_run_calls) == 1
    assert dry_run_calls[0].kwargs.get("severity") == "info"


# ---------------------------------------------------------------------------
# Experiment hook failure is swallowed
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_experiment_assignment_failure_is_swallowed():
    """If assign_pipeline_variant raises, the dispatcher continues with
    a no-op assignment dict — never raises."""
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    overrides, _runner, site_config_obj = _patch_externals()
    overrides["services.pipeline_experiment_hook"].assign_pipeline_variant = AsyncMock(
        side_effect=RuntimeError("experiment table missing"),
    )

    with _ImportPatchContext(overrides, site_config_obj):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            database_service=db, task_id="exp-fail",
        )

    # Pipeline still completed despite hook failure
    assert result["status"] == "awaiting_approval"
    assert result["experiment_assignment"]["experiment_key"] is None


# ---------------------------------------------------------------------------
# Models-by-phase + tags pass-through
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_models_by_phase_and_tags_seeded_in_context():
    """models_by_phase + tags both end up on the result dict that gets
    handed to TemplateRunner (and round-tripped back through final_state)."""
    from services.content_router_service import process_content_generation_task

    db = _make_db()
    overrides, tmpl_runner, site_config_obj = _patch_externals()

    with _ImportPatchContext(overrides, site_config_obj):
        result = await process_content_generation_task(
            topic="t", style="s", tone="t", target_length=500,
            tags=["a", "b"],
            models_by_phase={"writer": "qwen2.5:72b"},
            database_service=db, task_id="seed-test",
        )

    # The context passed to TemplateRunner.run contained the seeded values
    args, _kwargs = tmpl_runner.run.call_args
    context = args[1]
    assert context["tags"] == ["a", "b"]
    assert context["models_by_phase"]["writer"] == "qwen2.5:72b"

    # ...and they survive on the returned result (it's the same dict)
    assert result["tags"] == ["a", "b"]

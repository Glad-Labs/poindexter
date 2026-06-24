"""Tests for ``modules.content.stages.generate_video_shot_list``.

The LLM call + DB pool are mocked. Focus: contract behavior at the
stage boundary (input context → output context + audit_log + skip
conditions). The schema-level validation is covered separately in
``tests/unit/schemas/test_video_shot_list.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.stages.generate_video_shot_list import (
    GenerateVideoShotListStage,
    _estimate_short_duration,
    _estimate_target_duration,
    _extract_json_object,
    _reconcile_shot_list,
)
from schemas.video_shot_list import VideoShotList

# ---------------------------------------------------------------------------
# Pure helpers — _estimate_target_duration + _extract_json_object
# ---------------------------------------------------------------------------


def test_estimate_target_duration_empty_script_returns_default() -> None:
    assert _estimate_target_duration("") == 60.0


def test_estimate_target_duration_from_word_count() -> None:
    """~150 words → ~60 seconds at 2.5 wps."""
    script = " ".join(["word"] * 150)
    assert 55.0 <= _estimate_target_duration(script) <= 65.0


def test_estimate_target_duration_clamps_below_20() -> None:
    """A 10-word script doesn't get a 4-second video — clamp up to 20."""
    assert _estimate_target_duration("ten words " * 5) == 20.0


def test_estimate_target_duration_clamps_above_300() -> None:
    """Long scripts get clamped to 5 minutes — renderer practical limit."""
    long_script = " ".join(["word"] * 5000)
    assert _estimate_target_duration(long_script) == 300.0


def test_extract_json_object_strips_code_fence() -> None:
    text = '```json\n{"shots": []}\n```'
    assert _extract_json_object(text) == '{"shots": []}'


def test_extract_json_object_handles_prose_prefix() -> None:
    text = 'Here is the shot list:\n{"version": 1}'
    assert _extract_json_object(text) == '{"version": 1}'


def test_extract_json_object_balances_brackets() -> None:
    """The whole object including nested braces gets returned."""
    text = '{"a": {"b": 1}, "c": 2}'
    assert _extract_json_object(text) == '{"a": {"b": 1}, "c": 2}'


def test_extract_json_object_returns_none_when_no_object() -> None:
    assert _extract_json_object("no json here") is None
    assert _extract_json_object("") is None


# ---------------------------------------------------------------------------
# Stage skip conditions — non-critical, halts_on_failure=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_no_title() -> None:
    stage = GenerateVideoShotListStage()
    result = await stage.execute(
        context={"title": "", "content": "body", "podcast_script": "script"},
        config={},
    )
    assert result.ok
    assert result.metrics["skipped"] is True


@pytest.mark.asyncio
async def test_skips_when_no_podcast_script() -> None:
    """Without the narration script, shot durations can't be aligned."""
    stage = GenerateVideoShotListStage()
    result = await stage.execute(
        context={"title": "t", "content": "c", "podcast_script": ""},
        config={},
    )
    assert result.ok
    assert result.metrics["skipped"] is True


@pytest.mark.asyncio
async def test_skips_when_no_db_pool() -> None:
    """Tests / bootstrap path — no DB → no LLM call. Non-critical."""
    stage = GenerateVideoShotListStage()
    result = await stage.execute(
        context={
            "title": "t", "content": "c", "podcast_script": "script",
            "database_service": None,
        },
        config={},
    )
    assert result.ok


# ---------------------------------------------------------------------------
# Stage happy path + failure modes — LLM stubbed
# ---------------------------------------------------------------------------


def _make_valid_director_output() -> str:
    """JSON output that satisfies the schema validator."""
    return json.dumps({
        "version": 1,
        "total_duration_s": 15.0,
        "shots": [
            {
                "idx": 0, "duration_s": 5.0, "intent": "establish",
                "source": "pexels", "query": "data center",
                "narration_offset_s": 0.0,
            },
            {
                "idx": 1, "duration_s": 5.0, "intent": "abstract",
                "source": "sdxl_kenburns",
                "prompt": "a glass door with data flowing through",
                "narration_offset_s": 5.0,
            },
            {
                "idx": 2, "duration_s": 5.0, "intent": "closer",
                "source": "pexels", "query": "sunset over server farm",
                "narration_offset_s": 10.0,
            },
        ],
        "director_model": "test-model",
        "director_prompt_version": "v1",
        "director_decided_at": "2026-05-28T00:00:00+00:00",
    })


def _make_db_service() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    db = MagicMock()
    db.pool = pool
    return db


def _platform_with_dispatch(
    *, returns=None, raises=None, model: str = "test-model",
) -> MagicMock:
    """A stand-in Platform handle whose ``dispatch.complete`` is an AsyncMock.

    Seam 1 Wave 3d (#667): the stage reaches the LLM router via
    ``context['platform'].dispatch.complete`` instead of importing
    ``dispatch_complete``. Wave 3e (#667): it also resolves the director model
    via ``context['platform'].config.get`` instead of ``site_config``, so the
    handle stubs ``config.get`` to return ``model`` for every key (the stage's
    ``video_director_model or video_scene_model or default_ollama_model``
    chain resolves to it).
    """
    p = MagicMock()
    p.dispatch.complete = AsyncMock(return_value=returns, side_effect=raises)
    p.config.get = MagicMock(return_value=model)
    # get_int returns the caller's default (realistic: the director-timeout
    # setting is unset in these fixtures → cfg.get_int(key, default) → default).
    p.config.get_int = MagicMock(side_effect=lambda key, default=0: default)
    return p


@pytest.mark.asyncio
async def test_happy_path_persists_shot_list_to_context() -> None:
    db_service = _make_db_service()
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1",
        "database_service": db_service,
        "platform": _platform_with_dispatch(
            returns=MagicMock(text=_make_valid_director_output()),
            model="director-model-x",
        ),
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    # #674 fix: shot list is in context_updates, not mutated directly onto context.
    assert "video_shot_list" in result.context_updates
    assert len(result.context_updates["video_shot_list"]["shots"]) == 3
    # Wave 3e (#667): the model is resolved from the capability handle's
    # config, not a context['site_config'] object — pin that seam by asserting
    # dispatch received the model the handle's config returned.
    assert context["platform"].dispatch.complete.call_args.kwargs["model"] == "director-model-x"
    # Audit log got the success event.
    audit_call = db_service.pool.execute.call_args
    assert "video_director.shot_list_produced" in audit_call.args[1]


@pytest.mark.asyncio
async def test_director_timeout_is_configurable() -> None:
    """The per-call LLM ceiling comes from ``video_director_timeout_seconds``,
    not a hardcoded 120s. The writer-grade director model (gemma-4-31B) emits a
    full shot list and the old 120s cap timed out at exactly 120.0s, leaving an
    empty shot list so Stage-2 video never rendered.
    """
    db_service = _make_db_service()
    platform = _platform_with_dispatch(
        returns=MagicMock(text=_make_valid_director_output()),
        model="director-model-x",
    )
    # Operator-tuned director timeout overrides the seeded default.
    platform.config.get_int = MagicMock(return_value=480)
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-timeout",
        "database_service": db_service,
        "platform": platform,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    # The configured 480s — not the old hardcoded 120 — reaches the LLM dispatch.
    assert platform.dispatch.complete.call_args.kwargs["timeout_s"] == 480
    # And it was read from the right setting with a sane, non-120 default.
    platform.config.get_int.assert_any_call("video_director_timeout_seconds", 300)


@pytest.mark.asyncio
async def test_llm_failure_logs_audit_does_not_raise() -> None:
    """LLM dispatch raising must be swallowed — director is non-critical."""
    db_service = _make_db_service()
    context = {
        "title": "t", "content": "c body " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1", "database_service": db_service,
        "platform": _platform_with_dispatch(
            raises=RuntimeError("model unavailable"),
        ),
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok  # halts_on_failure=False
    assert result.metrics.get("failed") is True
    assert "video_shot_list" not in context
    # Audit log got the failure event.
    failure_calls = [
        c for c in db_service.pool.execute.call_args_list
        if "video_director.shot_list_failed" in c.args[1]
    ]
    assert failure_calls


@pytest.mark.asyncio
async def test_invalid_json_output_records_failure() -> None:
    """Director returning prose-only / malformed → failure metric."""
    db_service = _make_db_service()
    context = {
        "title": "t", "content": "c body " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1", "database_service": db_service,
        "platform": _platform_with_dispatch(
            returns=MagicMock(text="I refuse to output JSON."),
        ),
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert result.metrics.get("failed") is True
    assert "video_shot_list" not in context


@pytest.mark.asyncio
async def test_invalid_schema_output_records_failure() -> None:
    """Director returning genuinely-broken JSON → failure recorded.

    The example is an ``sdxl`` shot with no ``prompt`` — a per-shot source
    violation that ``_reconcile_shot_list`` deliberately does NOT repair (it
    never fabricates creative fields). Arithmetic / count slips are now
    reconciled instead (see ``test_stage_recovers_arithmetic_slip_via_reconcile``),
    so this test uses an unrepairable case to keep exercising the failure path.
    """
    bad_output = json.dumps({
        "version": 1,
        "total_duration_s": 5.0,
        "shots": [
            {
                "idx": 0, "duration_s": 5.0, "intent": "x",
                "source": "sdxl",  # requires a non-empty prompt — none given
                "narration_offset_s": 0.0,
            },
        ],
        "director_model": "test",
        "director_prompt_version": "v1",
        "director_decided_at": "2026-05-28T00:00:00+00:00",
    })
    db_service = _make_db_service()
    context = {
        "title": "t", "content": "c body " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1", "database_service": db_service,
        "platform": _platform_with_dispatch(returns=MagicMock(text=bad_output)),
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert result.metrics.get("failed") is True


# ---------------------------------------------------------------------------
# #674 regression: shot list must be returned via context_updates, not direct
# context mutation, so it survives the LangGraph graph_def state merge.
# ---------------------------------------------------------------------------


class _FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _director_json() -> str:
    return json.dumps({
        "version": 1,
        "total_duration_s": 15.0,
        "shots": [
            {"idx": 0, "duration_s": 5.0, "intent": "open", "source": "pexels",
             "query": "data center", "narration_offset_s": 0.0},
            {"idx": 1, "duration_s": 5.0, "intent": "mid", "source": "sdxl_kenburns",
             "prompt": "abstract data flow", "narration_offset_s": 5.0},
            {"idx": 2, "duration_s": 5.0, "intent": "close", "source": "pexels",
             "query": "server racks", "narration_offset_s": 10.0},
        ],
        "director_model": "llama3:latest",
        "director_prompt_version": "v1",
        "director_decided_at": "2026-06-07T00:00:00+00:00",
    })


@pytest.mark.asyncio
async def test_shot_list_returned_via_context_updates():
    platform = MagicMock()
    _cfg = {"site_name": "Test", "video_director_model": "llama3:latest"}
    platform.config.get = MagicMock(side_effect=lambda k, d=None: _cfg.get(k, d))
    platform.config.get_int = MagicMock(side_effect=lambda k, d=0: d)
    platform.dispatch.complete = AsyncMock(return_value=SimpleNamespace(text=_director_json()))

    db = SimpleNamespace(pool=MagicMock())
    ctx = {
        "title": "A Post",
        "content": "Body content that is long enough.",
        "podcast_script": "narration " * 50,
        "task_id": "t-1",
        "database_service": db,
        "platform": platform,
    }

    gpu = SimpleNamespace(lock=lambda *a, **k: _FakeLock())
    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.prompt_manager.get_prompt_manager") as pm, \
         patch("modules.content.stages.generate_video_shot_list._log_audit", new=AsyncMock()):
        pm.return_value.get_prompt.return_value = "director prompt"
        result = await GenerateVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert "video_shot_list" in result.context_updates
    assert result.context_updates["video_shot_list"]["shots"][0]["idx"] == 0
    assert result.context_updates["stages"]["video_shot_list"] is True


# ---------------------------------------------------------------------------
# Short-form director (Plan 3, #517): the stage ALSO produces a purpose-built
# 9:16 short_shot_list from short_summary_script. Best-effort — a short failure
# must not affect the long result or halt the stage.
# ---------------------------------------------------------------------------


def test_estimate_short_duration_empty_returns_20() -> None:
    assert _estimate_short_duration("") == 20.0


def test_estimate_short_duration_clamps_to_15_45() -> None:
    # A 2-word script (0.8s raw) clamps up to the 15s floor.
    assert _estimate_short_duration("two words") == 15.0
    # A 5000-word script clamps down to the 45s ceiling.
    long_script = " ".join(["word"] * 5000)
    assert _estimate_short_duration(long_script) == 45.0


def _make_valid_short_director_output() -> str:
    """JSON output for a 9:16 short that satisfies the schema validator."""
    return json.dumps({
        "version": 1,
        "aspect": "9:16",
        "total_duration_s": 6.0,
        "shots": [
            {
                "idx": 0, "duration_s": 2.0, "intent": "cold-open hook",
                "source": "sdxl_kenburns",
                "prompt": "cyberpunk neon illustration of a glowing server rack",
                "narration_offset_s": 0.0,
            },
            {
                "idx": 1, "duration_s": 4.0, "intent": "concrete payoff",
                "source": "pexels", "query": "circuit board macro close up vertical",
                "narration_offset_s": 2.0,
            },
        ],
        "director_model": "test-model",
        "director_prompt_version": "short_v1",
        "director_decided_at": "2026-06-08T00:00:00+00:00",
    })


@pytest.mark.asyncio
async def test_short_shot_list_produced_when_short_script_present() -> None:
    """short_summary_script present → both long + short shot lists produced.

    The mock dispatch is called TWICE (long then short) via side_effect.
    """
    db_service = _make_db_service()
    platform = _platform_with_dispatch(model="director-model-x")
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text=_make_valid_director_output()),
        MagicMock(text=_make_valid_short_director_output()),
    ])
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "short_summary_script": "short narration " * 10,
        "task_id": "task-1",
        "database_service": db_service,
        "platform": platform,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert platform.dispatch.complete.call_count == 2
    assert "video_shot_list" in result.context_updates
    assert result.context_updates["short_shot_list"]["aspect"] == "9:16"
    assert len(result.context_updates["short_shot_list"]["shots"]) == 2


@pytest.mark.asyncio
async def test_short_skipped_when_no_short_script() -> None:
    """No short_summary_script → short_shot_list absent, long still produced.

    The mock dispatch is called ONCE (long only)."""
    db_service = _make_db_service()
    platform = _platform_with_dispatch(
        returns=MagicMock(text=_make_valid_director_output()),
        model="director-model-x",
    )
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1",
        "database_service": db_service,
        "platform": platform,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert platform.dispatch.complete.call_count == 1
    assert "video_shot_list" in result.context_updates
    assert "short_shot_list" not in result.context_updates


@pytest.mark.asyncio
async def test_short_failure_does_not_break_long() -> None:
    """Short call (2nd dispatch) returns garbage → long still present, short absent."""
    db_service = _make_db_service()
    platform = _platform_with_dispatch(model="director-model-x")
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text=_make_valid_director_output()),
        MagicMock(text="I refuse to output JSON."),
    ])
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "short_summary_script": "short narration " * 10,
        "task_id": "task-1",
        "database_service": db_service,
        "platform": platform,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert "video_shot_list" in result.context_updates
    assert "short_shot_list" not in result.context_updates


# ---------------------------------------------------------------------------
# Model pin — the "auto"/unset sentinel skips the (non-critical) director
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_model_skips_gracefully() -> None:
    """When all config keys return 'auto', the (non-critical) director skips."""
    db_service = _make_db_service()
    platform = MagicMock()
    platform.config.get = MagicMock(return_value="auto")
    platform.dispatch.complete = AsyncMock(
        return_value=MagicMock(text=_make_valid_director_output()),
    )
    context = {
        "title": "t", "content": "c " * 50,
        "podcast_script": "narration " * 40,
        "task_id": "task-auto",
        "database_service": db_service,
        "platform": platform,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))
        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert result.metrics.get("skipped") is True
    # No dispatch when no model is configured.
    platform.dispatch.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_model_skips_gracefully() -> None:
    """When the config returns None, the director skips (ok=True) without crash."""
    db_service = _make_db_service()
    platform = MagicMock()
    platform.config.get = MagicMock(return_value=None)
    platform.dispatch.complete = AsyncMock()
    context = {
        "title": "t", "content": "c " * 50,
        "podcast_script": "narration " * 40,
        "task_id": "task-no-model",
        "database_service": db_service,
        "platform": platform,
    }

    result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert result.metrics.get("skipped") is True
    # Must not have dispatched to the LLM if no model is configured.
    platform.dispatch.complete.assert_not_awaited()


# ---------------------------------------------------------------------------
# _reconcile_shot_list — deterministic repair of the director's mechanical
# slips (total-duration arithmetic, >30 shot cap, non-contiguous idx, same-
# source pacing streaks) BEFORE schema validation. The local LLM reliably
# botches this bookkeeping (audit: 11 shot_list_failed / 0 produced over 10d),
# so every shot list was rejected and Stage-2 video rendering silently
# no-opped on every post. We COMPUTE the bookkeeping rather than reject the
# director's otherwise-usable creative output ("calculated vs generated").
# ---------------------------------------------------------------------------


def _raw_shot(idx: int, source: str, duration: float = 5.0, **extra) -> dict:
    """A director shot dict with the source-appropriate input field set."""
    shot = {
        "idx": idx,
        "duration_s": duration,
        "intent": "establish",
        "source": source,
        "narration_offset_s": float(idx) * duration,
    }
    if source == "pexels":
        shot["query"] = "data center server room"
    elif source in ("sdxl", "sdxl_kenburns", "wan21"):
        shot["prompt"] = "an abstract neon circuit board, faceless"
    shot.update(extra)
    return shot


def _raw_list(shots: list[dict], total: float) -> dict:
    return {
        "version": 1,
        "total_duration_s": total,
        "shots": shots,
        "director_model": "test-model",
        "director_prompt_version": "v1",
        "director_decided_at": "2026-06-11T00:00:00+00:00",
    }


def test_reconcile_sets_total_duration_to_sum_of_shots() -> None:
    """The #1 observed failure: total_duration_s=300 but shots sum to 15."""
    raw = _raw_list(
        [_raw_shot(0, "pexels"), _raw_shot(1, "sdxl"), _raw_shot(2, "pexels")],
        total=300.0,
    )
    fixed = _reconcile_shot_list(raw)
    assert fixed["total_duration_s"] == 15.0
    # Reconciled output must now pass the strict schema.
    VideoShotList.model_validate(fixed)


def test_reconcile_caps_shots_to_30_and_reindexes() -> None:
    """>30 shots (schema max) — keep the first 30, idx 0..29 contiguous."""
    shots = [
        _raw_shot(i, "pexels" if i % 2 == 0 else "sdxl") for i in range(33)
    ]
    fixed = _reconcile_shot_list(_raw_list(shots, total=999.0))
    assert len(fixed["shots"]) == 30
    assert [s["idx"] for s in fixed["shots"]] == list(range(30))
    VideoShotList.model_validate(fixed)


def test_reconcile_reindexes_noncontiguous_idx() -> None:
    """Director skipped/duplicated idx values — renormalize to 0..n-1."""
    shots = [_raw_shot(0, "pexels"), _raw_shot(7, "sdxl"), _raw_shot(99, "pexels")]
    fixed = _reconcile_shot_list(_raw_list(shots, total=15.0))
    assert [s["idx"] for s in fixed["shots"]] == [0, 1, 2]


def test_reconcile_breaks_same_source_streak_with_holdover() -> None:
    """4 consecutive wan21 shots would trip the pacing rule once duration is
    fixed — insert a holdover transition to break the run."""
    shots = [_raw_shot(i, "wan21") for i in range(4)]
    fixed = _reconcile_shot_list(_raw_list(shots, total=20.0))
    # No schema rejection (the pacing model_validator passes).
    VideoShotList.model_validate(fixed)
    assert any(s["source"] == "holdover" for s in fixed["shots"])


def test_reconcile_tolerates_non_numeric_duration() -> None:
    """A non-numeric duration_s must not crash reconcile — it contributes 0.0
    to the recomputed total (the per-shot schema validator rejects the shot
    later). Guards the recovery path that replaced the bare except."""
    shots = [_raw_shot(0, "pexels", duration=5.0), _raw_shot(1, "sdxl")]
    shots[1]["duration_s"] = "not-a-number"
    fixed = _reconcile_shot_list(_raw_list(shots, total=999.0))
    # 5.0 (valid) + 0.0 (the bad one) = 5.0
    assert fixed["total_duration_s"] == 5.0
    assert [s["idx"] for s in fixed["shots"]] == [0, 1]


def test_reconcile_passthrough_when_not_a_dict() -> None:
    assert _reconcile_shot_list("not a dict") == "not a dict"
    assert _reconcile_shot_list(None) is None


def test_reconcile_passthrough_when_shots_empty() -> None:
    """A genuinely empty director output is left untouched so the stage's
    director-failure path still fires (no fabricated success)."""
    assert _reconcile_shot_list({}) == {}
    assert _reconcile_shot_list({"shots": []}) == {"shots": []}


@pytest.mark.asyncio
async def test_stage_recovers_arithmetic_slip_via_reconcile() -> None:
    """End-to-end: a director output with a wrong total AND 31 shots — which
    previously failed validation and produced NO shot list (Stage-2 no-op
    root cause) — now yields a valid 30-shot list via reconcile."""
    shots = []
    for i in range(31):
        src = "pexels" if i % 3 else "sdxl"
        shot = {
            "idx": i, "duration_s": 5.0, "intent": "x", "source": src,
            "narration_offset_s": float(i) * 5,
        }
        shot["query" if src == "pexels" else "prompt"] = "neon faceless circuit"
        shots.append(shot)
    bad_output = json.dumps(_raw_list(shots, total=300.0))  # 155 != 300

    db_service = _make_db_service()
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-recover",
        "database_service": db_service,
        "platform": _platform_with_dispatch(
            returns=MagicMock(text=bad_output), model="director-model-x",
        ),
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert "video_shot_list" in result.context_updates
    assert len(result.context_updates["video_shot_list"]["shots"]) == 30
    # Success audit event, not failure.
    audit_events = [c.args[1] for c in db_service.pool.execute.call_args_list]
    assert any("video_director.shot_list_produced" in e for e in audit_events)

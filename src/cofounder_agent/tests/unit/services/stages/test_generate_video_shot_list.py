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
    _resolve_director_think,
    _tolerant_json_loads,
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


def test_estimate_target_duration_ceiling_is_configurable() -> None:
    """The clamp ceiling threads from video_long_max_seconds (silent-tail fix)
    — one ceiling shared with the narration-script trim, so the plan and the
    voice can't disagree past it."""
    long_script = "word " * 2000
    assert _estimate_target_duration(long_script, max_s=120.0) == 120.0


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


def test_extract_json_object_survives_embedded_triple_backticks() -> None:
    """poindexter#643: the old inline fence regex was non-greedy
    (```(?:json)?\\s*(.*?)\\s*```), so it matched the SHORTEST span
    between the opening fence and the FIRST later ``` — truncating at an
    embedded triple-backtick inside a string value instead of the actual
    closing fence, and losing the rest of the shot list."""
    text = (
        '```json\n'
        '{"shots": [{"note": "wrap code like ```this``` for emphasis"}]}\n'
        '```'
    )
    assert _extract_json_object(text) == (
        '{"shots": [{"note": "wrap code like ```this``` for emphasis"}]}'
    )


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
                "source": "image_kenburns",
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
    # setting is unset in these fixtures → cfg.get_int(key, default) → default),
    # EXCEPT video_director_max_retries → 0 so these legacy fixtures stay
    # single-dispatch and deterministic. Retry behavior has its own tests below.
    p.config.get_int = MagicMock(
        side_effect=lambda key, default=0: (
            0 if key == "video_director_max_retries" else default
        )
    )
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
async def test_long_director_plans_over_video_long_script() -> None:
    """Silent-tail fix (2026-07-31): the long director's script AND target
    duration come from the script the narration will actually voice
    (video_long_script), not the ~2× longer podcast script — the old source
    pinned every plan at the ~300s clamp while the voice ran ~175s, so every
    long video shipped minutes of silent footage."""
    db_service = _make_db_service()
    long_script = "spoken word here " * 100   # 300 words ≈ 120s at 2.5 WPS
    podcast = "podcast filler line " * 400    # 1200 words → would clamp at 300s
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "video_long_script": long_script,
        "podcast_script": podcast,
        "task_id": "task-narr-src",
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
    kw = mock_pm.return_value.get_prompt.call_args_list[0].kwargs
    # The {podcast_script} template var (legacy name, kept for frozen-override
    # backcompat) carries the NARRATION script…
    assert kw["podcast_script"] == long_script.strip()
    # …and the target derives from ITS word count (300 words / 2.5 ≈ 120s),
    # not the podcast's clamped 300s.
    assert float(kw["target_duration_s"]) == pytest.approx(120.0, abs=2.0)


@pytest.mark.asyncio
async def test_long_director_falls_back_to_podcast_script() -> None:
    """Without a video_long_script the podcast script remains the source —
    the same fallback order media.render_narration uses at Stage 2."""
    db_service = _make_db_service()
    podcast = "podcast words spoken " * 50  # 150 words ≈ 60s
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": podcast,
        "task_id": "task-narr-fb",
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
    kw = mock_pm.return_value.get_prompt.call_args_list[0].kwargs
    assert kw["podcast_script"] == podcast
    assert float(kw["target_duration_s"]) == pytest.approx(60.0, abs=2.0)


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
    # The effective per-call ceiling is recorded on the failure row, so a
    # timeout failure shows at a glance whether the DB-configured
    # video_director_timeout_seconds actually reached the dispatch
    # (2026-06-20 stale-worker incident diagnosability).
    failure_details = json.loads(failure_calls[0].args[3])
    assert failure_details["timeout_s"] == 300


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

    The example is an ``image_gen`` shot with no ``prompt`` — a per-shot source
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
                "source": "image_gen",  # requires a non-empty prompt — none given
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
            {"idx": 1, "duration_s": 5.0, "intent": "mid", "source": "image_kenburns",
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


def test_estimate_short_duration_clamps_to_target() -> None:
    # The upper clamp is the operator-tunable video_short_target_seconds (#867),
    # threaded in at the call site — no longer a hardcoded 45.
    # 200 words / 2.5 = 80s → clamps down to the target.
    assert _estimate_short_duration("word " * 200, target_seconds=45.0) == 45.0
    assert _estimate_short_duration("word " * 200, target_seconds=30.0) == 30.0
    # The 15s floor is independent of the target.
    assert _estimate_short_duration("word " * 10, target_seconds=45.0) == 15.0


def _make_valid_short_director_output() -> str:
    """JSON output for a 9:16 short that satisfies the schema validator."""
    return json.dumps({
        "version": 1,
        "aspect": "9:16",
        "total_duration_s": 6.0,
        "shots": [
            {
                "idx": 0, "duration_s": 2.0, "intent": "cold-open hook",
                "source": "image_kenburns",
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
        # 30 words — must clear video_short_min_words (25), the floor that
        # keeps a junk script (e.g. the literal "---") from planning a short.
        "short_summary_script": "short narration " * 15,
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
    elif source in ("image_gen", "image_kenburns", "wan21"):
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
        [_raw_shot(0, "pexels"), _raw_shot(1, "image_gen"), _raw_shot(2, "pexels")],
        total=300.0,
    )
    fixed = _reconcile_shot_list(raw)
    assert fixed["total_duration_s"] == 15.0
    # Reconciled output must now pass the strict schema.
    VideoShotList.model_validate(fixed)


def test_reconcile_caps_shots_to_30_and_reindexes() -> None:
    """>30 shots (schema max) — keep the first 30, idx 0..29 contiguous."""
    shots = [
        _raw_shot(i, "pexels" if i % 2 == 0 else "image_gen") for i in range(33)
    ]
    fixed = _reconcile_shot_list(_raw_list(shots, total=999.0))
    assert len(fixed["shots"]) == 30
    assert [s["idx"] for s in fixed["shots"]] == list(range(30))
    VideoShotList.model_validate(fixed)


def test_reconcile_reindexes_noncontiguous_idx() -> None:
    """Director skipped/duplicated idx values — renormalize to 0..n-1."""
    shots = [_raw_shot(0, "pexels"), _raw_shot(7, "image_gen"), _raw_shot(99, "pexels")]
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
    shots = [_raw_shot(0, "pexels", duration=5.0), _raw_shot(1, "image_gen")]
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


def test_reconcile_clamps_duration_over_30() -> None:
    """The dominant recent reject class (audit 2026-06/07): the director
    holds single shots 30.2–46s, and the schema's ``le=30`` gate rejected
    the whole list. Clamp to the ceiling and recompute the total."""
    shots = [
        _raw_shot(0, "pexels", duration=34.0),
        _raw_shot(1, "image_gen", duration=42.0),
        _raw_shot(2, "pexels", duration=10.0),
    ]
    fixed = _reconcile_shot_list(_raw_list(shots, total=86.0))
    assert [s["duration_s"] for s in fixed["shots"]] == [30.0, 30.0, 10.0]
    assert fixed["total_duration_s"] == 70.0
    VideoShotList.model_validate(fixed)


def test_reconcile_fills_missing_narration_offset() -> None:
    """The #2 recent reject class: individual shots missing the required
    ``narration_offset_s``. Derive it from cumulative prior durations (the
    prompt's own rule); present offsets are never overwritten."""
    shots = [
        _raw_shot(0, "pexels", duration=5.0),
        _raw_shot(1, "image_gen", duration=7.0),
        _raw_shot(2, "pexels", duration=4.0),
    ]
    del shots[1]["narration_offset_s"]
    shots[2]["narration_offset_s"] = 99.0  # present (even if odd) → untouched
    fixed = _reconcile_shot_list(_raw_list(shots, total=16.0))
    assert fixed["shots"][1]["narration_offset_s"] == 5.0  # cumulative prior
    assert fixed["shots"][2]["narration_offset_s"] == 99.0
    VideoShotList.model_validate(fixed)


def test_reconcile_derived_offsets_use_clamped_durations() -> None:
    """Offset derivation runs AFTER the duration clamp, so a derived offset
    reflects the durations the renderer will actually play."""
    shots = [
        _raw_shot(0, "pexels", duration=40.0),  # clamps to 30
        _raw_shot(1, "image_gen", duration=5.0),
    ]
    del shots[1]["narration_offset_s"]
    fixed = _reconcile_shot_list(_raw_list(shots, total=45.0))
    assert fixed["shots"][1]["narration_offset_s"] == 30.0
    VideoShotList.model_validate(fixed)


def test_reconcile_inserted_holdover_gets_derived_offset() -> None:
    """The pacing holdover no longer copies the following shot's (possibly
    absent) offset — it gets the cumulative-duration derivation like any
    other offset-less shot, so it's monotonic mid-list rather than 0.0."""
    shots = [_raw_shot(i, "wan21", duration=5.0) for i in range(4)]
    fixed = _reconcile_shot_list(_raw_list(shots, total=20.0))
    holdovers = [s for s in fixed["shots"] if s["source"] == "holdover"]
    assert holdovers
    # Inserted before the 3rd same-source shot → offset = 2 prior shots × 5s.
    assert holdovers[0]["narration_offset_s"] == 10.0
    VideoShotList.model_validate(fixed)


# ---------------------------------------------------------------------------
# _tolerant_json_loads — deterministic dialect repair before any LLM retry
# (feedback_calculated_vs_generated). Observed prod dialect: unquoted keys
# ("Expecting property name enclosed in double quotes", preview "{ sho…").
# ---------------------------------------------------------------------------


def test_tolerant_loads_strict_json_passthrough() -> None:
    body = '{"shots": [{"idx": 0}], "total_duration_s": 5.0}'
    assert _tolerant_json_loads(body) == {
        "shots": [{"idx": 0}], "total_duration_s": 5.0,
    }


def test_tolerant_loads_repairs_unquoted_keys() -> None:
    """The exact prod dialect: bare object keys."""
    body = '{ shots: [{ idx: 0, duration_s: 5.0, source: "pexels" }], version: 1 }'
    parsed = _tolerant_json_loads(body)
    assert parsed["version"] == 1
    assert parsed["shots"][0]["source"] == "pexels"


def test_tolerant_loads_repairs_trailing_commas_and_python_literals() -> None:
    body = '{ "a": True, "b": None, "c": [1, 2,], }'
    assert _tolerant_json_loads(body) == {"a": True, "b": None, "c": [1, 2]}


def test_tolerant_loads_repairs_single_quotes_and_bare_values() -> None:
    body = "{ source: 'pexels', intent: establish, ok: true }"
    assert _tolerant_json_loads(body) == {
        "source": "pexels", "intent": "establish", "ok": True,
    }


def test_tolerant_loads_never_touches_string_interiors() -> None:
    """Colons/commas/bare words inside a double-quoted string must survive."""
    body = '{ intent: "establish: servers, code on screens", idx: 0 }'
    parsed = _tolerant_json_loads(body)
    assert parsed["intent"] == "establish: servers, code on screens"


def test_tolerant_loads_still_raises_on_garbage() -> None:
    """Unrepairable output flows to the stage's existing failure path."""
    with pytest.raises(json.JSONDecodeError):
        _tolerant_json_loads('{ "shots": [ this is not eve')


@pytest.mark.asyncio
async def test_stage_recovers_unquoted_key_dialect_end_to_end() -> None:
    """End-to-end: the director emits the unquoted-key dialect WITH a >30s
    shot AND a missing narration_offset_s — the three recent reject classes
    at once — and the stage still produces a valid shot list."""
    bad_output = (
        '{ version: 1, total_duration_s: 45.0, shots: ['
        '{ idx: 0, duration_s: 35.0, intent: "open", source: "pexels", '
        'query: "data center", narration_offset_s: 0.0 },'
        '{ idx: 1, duration_s: 5.0, intent: "mid", source: "image_kenburns", '
        'prompt: "flat vector data flow, faceless" },'
        '{ idx: 2, duration_s: 5.0, intent: "close", source: "pexels", '
        'query: "server racks", narration_offset_s: 40.0 }'
        '], director_model: "test-model", director_prompt_version: "v1.3", '
        'director_decided_at: "2026-07-03T00:00:00+00:00" }'
    )
    db_service = _make_db_service()
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-dialect",
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
    shot_list = result.context_updates["video_shot_list"]
    assert shot_list["shots"][0]["duration_s"] == 30.0  # clamped
    assert shot_list["shots"][1]["narration_offset_s"] == 30.0  # derived
    assert shot_list["total_duration_s"] == 40.0  # recomputed post-clamp
    audit_events = [c.args[1] for c in db_service.pool.execute.call_args_list]
    assert any("video_director.shot_list_produced" in e for e in audit_events)


@pytest.mark.asyncio
async def test_stage_recovers_arithmetic_slip_via_reconcile() -> None:
    """End-to-end: a director output with a wrong total AND 31 shots — which
    previously failed validation and produced NO shot list (Stage-2 no-op
    root cause) — now yields a valid 30-shot list via reconcile."""
    shots = []
    for i in range(31):
        src = "pexels" if i % 3 else "image_gen"
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


# ---------------------------------------------------------------------------
# think=False wiring (2026-07-07): the thinking-capable director model
# (gemma-4-31B-it-qat) spends the shared 6144-token output budget in its
# reasoning channel and STARVES the JSON shot list — content comes back empty,
# _extract_json_object finds no object, and the shot list is lost so Stage-2
# video never renders (audit: shot_list_failed phase=json_extract with a
# reasoning-prose preview; the empty-content overflow was reproduced
# in-container). Disabling the channel via video_director_disable_thinking
# (default true) frees the whole budget for the structured output — mirrors the
# writer path fix (writer_disable_thinking, #2163).
# ---------------------------------------------------------------------------


def _cfg_get(mapping: dict) -> MagicMock:
    """A ``config.get`` stub backed by a dict (returns the caller default on miss)."""
    return MagicMock(side_effect=lambda k, d=None: mapping.get(k, d))


def test_resolve_director_think_defaults_to_disabled() -> None:
    """Key unset → the seeded default 'true' → think=False (channel disabled)."""
    cfg = MagicMock()
    cfg.get = _cfg_get({})  # key absent → default "true" reached
    assert _resolve_director_think(cfg) is False


def test_resolve_director_think_true_disables() -> None:
    cfg = MagicMock()
    cfg.get = _cfg_get({"video_director_disable_thinking": "true"})
    assert _resolve_director_think(cfg) is False


def test_resolve_director_think_false_leaves_backend_default() -> None:
    """Operator opt-out → None (don't pin ``think``; leave the backend default)."""
    cfg = MagicMock()
    cfg.get = _cfg_get({"video_director_disable_thinking": "false"})
    assert _resolve_director_think(cfg) is None


def test_resolve_director_think_none_cfg_disables() -> None:
    """No config handle (test/bootstrap) → default to disabling thinking."""
    assert _resolve_director_think(None) is False


def test_resolve_director_think_swallows_cfg_error() -> None:
    """A misbehaving config stub must not crash the director — default False."""
    cfg = MagicMock()
    cfg.get = MagicMock(side_effect=RuntimeError("boom"))
    assert _resolve_director_think(cfg) is False


def _platform_dict_cfg(*, cfg_map: dict, returns) -> MagicMock:
    """Platform handle with a dict-backed ``config.get`` (so per-key values —
    including video_director_disable_thinking — differ, unlike the return-model-
    for-every-key ``_platform_with_dispatch``)."""
    p = MagicMock()
    p.dispatch.complete = AsyncMock(return_value=returns)
    p.config.get = MagicMock(side_effect=lambda k, d=None: cfg_map.get(k, d))
    p.config.get_int = MagicMock(side_effect=lambda k, d=0: d)
    return p


@pytest.mark.asyncio
async def test_director_dispatch_disables_thinking_by_default() -> None:
    """think=False reaches the LLM dispatch when the default-on flag is set —
    the fix for the empty-{} shot list (thinking channel starving the JSON)."""
    db_service = _make_db_service()
    platform = _platform_dict_cfg(
        cfg_map={"video_director_model": "ollama/gemma-4-31B-it-qat:latest"},
        returns=MagicMock(text=_make_valid_director_output()),
    )
    context = {
        "title": "Test Post", "content": "Some content " * 50,
        "podcast_script": "script " * 40, "task_id": "task-think",
        "database_service": db_service, "platform": platform,
    }
    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))
        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert platform.dispatch.complete.call_args.kwargs["think"] is False


@pytest.mark.asyncio
async def test_director_dispatch_omits_think_when_flag_off() -> None:
    """Operator opt-out (video_director_disable_thinking=false) → ``think`` is
    NOT forwarded, leaving the backend default rather than pinning it off."""
    db_service = _make_db_service()
    platform = _platform_dict_cfg(
        cfg_map={
            "video_director_model": "ollama/gemma-4-31B-it-qat:latest",
            "video_director_disable_thinking": "false",
        },
        returns=MagicMock(text=_make_valid_director_output()),
    )
    context = {
        "title": "Test Post", "content": "Some content " * 50,
        "podcast_script": "script " * 40, "task_id": "task-think-off",
        "database_service": db_service, "platform": platform,
    }
    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))
        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert "think" not in platform.dispatch.complete.call_args.kwargs


# ---------------------------------------------------------------------------
# Truncation hardening (2026-07-07 follow-up to #2191): with thinking disabled
# the whole output budget is the JSON, but a verbose director can still
# serialize a long list past the cap and truncate mid-object → no complete
# object → empty shot list → no video (a real 300s post, d1979ebb, hit this).
# max_tokens is DB-tunable with headroom (8192), and a no-JSON extract retries
# a fresh generation before giving up.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_dispatch_uses_configured_max_tokens() -> None:
    """max_tokens reaches the dispatch from video_director_max_tokens (default
    8192, up from the old hardcoded 6144)."""
    db_service = _make_db_service()
    platform = _platform_dict_cfg(
        cfg_map={"video_director_model": "ollama/gemma-4-31B-it-qat:latest"},
        returns=MagicMock(text=_make_valid_director_output()),
    )
    context = {
        "title": "Test Post", "content": "Some content " * 50,
        "podcast_script": "script " * 40, "task_id": "task-maxtok",
        "database_service": db_service, "platform": platform,
    }
    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))
        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert platform.dispatch.complete.call_args.kwargs["max_tokens"] == 8192
    platform.config.get_int.assert_any_call("video_director_max_tokens", 8192)


@pytest.mark.asyncio
async def test_director_retries_on_empty_extract_and_recovers() -> None:
    """A first dispatch with no extractable JSON (the truncation symptom) is
    retried; the fresh generation lands a valid list → shot list produced."""
    db_service = _make_db_service()
    platform = _platform_dict_cfg(
        cfg_map={"video_director_model": "ollama/gemma-4-31B-it-qat:latest"},
        returns=None,
    )
    # 1st: truncated JSON (open brace, never closes → no object). 2nd: valid.
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text='{ "version": 1, "shots": [ {"idx": 0, "duration'),
        MagicMock(text=_make_valid_director_output()),
    ])
    context = {
        "title": "Test Post", "content": "Some content " * 50,
        "podcast_script": "script " * 40, "task_id": "task-retry",
        "database_service": db_service, "platform": platform,
    }
    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))
        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert platform.dispatch.complete.call_count == 2  # retried once
    assert "video_shot_list" in result.context_updates
    assert len(result.context_updates["video_shot_list"]["shots"]) == 3


@pytest.mark.asyncio
async def test_director_fails_after_retries_exhausted() -> None:
    """No JSON on every attempt → failure recorded with the attempts count.
    Default max_retries=1 → 2 dispatches, then give up (non-halting)."""
    db_service = _make_db_service()
    platform = _platform_dict_cfg(
        cfg_map={"video_director_model": "ollama/gemma-4-31B-it-qat:latest"},
        returns=MagicMock(text="{ truncated, never closes"),
    )
    context = {
        "title": "Test Post", "content": "Some content " * 50,
        "podcast_script": "script " * 40, "task_id": "task-exhaust",
        "database_service": db_service, "platform": platform,
    }
    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))
        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok  # non-halting
    assert result.metrics.get("failed") is True
    assert platform.dispatch.complete.call_count == 2  # 1 + 1 retry
    fail_rows = [
        c for c in db_service.pool.execute.call_args_list
        if "video_director.shot_list_failed" in c.args[1]
    ]
    assert fail_rows
    details = json.loads(fail_rows[-1].args[3])
    assert details["phase"] == "json_extract"
    assert details["attempts"] == 2


@pytest.mark.asyncio
async def test_director_dispatch_exception_does_not_retry() -> None:
    """A dispatch *exception* (infra) fails fast — it must NOT burn a retry."""
    db_service = _make_db_service()
    platform = _platform_dict_cfg(
        cfg_map={"video_director_model": "ollama/gemma-4-31B-it-qat:latest"},
        returns=None,
    )
    platform.dispatch.complete = AsyncMock(side_effect=RuntimeError("infra down"))
    context = {
        "title": "Test Post", "content": "Some content " * 50,
        "podcast_script": "script " * 40, "task_id": "task-infra",
        "database_service": db_service, "platform": platform,
    }
    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))
        result = await GenerateVideoShotListStage().execute(context, {})

    assert result.ok
    assert result.metrics.get("failed") is True
    assert platform.dispatch.complete.call_count == 1  # fail fast, no retry


@pytest.mark.asyncio
async def test_short_skipped_when_the_script_is_junk() -> None:
    """A non-empty but unusable short script must not plan a short lane.

    a5594ce1's frozen short script was the literal string "---" (a markdown
    rule). Truthiness let it through: the director planned a 6-shot short, the
    renderer produced a 12s video, and the TTS narrated it as "Null" followed
    by the outro. Held to video_short_min_words, the lane is skipped instead —
    a missing short is invisible, a nonsense short ships (2026-08-09).
    """
    db_service = _make_db_service()
    platform = _platform_with_dispatch(model="director-model-x")
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text=_make_valid_director_output()),
    ])
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "short_summary_script": "---",
        "task_id": "task-junk",
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
    # long lane only — the short director was never dispatched
    assert platform.dispatch.complete.call_count == 1
    assert "video_shot_list" in result.context_updates
    assert "short_shot_list" not in result.context_updates

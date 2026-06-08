# Video Stage-1 Artifact Persistence — Implementation Plan (Plan 1 of the media_pipeline sequence)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the content pipeline's media artifacts (`video_shot_list`, `video_scenes`, `podcast_script`, `short_summary_script`, `video_ambient_audio_path`) actually survive a `graph_def` run and land in the persisted task metadata, and add the `aspect` field to the shot-list schema — the foundation the `media_pipeline` (Plan 2+) reads from.

**Architecture:** On the live `graph_def` path, LangGraph drops any state key that is (a) not declared as a `PipelineState` channel, or (b) written via direct `context[...] =` instead of returned in `StageResult.context_updates`. Both bugs currently discard the media artifacts (audit issue #674). This plan declares the channels, fixes the two stages' returns, persists the one missing key, and locks the behavior with a graph_def regression test. It also adds `VideoShotList.aspect` (16:9 / 9:16) for the long/short render split (#517).

**Tech Stack:** Python 3.13, LangGraph, Pydantic v2, pytest (asyncio_mode=auto), Poetry.

**Spec:** `docs/architecture/video-pipeline-redesign.md` · **Epic:** Glad-Labs/poindexter#689 · **Closes the root of:** #674, groundwork for #517/#679.

---

## File structure (what each task touches)

| File                                                                     | Responsibility                       | Change                                                            |
| ------------------------------------------------------------------------ | ------------------------------------ | ----------------------------------------------------------------- |
| `src/cofounder_agent/services/template_runner.py`                        | `PipelineState` channel declarations | add 5 last-value channels                                         |
| `src/cofounder_agent/modules/content/stages/generate_video_shot_list.py` | director stage                       | return `video_shot_list` via `context_updates` (not direct write) |
| `src/cofounder_agent/modules/content/stages/generate_media_scripts.py`   | media-scripts stage                  | return `video_ambient_audio_path` via `context_updates`           |
| `src/cofounder_agent/modules/content/atoms/content_persist_task.py`      | finalize persist atom                | add `video_ambient_audio_path` to `task_metadata`                 |
| `src/cofounder_agent/schemas/video_shot_list.py`                         | director output contract             | add `VideoShotList.aspect`                                        |
| `tests/integration/test_graphdef_pipeline.py`                            | graph_def regression guard           | new test: media artifacts reach the terminal node                 |
| `tests/unit/services/stages/test_generate_video_shot_list.py`            | director unit test                   | new file: asserts `context_updates["video_shot_list"]`            |
| `tests/unit/services/stages/test_generate_media_scripts.py`              | media-scripts unit test              | new test: asserts `context_updates["video_ambient_audio_path"]`   |
| `tests/unit/services/atoms/test_content_persist_task.py`                 | persist unit test                    | new test: `task_metadata` carries `video_ambient_audio_path`      |
| `tests/unit/schemas/test_video_shot_list.py`                             | schema unit test                     | new tests for `aspect`                                            |

**All pytest commands run from `src/cofounder_agent`** (per the backend pytest harness — the repo-root `pyproject` `--load-dotenv` breaks stray invocations).

---

## Task 1: Channel propagation — declare media-artifact `PipelineState` channels (#674 part 1)

**Files:**

- Test: `src/cofounder_agent/tests/integration/test_graphdef_pipeline.py` (add one test)
- Modify: `src/cofounder_agent/services/template_runner.py:435` (inside `class PipelineState`)

- [ ] **Step 1: Write the failing regression test**

Append this test to `tests/integration/test_graphdef_pipeline.py` (it reuses the file's existing `_make_fake_pool` / `_make_site_config` helpers and mirrors `test_graphdef_run_propagates_content_to_finalize`):

```python
@pytest.mark.asyncio
async def test_graphdef_media_artifacts_survive_to_terminal(monkeypatch):
    """#674 guard: media artifacts returned by the media stages must reach
    the terminal node via LangGraph state channels. Fails if any of the
    five media keys is an undeclared PipelineState channel (LangGraph drops
    undeclared keys on the graph_def path)."""
    from services import atom_registry
    from services.atom_registry import discover
    from services.template_runner import TemplateRunner

    discover()
    seen: dict[str, Any] = {}

    async def _verify_runner(state):
        return {"verified": True}

    async def _media_scripts_runner(state):
        return {
            "podcast_script": "PODCAST",
            "video_scenes": ["scene-a", "scene-b"],
            "short_summary_script": "SHORT",
            "video_ambient_audio_path": "/tmp/ambient.wav",
        }

    async def _shot_list_runner(state):
        return {"video_shot_list": {"version": 1, "shots": [{"idx": 0}]}}

    async def _finalize_runner(state):
        seen["podcast_script"] = state.get("podcast_script")
        seen["video_scenes"] = state.get("video_scenes")
        seen["short_summary_script"] = state.get("short_summary_script")
        seen["video_ambient_audio_path"] = state.get("video_ambient_audio_path")
        seen["video_shot_list"] = state.get("video_shot_list")
        return {"status": "awaiting_approval"}

    runners = dict(atom_registry._RUNNERS)
    runners["stage.verify_task"] = _verify_runner
    runners["stage.generate_media_scripts"] = _media_scripts_runner
    runners["stage.generate_video_shot_list"] = _shot_list_runner
    runners["stage.finalize_task"] = _finalize_runner
    monkeypatch.setattr(atom_registry, "_RUNNERS", runners)

    minimal_spec = {
        "name": "canonical_blog",
        "description": "#674 media-artifact propagation guard",
        "entry": "verify_task",
        "nodes": [
            {"id": "verify_task", "atom": "stage.verify_task"},
            {"id": "generate_media_scripts", "atom": "stage.generate_media_scripts"},
            {"id": "generate_video_shot_list", "atom": "stage.generate_video_shot_list"},
            {"id": "finalize_task", "atom": "stage.finalize_task"},
        ],
        "edges": [
            {"from": "verify_task", "to": "generate_media_scripts"},
            {"from": "generate_media_scripts", "to": "generate_video_shot_list"},
            {"from": "generate_video_shot_list", "to": "finalize_task"},
            {"from": "finalize_task", "to": "END"},
        ],
    }

    async def _fake_load_active_graph_def(_pool, _slug):
        return minimal_spec

    monkeypatch.setattr(
        "services.pipeline_templates.load_active_graph_def",
        _fake_load_active_graph_def,
    )

    runner = TemplateRunner(pool=_make_fake_pool(), site_config=_make_site_config())
    summary = await runner.run("canonical_blog", {"task_id": "t-media-1", "topic": "Testing"})

    assert summary.ok, f"graph_def run halted at {summary.halted_at}"
    assert seen.get("podcast_script") == "PODCAST"
    assert seen.get("video_scenes") == ["scene-a", "scene-b"]
    assert seen.get("short_summary_script") == "SHORT"
    assert seen.get("video_ambient_audio_path") == "/tmp/ambient.wav"
    assert seen.get("video_shot_list") == {"version": 1, "shots": [{"idx": 0}]}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration/test_graphdef_pipeline.py::test_graphdef_media_artifacts_survive_to_terminal -v`
Expected: FAIL — the asserts see `None` (e.g. `assert seen.get("video_shot_list") == ...` fails) because these keys are undeclared `PipelineState` channels and LangGraph drops them.

- [ ] **Step 3: Declare the channels**

In `services/template_runner.py`, inside `class PipelineState(TypedDict, total=False)`, immediately before the `stages: dict` line (currently line 435), insert:

```python
    # Media artifacts (#674): the media stages produce these for the
    # downstream media_pipeline. Declared as last-value channels so they
    # survive LangGraph's graph_def state merge — undeclared keys are
    # silently dropped on the graph_def path (same lesson as
    # seo_keywords_list / research_context above).
    podcast_script: str
    video_scenes: list
    short_summary_script: str
    video_shot_list: dict
    video_ambient_audio_path: str
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration/test_graphdef_pipeline.py::test_graphdef_media_artifacts_survive_to_terminal -v`
Expected: PASS.

- [ ] **Step 5: Verify the canonical spec still compiles**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration/test_graphdef_pipeline.py -q`
Expected: PASS (including `test_canonical_blog_spec_compiles` — declaring these keys only adds seed_keys, it cannot break reachability).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/tests/integration/test_graphdef_pipeline.py
git commit -F - <<'EOF'
fix(pipeline): declare media-artifact PipelineState channels (#674)

video_shot_list / video_scenes / podcast_script / short_summary_script /
video_ambient_audio_path were undeclared channels, so LangGraph dropped
them on the graph_def path and they never reached persist_task. Declare
them as last-value channels + add a graph_def regression guard.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Task 2: `generate_video_shot_list` returns `video_shot_list` via `context_updates` (#674 part 2)

**Files:**

- Create test: `src/cofounder_agent/tests/unit/services/stages/test_generate_video_shot_list.py`
- Modify: `src/cofounder_agent/modules/content/stages/generate_video_shot_list.py:319-348`

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/services/stages/test_generate_video_shot_list.py`:

```python
"""Unit test: the director stage must return its shot list via
StageResult.context_updates, not a direct context mutation (#674)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.stages.generate_video_shot_list import GenerateVideoShotListStage


class _FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _director_json() -> str:
    # A valid VideoShotList payload (2 pexels + 1 sdxl_kenburns, 15s total).
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
    platform.config = {"site_name": "Test", "video_director_model": "llama3:latest"}
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_generate_video_shot_list.py -v`
Expected: FAIL — `KeyError: 'video_shot_list'` (the success `StageResult` at line 341 has no `context_updates`).

- [ ] **Step 3: Return the shot list via `context_updates`**

In `modules/content/stages/generate_video_shot_list.py`, replace the success block. Change lines 319-322 from:

```python
        # Success — write to context + audit_log.
        shot_list_dict = shot_list.model_dump(mode="json")
        context["video_shot_list"] = shot_list_dict
        context.setdefault("stages", {})["video_shot_list"] = True
```

to:

```python
        # Success — return via context_updates so it survives the graph_def
        # state merge (#674: direct context writes are dropped on graph_def).
        shot_list_dict = shot_list.model_dump(mode="json")
        stages = context.setdefault("stages", {})
        stages["video_shot_list"] = True
```

and change the success `return StageResult(...)` at lines 341-348 from:

```python
        return StageResult(
            ok=True,
            detail=f"{len(shot_list.shots)} shots, {shot_list.total_duration_s:.1f}s total",
            metrics={
                "shot_count": len(shot_list.shots),
                "total_duration_s": shot_list.total_duration_s,
            },
        )
```

to:

```python
        return StageResult(
            ok=True,
            detail=f"{len(shot_list.shots)} shots, {shot_list.total_duration_s:.1f}s total",
            context_updates={
                "video_shot_list": shot_list_dict,
                "stages": stages,
            },
            metrics={
                "shot_count": len(shot_list.shots),
                "total_duration_s": shot_list.total_duration_s,
            },
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_generate_video_shot_list.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/stages/generate_video_shot_list.py src/cofounder_agent/tests/unit/services/stages/test_generate_video_shot_list.py
git commit -F - <<'EOF'
fix(video): return video_shot_list via context_updates (#674)

The director wrote video_shot_list directly to context, which LangGraph
drops on the graph_def path. Return it in StageResult.context_updates so
it reaches persist_task.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Task 3: `generate_media_scripts` returns `video_ambient_audio_path` via `context_updates` (#679 groundwork)

**Files:**

- Modify: `src/cofounder_agent/modules/content/stages/generate_media_scripts.py:201-237`
- Test: `src/cofounder_agent/tests/unit/services/stages/test_generate_media_scripts.py` (add one test)

- [ ] **Step 1: Write the failing unit test**

Append to `tests/unit/services/stages/test_generate_media_scripts.py` (reuses the file's existing `_ctx()` and `_fake_lock` helpers; patches the ambient-audio seam):

```python
@pytest.mark.asyncio
async def test_ambient_path_returned_via_context_updates():
    from modules.content.stages.generate_media_scripts import GenerateMediaScriptsStage

    ctx = _ctx()
    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="P" * 600)), \
         patch("modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
               return_value=True), \
         patch("modules.content.stages.generate_media_scripts.generate_audio",
               new=AsyncMock(return_value=SimpleNamespace(file_path="/tmp/ambient.wav"))):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    assert result.context_updates.get("video_ambient_audio_path") == "/tmp/ambient.wav"
```

> NOTE: if `gpu`, `patch`, `AsyncMock`, `SimpleNamespace`, or `_ctx` are not already imported/defined at the top of this existing test file, add the imports used by the file's other tests (`from unittest.mock import AsyncMock, patch`, `from types import SimpleNamespace`) — match what the existing tests in this file already use.

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_generate_media_scripts.py::test_ambient_path_returned_via_context_updates -v`
Expected: FAIL — `context_updates["video_ambient_audio_path"]` is missing (the stage writes it to `context` directly at line 213 and never returns it).

- [ ] **Step 3: Capture and return the ambient path**

In `modules/content/stages/generate_media_scripts.py`, change the ambient block (lines 201-216) so the path is captured in a local, replacing the direct `context[...]` write:

```python
            # Audio gen — ambient video bed via StableAudioOpen.
            ambient_audio_path = ""
            if video_scenes and is_audio_gen_enabled(sc):
                try:
                    ambient_prompt = video_scenes[0][:200] if video_scenes else title
                    ambient_result = await generate_audio(
                        ambient_prompt,
                        "ambient",
                        site_config=sc,
                    )
                    if ambient_result is not None:
                        path = ambient_result.file_path or ""
                        if path:
                            ambient_audio_path = path
                            logger.info("[MEDIA] Video ambient bed: %s", path)
                except Exception as sfx_exc:
                    logger.warning("[MEDIA] audio_gen ambient bed failed: %s", sfx_exc)
```

Then add the key to the success `context_updates` (the dict at lines 229-237) — insert after the `"short_summary_length"` entry:

```python
                    "video_ambient_audio_path": ambient_audio_path,
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_generate_media_scripts.py -v`
Expected: PASS (the new test and the file's existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/stages/generate_media_scripts.py src/cofounder_agent/tests/unit/services/stages/test_generate_media_scripts.py
git commit -F - <<'EOF'
fix(video): return video_ambient_audio_path via context_updates (#679)

The ambient bed path was written to context directly and dropped on the
graph_def path. Return it in context_updates so it can be persisted and
later mixed into the video by the media_pipeline.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Task 4: Persist `video_ambient_audio_path` in `content.persist_task`

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/content_persist_task.py:106-134`
- Create test: `src/cofounder_agent/tests/unit/services/atoms/test_content_persist_task.py`

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/services/atoms/test_content_persist_task.py`:

```python
"""Unit test: content.persist_task writes the media artifacts (incl. the
ambient audio path) into task_metadata."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms.content_persist_task import run as persist_run


@pytest.mark.asyncio
async def test_persist_includes_media_artifacts():
    captured: dict = {}

    async def _update_task(*, task_id, updates):
        captured.update(updates)

    db = SimpleNamespace(
        pool=MagicMock(),
        update_task=_update_task,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )

    state = {
        "task_id": "t-1",
        "content": "body",
        "title": "Title",
        "database_service": db,
        "podcast_script": "POD",
        "video_scenes": ["a"],
        "short_summary_script": "SHORT",
        "video_shot_list": {"version": 1, "shots": [{"idx": 0}]},
        "video_ambient_audio_path": "/tmp/ambient.wav",
    }

    # pipeline_versions + log_revision are best-effort; let them no-op.
    import services.pipeline_db as _pdb
    _pdb.PipelineDB = lambda *_a, **_k: SimpleNamespace(upsert_version=AsyncMock())

    result = await persist_run(state)

    meta = captured["task_metadata"]
    assert meta["podcast_script"] == "POD"
    assert meta["video_shot_list"] == {"version": 1, "shots": [{"idx": 0}]}
    assert meta["video_ambient_audio_path"] == "/tmp/ambient.wav"
    assert result["status"] == "awaiting_approval"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_content_persist_task.py -v`
Expected: FAIL — `KeyError: 'video_ambient_audio_path'` (not in `task_metadata`).

- [ ] **Step 3: Add the key to `task_metadata`**

In `modules/content/atoms/content_persist_task.py`, in the `task_metadata` dict, immediately after line 133 (`"video_shot_list": state.get("video_shot_list"),`) add:

```python
        "video_ambient_audio_path": state.get("video_ambient_audio_path", ""),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_content_persist_task.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/content_persist_task.py src/cofounder_agent/tests/unit/services/atoms/test_content_persist_task.py
git commit -F - <<'EOF'
feat(video): persist video_ambient_audio_path in task_metadata (#679)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Task 5: Add `aspect` to the `VideoShotList` schema (#517 groundwork)

**Files:**

- Modify: `src/cofounder_agent/schemas/video_shot_list.py:163-168`
- Test: `src/cofounder_agent/tests/unit/schemas/test_video_shot_list.py` (add tests; reuse `_valid_shot_list`)

`aspect` belongs on `VideoShotList` (the whole video is one aspect ratio), not per-`Shot`. Default `"16:9"` keeps every existing serialized shot list valid (backcompat).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/schemas/test_video_shot_list.py`:

```python
def test_aspect_defaults_to_16x9() -> None:
    sl = VideoShotList.model_validate(_valid_shot_list())
    assert sl.aspect == "16:9"


def test_aspect_accepts_9x16() -> None:
    sl = VideoShotList.model_validate(_valid_shot_list(aspect="9:16"))
    assert sl.aspect == "9:16"


def test_aspect_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        VideoShotList.model_validate(_valid_shot_list(aspect="4:3"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/schemas/test_video_shot_list.py -k aspect -v`
Expected: FAIL — `AttributeError: 'VideoShotList' object has no attribute 'aspect'` (and the reject test fails because the field doesn't exist yet).

- [ ] **Step 3: Add the field**

In `schemas/video_shot_list.py`, in `class VideoShotList`, immediately after the `version` field (line 163) add:

```python
    aspect: Literal["16:9", "9:16"] = Field(
        "16:9",
        description="Output aspect ratio: 16:9 long-form, 9:16 short-form",
    )
```

(`Literal` and `Field` are already imported at the top of the file.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/schemas/test_video_shot_list.py -v`
Expected: PASS (new `aspect` tests + all existing schema tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/schemas/video_shot_list.py src/cofounder_agent/tests/unit/schemas/test_video_shot_list.py
git commit -F - <<'EOF'
feat(video): add VideoShotList.aspect (16:9/9:16) for long/short split (#517)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Final verification

- [ ] **Run the full affected test surface**

Run:

```bash
cd src/cofounder_agent
poetry run pytest tests/integration/test_graphdef_pipeline.py \
  tests/unit/services/stages/test_generate_video_shot_list.py \
  tests/unit/services/stages/test_generate_media_scripts.py \
  tests/unit/services/atoms/test_content_persist_task.py \
  tests/unit/schemas/test_video_shot_list.py -q
```

Expected: all PASS.

- [ ] **Confirm no behavior change on the legacy `dev_diary` path** — `dev_diary` uses the `TEMPLATES` factory + `stage.finalize_task`, untouched by these changes. Spot-check: `poetry run pytest tests/ -k "dev_diary" -q` (expected: PASS / unchanged).

---

## What this plan deliberately does NOT do (handed to later plans)

- It does **not** generate the new independent `long_script` / `short_script[]` artifacts — that's Plan 2 (new generator stages + DB-configurable prompts).
- It does **not** surface the artifacts onto `posts` (only `pipeline_tasks.task_metadata`) — the `media_pipeline` consumer (Plan 2) decides whether to read from `task_metadata` via the `pipeline_task_id` seam or add a typed `posts` column.
- It does **not** consume the ambient bed or render anything — Plans 3-4.
- No graph_def re-seed migration is needed (the `generate_media_scripts` / `generate_video_shot_list` nodes already exist in the spec; only stage internals + `PipelineState` change).

---

## Self-review (done at authoring time)

- **Spec coverage:** §4 persistence (#674) → Tasks 1-4; §4 `Shot`/`VideoShotList` `aspect` (#517) → Task 5; §5 ambient consumption groundwork (#679) → Tasks 3-4. Render engine / new script generators are explicitly out of scope (later plans). ✓
- **Placeholder scan:** every code/test step shows real code; every run step shows the exact command + expected result. ✓
- **Type consistency:** `video_shot_list` is a `dict` channel (Task 1) and `model_dump(mode="json")` returns a dict (Task 2); `video_ambient_audio_path` is `str` everywhere (Tasks 1/3/4); `aspect` is `Literal["16:9","9:16"]` (Task 5). ✓

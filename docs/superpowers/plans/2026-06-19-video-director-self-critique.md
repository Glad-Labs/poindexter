# Video Director Self-Critique (`review_video_shot_list`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a director self-critique pass that revises the video shot list (coverage, variety, hero-shot selection, on-brand) before Gate 1, run on a writer-grade model.

**Architecture:** A new `stage.review_video_shot_list` graph node is inserted between `generate_video_shot_list` and `capture_training_data` in the `canonical_blog` graph_def. It reads the persisted `video_shot_list` (and `short_shot_list`), runs a critique-and-revise LLM pass via a new `video.review_v1` / `video.review_short_v1` skill prompt, reuses the existing `_reconcile_shot_list` + `VideoShotList` validation, and writes the revised list back via `StageResult.context_updates`. It is **non-halting**: any failure falls back to the unreviewed list so the post is never blocked.

**Tech Stack:** Python 3.13, LangGraph graph_def (`pipeline_architect.build_graph_from_spec`), Pydantic (`VideoShotList`), `UnifiedPromptManager` skill prompts, `platform.dispatch.complete` (#667 capability handle), `gpu_scheduler`.

This is **Piece 1 of 4** from `docs/superpowers/specs/2026-06-19-video-quality-design.md` (§3.1). Pieces 2–4 (vision-QA loop, SFX, hero shots) get their own plans.

## Global Constraints

- **Self-critique uses the SAME model both passes** (spec §3.1, line 102): `video_director_model` is the "director + critique model". Both `generate_video_shot_list` and `review_video_shot_list` read this one key. Bumping it (Task 4) upgrades both passes to writer-grade. (Cross-model writer/reviser per `feedback_iterate_with_qa_not_oneshot` is a deliberately-deferred future refinement, NOT in this plan.)
- **DB-first config; new settings → `services/settings_defaults.py`** (`DEFAULTS` + `METADATA`), applied every boot via `seed_all_defaults`. Never seed settings in migration files.
- **No silent defaults** (`feedback_no_silent_defaults`): best-effort steps that swallow must log at `warning`+ (the silent-except ratchet, `scripts/ci/lint_silent_excepts.py`, fails CI on a net-new `except: pass` / `return <sentinel>` / `logger.debug|info` handler). Every `except` in new code logs at `warning`.
- **Graph-node changes require a reseed migration** with a UTC-timestamp prefix (`YYYYMMDD_HHMMSS_<slug>.py`), copied from `services/migrations/20260618_031620_reseed_canonical_blog_graph_def_qa_rescue_cycle.py`. Editing `CANONICAL_BLOG_GRAPH_DEF` alone does NOT update prod — the live graph lives in the `pipeline_templates` table.
- **Prompts are skill/Langfuse-overridable** (`feedback_prompts_must_be_db_configurable`): the prompt body goes in `skills/content/video-director/SKILL.md` as a `## <key>` section declared in `metadata.prompts`.
- **graph_def state-merge trap (#674):** the stage MUST return output via `StageResult(context_updates={...})`, never a bare `context[...] = …` — LangGraph silently drops direct context writes on the graph_def path.
- **Tests run from the worktree** via the repo-root venv:
  `PYTHONPATH="C:/Users/mattm/glad-labs-website/.claude/worktrees/vigorous-mcnulty-517cc9/src/cofounder_agent" poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python -m pytest "<ABSOLUTE worktree test path>" -q -p no:cacheprovider`
  (Relative paths drift after `cd` and pick up the main-repo copy — always use the absolute worktree path.)

---

### Task 1: Add the review prompts to the video-director skill

**Files:**

- Modify: `src/cofounder_agent/skills/content/video-director/SKILL.md` (frontmatter `metadata.prompts:` list + two new `## <key>` body sections)
- Test: `src/cofounder_agent/tests/unit/services/test_prompt_manager_video_review.py`

**Interfaces:**

- Produces: prompt keys `video.review_v1` and `video.review_short_v1`, resolvable by `UnifiedPromptManager.get_prompt(key, **vars) -> str` (synchronous). Template vars: `{site_name}`, `{title}`, `{content}`, `{podcast_script}` (long) / `{short_script}` (short), `{current_shot_list}` (the existing list as a JSON string), `{model}`, `{now_iso}`. `get_prompt` substitutes only placeholders present in the template and ignores extra/missing kwargs (same contract the director relies on).

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/test_prompt_manager_video_review.py
"""The video.review_v1 / _short_v1 director self-critique prompts resolve
from skills/content/video-director/SKILL.md (Piece 1, video-quality spec §3.1)."""

from __future__ import annotations

import pytest

from services.prompt_manager import get_prompt_manager


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "script_kwarg"),
    [("video.review_v1", "podcast_script"), ("video.review_short_v1", "short_script")],
)
def test_review_prompt_renders_and_substitutes(key: str, script_kwarg: str) -> None:
    pm = get_prompt_manager()
    text = pm.get_prompt(
        key,
        site_name="Glad Labs",
        title="My Title",
        content="Body content.",
        current_shot_list='{"shots": []}',
        model="ollama/gemma-4-31B-it-qat:latest",
        now_iso="2026-06-19T00:00:00Z",
        **{script_kwarg: "the narration script"},
    )
    # Placeholders were substituted (not echoed literally).
    assert "{current_shot_list}" not in text
    assert "{title}" not in text
    # The draft list value was injected, and the revise instruction is present.
    assert '{"shots": []}' in text
    assert "REVISE" in text.upper()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=".../src/cofounder_agent" poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python -m pytest ".../tests/unit/services/test_prompt_manager_video_review.py" -q -p no:cacheprovider`
Expected: FAIL or ERROR — keys `video.review_v1` / `video.review_short_v1` are not registered, so `get_prompt` cannot resolve them.

- [ ] **Step 3: Declare the two prompt keys in the SKILL.md frontmatter**

In `skills/content/video-director/SKILL.md`, the frontmatter `metadata.prompts:` list currently ends at the `video.director_short_v1` entry (line ~19). Append two entries (same shape as the existing ones — `key` / `output_format` / `description`; no per-prompt `category` needed, the pack-level `metadata.category: video` covers it):

```yaml
- key: video.review_v1
  output_format: json
  description: 'Director self-critique — revise the long-form shot list (coverage, variety, hero selection, on-brand)'
- key: video.review_short_v1
  output_format: json
  description: 'Director self-critique — revise the 9:16 short-form shot list for retention'
```

- [ ] **Step 4: Add the two prompt body sections to SKILL.md**

Append to the END of the file. Each section is a `## <key>` heading followed by a fenced block — `_extract_skill_section` (prompt_manager.py:300) captures the first fenced block after the heading. **Use literal TRIPLE backticks** with a `text` info-string, exactly like the existing `## video.director_v1` section. The two sections to add:

`## video.review_v1` — fenced ` ```text ` block containing:

```
You are the senior video director reviewing a JUNIOR director's shot list
for a {site_name} blog post before it goes to a human for approval. Improve it.

POST TITLE: {title}

POST BODY:
{content}

PODCAST SCRIPT (the narration the video plays over):
{podcast_script}

THE DRAFT SHOT LIST you are revising (JSON):
{current_shot_list}

REVISE it against these criteria, then output the REVISED shot list:
1. COVERAGE - every important beat of the narration has a shot that carries
   it; no dead air where the visual stops tracking the script.
2. VARIETY - kill runs of near-identical shots. Vary subject AND source
   (pexels / sdxl_kenburns / sdxl / wan21). Visual monotony is the #1 quality
   killer.
3. HERO SHOTS - pick the 1-3 highest-impact beats (the open's payoff, a key
   reveal, the close) and upgrade them to source "wan21" for real motion. Keep
   wan21 OFF the very first and very last shot. Never exceed 3 wan21 shots.
4. ON-BRAND - sdxl / sdxl_kenburns / wan21 prompts use the dark-techno palette
   (deep navy, cyan, teal, gold accents) and a stylized modifier (flat vector /
   cinematic illustration / isometric 3D / cyberpunk neon / glassmorphism).
   Never photoreal.

CONSTRAINTS (keep the draft valid):
- HUMAN-SUBJECT POLICY unchanged: humans go to source "pexels"; never name a
  human noun in an sdxl / sdxl_kenburns / wan21 prompt, not even as "no people".
- shots idx 0-indexed and contiguous; sum of duration_s equals total_duration_s
  within 0.5s; narration_offset_s equals the cumulative prior durations; never
  more than 2 consecutive shots with the same source.
- Output EXACTLY one JSON object in the same schema as the draft. No prose, no
  code fences.
- Set director_model to "{model}", director_prompt_version to "review_v1",
  director_decided_at to "{now_iso}".

OUTPUT THE REVISED SHOT LIST JSON NOW:
```

`## video.review_short_v1` — fenced ` ```text ` block containing:

```
You are the senior short-form director revising a 9:16 vertical shot list for
a {site_name} post before human approval.

POST TITLE: {title}

SHORT NARRATION (audio over the vertical clip):
{short_script}

THE DRAFT SHOT LIST you are revising (JSON):
{current_shot_list}

REVISE for retention, then output the REVISED list:
1. COLD-OPEN - shot 0 is at most 2.5s and visually arresting; lands the promise
   in the first second. Never "holdover" or "wan21" on the open.
2. PACE - punchy; kill slow holds. 4-8 shots, each 2-6s.
3. VARIETY + HERO - vary source; upgrade at most 1-2 mid-clip beats to "wan21"
   for motion (never the first or last shot; never more than 2 wan21 in a short).
4. ON-BRAND + HUMAN/STYLE POLICY - identical to the long director (dark-techno
   palette, stylized not photoreal, humans go to pexels, no human noun in an AI
   prompt).

CONSTRAINTS: aspect "9:16"; idx contiguous; sum of duration_s equals
total_duration_s within 0.5s; narration_offset_s cumulative; never more than 2
consecutive shots with the same source. Output ONE JSON object in the draft's
schema, no prose or fences. Set director_model to "{model}",
director_prompt_version to "review_short_v1", director_decided_at to "{now_iso}".

OUTPUT THE REVISED SHOT LIST JSON NOW:
```

- [ ] **Step 5: Run the test to verify it passes**

Run: same command as Step 2.
Expected: PASS (2 parametrized cases).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/skills/content/video-director/SKILL.md src/cofounder_agent/tests/unit/services/test_prompt_manager_video_review.py
git commit -m "feat(video): add director self-critique prompts (video.review_v1/_short_v1)"
```

---

### Task 2: Add and register the `ReviewVideoShotListStage`

**Files:**

- Create: `src/cofounder_agent/modules/content/stages/review_video_shot_list.py`
- Modify: `src/cofounder_agent/plugins/registry.py` (add one tuple to the `("stages", …)` `_SAMPLES` entries, next to the `generate_video_shot_list` line ~864)
- Test: `src/cofounder_agent/tests/unit/services/stages/test_review_video_shot_list.py`

**Interfaces:**

- Consumes from context: `video_shot_list` (dict, from `generate_video_shot_list`), `short_shot_list` (dict, optional), `title`, `content`, `podcast_script`, `short_summary_script`, `platform` (capability handle — `.config.get` + `.dispatch.complete`), `database_service` (`.pool`), `task_id`. Reuses module-level `_extract_json_object`, `_reconcile_shot_list`, `_log_audit` from `modules.content.stages.generate_video_shot_list`.
- Produces via `StageResult.context_updates`: `video_shot_list` (revised dict; the ORIGINAL on any failure), optional `short_shot_list` (revised / original), `stages["review_video_shot_list"] = True`.
- Model resolution mirrors the director exactly: `cfg.get("video_director_model") or cfg.get("video_scene_model") or cfg.get("default_ollama_model")`; if unset or `"auto"`, `await resolve_tier_model(pool, "standard")`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/stages/test_review_video_shot_list.py
"""ReviewVideoShotListStage — director self-critique pass (Piece 1, spec §3.1).

Mirrors the dispatch-path test harness from test_generate_video_shot_list.py:
both services.gpu_scheduler.gpu and services.prompt_manager.get_prompt_manager
are patched, because the stage acquires the GPU lock and renders a skill prompt.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _valid_list(*, source1: str = "sdxl_kenburns") -> dict:
    return {
        "version": 1,
        "aspect": "16:9",
        "total_duration_s": 10.0,
        "shots": [
            {"idx": 0, "duration_s": 5.0, "intent": "open", "source": "pexels",
             "query": "server room", "narration_offset_s": 0.0},
            {"idx": 1, "duration_s": 5.0, "intent": "close", "source": source1,
             "prompt": "flat vector circuit, deep navy and cyan, faceless",
             "narration_offset_s": 5.0},
        ],
        "director_model": "draft-model",
        "director_prompt_version": "v1.2",
        "director_decided_at": "2026-06-19T00:00:00+00:00",
    }


def _make_db() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    db = MagicMock()
    db.pool = pool
    return db


def _platform(*, dispatch_text: str | None = None, model: str = "reviewer") -> MagicMock:
    p = MagicMock()
    p.config.get = MagicMock(return_value=model)
    p.dispatch.complete = AsyncMock(return_value=MagicMock(text=dispatch_text))
    return p


@pytest.mark.asyncio
async def test_revised_list_replaces_original() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")  # reviewer promoted a hero shot
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": _platform(dispatch_text=json.dumps(revised)),
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "wan21"


@pytest.mark.asyncio
async def test_failure_keeps_original_non_halting() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    original = _valid_list()  # shot[1].source == "sdxl_kenburns"
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": original,
        "platform": _platform(dispatch_text="I refuse to output JSON."),
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok  # non-halting
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "sdxl_kenburns"


@pytest.mark.asyncio
async def test_skips_when_no_shot_list() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage
    result = await ReviewVideoShotListStage().execute({"task_id": "t"}, {})
    assert result.ok
    assert result.metrics.get("skipped") is True


@pytest.mark.asyncio
async def test_short_list_also_reviewed() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    long_revised = _valid_list(source1="wan21")
    short_revised = _valid_list(source1="wan21")
    short_revised["aspect"] = "9:16"
    platform = _platform()
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text=json.dumps(long_revised)),
        MagicMock(text=json.dumps(short_revised)),
    ])
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "short_summary_script": "short " * 10,
        "video_shot_list": _valid_list(),
        "short_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert platform.dispatch.complete.call_count == 2
    assert result.context_updates["short_shot_list"]["aspect"] == "9:16"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=".../src/cofounder_agent" poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python -m pytest ".../tests/unit/services/stages/test_review_video_shot_list.py" -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.content.stages.review_video_shot_list'`.

- [ ] **Step 3: Implement the stage**

```python
# src/cofounder_agent/modules/content/stages/review_video_shot_list.py
"""ReviewVideoShotListStage — director self-critique of the video shot list.

Runs after ``generate_video_shot_list`` and before ``capture_training_data``
in the ``canonical_blog`` graph. Feeds the director its OWN draft shot list and
asks for a revised one (better coverage / variety / hero-shot selection /
on-brand), validates the result against ``VideoShotList``, and replaces the
draft. Non-halting: any failure keeps the unreviewed list so the post is never
blocked. In Stage 1 the reviewed plan is what the human approves at Gate 1.

Reuses the JSON-extract / reconcile / audit helpers from
``generate_video_shot_list`` so the two director passes can't drift, and
mirrors its exact model-resolution chain (``video_director_model`` is the
shared "director + critique model" key per the video-quality spec §3.1).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from modules.content.stages.generate_video_shot_list import (
    _extract_json_object,
    _log_audit,
    _reconcile_shot_list,
)
from plugins.stage import StageResult
from schemas.video_shot_list import VideoShotList

logger = logging.getLogger(__name__)


class ReviewVideoShotListStage:
    """Pipeline stage: director self-critique + revision of the shot list."""

    name = "review_video_shot_list"
    description = "Director self-critique — revise the shot list before Gate 1"
    # Up to two writer-grade LLM calls (long + short). gemma-4-31B is slow; budget
    # generously. Non-halting, so an overrun just skips the review, never blocks.
    timeout_seconds = 600
    halts_on_failure = False

    async def _resolve_model(self, *, cfg: Any, pool: Any) -> str | None:
        """Mirror generate_video_shot_list's model chain exactly."""
        configured = (
            cfg.get("video_director_model")
            or cfg.get("video_scene_model")
            or cfg.get("default_ollama_model")
        )
        if configured and configured != "auto":
            return configured
        from services.llm_providers.dispatcher import resolve_tier_model
        try:
            return await resolve_tier_model(pool, "standard")
        except Exception as exc:
            logger.warning(
                "[VIDEO_REVIEW] resolve_tier_model failed (%s) — review skipped", exc,
            )
            return None

    async def _review_one(
        self,
        *,
        platform: Any,
        pool: Any,
        model: str,
        prompt_key: str,
        script_var: str,
        script: str,
        current: dict[str, Any],
        title: str,
        content_text: str,
        site_name: str,
        task_id: str | None,
        now_iso: str,
    ) -> dict[str, Any] | None:
        """Render the review prompt, dispatch, validate. ``None`` on any failure."""
        from services.gpu_scheduler import gpu
        from services.prompt_manager import get_prompt_manager

        try:
            pm = get_prompt_manager()
            rendered = pm.get_prompt(
                prompt_key,
                title=title,
                content=content_text,
                current_shot_list=json.dumps(current),
                model=model,
                now_iso=now_iso,
                site_name=site_name,
                **{script_var: script},
            )
        except Exception as exc:
            logger.warning("[VIDEO_REVIEW] prompt render failed (%s, %s)", exc, prompt_key)
            return None

        try:
            async with gpu.lock("ollama", model=model, task_id=task_id, phase="video_review"):
                result = await platform.dispatch.complete(
                    pool=pool,
                    messages=[{"role": "user", "content": rendered}],
                    model=model,
                    tier="standard",
                    timeout_s=120,
                    temperature=0.4,
                    max_tokens=6144,
                )
            output = (getattr(result, "text", "") or "").strip()
        except Exception as exc:
            logger.warning("[VIDEO_REVIEW] dispatch failed (%s, %s)", exc, prompt_key)
            return None

        body = _extract_json_object(output)
        if not body:
            logger.warning("[VIDEO_REVIEW] no JSON in review output (%s)", prompt_key)
            return None
        try:
            parsed = _reconcile_shot_list(json.loads(body))
            revised = VideoShotList.model_validate(parsed)
        except Exception as exc:
            logger.warning("[VIDEO_REVIEW] revised list invalid (%s): %s", prompt_key, exc)
            return None

        await _log_audit(
            pool,
            event_type="video_director.shot_list_reviewed",
            task_id=task_id,
            details={
                "prompt_key": prompt_key,
                "shot_count": len(revised.shots),
                "sources": [s.source for s in revised.shots],
            },
        )
        return revised.model_dump(mode="json")

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        current = context.get("video_shot_list")
        if not current:
            return StageResult(
                ok=True,
                detail="no shot list to review",
                metrics={"skipped": True},
            )

        platform = context.get("platform")
        database_service = context.get("database_service")
        pool = getattr(database_service, "pool", None) if database_service else None
        if platform is None or pool is None:
            return StageResult(
                ok=True,
                detail="no platform / pool in context — review skipped",
                metrics={"skipped": True},
            )

        cfg = platform.config
        model = await self._resolve_model(cfg=cfg, pool=pool)
        if not model:
            return StageResult(
                ok=True,
                detail="model resolution failed — review skipped",
                metrics={"skipped": True},
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        site_name = cfg.get("site_name") or ""
        title = context.get("title", "")
        content_text = context.get("content", "")
        task_id = context.get("task_id")

        # LONG. Non-halting: fall back to the unreviewed list on any failure.
        revised = await self._review_one(
            platform=platform, pool=pool, model=model,
            prompt_key="video.review_v1", script_var="podcast_script",
            script=context.get("podcast_script", ""), current=current,
            title=title, content_text=content_text, site_name=site_name,
            task_id=task_id, now_iso=now_iso,
        )
        updates: dict[str, Any] = {
            "video_shot_list": revised if revised is not None else current,
        }

        # SHORT (best-effort, only when present).
        short = context.get("short_shot_list")
        if short:
            revised_short = await self._review_one(
                platform=platform, pool=pool, model=model,
                prompt_key="video.review_short_v1", script_var="short_script",
                script=context.get("short_summary_script", ""), current=short,
                title=title, content_text=content_text, site_name=site_name,
                task_id=task_id, now_iso=now_iso,
            )
            updates["short_shot_list"] = revised_short if revised_short is not None else short

        stages = context.setdefault("stages", {})
        stages["review_video_shot_list"] = True
        updates["stages"] = stages

        return StageResult(
            ok=True,
            detail="reviewed" if revised is not None else "review fell back to draft",
            context_updates=updates,
            metrics={"reviewed": revised is not None},
        )
```

- [ ] **Step 4: Register the stage**

In `src/cofounder_agent/plugins/registry.py`, directly after the `GenerateVideoShotListStage` tuple (~line 864), add:

```python
        ("stages", "modules.content.stages.review_video_shot_list", "ReviewVideoShotListStage"),
```

- [ ] **Step 5: Run the test to verify it passes**

Run: same command as Step 2.
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/stages/review_video_shot_list.py src/cofounder_agent/plugins/registry.py src/cofounder_agent/tests/unit/services/stages/test_review_video_shot_list.py
git commit -m "feat(video): ReviewVideoShotListStage — director self-critique pass"
```

---

### Task 3: Wire the node into the canonical_blog graph + reseed migration

**Files:**

- Modify: `src/cofounder_agent/services/canonical_blog_spec.py` (`CANONICAL_BLOG_GRAPH_DEF` — add 1 node, replace 1 edge with 2)
- Create: `src/cofounder_agent/services/migrations/<UTC>_reseed_canonical_blog_graph_def_v6_director_review.py`
- Test: `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py` (append a method to `TestCanonicalBlogSpec`)

**Interfaces:**

- Consumes: stage `stage.review_video_shot_list` (Task 2, registered). Produces: a `canonical_blog` graph where `generate_video_shot_list → review_video_shot_list → capture_training_data`. Node count goes 37 → 38.

- [ ] **Step 1: Write the failing test**

Append to the `TestCanonicalBlogSpec` class in `test_canonical_blog_spec.py`:

```python
    def test_review_video_shot_list_node_between_director_and_training(self):
        spec = CANONICAL_BLOG_GRAPH_DEF
        node_atoms = {n["atom"] for n in spec["nodes"]}
        assert "stage.review_video_shot_list" in node_atoms
        edges = {(e["from"], e["to"]) for e in spec["edges"]}
        assert ("generate_video_shot_list", "review_video_shot_list") in edges
        assert ("review_video_shot_list", "capture_training_data") in edges
        # The old direct edge is gone — review sits in between now.
        assert ("generate_video_shot_list", "capture_training_data") not in edges
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=".../src/cofounder_agent" poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python -m pytest ".../tests/unit/services/test_canonical_blog_spec.py::TestCanonicalBlogSpec::test_review_video_shot_list_node_between_director_and_training" -q -p no:cacheprovider`
Expected: FAIL — node/edges absent.

- [ ] **Step 3: Edit `CANONICAL_BLOG_GRAPH_DEF`**

In `services/canonical_blog_spec.py`, after the `generate_video_shot_list` node (line ~128):

```python
        {"id": "generate_video_shot_list", "atom": "stage.generate_video_shot_list"},
        {"id": "review_video_shot_list", "atom": "stage.review_video_shot_list"},
        {"id": "capture_training_data", "atom": "stage.capture_training_data"},
```

Then in the edges block (line ~177), replace the single edge

```python
        {"from": "generate_video_shot_list", "to": "capture_training_data"},
```

with the two edges:

```python
        {"from": "generate_video_shot_list", "to": "review_video_shot_list"},
        {"from": "review_video_shot_list", "to": "capture_training_data"},
```

- [ ] **Step 4: Bump any exact node-count assertions**

The reseed-migration docstrings and `test_canonical_blog_spec.py` reference a node count. Update `37` → `38` only where it counts `CANONICAL_BLOG_GRAPH_DEF` nodes:

Run: `PYTHONPATH=".../src/cofounder_agent" poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python -m pytest ".../tests/unit/services/test_canonical_blog_spec.py" -q -p no:cacheprovider`
If a test asserts `len(spec["nodes"]) == 37`, change it to `38`. If no such assertion exists, no edit is needed (the subset-style `test_shape` does not count nodes).

- [ ] **Step 5: Generate + fill the reseed migration**

```bash
python scripts/new-migration.py "reseed canonical blog graph def v6 director review"
```

Replace the generated file's body with this (copied from `20260618_031620_reseed_canonical_blog_graph_def_qa_rescue_cycle.py`, docstring updated):

```python
"""Migration: reseed canonical_blog graph_def — add the director self-critique node.

Inserts the review_video_shot_list node (stage.review_video_shot_list) between
generate_video_shot_list and capture_training_data so the director critiques and
revises its own shot list before Gate 1 (video-quality spec §3.1, Piece 1). The
graph_def source of truth is services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF
(now 38 nodes); this migration writes json.dumps(that) into the active
canonical_blog pipeline_templates row.

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF  # noqa: E402


async def up(pool) -> None:
    graph_def_json = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def  = $1::jsonb,
                   updated_at = NOW()
             WHERE slug   = 'canonical_blog'
               AND active = true
            """,
            graph_def_json,
        )
    logger.info(
        "Migration reseed_canonical_blog_graph_def_v6_director_review up: "
        "added review_video_shot_list node (38 nodes). result=%s",
        result,
    )


async def down(pool) -> None:
    logger.warning(
        "Migration reseed_canonical_blog_graph_def_v6_director_review down: "
        "no-op — re-apply the previous graph_def seed migration to revert."
    )
```

- [ ] **Step 6: Run the spec tests + migrations smoke**

Run: `PYTHONPATH=".../src/cofounder_agent" poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python -m pytest ".../tests/unit/services/test_canonical_blog_spec.py" -q -p no:cacheprovider`
Expected: PASS — including the existing graph-compile test (proves `stage.review_video_shot_list` resolves and the graph still compiles with reachable `requires`/`produces`).
Run: `poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python scripts/ci/migrations_smoke.py`
Expected: applies cleanly (exit 0).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/canonical_blog_spec.py "src/cofounder_agent/services/migrations/"*reseed_canonical_blog_graph_def_v6_director_review.py src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py
git commit -m "feat(video): wire review_video_shot_list into canonical_blog (v6 reseed)"
```

---

### Task 4: Point the director at a writer-grade model

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (`DEFAULTS` + `METADATA`)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py` (extend)

**Interfaces:** Consumes: the `video_director_model` key that BOTH `generate_video_shot_list` and `review_video_shot_list` read. Produces: a default that resolves to the writer model instead of the weak `standard` tier. **This single bump upgrades both the draft pass and the critique pass** (shared key per spec §3.1) — intended.

- [ ] **Step 1: Write the failing test**

Add to `test_settings_defaults.py`:

```python
@pytest.mark.unit
def test_video_director_model_matches_writer():
    from services.settings_defaults import DEFAULTS, METADATA
    # Self-critique runs on the writer model, not the standard tier (spec §3.1).
    assert DEFAULTS["video_director_model"] == DEFAULTS["pipeline_writer_model"]
    # Model keys carry an owner/value_type METADATA entry, like every other *_model key.
    assert METADATA["video_director_model"]["value_type"] == "model"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=".../src/cofounder_agent" poetry -C "C:/Users/mattm/glad-labs-website/src/cofounder_agent" run python -m pytest ".../tests/unit/services/test_settings_defaults.py::test_video_director_model_matches_writer" -q -p no:cacheprovider`
Expected: FAIL — `KeyError: 'video_director_model'` (the key is not currently in `DEFAULTS`; today the director's `or`-chain falls through to `default_ollama_model: 'auto'` → standard tier).

- [ ] **Step 3: Add the default + metadata**

In `settings_defaults.py`, in `DEFAULTS`, next to `'pipeline_writer_model'` (line ~122), add — **using the same literal value `pipeline_writer_model` currently holds** (verify it is still `'ollama/gemma-4-31B-it-qat:latest'`; copy whatever it is so the two stay equal):

```python
    # Video director + self-critique run on the writer model — scene judgment is
    # the top video-quality lever (video-quality spec §3.1). Shared by both the
    # generate_video_shot_list draft pass and the review_video_shot_list critique.
    'video_director_model': 'ollama/gemma-4-31B-it-qat:latest',
```

In `METADATA` (line ~1008, beside the other `*_model` entries), add:

```python
    'video_director_model': {'owner': 'video_director', 'value_type': 'model'},
```

- [ ] **Step 4: Run the test to verify it passes**

Run: same command as Step 2. Expected: PASS.

- [ ] **Step 5: Note the prod backfill (manual, post-merge)**

`seed_all_defaults` inserts `DEFAULTS` with `ON CONFLICT (key) DO NOTHING`, so a value-bearing prod row is not overwritten — but `video_director_model` does not exist in prod today (unset → standard tier), so the seed WILL take on next boot. No manual step is strictly required. To make it live before the next worker restart: `poindexter settings set video_director_model "ollama/gemma-4-31B-it-qat:latest"`.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(video): default the director + critique model to writer-grade"
```

---

## Self-Review

- **Spec §3.1 coverage:** distinct graph node ✓ (Task 3 — captured independently in `atom_runs`/audit); writer-grade model ✓ (Task 4 — bumps the shared `video_director_model`, matching spec line 102 "director + critique model"); same `VideoShotList` schema re-validated + re-reconciled ✓ (Task 2 imports `_reconcile_shot_list` + `VideoShotList.model_validate`); non-halting fallback to the unreviewed list ✓ (Task 2 `test_failure_keeps_original_non_halting`); revise-once ✓ (single pass per list, no loop); Gate-1 placement ✓ (node sits before `capture_training_data`, still inside Stage 1). Hero selection rides the EXISTING `wan21` source — no `Shot`/schema change in this piece (confirmed `wan21 ∈ ShotSource`).
- **Type/interface consistency (verified against source):** `StageResult(ok, detail, context_updates, metrics)` matches `plugins/stage.py:45`; the Stage entry point is `async execute(self, context, config)` matching `plugins/stage.py:74` and `GenerateVideoShotListStage.execute`; `_extract_json_object` / `_reconcile_shot_list` / `_log_audit(pool, *, event_type, task_id, details, severity="info")` signatures match `generate_video_shot_list.py`; the dispatch call shape (`platform.dispatch.complete(pool=, messages=, model=, tier=, timeout_s=, temperature=, max_tokens=)`) + `gpu.lock("ollama", model=, task_id=, phase=)` mirror the director; model chain (`video_director_model → video_scene_model → default_ollama_model → resolve_tier_model("standard")`) mirrors `generate_video_shot_list.execute`.
- **Test harness fidelity:** Task 2 tests patch BOTH `services.gpu_scheduler.gpu` (via `_FakeLock`) and `services.prompt_manager.get_prompt_manager`, and use `@pytest.mark.asyncio` — exactly the pattern in `test_generate_video_shot_list.py` (the repo's `asyncio_mode` is not "auto"). Task 1's prompt-shape (`metadata.prompts` entry + `## <key>` ` ```text ` fence) matches `_extract_skill_section` (prompt_manager.py:300) and the existing `video.director_v1` entry.
- **Placeholder scan:** none — every step has runnable code/commands. The two "copy this verbatim" references (Task 3 migration body, Task 4 model literal) point at exact existing source, not invented content.
- **Out of scope (deferred to later pieces, correctly):** vision-QA render-check loop (Piece 2), SFX cue layer (Piece 3), the Wan 2.2 weight/renderer wiring (Piece 4 — this piece only has the director _select_ `wan21`; making that source render via Wan 2.2 is Piece 4). Cross-model reviewer (vs. self-critique) is a noted future refinement, not in scope.

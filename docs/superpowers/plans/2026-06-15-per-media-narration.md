# Per-media-type narration (script + CTA + audio) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give long and short videos their own narration audio (each from its own script + CTA), fixing the silent-video bug, and prove it end-to-end including the first-ever short-video render.

**Architecture:** Stage-1 gains a distinct `video_long_script`. Stage-2's `media_pipeline` gains a `media.render_narration` node that regenerates long+short narration audio (own CTA) from the durable scripts; `transcribe_narration` / `qa_audio` / the two video renders are re-pointed from the shared `podcast_audio_path` to per-lane narration + caption channels. A shared `_narration_render.py` helper backs both the new atom and `podcast.render`.

**Tech Stack:** Python 3.12, asyncio, LangGraph graph_def pipeline, asyncpg, pytest. Local Ollama (LLM) + Kokoro/Speaches (TTS) + ffmpeg.

**Spec:** `docs/superpowers/specs/2026-06-15-per-media-narration-design.md`

**Run tests from:** `src/cofounder_agent` (so `pytest` picks up the package). All `pytest` commands below assume that cwd.

---

## File Structure

**Create:**

- `src/cofounder_agent/modules/content/atoms/_narration_render.py` — shared CTA-append + TTS helper (underscore → not an atom)
- `src/cofounder_agent/modules/content/atoms/media_render_narration.py` — `media.render_narration` atom (long+short narration)
- `src/cofounder_agent/services/migrations/<ts>_reseed_media_pipeline_narration_node.py` — graph_def reseed
- `src/cofounder_agent/tests/unit/services/atoms/test_narration_render.py` — helper tests
- `src/cofounder_agent/tests/unit/services/atoms/test_media_render_narration.py` — atom tests

**Modify:**

- `src/cofounder_agent/modules/content/stages/generate_media_scripts.py` — generate `video_long_script`
- `src/cofounder_agent/modules/content/task_metadata.py` — persist `video_long_script`
- `src/cofounder_agent/services/template_runner.py` — 5 new `PipelineState` channels
- `src/cofounder_agent/modules/content/atoms/media_load_scripts.py` — load `video_long_script`
- `src/cofounder_agent/modules/content/atoms/_media_render.py` — `narration_key` + `caption_key` params
- `src/cofounder_agent/modules/content/atoms/media_render_long_video.py` — pass long keys
- `src/cofounder_agent/modules/content/atoms/media_render_short_video.py` — pass short keys
- `src/cofounder_agent/modules/content/atoms/media_transcribe_narration.py` — per-lane ASR
- `src/cofounder_agent/modules/content/atoms/qa_audio.py` — dual-track QA
- `src/cofounder_agent/modules/content/atoms/podcast_render.py` — delegate to helper
- `src/cofounder_agent/services/media_pipeline_spec.py` — add `render_narration` node
- `src/cofounder_agent/tests/unit/services/stages/test_task_metadata_parity_693.py` — add key
- `docs/architecture/podcast-pipeline-stage3.md`, `docs/architecture/video-pipeline-redesign.md`

**Open follow-up (flagged, not in this plan):** the media prompts in `generate_media_scripts.py` are inline (`_build_scene_prompt`, and the new `_build_video_narration_prompt`). The whole file's prompts should migrate to `UnifiedPromptManager` (per `feedback_prompts_must_be_db_configurable`) as one consistent pass — out of scope here to avoid half-migrating a single prompt.

---

## Task A: Stage-1 generates `video_long_script`

**Files:**

- Modify: `src/cofounder_agent/modules/content/stages/generate_media_scripts.py`
- Modify: `src/cofounder_agent/modules/content/task_metadata.py:102-116`
- Modify: `src/cofounder_agent/tests/unit/services/stages/test_task_metadata_parity_693.py`
- Test: `src/cofounder_agent/tests/unit/services/stages/test_generate_media_scripts.py`

- [ ] **Step 1: Write the failing test** (append to `test_generate_media_scripts.py`)

```python
@pytest.mark.asyncio
async def test_video_long_script_emitted_via_context_updates(monkeypatch):
    """generate_media_scripts emits a distinct video_long_script."""
    from modules.content.stages import generate_media_scripts as gms

    # platform.dispatch.complete returns canned text per call; the long-video
    # narration call is the one whose prompt contains "voiceover narration".
    class _Result:
        def __init__(self, text): self.text = text

    async def _complete(*, messages, **kw):
        prompt = messages[0]["content"]
        if "voiceover narration" in prompt:
            return _Result("On screen we see the new GPU. Here is why it matters.")
        return _Result("1. a cinematic shot\n\nSHORT: quick hook here")

    class _Dispatch:  # noqa: D401
        complete = staticmethod(_complete)

    class _Platform:
        dispatch = _Dispatch()
        config = {"site_name": "Glad Labs"}

    # _build_script_with_llm (podcast) + normalize are imported inside execute;
    # stub them to keep the test to the video-narration path.
    monkeypatch.setattr(
        "services.podcast_service._build_script_with_llm",
        lambda *a, **k: _async_return("Podcast script body that is long enough." * 10),
    )

    result = await gms.GenerateMediaScriptsStage().execute(
        context={
            "title": "New GPU", "content": "Body text " * 200,
            "platform": _Platform(),
            "database_service": _FakeDBService(),  # exposes .pool truthy
            "site_config": _FakeSiteConfig({"podcast_tts_enabled": "false"}),
        },
        config={},
    )

    assert result.context_updates.get("video_long_script", "").strip() != ""
    assert "screen" in result.context_updates["video_long_script"].lower()
```

> If `_async_return` / `_FakeDBService` / `_FakeSiteConfig` helpers don't already exist in this test module, copy the equivalents already used by the sibling tests in the file (it has fakes for the pool + site_config — reuse them rather than inventing new ones).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/stages/test_generate_media_scripts.py::test_video_long_script_emitted_via_context_updates -v`
Expected: FAIL (`video_long_script` is `""` / KeyError — not generated yet).

- [ ] **Step 3: Add the prompt builder** (in `generate_media_scripts.py`, next to `_build_scene_prompt`)

```python
def _build_video_narration_prompt(title: str, clean_content: str, site_name: str) -> str:
    """Prompt for the long-form VIDEO narration script — distinct from the
    podcast script (paced to on-screen visuals, no audio-only filler).
    Length is left to the work (no pinned word count); CTA is appended later."""
    return (
        "Write a voiceover narration script for a long-form video about the "
        "article below. It is spoken over on-screen visuals (b-roll, diagrams, "
        "stock footage), so:\n"
        "- Write for the ear, but assume the viewer also sees supporting imagery "
        "— reference what's shown where it's natural ('here we see…', 'on "
        "screen…').\n"
        "- Tighter and more visual than an audio-only podcast; no 'welcome back' "
        "radio filler.\n"
        "- Open with a brief hook, walk the key points in order, then a natural "
        "closing line. Do NOT add a like/subscribe call-to-action — that's "
        "appended separately.\n"
        "- Plain prose. No headings, no bracketed stage directions.\n\n"
        f"TITLE: {title}\n\n"
        f"ARTICLE:\n{clean_content[:3500]}\n\n"
        "NARRATION:"
    )
```

- [ ] **Step 4: Generate the script in `execute()`**

Near the other declarations (after `podcast_intro_audio_path = ""`), add:

```python
        video_long_script = ""
```

After the podcast intro-sting block and before the `# Call 2: Video scenes` block, add (it reuses the already-resolved `model` + the `platform`/`pool` guard the scene call uses):

```python
            # Long-form VIDEO narration script (poindexter#689) — distinct from
            # the podcast script, paced to on-screen visuals; its CTA is appended
            # at render time. Guarded + fail-soft like the scene call below.
            if pool is not None and platform is not None:
                try:
                    async with gpu.lock(
                        "ollama", model=model,
                        task_id=context.get("task_id"), phase="media_scripts",
                    ):
                        vn_result = await platform.dispatch.complete(
                            pool=pool,
                            messages=[{"role": "user", "content": _build_video_narration_prompt(
                                title, clean_content,
                                sc.get("site_name", "our site") if sc is not None else "our site",
                            )}],
                            model=model,
                            tier="standard",
                            timeout_s=120,
                            temperature=0.6,
                            max_tokens=2048,
                        )
                    vn_text = (getattr(vn_result, "text", "") or "").strip()
                    video_long_script = (
                        _normalize_for_speech(vn_text, site_config=sc) if vn_text else ""
                    )
                    if video_long_script:
                        logger.info("[MEDIA] Video narration script: %d chars", len(video_long_script))
                except Exception as vn_exc:
                    logger.warning("[MEDIA] video narration script failed: %s", vn_exc)
```

Add `"video_long_script": video_long_script,` to **both** `context_updates` dicts (the success return ~line 267 and the `except` return ~line 300), so a later scene-parse failure still preserves it.

- [ ] **Step 5: Persist it** — in `task_metadata.py`, add to the returned dict (next to `"podcast_script"`):

```python
        "video_long_script": state.get("video_long_script", ""),
```

- [ ] **Step 6: Update the parity test** — in `test_task_metadata_parity_693.py`, add `"video_long_script"` to the expected-keys set/list and add `"video_long_script": "vo script"` to the state fixture it builds.

- [ ] **Step 7: Run tests**

Run: `poetry run pytest tests/unit/services/stages/test_generate_media_scripts.py tests/unit/services/stages/test_task_metadata_parity_693.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/modules/content/stages/generate_media_scripts.py src/cofounder_agent/modules/content/task_metadata.py src/cofounder_agent/tests/unit/services/stages/test_generate_media_scripts.py src/cofounder_agent/tests/unit/services/stages/test_task_metadata_parity_693.py
git commit -m "feat(media): generate distinct video_long_script in Stage-1 (#689)"
```

---

## Task B: PipelineState channels + load `video_long_script`

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py:447-474`
- Modify: `src/cofounder_agent/modules/content/atoms/media_load_scripts.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_media_load_scripts.py`

- [ ] **Step 1: Add the 5 channels** to the `PipelineState` TypedDict (after `short_video_path` ~line 466):

```python
    # Per-media narration (poindexter#689): each video lane renders its own
    # narration audio from its own script + CTA, replacing the shared
    # podcast_audio_path the renders used to read. media.render_narration
    # produces the audio paths; media.transcribe_narration produces the SRTs.
    # Same #674 last-value-channel discipline — undeclared keys are dropped on
    # the graph_def path.
    video_long_script: str
    long_narration_audio_path: str
    short_narration_audio_path: str
    long_caption_srt_path: str
    short_caption_srt_path: str
```

- [ ] **Step 2: Write the failing test** (append to `test_media_load_scripts.py`)

```python
@pytest.mark.asyncio
async def test_load_scripts_loads_video_long_script():
    from modules.content.atoms import media_load_scripts
    pool = _FakePoolReturning({  # reuse the module's existing fake-pool helper
        "podcast_script": "p", "video_long_script": "the long video narration",
    })
    out = await media_load_scripts.run(
        {"task_id": "t1", "database_service": _DBService(pool)}
    )
    assert out["video_long_script"] == "the long video narration"
```

> Reuse whatever fake-pool/`task_metadata` row helper the existing tests in this file already use; the key point is the `task_metadata` JSON carries `video_long_script`.

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_media_load_scripts.py::test_load_scripts_loads_video_long_script -v`
Expected: FAIL (`KeyError: 'video_long_script'`).

- [ ] **Step 4: Load it** in `media_load_scripts.py`:

Add to `_EMPTY`: `"video_long_script": "",`
Add to the `result` dict: `"video_long_script": meta.get("video_long_script", _EMPTY["video_long_script"]),`
Add to `ATOM_META.outputs`: `FieldSpec(name="video_long_script", type="str", description="long-form video narration script"),`
Add to `ATOM_META.produces`: `"video_long_script",`

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/atoms/test_media_load_scripts.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/modules/content/atoms/media_load_scripts.py src/cofounder_agent/tests/unit/services/atoms/test_media_load_scripts.py
git commit -m "feat(media): declare per-media narration channels + load video_long_script (#689)"
```

---

## Task C: `_narration_render.py` shared helper

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/_narration_render.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_narration_render.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from modules.content.atoms import _narration_render


class _SC:
    def __init__(self, d): self._d = d
    def get(self, k, default=None): return self._d.get(k, default)


@pytest.mark.asyncio
async def test_empty_script_returns_empty():
    out = await _narration_render.render_narration(
        script="  ", cta_key="media.cta.video", site_config=_SC({}),
        task_id="t1", key="t1_long",
    )
    assert out == ""


@pytest.mark.asyncio
async def test_no_site_config_returns_empty():
    out = await _narration_render.render_narration(
        script="hello", cta_key="media.cta.video", site_config=None,
        task_id="t1", key="t1_long",
    )
    assert out == ""


@pytest.mark.asyncio
async def test_appends_cta_and_synthesizes(monkeypatch):
    seen = {}

    class _PS:
        def __init__(self, *, site_config): pass
        async def synthesize(self, text, *, key):
            seen["text"], seen["key"] = text, key
            return "/tmp/out.mp3", 12.0

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Body.", cta_key="media.cta.video",
        site_config=_SC({"media.cta.video": "Like and subscribe."}),
        task_id="t1", key="t1_long",
    )
    assert out == "/tmp/out.mp3"
    assert seen["text"].endswith("Like and subscribe.")
    assert seen["key"] == "t1_long"


@pytest.mark.asyncio
async def test_tts_exception_is_failsoft(monkeypatch):
    class _PS:
        def __init__(self, *, site_config): pass
        async def synthesize(self, text, *, key):
            raise RuntimeError("speaches down")

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Body.", cta_key="media.cta.video",
        site_config=_SC({}), task_id="t1", key="t1_long",
    )
    assert out == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_narration_render.py -v`
Expected: FAIL (`ModuleNotFoundError: _narration_render`).

- [ ] **Step 3: Create the helper**

```python
"""Shared narration-TTS helper for the Stage-2 media render atoms (#689).

Underscore-prefixed so the atom-registry filesystem scan SKIPS it
(``services/atom_registry.py``: files starting with ``_`` are not discovered
as atoms). This is plumbing, not an atom — mirrors ``_media_render.py``.

Synthesizes a narration script into an MP3 via the existing ``PodcastService``
TTS, after appending the per-medium CTA outro. Used by ``media.render_narration``
(long + short video narration) AND ``podcast.render`` (podcast narration), so the
CTA-append + synth + fail-soft contract lives in ONE place.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def render_narration(
    *,
    script: str,
    cta_key: str,
    site_config: Any,
    task_id: Any,
    key: str,
) -> str:
    """Synthesize ``script`` (+ the CTA at ``cta_key``) to an MP3.

    Returns the temp render path, or ``""`` on any fail-soft condition (empty
    script, no ``site_config``, or a TTS exception). NEVER raises — a narration
    failure must not halt the media graph.
    """
    text = (script or "").strip()
    if not text or site_config is None:
        return ""

    cta = (site_config.get(cta_key, "") or "").strip()
    if cta:
        text = f"{text}\n\n{cta}"

    from services.podcast_service import PodcastService

    try:
        path, _duration = await PodcastService(site_config=site_config).synthesize(
            text, key=key,
        )
    except Exception as exc:  # noqa: BLE001 — TTS failure must not halt the graph
        logger.warning(
            "[_narration_render] synthesis failed (key=%s, task=%s): %s",
            key, task_id, exc,
        )
        return ""
    return path or ""


__all__ = ["render_narration"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/atoms/test_narration_render.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_narration_render.py src/cofounder_agent/tests/unit/services/atoms/test_narration_render.py
git commit -m "feat(media): shared narration-TTS helper (CTA-append + synth, fail-soft) (#689)"
```

---

## Task D: `media.render_narration` atom

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/media_render_narration.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_media_render_narration.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from modules.content.atoms import media_render_narration


@pytest.mark.asyncio
async def test_renders_long_and_short_with_own_cta(monkeypatch):
    calls = []

    async def _fake_render(*, script, cta_key, site_config, task_id, key):
        calls.append((cta_key, key, script))
        return f"/tmp/{key}.mp3"

    monkeypatch.setattr(
        "modules.content.atoms._narration_render.render_narration", _fake_render
    )
    out = await media_render_narration.run({
        "task_id": "t1",
        "video_long_script": "long vo",
        "short_summary_script": "short vo",
        "site_config": object(),
    })
    assert out["long_narration_audio_path"] == "/tmp/t1_long.mp3"
    assert out["short_narration_audio_path"] == "/tmp/t1_short.mp3"
    cta_by_key = {k: c for (c, k, _s) in calls}
    assert cta_by_key["t1_long"] == "media.cta.video"
    assert cta_by_key["t1_short"] == "media.cta.video_short"


@pytest.mark.asyncio
async def test_long_falls_back_to_podcast_script(monkeypatch):
    seen = {}

    async def _fake_render(*, script, cta_key, **kw):
        seen[cta_key] = script
        return "/tmp/x.mp3"

    monkeypatch.setattr(
        "modules.content.atoms._narration_render.render_narration", _fake_render
    )
    await media_render_narration.run({
        "task_id": "t1", "video_long_script": "",   # empty → fallback
        "podcast_script": "podcast body", "short_summary_script": "s",
        "site_config": object(),
    })
    assert seen["media.cta.video"] == "podcast body"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_media_render_narration.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Create the atom**

```python
"""media.render_narration — Stage-2 narration TTS atom (#689).

Renders the long-form AND short-form video narration audio from their OWN
scripts + CTAs, BEFORE the transcribe / QA / render nodes that consume them:

  - long:  ``video_long_script`` (fallback ``podcast_script``) + ``media.cta.video``
           → ``long_narration_audio_path``
  - short: ``short_summary_script`` + ``media.cta.video_short``
           → ``short_narration_audio_path``

Fail-soft per channel (empty path on TTS failure / empty script) — a narration
failure must NOT halt the graph; the downstream render no-ops audio gracefully.
Delegates CTA-append + synth to ``_narration_render.render_narration`` so this
atom and ``podcast.render`` share one TTS code path.

NOTE (#674 trap): the two output channels MUST be declared ``PipelineState``
channels (they are — Task B) or LangGraph silently drops them.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="media.render_narration",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: synthesize the long-form + short-form video narration audio "
        "from their own scripts + CTAs (media.cta.video / media.cta.video_short)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="video_long_script", type="str", description="long-form narration script", required=False),
        FieldSpec(name="podcast_script", type="str", description="fallback long narration script", required=False),
        FieldSpec(name="short_summary_script", type="str", description="short-form narration script", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (TTS + CTA config)", required=False),
    ),
    outputs=(
        FieldSpec(name="long_narration_audio_path", type="str", description="long narration MP3 ('' on no-op/failure)"),
        FieldSpec(name="short_narration_audio_path", type="str", description="short narration MP3 ('' on no-op/failure)"),
    ),
    requires=("task_id",),
    produces=("long_narration_audio_path", "short_narration_audio_path"),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Render long + short narration audio. Best-effort — never raises."""
    from modules.content.atoms._narration_render import render_narration

    task_id = state.get("task_id")
    site_config = state.get("site_config")

    # Long: prefer the purpose-built long script, fall back to the podcast
    # script so a missing long script degrades to "has audio", not silence.
    long_script = (state.get("video_long_script") or "").strip() or (
        state.get("podcast_script") or ""
    )
    long_path = await render_narration(
        script=long_script,
        cta_key="media.cta.video",
        site_config=site_config,
        task_id=task_id,
        key=f"{task_id}_long",
    )

    # Short: its own script only (a "short" narrated by the full article would
    # be wrong) — empty short script → no short narration.
    short_path = await render_narration(
        script=state.get("short_summary_script") or "",
        cta_key="media.cta.video_short",
        site_config=site_config,
        task_id=task_id,
        key=f"{task_id}_short",
    )

    return {
        "long_narration_audio_path": long_path,
        "short_narration_audio_path": short_path,
    }


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/atoms/test_media_render_narration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/media_render_narration.py src/cofounder_agent/tests/unit/services/atoms/test_media_render_narration.py
git commit -m "feat(media): media.render_narration atom — per-lane narration audio (#689)"
```

---

## Task E: Refactor `podcast.render` onto the shared helper

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/podcast_render.py:50-78`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_podcast_render.py` (must stay green)

- [ ] **Step 1: Replace `run()`** with the delegating version (keeps the same contract: `task_id` required; empty script/no config/TTS failure → `""`; CTA `media.cta.podcast` appended):

```python
async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Render the podcast narration MP3, returning its temp path (or '')."""
    from modules.content.atoms._narration_render import render_narration

    task_id = state.get("task_id")
    if not task_id:
        raise ValueError("podcast.render requires task_id")

    path = await render_narration(
        script=state.get("podcast_script") or "",
        cta_key="media.cta.podcast",
        site_config=state.get("site_config"),
        task_id=task_id,
        key=str(task_id),
    )
    return {"podcast_audio_path": path}
```

Remove the now-unused inline `PodcastService` import / CTA logic from the old body.

- [ ] **Step 2: Run the existing test**

Run: `poetry run pytest tests/unit/services/atoms/test_podcast_render.py -v`
Expected: PASS. If a test patches `services.podcast_service.PodcastService`, it still works (the helper imports it from the same path). If one asserts on the missing-task_id `ValueError`, that path is preserved above.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/podcast_render.py
git commit -m "refactor(media): podcast.render delegates to shared narration helper (#689)"
```

---

## Task F: Re-point the video renders to per-lane narration + captions

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_media_render.py:51-113`
- Modify: `src/cofounder_agent/modules/content/atoms/media_render_long_video.py:52-58`
- Modify: `src/cofounder_agent/modules/content/atoms/media_render_short_video.py:51-57`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_media_render_video.py`

- [ ] **Step 1: Write the failing test** (append to `test_media_render_video.py`)

```python
@pytest.mark.asyncio
async def test_long_render_uses_long_narration_channel(monkeypatch):
    captured = {}

    async def _fake_render_shot_list(*, audio_path, caption_path, **kw):
        captured["audio_path"] = audio_path
        captured["caption_path"] = caption_path
        class _R:  # minimal ShotListRenderResult stand-in
            success = True; output_path = "/tmp/out.mp4"
            shots_rendered = 1; shots_total = 1
        return _R()

    monkeypatch.setattr(
        "modules.content.atoms._media_render.render_shot_list",
        _fake_render_shot_list,
    )
    from modules.content.atoms import media_render_long_video
    await media_render_long_video.run({
        "task_id": "t1",
        "video_shot_list": {"aspect": "16:9", "shots": [{"idx": 0, "source": "image_gen", "prompt": "x", "duration_s": 2.0}], "total_duration_s": 2.0},
        "long_narration_audio_path": "/tmp/long.mp3",
        "short_narration_audio_path": "/tmp/short.mp3",
        "long_caption_srt_path": "/tmp/long.srt",
    })
    assert captured["audio_path"] == "/tmp/long.mp3"
    assert captured["caption_path"] == "/tmp/long.srt"
```

> Match the exact `video_shot_list` shape the existing tests in this file use (copy one) so `VideoShotList.model_validate` passes.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_media_render_video.py::test_long_render_uses_long_narration_channel -v`
Expected: FAIL (`audio_path` is `""` — still reads `podcast_audio_path`).

- [ ] **Step 3: Parameterize `_media_render.render_from_state`**

Change the signature (line ~51) and the two `state.get` reads (lines ~107, ~112):

```python
async def render_from_state(
    state: dict[str, Any],
    *,
    shot_list_key: str,
    output_key: str,
    narration_key: str = "podcast_audio_path",
    caption_key: str = "caption_srt_path",
) -> dict[str, Any]:
```

```python
    narration = state.get(narration_key) or ""
    ambient = state.get("video_ambient_audio_path") or None
    caption = state.get(caption_key) or None
```

- [ ] **Step 4: Pass the per-lane keys from the thin atoms**

`media_render_long_video.py` `run()`:

```python
    return await render_from_state(
        state,
        shot_list_key="video_shot_list",
        output_key="long_video_path",
        narration_key="long_narration_audio_path",
        caption_key="long_caption_srt_path",
    )
```

`media_render_short_video.py` `run()`:

```python
    return await render_from_state(
        state,
        shot_list_key="short_shot_list",
        output_key="short_video_path",
        narration_key="short_narration_audio_path",
        caption_key="short_caption_srt_path",
    )
```

Also update each atom's `ATOM_META.inputs`: replace the `podcast_audio_path` FieldSpec with the lane's narration channel (`long_narration_audio_path` / `short_narration_audio_path`) and add the lane's caption channel — keeps the manifest honest (both `required=False`).

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/unit/services/atoms/test_media_render_video.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_media_render.py src/cofounder_agent/modules/content/atoms/media_render_long_video.py src/cofounder_agent/modules/content/atoms/media_render_short_video.py src/cofounder_agent/tests/unit/services/atoms/test_media_render_video.py
git commit -m "feat(media): video renders read per-lane narration + caption channels (#689)"
```

---

## Task G: Per-lane captions in `media.transcribe_narration`

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/media_transcribe_narration.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_media_transcribe_narration.py`

**Refactor shape:** Extract the existing `run()` body into a helper
`_transcribe_one(*, audio_path, script, task_id, label, site_config) -> str`
that returns the SRT path (or `""`), then call it twice. Mechanical changes to
the moved body:

- `narration = state.get("podcast_audio_path")` → `audio_path` parameter.
- `script = state.get("podcast_script")` → `script` parameter.
- SRT temp path `captions_{task_id}.srt` → `captions_{task_id}_{label}.srt`.
- every `dedup_key=f"...:{task_id}"` → `f"...:{task_id}:{label}"`.
- each `return {"caption_srt_path": ..., "asr_transcript": ...}` → `return srt_path` (just the path; the transcript stays internal to the fidelity check).

- [ ] **Step 1: Write the failing test** (append)

```python
@pytest.mark.asyncio
async def test_transcribes_both_lanes(monkeypatch):
    seen = []

    async def _fake_one(*, audio_path, script, task_id, label, site_config):
        seen.append((label, audio_path))
        return f"/tmp/{task_id}_{label}.srt"

    monkeypatch.setattr(
        "modules.content.atoms.media_transcribe_narration._transcribe_one",
        _fake_one,
    )
    from modules.content.atoms import media_transcribe_narration as m
    out = await m.run({
        "task_id": "t1",
        "long_narration_audio_path": "/tmp/long.mp3",
        "short_narration_audio_path": "/tmp/short.mp3",
        "video_long_script": "long", "short_summary_script": "short",
    })
    assert out["long_caption_srt_path"] == "/tmp/t1_long.srt"
    assert out["short_caption_srt_path"] == "/tmp/t1_short.srt"
    assert {lbl for (lbl, _a) in seen} == {"long", "short"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_media_transcribe_narration.py::test_transcribes_both_lanes -v`
Expected: FAIL (`_transcribe_one` undefined / output keys missing).

- [ ] **Step 3: Implement.** Update `ATOM_META`:
- `inputs`: `long_narration_audio_path`, `short_narration_audio_path`, `video_long_script` (req=False), `short_summary_script` (req=False), `site_config` (req=False), `task_id`.
- `outputs` / `produces`: `long_caption_srt_path`, `short_caption_srt_path`.

Rename the existing `run` body to `async def _transcribe_one(*, audio_path, script, task_id, label, site_config) -> str:` applying the mechanical changes above (return the SRT path string; `""` on every no-op/failure branch). Then add the new `run`:

```python
async def run(state: dict[str, Any]) -> dict[str, Any]:
    """One ASR pass per video lane → per-lane SRT caption tracks."""
    task_id = state.get("task_id")
    site_config = state.get("site_config")
    long_srt = await _transcribe_one(
        audio_path=state.get("long_narration_audio_path") or "",
        script=state.get("video_long_script") or state.get("podcast_script") or "",
        task_id=task_id, label="long", site_config=site_config,
    )
    short_srt = await _transcribe_one(
        audio_path=state.get("short_narration_audio_path") or "",
        script=state.get("short_summary_script") or "",
        task_id=task_id, label="short", site_config=site_config,
    )
    return {
        "long_caption_srt_path": long_srt,
        "short_caption_srt_path": short_srt,
    }
```

- [ ] **Step 4: Fix the existing single-lane tests.** The older tests in this file call `run({...,"podcast_audio_path":...})` and assert `caption_srt_path`. Re-point them at `_transcribe_one` directly (it has the old single-lane shape, returning a path string), or update them to the new lane keys. Keep coverage of: no-audio no-op, provider-failure fail-soft, fidelity-finding-on-low-ratio.

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/unit/services/atoms/test_media_transcribe_narration.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/media_transcribe_narration.py src/cofounder_agent/tests/unit/services/atoms/test_media_transcribe_narration.py
git commit -m "feat(media): per-lane caption tracks in transcribe_narration (#689)"
```

---

## Task H: Dual-track `qa.audio`

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/qa_audio.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_audio.py`

**Refactor shape:** Extract the current `run()` body (the `try:` block, lines
~227-428) into `_qa_one(*, audio_path, script, task_id, label, site_config) -> dict`
returning the per-check `result` dict. Mechanical changes: `audio_path` /
`podcast_script` become parameters; every `dedup_key=f"...:{task_id}"` →
`f"...:{task_id}:{label}"`. Then `run` calls it per lane and nests the results.

- [ ] **Step 1: Write the failing test** (append)

```python
@pytest.mark.asyncio
async def test_qa_audio_checks_both_lanes(monkeypatch):
    seen = []

    async def _fake_one(*, audio_path, script, task_id, label, site_config):
        seen.append(label)
        return {"volume_check": "ok"}

    monkeypatch.setattr(
        "modules.content.atoms.qa_audio._qa_one", _fake_one
    )
    from modules.content.atoms import qa_audio
    out = await qa_audio.run({
        "task_id": "t1",
        "long_narration_audio_path": "/tmp/long.mp3",
        "short_narration_audio_path": "/tmp/short.mp3",
    })
    assert set(seen) == {"long", "short"}
    assert out["audio_qa_result"]["long"]["volume_check"] == "ok"
    assert out["audio_qa_result"]["short"]["volume_check"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_qa_audio.py::test_qa_audio_checks_both_lanes -v`
Expected: FAIL (`_qa_one` undefined / nested keys missing).

- [ ] **Step 3: Implement.** Update `ATOM_META.inputs` to reference
      `long_narration_audio_path` + `short_narration_audio_path` (+ keep
      `video_long_script` / `short_summary_script` req=False for the duration check,

* `task_id`, `site_config`). Extract the body into `_qa_one(...)` (per the shape
  above; it returns the `result` dict, NOT wrapped in `audio_qa_result`). New `run`:

```python
async def run(state: dict[str, Any]) -> dict[str, Any]:
    """QA both narration lanes. Best-effort — never raises."""
    task_id = state.get("task_id")
    site_config = state.get("site_config")
    long_res = await _qa_one(
        audio_path=(state.get("long_narration_audio_path") or "").strip(),
        script=state.get("video_long_script") or state.get("podcast_script") or "",
        task_id=task_id, label="long", site_config=site_config,
    )
    short_res = await _qa_one(
        audio_path=(state.get("short_narration_audio_path") or "").strip(),
        script=state.get("short_summary_script") or "",
        task_id=task_id, label="short", site_config=site_config,
    )
    return {"audio_qa_result": {"long": long_res, "short": short_res}}
```

`_qa_one` keeps the existing fail-soft contract (its own `try/except`, returns
`{}` when the audio file is missing/unreadable).

- [ ] **Step 4: Fix existing single-track tests** — re-point them at `_qa_one(audio_path=..., script=..., task_id=..., label="long", site_config=...)`, which has the old shape (returns the `result` dict). Keep coverage of: missing-file skip, silence finding, clipping/too-quiet, duration mismatch.

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/unit/services/atoms/test_qa_audio.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/qa_audio.py src/cofounder_agent/tests/unit/services/atoms/test_qa_audio.py
git commit -m "feat(media): qa.audio checks both narration lanes (#689)"
```

---

## Task I: Wire `render_narration` into the graph + reseed

**Files:**

- Modify: `src/cofounder_agent/services/media_pipeline_spec.py:54-87`
- Create: `src/cofounder_agent/services/migrations/<ts>_reseed_media_pipeline_narration_node.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_registry.py` style — add a graph-compile assertion (see Step 1)

- [ ] **Step 1: Write the failing test** — new file `tests/unit/services/test_media_pipeline_graph.py`:

```python
import pytest


def test_media_pipeline_spec_has_narration_node():
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
    ids = [n["id"] for n in MEDIA_PIPELINE_GRAPH_DEF["nodes"]]
    assert "render_narration" in ids
    # render_narration runs right after load_scripts, before transcribe.
    assert ids.index("render_narration") == ids.index("load_scripts") + 1
    assert ids.index("render_narration") < ids.index("transcribe_narration")


@pytest.mark.asyncio
async def test_media_pipeline_graph_compiles():
    """build_graph_from_spec resolves every atom + passes the I/O contract."""
    from services.pipeline_architect import build_graph_from_spec
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
    # pool=None is fine: atoms resolve via the registry; no DB needed to compile.
    graph = build_graph_from_spec(MEDIA_PIPELINE_GRAPH_DEF, pool=None)
    assert graph is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/test_media_pipeline_graph.py -v`
Expected: FAIL (`render_narration` not in node ids).

- [ ] **Step 3: Add the node + edges** in `media_pipeline_spec.py`.

In `"nodes"`, insert after the `load_scripts` entry:

```python
        {"id": "render_narration", "atom": "media.render_narration"},
```

In `"edges"`, replace `{"from": "load_scripts", "to": "transcribe_narration"}` with:

```python
        {"from": "load_scripts", "to": "render_narration"},
        {"from": "render_narration", "to": "transcribe_narration"},
```

Update the module docstring + the `"description"` string to mention the
narration-render step.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/test_media_pipeline_graph.py -v`
Expected: PASS. (If `test_media_pipeline_graph_compiles` raises an I/O-contract error, a downstream node's `requires` names a channel nothing produces — verify the render atoms use `required=False` for the narration/caption inputs, which they do.)

- [ ] **Step 5: Generate + fill the reseed migration**

Run: `python scripts/new-migration.py "reseed media_pipeline graph_def with render_narration node"`

Replace the generated file body with (mirrors `20260608_180000_reseed_media_pipeline_audio_qa_node.py`):

```python
"""Migration: re-seed media_pipeline graph_def with the render_narration node (#689).

Inserts ``render_narration`` (``media.render_narration``) between
``load_scripts`` and ``transcribe_narration`` so each video lane gets its own
narration audio (own script + CTA) before the transcribe / QA / render nodes.
Final graph:

  load_scripts → render_narration → transcribe_narration → qa_audio →
  render_long_video → render_short_video → media_qa → persist_media → END

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so migrations-smoke CI can apply it without a full app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF  # noqa: E402


async def up(pool) -> None:
    graph_def_json = json.dumps(MEDIA_PIPELINE_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO pipeline_templates
                (slug, name, description, version, active, graph_def, created_by)
            VALUES ('media_pipeline', 'Media Pipeline', $1, 1, true, $2::jsonb, 'factory')
            ON CONFLICT (slug) DO UPDATE
               SET graph_def   = EXCLUDED.graph_def,
                   description  = EXCLUDED.description,
                   version      = EXCLUDED.version,
                   active       = EXCLUDED.active,
                   updated_at   = NOW()
            """,
            MEDIA_PIPELINE_GRAPH_DEF["description"],
            graph_def_json,
        )
    logger.info(
        "reseed_media_pipeline_narration_node up: inserted render_narration "
        "node (#689). result=%s", result,
    )


async def down(pool) -> None:  # noqa: ARG001
    logger.info(
        "reseed_media_pipeline_narration_node down: no-op — re-seed; "
        "media_pipeline row intentionally retained.",
    )
```

- [ ] **Step 6: Verify the migration applies + lints**

Run: `python scripts/ci/migrations_lint.py` then `python scripts/ci/migrations_smoke.py`
Expected: both PASS (migration applies against a fresh DB; the reseed updates the existing row).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/media_pipeline_spec.py src/cofounder_agent/services/migrations/ src/cofounder_agent/tests/unit/services/test_media_pipeline_graph.py
git commit -m "feat(media): wire render_narration into media_pipeline graph_def (#689)"
```

---

## Task J: Docs

**Files:**

- Modify: `docs/architecture/podcast-pipeline-stage3.md` (§11 / the CTA table)
- Modify: `docs/architecture/video-pipeline-redesign.md`

- [ ] **Step 1: Update the docs.** In `podcast-pipeline-stage3.md`, change the §11 line that says the video render atoms append a CTA to a _shared_ base narration — the long/short lanes now each render their **own** narration (own script + `media.cta.video` / `media.cta.video_short`). In `video-pipeline-redesign.md`, update the §6 "one ASR pass … same `podcast_audio_path`" claim and the media-pipeline node list to the new 8-node graph with `render_narration` and per-lane narration/caption channels.

- [ ] **Step 2: Commit**

```bash
git add docs/architecture/podcast-pipeline-stage3.md docs/architecture/video-pipeline-redesign.md
git commit -m "docs(media): per-lane video narration supersedes shared podcast base (#689)"
```

---

## Task K: Full-suite gate + end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the media-atom + stage test slice**

Run: `poetry run pytest tests/unit/services/atoms/ tests/unit/services/stages/test_generate_media_scripts.py tests/unit/services/stages/test_task_metadata_parity_693.py tests/unit/services/test_media_pipeline_graph.py -q`
Expected: all PASS, no collection errors.

- [ ] **Step 2: Apply the migration on prod-dev + rebuild the worker** (graph_def lives in the DB; atom code is bind-mounted but the prefect-worker image must pick up the new atom file):

```bash
docker compose up -d --build poindexter-prefect-worker
```

- [ ] **Step 3: Run ONE real task end-to-end through the fixed chain.** Pick a recent published post's task, or generate a fresh one, ensuring Stage-1 runs with this code so `video_long_script` **and** `short_summary_script` populate and both directors produce shot lists. Then dispatch Stage-2 for it (null its `media_pipeline_dispatched_at` so the 5-min `dispatch_media_pipeline` job re-runs it, or invoke `TemplateRunner.run("media_pipeline", …)` for the task). Confirm via DB:

```sql
SELECT task_id,
  length(stage_data->'task_metadata'->>'video_long_script')   AS long_script,
  length(stage_data->'task_metadata'->>'short_summary_script') AS short_script,
  jsonb_array_length(stage_data->'task_metadata'->'short_shot_list'->'shots') AS short_shots
FROM pipeline_versions WHERE task_id = '<task>' ORDER BY version DESC LIMIT 1;
```

Expected: `long_script` and `short_script` both > 0; `short_shots` > 0.

- [ ] **Step 4: ffprobe BOTH rendered MP4s** (the proven command from diagnosis):

```powershell
$dir = Join-Path $env:USERPROFILE '.poindexter\video'
foreach ($f in (Get-ChildItem $dir -Filter *.mp4 | Sort-Object LastWriteTime -Descending | Select-Object -First 2)) {
  Write-Output "=== $($f.Name) ==="
  ffmpeg -hide_banner -i $f.FullName -af volumedetect -f null NUL 2>&1 |
    Select-String 'mean_volume|max_volume' | ForEach-Object { $_.Line.Trim() }
}
```

Expected: **`mean_volume` well above −91 dB** (real narration, e.g. −20 to −30 dB) on the long-form MP4, AND a short-form (9:16) MP4 exists with audio — the **first-ever** short render. A −91 dB result means narration didn't reach the renderer — re-trace `long_narration_audio_path` in the run's state.

- [ ] **Step 5: Final commit (if any doc/notes tweaks from verification)** — otherwise the feature is complete and ready for PR.

---

## Self-Review

**Spec coverage:**

- Stage-1 distinct `video_long_script` → Task A ✓
- 3 independent scripts + CTAs (podcast/video/video_short) → A (script) + D (CTA wiring) + existing seeded settings ✓
- Stage-2 regenerates narration (Option A) → C + D ✓
- `render_narration` node + re-pointed transcribe/qa/renders → F, G, H, I ✓
- Shared helper + `podcast.render` refactor → C, E ✓
- 5 PipelineState channels + load_scripts + task_metadata → A, B ✓
- graph_def reseed migration → I ✓
- per-lane captions → G ✓
- long→podcast fallback / short no fallback → D ✓
- first-ever short-video end-to-end verification → K ✓
- docs → J ✓

**Placeholder scan:** No "TBD"/"handle errors"/"similar to". The two mechanical refactors (G, H) specify the exact extract-and-substitute changes rather than re-pasting unchanged bodies; the new `run()` for each is shown in full.

**Type consistency:** Channel names are identical everywhere — `video_long_script`, `long_narration_audio_path`, `short_narration_audio_path`, `long_caption_srt_path`, `short_caption_srt_path`. Helper signature `render_narration(*, script, cta_key, site_config, task_id, key)` matches all three call sites (D ×2, E ×1). `render_from_state(..., narration_key, caption_key)` matches both thin-atom call sites (F).

**One-PR note:** the tasks are interdependent and the verification needs all of them, so land as a single PR/branch (separate commits per task for reviewable history).

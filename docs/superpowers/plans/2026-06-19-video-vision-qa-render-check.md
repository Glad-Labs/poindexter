# Per-Shot Vision-QA Render-Check Loop (Video Quality Piece 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the open scene-selection loop — score every rendered shot frame with a vision model and regenerate-or-fall-back on a miss, so a degraded shot never silently ships.

**Architecture:** A focused frame-scorer in substrate (`services/video_renderers/shot_vision_qa.py`) reuses the existing vision model (`qa_vision_model`, qwen3-vl:30b) via Ollama `/api/chat`. `render_shot_list` wraps each per-shot render in a bounded verify-and-repair loop: render → score → (re-render stochastic sources up to N, keep-best) → fallback to holdover, emitting a `shot_quality_fallback` finding on any fallback. The whole loop is gated behind `video_shot_qa_enabled` and reads its tunables off the `site_config` DI seam already passed to the renderer.

**Tech Stack:** Python 3.13, asyncio, pydantic (`VideoShotList`/`Shot`), Ollama vision API, ffmpeg (frame extraction), `UnifiedPromptManager`, `utils.findings.emit_finding`, `SiteConfig` DI.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-19-video-quality-design.md` §3.2 (this piece), §5 (config), §6 (error handling), §7 (observability), §8 (testing). Piece 2 of the §10 rollout.
- **Substrate cannot import `modules/content`** (module-purity lint). The scorer lives in `services/`; it reuses the vision _technique_, never imports `qa_vision.py`.
- **Config in DB:** all tunables in `services/settings_defaults.py` (`DEFAULTS` + `METADATA`), never new migration files, never hardcoded literals in code.
- **Prompts DB-configurable:** the shot-QA prompt is a `UnifiedPromptManager` key in a SKILL.md, not an inline constant.
- **Fail-soft + visible (§6):** vision-QA infra failures degrade (accept the shot), never crash; every _fallback_ emits a `finding`; nothing silently ships degraded.
- **Backcompat:** `render_shot_list(site_config=None)` ⇒ QA disabled (the existing test suite passes `site_config=None` and must stay green). Default `video_shot_qa_enabled='true'` only bites when a real `site_config` is present.
- **No-silent-defaults:** required settings are seeded; a configured-but-unreachable vision model logs a warning (above the silent bar), it does not page (rendering must not block on QA infra).
- **All changes via PR; CI green = merge; squash; linear history.** Contract tests + this doc ship with the change.

---

## File Structure

- **Create** `src/cofounder_agent/services/video_renderers/shot_vision_qa.py` — the frame scorer (`ShotQAResult`, `score_shot_frame`, `_ensure_image_frame`). Substrate; one responsibility: score one frame against one shot.
- **Modify** `src/cofounder_agent/services/video_renderers/shot_list_renderer.py` — add `_QAConfig`, `_build_qa_config`, `_render_shot_with_qa`; wire the loop; enrich `_log_shot_audit`.
- **Modify** `src/cofounder_agent/services/settings_defaults.py` — 3 new keys + METADATA.
- **Modify** `src/cofounder_agent/skills/content/content-qa/SKILL.md` — add `qa.video_shot_quality` prompt (frontmatter + section).
- **Create** `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_vision_qa.py` — scorer tests.
- **Modify** `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py` — QA-loop state-machine tests.
- **Modify** `src/cofounder_agent/tests/unit/services/test_settings_defaults.py` — assert the 3 keys.
- **Modify** Grafana video-render dashboard JSON (Task 5) — per-source QA outcome panel.

Order: settings (foundation) → scorer (leaf, no deps on the loop) → loop wiring (consumes the scorer) → Grafana (reads the enriched audit + findings).

---

### Task 1: Settings — the three render-check tunables

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: DB-seeded keys `video_shot_qa_enabled='true'`, `video_shot_qa_threshold='60'`, `video_shot_qa_max_retries='2'`. The model is the EXISTING `qa_vision_model` (already seeded) — no new model key.

- [ ] **Step 1: Write the failing test**

In `test_settings_defaults.py`, add:

```python
def test_video_shot_qa_keys_seeded():
    from services.settings_defaults import DEFAULTS
    assert DEFAULTS["video_shot_qa_enabled"] == "true"
    assert DEFAULTS["video_shot_qa_threshold"] == "60"
    assert DEFAULTS["video_shot_qa_max_retries"] == "2"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `poetry run pytest tests/unit/services/test_settings_defaults.py::test_video_shot_qa_keys_seeded -v`
Expected: FAIL with `KeyError: 'video_shot_qa_enabled'`.

- [ ] **Step 3: Add the defaults + metadata**

In `settings_defaults.py`, near the existing `video_director_model` line in `DEFAULTS`, add:

```python
    'video_shot_qa_enabled': 'true',
    'video_shot_qa_threshold': '60',
    'video_shot_qa_max_retries': '2',
```

And in the `METADATA` dict (mirroring the `video_director_model` metadata entry shape), add:

```python
    'video_shot_qa_enabled': {'owner': 'video', 'value_type': 'bool'},
    'video_shot_qa_threshold': {'owner': 'video', 'value_type': 'int'},
    'video_shot_qa_max_retries': {'owner': 'video', 'value_type': 'int'},
```

(Read the file first to copy the exact `DEFAULTS`/`METADATA` formatting and the `value_type` vocabulary used by neighbouring keys.)

- [ ] **Step 4: Run the test to confirm it passes**

Run: `poetry run pytest tests/unit/services/test_settings_defaults.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(video): seed video_shot_qa_* render-check settings"
```

---

### Task 2: The per-shot frame scorer (`shot_vision_qa.py`)

**Files:**

- Create: `src/cofounder_agent/services/video_renderers/shot_vision_qa.py`
- Modify: `src/cofounder_agent/skills/content/content-qa/SKILL.md`
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_vision_qa.py`

**Interfaces:**

- Consumes: `Shot` (from `schemas.video_shot_list`), `SiteConfig` (`.get(key, default)` sync for the non-secret `qa_vision_model` / `ollama_base_url`), `get_prompt_manager` (prompt `qa.video_shot_quality`).
- Produces:
  - `@dataclass ShotQAResult: score: float | None; reason: str` — `score is None` means "could not score" (no model / call failed / unparseable); callers treat None as "accept, don't penalize".
  - `async def score_shot_frame(*, frame_path: str, shot: Shot, site_config: Any, http_client_factory: Any = None) -> ShotQAResult` — score 0–100.

- [ ] **Step 1: Add the prompt to the content-qa skill**

Read `src/cofounder_agent/skills/content/content-qa/SKILL.md`, find the frontmatter `metadata.prompts` list (where `qa.vision_image_relevance` is declared) and copy its per-prompt entry shape. Add an entry for `qa.video_shot_quality` (output_format `json`). Then add a section in the body:

````markdown
## qa.video_shot_quality

```text
You are a video-quality reviewer scoring a SINGLE rendered shot from a Glad Labs
explainer video. Judge only this one image as this one shot.

SHOT INTENT (why this shot exists): {intent}
SHOT SUBJECT (what it should show): {visual}
SHOT SOURCE: {source}

Judge:
- MATCH - does the image depict the shot's subject / intent?
- BRAND - dark-techno palette (deep navy, cyan, teal, gold), stylized not photoreal
  for AI sources; clean real footage for pexels. No garbled text, no warped
  artifacts, no melted faces / six-fingered hands.
- USABLE - would this hold the screen for a few seconds, or is it AI slop?

Output EXACTLY one JSON object, no prose, no code fences:
{{"score": <integer 0-100>, "reason": "<one short sentence>"}}
```
````

(The literal `{{` / `}}` escape the braces for `str.format`-style substitution — same convention as the `video.review_v1` prompt. Placeholders `{intent}` / `{visual}` / `{source}` stay single-braced.)

- [ ] **Step 2: Write the failing tests**

Create `test_shot_vision_qa.py`:

```python
"""Tests for the per-shot vision-QA frame scorer (video-quality Piece 2)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.video_shot_list import Shot
from services.config.site_config import SiteConfig
from services.video_renderers.shot_vision_qa import ShotQAResult, score_shot_frame


def _shot(source="image_gen", prompt="a cyan circuit board, dark navy backdrop"):
    return Shot(idx=0, duration_s=4.0, intent="opening payoff",
                source=source, prompt=prompt, narration_offset_s=0.0)


def _ollama_client(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"message": {"content": json_body}})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_scores_a_still_frame(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    sc = SiteConfig(initial_config={"qa_vision_model": "ollama/qwen3-vl:30b",
                                    "ollama_base_url": "http://ollama:11434"})
    client = _ollama_client('{"score": 82, "reason": "on-brand, sharp"}')
    res = await score_shot_frame(frame_path=str(frame), shot=_shot(),
                                 site_config=sc, http_client_factory=lambda *a, **k: client)
    assert isinstance(res, ShotQAResult)
    assert res.score == 82.0


@pytest.mark.asyncio
async def test_no_model_returns_none_score(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    sc = SiteConfig(initial_config={"qa_vision_model": ""})
    res = await score_shot_frame(frame_path=str(frame), shot=_shot(),
                                 site_config=sc, http_client_factory=lambda *a, **k: None)
    assert res.score is None


@pytest.mark.asyncio
async def test_unparseable_response_returns_none_score(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    sc = SiteConfig(initial_config={"qa_vision_model": "qwen3-vl:30b"})
    client = _ollama_client("the image looks fine to me")
    res = await score_shot_frame(frame_path=str(frame), shot=_shot(),
                                 site_config=sc, http_client_factory=lambda *a, **k: client)
    assert res.score is None


@pytest.mark.asyncio
async def test_video_frame_is_extracted_before_scoring(tmp_path):
    clip = tmp_path / "shot_00.mp4"
    clip.write_bytes(b"fake-mp4")
    sc = SiteConfig(initial_config={"qa_vision_model": "qwen3-vl:30b"})
    client = _ollama_client('{"score": 50, "reason": "ok"}')
    extracted = tmp_path / "frame.png"
    extracted.write_bytes(b"extracted-png")
    with patch("services.video_renderers.shot_vision_qa._extract_video_frame",
               AsyncMock(return_value=str(extracted))) as ex:
        res = await score_shot_frame(frame_path=str(clip), shot=_shot(source="wan21"),
                                     site_config=sc, http_client_factory=lambda *a, **k: client)
    ex.assert_awaited_once()
    assert res.score == 50.0
```

(Confirm the `SiteConfig` import path by grepping `class SiteConfig` — adjust the import if it differs from `services.config.site_config`.)

- [ ] **Step 3: Run them to confirm they fail**

Run: `poetry run pytest tests/unit/services/video_renderers/test_shot_vision_qa.py -q`
Expected: FAIL — module `shot_vision_qa` does not exist.

- [ ] **Step 4: Implement the scorer**

Create `shot_vision_qa.py`:

````python
"""Per-shot vision-QA frame scorer (video-quality Piece 2, spec §3.2).

Substrate twin of the blog-image vision gate (``MultiModelQA._check_image_relevance``).
It reuses the SAME vision model (``qa_vision_model``, default qwen3-vl:30b) and the
same Ollama ``/api/chat`` images shape, but scores a SINGLE rendered shot frame
against its ``Shot`` instead of inline blog-image URLs. Lives in ``services/``
(not ``modules/content``) so ``shot_list_renderer`` can call it without crossing
the module-purity boundary.

Fail-soft: any miss (no model configured, call error, unparseable response)
returns ``ShotQAResult(score=None)`` — the caller treats None as "could not
score, accept the shot" so vision-QA infra being down never blocks a render.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any

from schemas.video_shot_list import Shot

logger = logging.getLogger(__name__)

_DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434"
_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv")


@dataclass
class ShotQAResult:
    """Outcome of scoring one rendered shot frame.

    ``score`` is 0-100; ``None`` means the frame could not be scored
    (no model / call failed / unparseable) — callers accept the shot.
    """

    score: float | None
    reason: str = ""


async def _extract_video_frame(video_path: str) -> str | None:
    """Pull a representative (midpoint) still from a video clip via ffmpeg.

    Returns the PNG path, or None on failure. Vision models score images,
    not clips, so wan21/generative shots need a frame pulled first.
    """
    out = os.path.join(tempfile.gettempdir(), f"shotqa_{os.path.basename(video_path)}.png")
    # -ss before -i seeks fast; grab one frame ~1s in (covers the open of
    # short clips without needing the duration).
    cmd = ["ffmpeg", "-y", "-ss", "1", "-i", video_path, "-frames:v", "1", out]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] ffmpeg frame extract raised for %s: %s", video_path, exc)
        return None
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    return None


async def _ensure_image_frame(frame_path: str) -> str | None:
    """Return an image path to score: passthrough for stills, extract for video."""
    if frame_path.lower().endswith(_VIDEO_EXTS):
        return await _extract_video_frame(frame_path)
    if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
        return frame_path
    return None


def _parse_score(text: str) -> ShotQAResult:
    """Parse {"score": int, "reason": str} from a (possibly fenced) response."""
    json_text = text
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            json_text = m.group(1)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\"score\".*?\}", text, re.DOTALL)
        if not m:
            return ShotQAResult(score=None, reason="unparseable vision response")
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return ShotQAResult(score=None, reason="unparseable vision response")
    raw = parsed.get("score")
    if not isinstance(raw, (int, float)):
        return ShotQAResult(score=None, reason="vision response missing numeric score")
    return ShotQAResult(score=float(raw), reason=str(parsed.get("reason", ""))[:200])


async def score_shot_frame(
    *,
    frame_path: str,
    shot: Shot,
    site_config: Any,
    http_client_factory: Any = None,
) -> ShotQAResult:
    """Score one rendered shot frame 0-100 with the vision model.

    Returns ``ShotQAResult(score=None)`` on any failure (fail-soft).
    """
    if site_config is None:
        return ShotQAResult(score=None, reason="no site_config")
    model = (site_config.get("qa_vision_model", "") or "").strip().removeprefix("ollama/")
    if not model:
        logger.debug("[SHOT_QA] qa_vision_model not set — shot QA skipped")
        return ShotQAResult(score=None, reason="no vision model configured")

    image_path = await _ensure_image_frame(frame_path)
    if not image_path:
        return ShotQAResult(score=None, reason="no scoreable frame")

    try:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] frame read failed for %s: %s", image_path, exc)
        return ShotQAResult(score=None, reason="frame read failed")

    from services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.video_shot_quality",
        intent=shot.intent,
        visual=(shot.prompt or shot.query or ""),
        source=shot.source,
    )
    base = site_config.get("ollama_base_url", _DEFAULT_OLLAMA_URL)
    url = f"{str(base).rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "options": {"temperature": 0.2, "num_predict": 200},
    }

    import httpx

    if http_client_factory is None:
        http_client_factory = httpx.AsyncClient
    timeout = httpx.Timeout(120.0, connect=5.0)
    try:
        async with http_client_factory(timeout=timeout) as client:
            resp = await asyncio.wait_for(
                client.post(url, json=payload, timeout=timeout), timeout=150,
            )
        if resp.status_code != 200:
            logger.warning("[SHOT_QA] ollama HTTP %d for shot %d", resp.status_code, shot.idx)
            return ShotQAResult(score=None, reason=f"ollama http {resp.status_code}")
        text = resp.json().get("message", {}).get("content", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_QA] vision call failed for shot %d (non-critical): %s", shot.idx, exc)
        return ShotQAResult(score=None, reason="vision call failed")

    if not text:
        return ShotQAResult(score=None, reason="empty vision response")
    return _parse_score(text)


__all__ = ["ShotQAResult", "score_shot_frame", "_extract_video_frame"]
````

- [ ] **Step 5: Run the scorer tests**

Run: `poetry run pytest tests/unit/services/video_renderers/test_shot_vision_qa.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_vision_qa.py \
        src/cofounder_agent/skills/content/content-qa/SKILL.md \
        src/cofounder_agent/tests/unit/services/video_renderers/test_shot_vision_qa.py
git commit -m "feat(video): per-shot vision-QA frame scorer + qa.video_shot_quality prompt"
```

---

### Task 3: The verify-and-repair loop in `render_shot_list`

**Files:**

- Modify: `src/cofounder_agent/services/video_renderers/shot_list_renderer.py`
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Consumes: `score_shot_frame` / `ShotQAResult` (Task 2), `emit_finding` (`utils.findings`), the 3 settings (Task 1) off `site_config`.
- Produces: same `ShotListRenderResult`; per-shot behaviour now includes regenerate/fallback. New module-level constant `_REGENERABLE_SOURCES = {"image_gen", "image_kenburns", "wan21"}`. New helpers `_build_qa_config(site_config) -> _QAConfig`, `_render_shot_with_qa(...)`. `_log_shot_audit` gains optional `qa_score`/`qa_outcome` kwargs.

- [ ] **Step 1: Write the failing state-machine tests**

Append a `TestRenderCheckLoop` class to `test_shot_list_renderer.py`. These patch `score_shot_frame` (imported into the renderer module) and the compositor, and pass a seeded `SiteConfig`:

```python
class TestRenderCheckLoop:
    """Per-shot vision-QA verify-and-repair loop (video-quality Piece 2)."""

    def _image_gen_list(self, n=1):
        shots = [Shot(idx=i, duration_s=3.0, intent=f"beat {i}", source="image_gen",
                      prompt=f"a cyan abstract circuit {i}", narration_offset_s=3.0 * i)
                 for i in range(n)]
        return _build_shot_list(shots)

    def _qa_site_config(self, **over):
        from services.config.site_config import SiteConfig
        cfg = {"video_shot_qa_enabled": "true", "video_shot_qa_threshold": "60",
               "video_shot_qa_max_retries": "2", "qa_vision_model": "qwen3-vl:30b"}
        cfg.update(over)
        return SiteConfig(initial_config=cfg)

    def _image_gen_factory(self):
        resp = MagicMock(status_code=200, headers={"content-type": "image/png"},
                         content=b"png")
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=resp)
        return lambda *a, **k: client

    def _mock_compositor(self):
        class _C:
            def __init__(self, site_config=None):
                pass
            async def compose(self, request, **kw):
                with open(request.output_path, "wb") as f:
                    f.write(b"mp4")
                return MagicMock(success=True, output_path=request.output_path,
                                 file_size_bytes=3, duration_s=request.scenes[0].duration_s)
        return _C

    @pytest.mark.asyncio
    async def test_accept_above_threshold_no_regen(self, tmp_path):
        """A shot scoring >= threshold renders once — no regeneration."""
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        scorer = AsyncMock(return_value=ShotQAResult(score=90.0, reason="great"))
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=self._image_gen_list(1),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen-server:9836", site_config=self._qa_site_config(),
                http_client_factory=self._image_gen_factory())
        assert result.success is True
        assert scorer.await_count == 1  # scored once, accepted

    @pytest.mark.asyncio
    async def test_regenerate_then_fallback_emits_finding(self, tmp_path):
        """Below threshold on every attempt → regen up to max_retries, then
        fall back to holdover and emit a shot_quality_fallback finding."""
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        # Two shots: shot 0 passes (gives a prior clip), shot 1 always fails.
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="beat", source="image_gen",
                      prompt="teal mesh", narration_offset_s=3.0)]
        shot_list = _build_shot_list(shots)
        scorer = AsyncMock(side_effect=[ShotQAResult(90.0), ShotQAResult(20.0),
                                        ShotQAResult(25.0), ShotQAResult(30.0)])
        findings = []
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "emit_finding", lambda **kw: findings.append(kw)), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=shot_list,
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen-server:9836", site_config=self._qa_site_config(),
                http_client_factory=self._image_gen_factory())
        assert result.success is True
        # shot 1: 1 initial + 2 regens = 3 scores; shot 0: 1 score => 4 total.
        assert scorer.await_count == 4
        assert any(f["kind"] == "shot_quality_fallback" for f in findings)

    @pytest.mark.asyncio
    async def test_qa_disabled_when_site_config_none(self, tmp_path):
        """site_config=None ⇒ QA never runs (backcompat for the existing suite)."""
        import services.video_renderers.shot_list_renderer as mod
        scorer = AsyncMock()
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=self._image_gen_list(1),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen-server:9836", site_config=None,
                http_client_factory=self._image_gen_factory())
        assert result.success is True
        scorer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pexels_not_regenerated(self, tmp_path):
        """Pexels is deterministic — a low score falls back without re-fetching."""
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="person", source="pexels",
                      query="developer at desk", narration_offset_s=3.0)]
        scorer = AsyncMock(side_effect=[ShotQAResult(90.0), ShotQAResult(10.0)])
        pexels = AsyncMock(return_value=True)
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "_render_pexels_image", pexels), \
             patch.object(mod, "emit_finding", lambda **kw: None), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen-server:9836", site_config=self._qa_site_config(),
                http_client_factory=self._image_gen_factory())
        # pexels shot scored once (10 < 60) but NOT re-fetched: 1 pexels call total.
        assert pexels.await_count == 1
        assert scorer.await_count == 2  # image_gen(1) + pexels(1), no regen
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestRenderCheckLoop -q`
Expected: FAIL (`score_shot_frame` not importable in the renderer module; QA loop absent).

- [ ] **Step 3: Implement the loop**

In `shot_list_renderer.py`:

(a) Add imports + constant near the top:

```python
from dataclasses import dataclass

from services.video_renderers.shot_vision_qa import ShotQAResult, score_shot_frame
from utils.findings import emit_finding

_REGENERABLE_SOURCES = frozenset({"image_gen", "image_kenburns", "wan21"})
```

(b) Add the QA config + builder:

```python
@dataclass
class _QAConfig:
    enabled: bool
    threshold: float
    max_retries: int


def _build_qa_config(site_config: Any) -> _QAConfig:
    """Read the render-check tunables off the DI seam.

    site_config=None (the legacy/test path) ⇒ disabled, so the existing
    suite and the captionless video_service caller are unaffected.
    """
    if site_config is None:
        return _QAConfig(enabled=False, threshold=60.0, max_retries=2)
    def _b(key: str, default: str) -> str:
        return str(site_config.get(key, default) or default).strip().lower()
    enabled = _b("video_shot_qa_enabled", "true") in ("true", "1", "yes")
    try:
        threshold = float(site_config.get("video_shot_qa_threshold", "60") or "60")
    except (TypeError, ValueError):
        threshold = 60.0
    try:
        max_retries = int(site_config.get("video_shot_qa_max_retries", "2") or "2")
    except (TypeError, ValueError):
        max_retries = 2
    return _QAConfig(enabled=enabled, threshold=threshold, max_retries=max(0, max_retries))
```

(c) Update `_log_shot_audit` to accept and record optional QA fields:

```python
async def _log_shot_audit(
    pool: Any,
    *,
    post_id: str,
    shot_result: ShotRenderResult,
    qa_score: float | None = None,
    qa_outcome: str | None = None,
) -> None:
    ...
    json.dumps({
        "post_id": post_id,
        "shot_idx": shot_result.idx,
        "source": shot_result.source,
        "success": shot_result.success,
        "duration_s": shot_result.duration_s,
        "error": shot_result.error,
        "qa_score": qa_score,
        "qa_outcome": qa_outcome,
    }),
```

(d) Add the per-shot QA wrapper:

```python
async def _render_shot_with_qa(
    shot: Shot,
    *,
    prior_clip: str | None,
    qa: _QAConfig,
    site_config: Any,
    render_kwargs: dict[str, Any],
    http_client_factory: Any,
) -> tuple[ShotRenderResult, float | None, str | None]:
    """Render one shot, then verify-and-repair against the vision-QA score.

    Returns (result, qa_score, qa_outcome). qa_outcome is one of
    None | "accepted" | "regenerated" | "fallback_holdover" | "kept_below".
    Bounded: at most ``qa.max_retries`` regenerations for stochastic sources,
    then a deterministic holdover fallback — never loops.
    """
    result = await _render_one_shot(shot, prior_clip=prior_clip, **render_kwargs)

    # QA off / nothing rendered / reused the prior clip (holdover or a
    # pexels-miss that already held over) → no scoring; the prior clip was
    # already vetted when it was first produced.
    if (not qa.enabled or not result.success or not result.clip_path
            or result.clip_path == prior_clip):
        return result, None, None

    best = result
    best_qa = await score_shot_frame(
        frame_path=result.clip_path, shot=shot, site_config=site_config,
        http_client_factory=http_client_factory,
    )
    # Could not score (no model / infra down) → accept as-is, don't penalise.
    if best_qa.score is None:
        return best, None, None

    attempts = 0
    while (best_qa.score < qa.threshold and attempts < qa.max_retries
           and shot.source in _REGENERABLE_SOURCES):
        attempts += 1
        cand = await _render_one_shot(shot, prior_clip=prior_clip, **render_kwargs)
        if not (cand.success and cand.clip_path):
            continue
        cand_qa = await score_shot_frame(
            frame_path=cand.clip_path, shot=shot, site_config=site_config,
            http_client_factory=http_client_factory,
        )
        if cand_qa.score is not None and cand_qa.score > (best_qa.score or 0):
            best, best_qa = cand, cand_qa

    if best_qa.score is not None and best_qa.score < qa.threshold:
        if prior_clip:
            emit_finding(
                source="shot_list_renderer", kind="shot_quality_fallback",
                title=f"shot {shot.idx} ({shot.source}) fell back to holdover",
                body=(f"shot {shot.idx} scored {best_qa.score:.0f} < "
                      f"{qa.threshold:.0f} after {attempts} regen(s); held over the "
                      f"prior clip. reason: {best_qa.reason}"),
                severity="warn",
                dedup_key=f"shot_quality_fallback:{render_kwargs.get('post_id','')}:{shot.idx}",
                extra={"shot_idx": shot.idx, "source": shot.source,
                       "score": best_qa.score, "threshold": qa.threshold},
            )
            held = ShotRenderResult(idx=shot.idx, source=shot.source, success=True,
                                    clip_path=prior_clip, duration_s=shot.duration_s)
            return held, best_qa.score, "fallback_holdover"
        # idx 0 with no prior clip: ship the best attempt, but flag it.
        emit_finding(
            source="shot_list_renderer", kind="shot_quality_fallback",
            title=f"shot {shot.idx} ({shot.source}) kept below threshold",
            body=(f"shot {shot.idx} scored {best_qa.score:.0f} < {qa.threshold:.0f} "
                  f"and has no prior clip to hold over; kept best attempt. "
                  f"reason: {best_qa.reason}"),
            severity="warn",
            dedup_key=f"shot_quality_fallback:{render_kwargs.get('post_id','')}:{shot.idx}",
            extra={"shot_idx": shot.idx, "source": shot.source,
                   "score": best_qa.score, "threshold": qa.threshold},
        )
        return best, best_qa.score, "kept_below"

    return best, best_qa.score, ("regenerated" if attempts else "accepted")
```

> Note: `post_id` is not currently in `render_kwargs`; in step (e) we do NOT add it there (it's a `render_shot_list` arg). Replace `render_kwargs.get('post_id','')` with the `post_id` closure variable by defining `_render_shot_with_qa` to also take `post_id: str`. Add `post_id: str` as a keyword arg and use it directly in both `dedup_key`s.

(e) Rewire the loop in `render_shot_list`. Replace the existing `for shot in shot_list.shots:` body:

```python
    qa = _build_qa_config(site_config)
    render_kwargs = dict(
        work_dir=work_dir, image_gen_url=image_gen_url, site_config=site_config,
        http_client_factory=http_client_factory, pexels_key=pexels_key,
        orientation=orientation,
    )

    shot_results: list[ShotRenderResult] = []
    prior_clip: str | None = None
    for shot in shot_list.shots:
        result, qa_score, qa_outcome = await _render_shot_with_qa(
            shot, prior_clip=prior_clip, qa=qa, site_config=site_config,
            render_kwargs=render_kwargs, http_client_factory=http_client_factory,
            post_id=post_id,
        )
        await _log_shot_audit(pool, post_id=post_id, shot_result=result,
                              qa_score=qa_score, qa_outcome=qa_outcome)
        shot_results.append(result)
        if result.success and result.clip_path:
            prior_clip = result.clip_path
```

(`_render_one_shot` keeps its `post_id`-free signature — `post_id` is only used for the finding dedup key, passed straight to `_render_shot_with_qa`.)

- [ ] **Step 4: Run the new + existing renderer tests**

Run: `poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py -q`
Expected: PASS — `TestRenderCheckLoop` (4 new) AND every pre-existing test (they pass `site_config=None` ⇒ QA disabled ⇒ unchanged behaviour).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_list_renderer.py \
        src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -m "feat(video): per-shot vision-QA verify-and-repair loop in render_shot_list"
```

---

### Task 4: Findings + audit observability are live (verification task)

**Files:**

- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py` (one more assertion)

**Interfaces:**

- Consumes: the loop from Task 3. No new production code — this task proves the `finding` + enriched audit row are correctly shaped for the Findings dashboard (`event_type='finding'`, `kind='shot_quality_fallback'`) and the per-shot panel (`qa_score`/`qa_outcome` on `video_shot_rendered`).

- [ ] **Step 1: Write the assertion test**

```python
    @pytest.mark.asyncio
    async def test_fallback_finding_shape_is_dashboard_ready(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="beat", source="image_gen",
                      prompt="teal mesh", narration_offset_s=3.0)]
        scorer = AsyncMock(side_effect=[ShotQAResult(90.0)] + [ShotQAResult(15.0)] * 3)
        captured = []
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "emit_finding", lambda **kw: captured.append(kw)), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            await render_shot_list(
                post_id="post-xyz", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen-server:9836", site_config=self._qa_site_config(),
                http_client_factory=self._image_gen_factory())
        f = next(f for f in captured if f["kind"] == "shot_quality_fallback")
        assert f["source"] == "shot_list_renderer"
        assert f["severity"] == "warn"
        assert f["dedup_key"] == "shot_quality_fallback:post-xyz:1"
        assert f["extra"]["score"] == 15.0
```

- [ ] **Step 2: Run it**

Run: `poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestRenderCheckLoop::test_fallback_finding_shape_is_dashboard_ready -q`
Expected: PASS (Task 3 already produces this shape; if the dedup_key differs, fix Task 3's key to `f"shot_quality_fallback:{post_id}:{shot.idx}"`).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -m "test(video): assert shot_quality_fallback finding is dashboard-ready"
```

---

### Task 5: Grafana — per-source QA outcome panel

**Files:**

- Modify: the video-render dashboard JSON under `infrastructure/grafana/` (grep for the `video_shot_rendered` panel / the #678 video row to find the exact dashboard file).

**Interfaces:**

- Consumes: enriched `audit_log` `video_shot_rendered` rows (`details->>'qa_outcome'`, `details->>'qa_score'`) + `finding` rows (`kind='shot_quality_fallback'`). Read-only; no app code.

- [ ] **Step 1: Locate the dashboard + existing video row**

Run: `grep -rl "video_shot_rendered" infrastructure/grafana/` and open the matching dashboard JSON. Identify the panel array and an adjacent panel's `gridPos` to place a new one below the #678 row.

- [ ] **Step 2: Add a stat/timeseries panel**

Add a panel that counts QA outcomes from `audit_log`, e.g. (Postgres datasource):

```sql
SELECT date_trunc('hour', timestamp) AS time,
       details->>'qa_outcome' AS outcome, count(*)
FROM audit_log
WHERE event_type = 'video_shot_rendered' AND details->>'qa_outcome' IS NOT NULL
GROUP BY 1, 2 ORDER BY 1;
```

Match the surrounding panels' datasource UID, `fieldConfig`, and 960px-friendly `gridPos` width. Give it a stable `id` (max existing id + 1) and a title like "Shot QA — outcomes/hr".

- [ ] **Step 3: Validate the JSON**

Run: `python -c "import json,sys; json.load(open(sys.argv[1]))" <dashboard.json>`
Expected: no error (valid JSON).

- [ ] **Step 4: Commit**

```bash
git add infrastructure/grafana/
git commit -m "feat(grafana): per-source shot vision-QA outcome panel"
```

---

## Self-Review

**Spec coverage (§3.2 / §5 / §6 / §7 / §8):**

- render → score → regen(≤max_retries) → fallback(holdover) → never-ship-bad: Task 3 ✓ (stock-query intermediate fallback intentionally deferred — see below).
- reuse `qa.vision` model: Task 2 reuses `qa_vision_model` + `/api/chat` shape ✓.
- bounded / terminates: `max_retries` cap + holdover terminal, `_REGENERABLE_SOURCES` only ✓.
- fallback emits a `finding`: Task 3 ✓; dashboard-ready shape: Task 4 ✓.
- `video_shot_qa_enabled` gate + 3 keys: Task 1 + `_build_qa_config` ✓.
- fail-soft (score None ⇒ accept): Task 2/3 ✓.
- config in `settings_defaults` not migrations ✓; prompt DB-configurable ✓; observability (audit enrich + Findings + Grafana) ✓; contract tests ✓.

**Deliberate scope cuts (documented, not gaps):**

- **Regenerate = re-render (re-roll), not LLM-prompt-revision.** Per `feedback_calculated_vs_generated` (prefer deterministic) + `feedback_always_keep_ml_in_mind` — re-roll is the deterministic V1; a critic-revised prompt is the noted ML successor.
- **Fallback terminal = holdover (no stock-query intermediate).** Holdover already guarantees "never ship a bad shot" + termination; deriving a stock query from a stylized image-gen prompt is unreliable. Stock-intermediate is a documented follow-up.
- **No `gpu.lock` around the vision call** — matches the existing `_check_image_relevance` (Ollama serialises internally). Note as a refinement if GPU contention shows on the Hardware board.

**Type consistency:** `ShotQAResult.score: float | None` is the single "couldn't score" signal threaded everywhere; `score_shot_frame` is the one name used in both the scorer module and the renderer import; `_render_shot_with_qa` returns the `(result, qa_score, qa_outcome)` triple consumed verbatim by the loop and `_log_shot_audit`.

**Placeholder scan:** every code step has complete code; the one inline TODO (the `post_id` closure in Task 3 step (d)) is resolved explicitly in step (e)'s note.

## Execution

Inline execution in this session (continuing the Piece-1 cadence; the operator directed "start on the second piece, then enable and test in prod"). After Task 5: one PR → CI green → squash-merge → deploy (ff deploy clone + restart `poindexter-prefect-worker`) → in-process prod test of the loop on a real shot list, then enable/verify.

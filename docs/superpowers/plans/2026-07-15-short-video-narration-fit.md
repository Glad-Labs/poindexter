# Short-video fit-to-narration + script cap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the short-form frozen-tail: the video's visuals span the actual narration (no freeze, no cut audio), and script generation keeps shorts short.

**Architecture:** Part 1 (Stage-2 renderer, short lane only) rescales the director's shots to the real narration duration with a per-shot ceiling + cycle-on-overflow. Part 2 (Stage-1 generation) caps the script via one canonical target + a deterministic runaway trim. Part 1 is the structural backstop; Part 2 keeps the rescale factor near 1.0.

**Tech Stack:** Python 3.13, async FastAPI worker, ffmpeg/ffprobe, Pydantic shot-list schema, LangGraph atoms, DB-backed `app_settings`.

## Global Constraints

- **Every tunable is a DB setting** in `src/cofounder_agent/services/settings_defaults.py` — add to BOTH the `DEFAULTS` value dict AND the metadata dict. Never seed settings in migration files.
- **No hardcoded counts in prompts** — the short prompt's second/word targets are substituted from settings, never literals.
- **Test runner (worktree has no venv):** `PY="C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe"; WT="C:/Users/mattm/glad-labs-website/.claude/worktrees/silly-austin-778304/src/cofounder_agent"; cd "$WT" && PYTHONPATH="$WT" "$PY" -m pytest <target> -o addopts="" -q`
- **Before each commit:** ruff clean on touched files (`"$PY" -m ruff check <files>`); `"$PY" scripts/ci/lint_silent_excepts.py` exit 0.
- **Commit trailer:** end every commit body with `Refs Glad-Labs/poindexter#867` then `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Defaults (approved):** `video_short_target_seconds=45`, `video_short_max_seconds=60`, `video_short_max_shot_seconds=9`, `video_narration_fit_enabled=true`.
- **Pace constant:** `_WORDS_PER_SECOND = 2.5` (~150 WPM) — already in `generate_video_shot_list.py`; mirror locally where needed with a comment.

---

## File Structure

- `services/video_renderers/shot_list_renderer.py` — add `_fit_scene_durations` (pure) + `_probe_duration_s` (async ffprobe) + `narration_fit`/`max_shot_s` params on `render_shot_list`; replace the scene-build loop.
- `modules/content/atoms/_media_render.py` — add `narration_fit` param to `render_from_state`; resolve settings; pass to `render_shot_list`.
- `modules/content/atoms/media_render_short_video.py` — pass `narration_fit=True`.
- `modules/content/stages/generate_video_shot_list.py` — `_estimate_short_duration(target_seconds=…)`; thread setting at the call site.
- `modules/content/stages/generate_media_scripts.py` — parameterize `_build_scene_prompt`; add `_trim_to_word_budget` + wire after parse + finding.
- `services/settings_defaults.py` — 4 new settings.
- Tests: `tests/unit/services/video_renderers/test_shot_list_renderer.py`, `tests/unit/services/atoms/test_media_render_video.py`, `tests/unit/services/stages/test_generate_video_shot_list.py` (or nearest existing), `tests/unit/services/stages/test_generate_media_scripts.py` (or nearest existing).

---

### Task 1: `_fit_scene_durations` — the pure layout function (Part 1 core)

**Files:**

- Modify: `services/video_renderers/shot_list_renderer.py`
- Test: `tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Produces: `_fit_scene_durations(shot_durations: list[float], narration_dur_s: float, *, max_shot_s: float, max_scenes: int = 200, tail_pad_s: float = 0.0, overhang_tolerance_s: float = 1.0) -> list[tuple[int, float]]` — returns `(shot_index, duration_s)` pairs to lay out. No-op (original durations, one pair per shot in order) when the narration already fits; else proportional rescale capped at `max_shot_s`, cycling shot indices to fill.

- [ ] **Step 1: Write the failing test**

Add near the other renderer unit tests:

```python
class TestFitSceneDurations:
    """Pure narration-fit layout: rescale director shots to span the voiceover."""

    def test_fits_already_is_noop(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [2.0, 5.0, 6.0, 5.0]
        # narration (17s) within tolerance of total (18s) → keep director durations
        out = _fit_scene_durations(durs, 17.0, max_shot_s=9.0)
        assert out == [(0, 2.0), (1, 5.0), (2, 6.0), (3, 5.0)]

    def test_video_longer_than_narration_is_noop(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [5.0, 5.0, 5.0]
        out = _fit_scene_durations(durs, 8.0, max_shot_s=9.0)  # narration shorter
        assert out == [(0, 5.0), (1, 5.0), (2, 5.0)]

    def test_proportional_rescale_spans_narration_and_preserves_pacing(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [2.0, 5.0, 6.0, 5.0, 6.0, 6.0, 7.0, 8.0]  # 45s, avg 5.6
        out = _fit_scene_durations(durs, 54.0, max_shot_s=9.0)  # scale 1.2
        total = sum(d for _, d in out)
        assert abs(total - 54.0) < 0.05
        assert [i for i, _ in out] == list(range(8))  # no cycle
        # hook still shortest, last still longest (relative pacing preserved)
        assert out[0][1] < out[1][1] < out[-1][1]
        assert all(d <= 9.0 + 1e-6 for _, d in out)

    def test_large_scale_caps_per_shot_and_cycles(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [2.0, 5.0, 6.0, 5.0, 6.0, 6.0, 7.0, 8.0]  # 45s
        out = _fit_scene_durations(durs, 158.0, max_shot_s=9.0)  # scale 3.5
        total = sum(d for _, d in out)
        assert abs(total - 158.0) < 0.05
        assert all(d <= 9.0 + 1e-6 for _, d in out)   # ceiling holds
        assert len(out) > 8                            # cycled past the 8 shots
        assert out[8][0] == 0                          # wrapped back to shot 0

    def test_scene_count_guard_bounds_pathological_fill(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        out = _fit_scene_durations([1.0], 10_000.0, max_shot_s=1.0, max_scenes=50)
        assert len(out) == 50

    def test_empty_or_zero_total_returns_originals(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        assert _fit_scene_durations([], 30.0, max_shot_s=9.0) == []
        assert _fit_scene_durations([0.0, 0.0], 30.0, max_shot_s=9.0) == [(0, 0.0), (1, 0.0)]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH="$WT" "$PY" -m pytest tests/unit/services/video_renderers/test_shot_list_renderer.py -o addopts="" -q -k TestFitSceneDurations`
Expected: FAIL with `ImportError: cannot import name '_fit_scene_durations'`.

- [ ] **Step 3: Implement `_fit_scene_durations`**

Add near the other module-level helpers in `shot_list_renderer.py` (e.g. just above `render_shot_list`):

```python
def _fit_scene_durations(
    shot_durations: list[float],
    narration_dur_s: float,
    *,
    max_shot_s: float,
    max_scenes: int = 200,
    tail_pad_s: float = 0.0,
    overhang_tolerance_s: float = 1.0,
) -> list[tuple[int, float]]:
    """Lay out ``(shot_index, duration_s)`` pairs so the concat spans the
    narration — the short-lane fix for the frozen tail (issue #867).

    No-op (returns the director's durations, one pair per shot in order) when the
    narration already fits: the video is longer than the narration, or the
    overhang is within ``overhang_tolerance_s`` (the compositor's tail-pad
    absorbs it). Otherwise rescales every shot by ``narration/total`` — which
    preserves the director's RELATIVE pacing (the ~2s hook stays proportionally
    shortest) — caps each at ``max_shot_s`` so no single image is held too long,
    and CYCLES the shot sequence (repeating visuals) when the cap leaves a gap.
    ``max_scenes`` bounds a pathological fill.
    """
    n = len(shot_durations)
    total = sum(shot_durations)
    originals = list(enumerate(shot_durations))
    if n == 0 or total <= 0 or narration_dur_s <= 0:
        return originals
    if narration_dur_s - total <= overhang_tolerance_s:
        return originals
    scale = narration_dur_s / total
    target = narration_dur_s + max(0.0, tail_pad_s)
    capped = [min(d * scale, max_shot_s) for d in shot_durations]
    layout: list[tuple[int, float]] = []
    acc = 0.0
    i = 0
    while acc < target - 1e-6 and len(layout) < max_scenes:
        idx = i % n
        dur = capped[idx]
        remaining = target - acc
        if dur >= remaining:
            layout.append((idx, round(remaining, 3)))
            break
        layout.append((idx, round(dur, 3)))
        acc += dur
        i += 1
    return layout
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH="$WT" "$PY" -m pytest tests/unit/services/video_renderers/test_shot_list_renderer.py -o addopts="" -q -k TestFitSceneDurations`
Expected: PASS (6 tests).

- [ ] **Step 5: Ruff + commit**

```bash
"$PY" -m ruff check services/video_renderers/shot_list_renderer.py tests/unit/services/video_renderers/test_shot_list_renderer.py
git add services/video_renderers/shot_list_renderer.py tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -m "feat(video): _fit_scene_durations — layout shots to span narration

Refs Glad-Labs/poindexter#867
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: async ffprobe helper + wire `narration_fit` into `render_shot_list`

**Files:**

- Modify: `services/video_renderers/shot_list_renderer.py`
- Modify: `services/settings_defaults.py`
- Test: `tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Consumes: `_fit_scene_durations` (Task 1).
- Produces: `_probe_duration_s(path: str, *, ffprobe: str = "ffprobe") -> float | None` (async); `render_shot_list(..., narration_fit: bool = False, narration_fit_max_shot_s: float = 9.0)`.

- [ ] **Step 1: Write the failing test**

```python
class TestRenderShotListNarrationFit:
    @pytest.mark.asyncio
    async def test_narration_fit_rescales_scenes_to_narration(self, monkeypatch, tmp_path):
        """With narration_fit on, scene durations are laid out to span the probed
        narration (54s over a 45s shot list)."""
        from services.video_renderers import shot_list_renderer as slr

        captured = {}

        class _FakeComposition:
            success = True
            output_path = str(tmp_path / "out.mp4")
            file_size_bytes = 10
            duration_s = 54.0

        class _FakeCompositor:
            def __init__(self, *a, **k): ...
            async def compose(self, request):
                captured["scene_durs"] = [s.duration_s for s in request.scenes]
                return _FakeComposition()

        monkeypatch.setattr(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            _FakeCompositor,
        )

        async def _fake_probe(path, *, ffprobe="ffprobe"):
            return 54.0
        monkeypatch.setattr(slr, "_probe_duration_s", _fake_probe)

        # 3 shots × 15s = 45s planned; narration 54s → scale 1.2.
        durs = [15.0, 15.0, 15.0]
        _patch_render_pass_with_durations(monkeypatch, slr, durs)  # helper below

        result = await slr.render_shot_list(
            post_id="t", shot_list=_shot_list_with_durations(durs),
            audio_path=str(tmp_path / "narr.mp3"),
            output_path=str(tmp_path / "out.mp4"),
            image_gen_url="http://x", site_config=None,
            narration_fit=True, narration_fit_max_shot_s=9.0,
        )
        assert result.success
        assert abs(sum(captured["scene_durs"]) - 54.0) < 0.1

    @pytest.mark.asyncio
    async def test_narration_fit_off_keeps_director_durations(self, monkeypatch, tmp_path):
        """Default (long lane): scene durations are the director's, unrescaled."""
        # identical harness as above but narration_fit defaults False;
        # assert sum(scene_durs) == 45.0 (the director total), probe NOT consulted.
        ...
```

Add two small local helpers in the test module if not already present (mirror the existing render-path patching used by `TestRenderShotList*`; reuse the file's existing `_shot_list`/`_render_one_shot` mock pattern rather than inventing new fakes). If the existing tests already have a compositor-capture + render-pass-mock fixture, reuse it and only add the two `narration_fit` assertions.

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH="$WT" "$PY" -m pytest tests/unit/services/video_renderers/test_shot_list_renderer.py -o addopts="" -q -k TestRenderShotListNarrationFit`
Expected: FAIL (`render_shot_list() got an unexpected keyword argument 'narration_fit'`).

- [ ] **Step 3a: Add the async ffprobe helper**

Add to `shot_list_renderer.py` (module needs `import asyncio`, `import json` — check existing imports first, add only what's missing):

```python
async def _probe_duration_s(path: str, *, ffprobe: str = "ffprobe") -> float | None:
    """Return a media file's duration in seconds via ffprobe, or None if it
    can't be read. Async (non-blocking) — used to fit the short's visuals to the
    real narration length. Best-effort: any failure returns None and the caller
    falls back to the director's durations."""
    if not path or not os.path.exists(path):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "quiet", "-print_format", "json",
            "-show_format", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        fmt = (json.loads(stdout.decode() or "{}") or {}).get("format") or {}
        dur = float(fmt.get("duration", 0.0))
        return dur if dur > 0 else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("[SHOT_LIST] narration ffprobe failed for %s: %s", path, exc)
        return None
```

- [ ] **Step 3b: Add params + replace the scene-build loop**

In `render_shot_list`'s signature (after `progress_cb`):

```python
    narration_fit: bool = False,
    narration_fit_max_shot_s: float = 9.0,
```

Replace the current scene-build loop (`scenes: list[CompositionScene] = []` … `for r in rendered:` … append) with:

```python
    # Narration-fit (short lane, issue #867): lay out the scenes to span the
    # ACTUAL narration so the compositor never clones the final frame to cover an
    # overhang (the frozen tail). No-op when narration already fits; scoped by the
    # caller to the short lane so the long lane's pacing is untouched.
    shot_durs = [float(r.duration_s) for r in rendered]
    scene_plan: list[tuple[int, float]] = list(enumerate(shot_durs))
    if narration_fit and audio_path:
        narration_dur = await _probe_duration_s(audio_path)
        if narration_dur:
            scene_plan = _fit_scene_durations(
                shot_durs, narration_dur, max_shot_s=narration_fit_max_shot_s,
            )
            logger.info(
                "[SHOT_LIST] narration-fit: %d shots (%.1fs) → %d scenes for %.1fs "
                "narration", len(rendered), sum(shot_durs), len(scene_plan),
                narration_dur,
            )
    scenes: list[CompositionScene] = [
        CompositionScene(
            clip_path=rendered[idx].clip_path or "",
            narration_path=None,
            duration_s=dur,
        )
        for idx, dur in scene_plan
    ]
```

Also update the `metadata={... "shot_count": len(rendered) ...}` to `"shot_count": len(scenes)` so the metadata reflects the laid-out scene count.

- [ ] **Step 3c: Add the two settings**

In `settings_defaults.py` `DEFAULTS` (near the other `video_*` keys), with a comment:

```python
    # Short-lane fit-to-narration (issue #867). Master switch: the short renderer
    # rescales the director's shots to span the ACTUAL narration so the final
    # frame is never frozen to cover an overhang. false ⇒ legacy (freeze tail).
    'video_narration_fit_enabled': 'true',
    # Per-shot ceiling for the fit rescale — no single image is held longer than
    # this; beyond it the shot sequence cycles instead of stretching.
    'video_short_max_shot_seconds': '9',
```

In the metadata dict:

```python
    'video_narration_fit_enabled': {'owner': 'media_render', 'value_type': 'boolean'},
    'video_short_max_shot_seconds': {'owner': 'media_render', 'value_type': 'float'},
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH="$WT" "$PY" -m pytest tests/unit/services/video_renderers/test_shot_list_renderer.py -o addopts="" -q`
Expected: PASS (all, incl. the two new narration-fit tests).

- [ ] **Step 5: Ruff + silent-except lint + commit**

```bash
"$PY" -m ruff check services/video_renderers/shot_list_renderer.py services/settings_defaults.py tests/unit/services/video_renderers/test_shot_list_renderer.py
"$PY" scripts/ci/lint_silent_excepts.py
git add services/video_renderers/shot_list_renderer.py services/settings_defaults.py tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -m "feat(video): render_shot_list fits short visuals to narration

Refs Glad-Labs/poindexter#867
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `render_from_state` short-lane plumbing

**Files:**

- Modify: `modules/content/atoms/_media_render.py`
- Modify: `modules/content/atoms/media_render_short_video.py`
- Test: `tests/unit/services/atoms/test_media_render_video.py`

**Interfaces:**

- Consumes: `render_shot_list(..., narration_fit, narration_fit_max_shot_s)` (Task 2).
- Produces: `render_from_state(..., narration_fit: bool = False)`.

- [ ] **Step 1: Write the failing test**

```python
    @pytest.mark.asyncio
    async def test_short_lane_passes_narration_fit_to_renderer(self):
        """media.render_short_video → render_from_state(narration_fit=True) →
        render_shot_list gets narration_fit=True + the site-config ceiling."""
        site_config = SiteConfig(initial_config={
            "video_narration_fit_enabled": "true",
            "video_short_max_shot_seconds": "9",
        })
        state = {"task_id": "t", "short_shot_list": _LONG_SHOT_LIST, "site_config": site_config}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            from modules.content.atoms import media_render_short_video
            await media_render_short_video.run(state)
        kw = mock_render.await_args.kwargs
        assert kw["narration_fit"] is True
        assert kw["narration_fit_max_shot_s"] == 9.0

    @pytest.mark.asyncio
    async def test_long_lane_does_not_enable_narration_fit(self):
        state = {"task_id": "t", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert mock_render.await_args.kwargs["narration_fit"] is False

    @pytest.mark.asyncio
    async def test_narration_fit_disabled_via_setting(self):
        site_config = SiteConfig(initial_config={"video_narration_fit_enabled": "false"})
        state = {"task_id": "t", "short_shot_list": _LONG_SHOT_LIST, "site_config": site_config}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="short_shot_list", output_key="short_video_path",
                narration_fit=True,
            )
        assert mock_render.await_args.kwargs["narration_fit"] is False
```

(`_LONG_SHOT_LIST` / `_ok_result` / the `_patch_gpu_lock` autouse fixture already exist in this module. `_ok_result` already carries `shots_carded`/`shots_substituted` from the ladder PR.)

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH="$WT" "$PY" -m pytest tests/unit/services/atoms/test_media_render_video.py -o addopts="" -q -k "narration_fit or short_lane or long_lane"`
Expected: FAIL (`render_shot_list` mock asserts `narration_fit` not passed / `render_from_state` has no such kwarg).

- [ ] **Step 3a: `render_from_state` — add the param + resolve + pass**

Add `narration_fit: bool = False` to the signature (after `caption_key`). Then, where `narration`/`ambient`/`caption` are resolved (around line 124-130), add:

```python
    # Narration-fit is opt-in per lane (short only) AND gated by the setting.
    fit_enabled = bool(narration_fit) and (
        site_config.get_bool("video_narration_fit_enabled", True)
        if site_config is not None else True
    )
    fit_max_shot_s = (
        site_config.get_float("video_short_max_shot_seconds", 9.0)
        if site_config is not None else 9.0
    )
```

In the `render_shot_list(...)` call (line 170-183), add:

```python
                    narration_fit=fit_enabled,
                    narration_fit_max_shot_s=fit_max_shot_s,
```

- [ ] **Step 3b: `media_render_short_video.run` — opt in**

```python
    return await render_from_state(
        state,
        shot_list_key="short_shot_list",
        output_key="short_video_path",
        narration_key="short_narration_audio_path",
        caption_key="short_caption_srt_path",
        narration_fit=True,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH="$WT" "$PY" -m pytest tests/unit/services/atoms/test_media_render_video.py -o addopts="" -q`
Expected: PASS (all).

- [ ] **Step 5: Ruff + commit**

```bash
"$PY" -m ruff check modules/content/atoms/_media_render.py modules/content/atoms/media_render_short_video.py tests/unit/services/atoms/test_media_render_video.py
git add modules/content/atoms/_media_render.py modules/content/atoms/media_render_short_video.py tests/unit/services/atoms/test_media_render_video.py
git commit -m "feat(video): short render atom opts into narration-fit

Refs Glad-Labs/poindexter#867
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Part 2a — canonical target setting + clamp + parameterized prompt

**Files:**

- Modify: `modules/content/stages/generate_video_shot_list.py`
- Modify: `modules/content/stages/generate_media_scripts.py`
- Modify: `services/settings_defaults.py`
- Test: the nearest existing stage tests (find with `Glob tests/unit/**/*generate_video_shot_list*` and `*generate_media_scripts*`; create `tests/unit/services/stages/test_generate_media_scripts.py` if none covers `_build_scene_prompt`).

**Interfaces:**

- Produces: `_estimate_short_duration(short_script: str, target_seconds: float = 45.0)`; `_build_scene_prompt(title, clean_content, site_name, *, target_seconds: int, target_words: int)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_estimate_short_duration_clamps_to_target():
    from modules.content.stages.generate_video_shot_list import _estimate_short_duration
    # 200 words / 2.5 = 80s → clamped to the target
    assert _estimate_short_duration("word " * 200, target_seconds=45.0) == 45.0
    assert _estimate_short_duration("word " * 200, target_seconds=30.0) == 30.0
    # short scripts still floor at 15s
    assert _estimate_short_duration("word " * 10, target_seconds=45.0) == 15.0

def test_scene_prompt_carries_target_no_hardcoded_counts():
    from modules.content.stages.generate_media_scripts import _build_scene_prompt
    p = _build_scene_prompt("T", "body", "Site", target_seconds=45, target_words=112)
    assert "45-second" in p and "112 words" in p
    assert "150 words" not in p and "60-second" not in p
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH="$WT" "$PY" -m pytest <the two test files> -o addopts="" -q -k "estimate_short_duration_clamps or scene_prompt_carries"`
Expected: FAIL (`_estimate_short_duration` has no `target_seconds`; prompt still says "150 words").

- [ ] **Step 3a: `_estimate_short_duration` — take the target**

```python
def _estimate_short_duration(short_script: str, target_seconds: float = 45.0) -> float:
    """Estimate the short-form clip duration from word count, clamped to
    [15, target_seconds]. ``target_seconds`` is the operator-tunable short target
    (``video_short_target_seconds``); the upper bound was a hardcoded 45."""
    if not short_script:
        return _DEFAULT_SHORT_DURATION_S
    word_count = len(short_script.split())
    estimated = word_count / _WORDS_PER_SECOND
    return max(15.0, min(estimated, target_seconds))
```

Call site (line 803) — `cfg` is in scope:

```python
                target_duration_s=_estimate_short_duration(
                    short_summary_script,
                    cfg.get_int("video_short_target_seconds", 45),
                ),
```

- [ ] **Step 3b: `_build_scene_prompt` — parameterize the ask**

```python
def _build_scene_prompt(
    title: str, clean_content: str, site_name: str,
    *, target_seconds: int, target_words: int,
) -> str:
    """Build the prompt for the video-scenes + short-summary LLM call.

    The short narration's second/word target is substituted from
    ``video_short_target_seconds`` (issue #867) — never hardcoded — so the prompt
    ask and the shot-list clamp agree.
    """
    return (
        "Generate TWO things for a blog post video:\n\n"
        "PART 1 — Write 6-8 numbered lines, each describing a photorealistic image "
        "for a video slideshow about this article. Each line is a Stable Diffusion XL prompt. "
        "Requirements: cinematic lighting, no people, no text, no faces, no hands, 4K quality. "
        "One scene per line.\n\n"
        "PART 2 — After a blank line, write \"SHORT:\" on its own line, then write a "
        f"~{target_seconds}-second narration (about {target_words} words) "
        "summarizing the article for TikTok/YouTube Shorts. "
        f"Start with a hook, cover 2-3 key takeaways, end with \"Full article at {site_name}.\"\n\n"
        f"ARTICLE: {title}\n\n"
        f"{clean_content[:3000]}\n\n"
        "SCENES:"
    )
```

Call site (line 211) — `sc` is in scope; add a local pace constant near the top of `generate_media_scripts.py` (mirrors the director's, kept local to avoid cross-stage import):

```python
# Narration pace estimate, mirrors generate_video_shot_list._WORDS_PER_SECOND.
_WORDS_PER_SECOND = 2.5
```

```python
            short_target_s = sc.get_int("video_short_target_seconds", 45) if sc is not None else 45
            scene_prompt = _build_scene_prompt(
                title, clean_content,
                sc.get("site_name", "our site") if sc is not None else "our site",
                target_seconds=short_target_s,
                target_words=round(short_target_s * _WORDS_PER_SECOND),
            )
```

- [ ] **Step 3c: Add the setting**

`DEFAULTS`:

```python
    # Canonical short-form target length (issue #867): drives BOTH the short
    # prompt's narration ask AND the shot-list duration clamp, so they can't
    # disagree (they used to — 60s prompt vs 45s clamp → guaranteed frozen tail).
    'video_short_target_seconds': '45',
```

metadata:

```python
    'video_short_target_seconds': {'owner': 'video', 'value_type': 'integer'},
```

- [ ] **Step 4: Run to verify pass**

Run the two test files `-k "estimate_short_duration or scene_prompt"` → PASS, then the whole stage test files → PASS.

- [ ] **Step 5: Ruff + commit**

```bash
"$PY" -m ruff check modules/content/stages/generate_video_shot_list.py modules/content/stages/generate_media_scripts.py services/settings_defaults.py <test files>
git add -A
git commit -m "feat(video): one canonical short target drives clamp + prompt

Refs Glad-Labs/poindexter#867
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Part 2b — deterministic runaway trim + advisory finding

**Files:**

- Modify: `modules/content/stages/generate_media_scripts.py`
- Modify: `services/settings_defaults.py`
- Test: `tests/unit/services/stages/test_generate_media_scripts.py`

**Interfaces:**

- Produces: `_trim_to_word_budget(text: str, max_words: int) -> tuple[str, int, int]` → `(trimmed_text, original_words, kept_words)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_trim_noop_within_budget():
    from modules.content.stages.generate_media_scripts import _trim_to_word_budget
    text = "One sentence. Two sentence."
    assert _trim_to_word_budget(text, 50) == (text, 4, 4)

def test_trim_cuts_on_sentence_boundary():
    from modules.content.stages.generate_media_scripts import _trim_to_word_budget
    text = "Aaa bbb ccc. Ddd eee fff. Ggg hhh iii jjj kkk."  # 3+3+5 = 11 words
    trimmed, orig, kept = _trim_to_word_budget(text, 7)
    assert trimmed == "Aaa bbb ccc. Ddd eee fff."   # stops at the sentence ≤ 7
    assert orig == 11 and kept == 6

def test_trim_hardcuts_a_single_overlong_sentence():
    from modules.content.stages.generate_media_scripts import _trim_to_word_budget
    text = "word " * 200  # one run, no sentence break
    trimmed, orig, kept = _trim_to_word_budget(text.strip(), 50)
    assert kept == 50 and orig == 200
```

- [ ] **Step 2: Run to verify failure**

Run the file `-k trim` → FAIL (`ImportError: _trim_to_word_budget`).

- [ ] **Step 3a: Implement the trim helper**

Add to `generate_media_scripts.py` (module already imports `re`):

```python
# Sentence boundary for the runaway-short trim (#867): split after . ! ? + space.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _trim_to_word_budget(text: str, max_words: int) -> tuple[str, int, int]:
    """Trim ``text`` to the last COMPLETE sentence within ``max_words``.

    Returns ``(trimmed, original_words, kept_words)``. No-op when already within
    budget. If the very first sentence already exceeds the budget (a single
    run-on), hard-cut at ``max_words`` as a last resort (rare). Never cuts
    mid-sentence otherwise — the short narration stays coherent.
    """
    words = text.split()
    original = len(words)
    if original <= max_words:
        return text, original, original
    kept: list[str] = []
    count = 0
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        w = len(sentence.split())
        if count + w > max_words:
            break
        kept.append(sentence)
        count += w
    trimmed = " ".join(kept).strip()
    if not trimmed:
        return " ".join(words[:max_words]).strip(), original, max_words
    return trimmed, original, count
```

- [ ] **Step 3b: Wire it in after the parse + emit the finding**

`generate_media_scripts.py` imports `emit_finding` (add `from utils.findings import emit_finding` with the other imports). After `_parse_scene_output` (right after the `logger.info("[MEDIA] Video scenes…")` at ~line 252-255), inside the `if scene_output:` block:

```python
                if short_summary:
                    short_max_s = sc.get_int("video_short_max_seconds", 60) if sc is not None else 60
                    max_short_words = round(short_max_s * _WORDS_PER_SECOND)
                    short_summary, orig_w, kept_w = _trim_to_word_budget(
                        short_summary, max_short_words,
                    )
                    if kept_w < orig_w:
                        logger.info(
                            "[MEDIA] short script trimmed %d→%d words (max %d)",
                            orig_w, kept_w, max_short_words,
                        )
                        emit_finding(
                            source="media.generate_scripts",
                            kind="short_script_trimmed",
                            title=f"short script trimmed {orig_w}→{kept_w} words",
                            body=(
                                f"The short summary for task {context.get('task_id')} ran "
                                f"{orig_w} words (~{orig_w / _WORDS_PER_SECOND:.0f}s), over the "
                                f"video_short_max_seconds budget of {short_max_s}s "
                                f"({max_short_words} words). Trimmed to the last full sentence "
                                f"within budget ({kept_w} words) so the short stays short. "
                                "Advisory — tighten the short prompt if this is frequent."
                            ),
                            severity="info",
                            dedup_key=f"short_script_trimmed:{context.get('task_id')}",
                            extra={
                                "task_id": str(context.get("task_id") or ""),
                                "original_words": orig_w,
                                "trimmed_words": kept_w,
                                "max_words": max_short_words,
                            },
                        )
```

- [ ] **Step 3c: Add the setting**

`DEFAULTS`:

```python
    # Hard cap on the short narration length (issue #867). A runaway short script
    # (the model ignoring the target) is trimmed to the last full sentence within
    # this budget so a "short" can't balloon to 2-3 minutes. > target_seconds to
    # leave headroom — the renderer's narration-fit stretches the gap.
    'video_short_max_seconds': '60',
```

metadata:

```python
    'video_short_max_seconds': {'owner': 'video', 'value_type': 'integer'},
```

- [ ] **Step 4: Run to verify pass**

Run the file `-k trim` → PASS; then the whole `test_generate_media_scripts.py` → PASS.

- [ ] **Step 5: Ruff + silent-except lint + commit**

```bash
"$PY" -m ruff check modules/content/stages/generate_media_scripts.py services/settings_defaults.py tests/unit/services/stages/test_generate_media_scripts.py
"$PY" scripts/ci/lint_silent_excepts.py
git add -A
git commit -m "feat(video): trim a runaway short script to keep shorts short

Refs Glad-Labs/poindexter#867
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Live proof, full regression, docs, spec status

**Files:**

- Scratchpad proof script (NOT committed).
- Modify: `docs/superpowers/specs/2026-07-15-short-video-narration-fit-design.md` (status → implemented).

- [ ] **Step 1: In-process live proof (real ffmpeg)** — write a scratchpad script that renders a short via `render_shot_list(narration_fit=True)` with a real (synthesized-silence-or-tone) narration audio LONGER than the shot list (e.g. a 45s shot list + a ~70s audio), then ffprobes the output. Assert `abs(output_dur − 70s) ≤ ~1s` (spans narration, no frozen tail) and every scene ≤ 9s. Generate the test audio with ffmpeg (`-f lavfi -i anullsrc -t 70`). Run with the worktree venv + `PYTHONPATH`.

- [ ] **Step 2: Full regression**

Run: `PYTHONPATH="$WT" "$PY" -m pytest tests/unit/services/video_renderers/ tests/unit/services/atoms/ tests/unit/services/stages/ tests/unit/services/test_settings_defaults.py tests/unit/services/test_settings_categories.py -o addopts="" -q`
Expected: all green. Fix any fake/result-shape breakage (mirror the ladder PR's `_ok_result` pattern).

- [ ] **Step 3: services.md freshness** — no new `services/` module is created (only edits + a `_`-prefixed helper), so the services-doc ratchet should not trip; confirm with `git diff --name-status main...HEAD | grep '^A.*services/'` (expect only docs). If a new module slipped in, rerun `scripts/regen-services-doc.py`.

- [ ] **Step 4: Spec status → implemented**

Edit the spec header `**Status:**` to `implemented 2026-07-15 (glad-labs-stack#2637)` with a one-line proof summary. Commit:

```bash
git add docs/superpowers/specs/2026-07-15-short-video-narration-fit-design.md
git commit -m "docs(video): mark short-video fit-to-narration spec implemented

Refs Glad-Labs/poindexter#867
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: Push, verify CI green, merge**

`git push`; poll `test-backend` to terminal (it gates behind the silent-except lint); on green, squash-merge `#2637` with `Closes Glad-Labs/poindexter#867` in the body; then deploy (FF the deploy checkout, `docker restart poindexter-prefect-worker poindexter-worker`, verify the 4 settings seeded). Use superpowers:finishing-a-development-branch for the merge flow.

---

## Self-Review

- **Spec coverage:** Part 1 (fit) → Tasks 1-3; Part 2a (target+clamp+prompt) → Task 4; Part 2b (trim) → Task 5; settings → Tasks 2/4/5; live proof + rollout → Task 6. All spec sections mapped.
- **Type consistency:** `_fit_scene_durations` returns `list[tuple[int, float]]` (produced T1, consumed T2). `render_shot_list(narration_fit, narration_fit_max_shot_s)` (defined T2, called T3). `_estimate_short_duration(target_seconds)` / `_build_scene_prompt(*, target_seconds, target_words)` (T4). `_trim_to_word_budget → (str, int, int)` (T5). Consistent across tasks.
- **No placeholders:** every code step shows real code; the only "find the nearest test file" instruction (T4) is explicit about the Glob to run and the fallback (create the file).
- **Settings:** 4 total — `video_narration_fit_enabled` + `video_short_max_shot_seconds` (T2), `video_short_target_seconds` (T4), `video_short_max_seconds` (T5); each added to BOTH dicts in `settings_defaults.py`.

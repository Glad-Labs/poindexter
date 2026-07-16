# Never Drop a Shot — Fallback Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (Subagent-driven development is disabled in this environment per operator policy — execute inline.)

**Goal:** Make every planned shot render _something_ so the video's visual timeline always equals the plan — eliminating the dropped-shot defect that shortens ~a third of videos.

**Architecture:** Add a per-shot **fallback ladder** to `shot_list_renderer.py`: a shot that produces no clip cascades through (2) a cross-family Pexels substitute and (3) a guaranteed Pillow-rendered branded card, so `rendered = [r for r in shot_results if r.success and r.clip_path]` (line 1121) never drops anything. Re-key the ship/redispatch gate in `_media_render.py` from "shots rendered" to a "real-source ratio" so a mostly-card render (a real outage) still defers.

**Tech Stack:** Python 3.12, asyncio, Pillow (PIL — already a dependency), pytest/pytest-asyncio, the existing `PexelsVideoProvider`/`PexelsProvider`, `emit_finding`, `site_config` DI seam.

## Global Constraints

- **Never raise from the renderer.** Every new path is best-effort; a failure falls to the next rung, and the card floor cannot fail. A backfill exception must not take the render down.
- **No-AI-humans policy holds.** The cross-family substitute is **image-gen-family → Pexels only**; never Pexels → image-gen (would risk AI-generating a human subject). Pexels misses go straight to the card.
- **Config in DB, not code:** new tunables go in `services/settings_defaults.py` — both the `DEFAULTS` value dict AND the owner/`value_type` metadata dict. Never in a migration.
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **Worktree venv caveat:** a fresh worktree may have no venv. If `poetry run pytest` errors on a missing env, run the main checkout's poetry python against the ABS test path with `-o addopts=""` (see `reference_run_worktree_tests`). Commands below assume `cd src/cofounder_agent`.
- **`site_config=None`** is the legacy/test path and MUST keep working (card enabled by default, real-source ratio defaults to 0.5).

---

### Task 1: Guaranteed branded card (`_render_brand_card`)

The rung-3 floor: a pure-Pillow still (navy field + centered wordmark) that physically cannot fail. Rendered as a PNG the compositor consumes exactly like an image-gen still — no ffmpeg, no network, no GPU.

**Files:**

- Modify: `src/cofounder_agent/services/video_renderers/shot_list_renderer.py` (add helpers after `_render_generative_clip`, ~line 440)
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Produces: `def _render_brand_card(*, output_path: str, width: int, height: int, wordmark: str) -> bool` — returns True when a PNG is on disk. `def _load_card_font(size: int)` (internal).

- [ ] **Step 1: Write the failing test**

Add to `test_shot_list_renderer.py`:

```python
class TestRenderBrandCard:
    """The guaranteed rung-3 card — pure PIL, no network, cannot fail."""

    def test_card_writes_valid_png_at_requested_dims(self, tmp_path):
        from PIL import Image
        from services.video_renderers.shot_list_renderer import _render_brand_card

        out = str(tmp_path / "card.png")
        ok = _render_brand_card(output_path=out, width=1920, height=1080, wordmark="Glad Labs")

        assert ok is True
        with Image.open(out) as im:
            assert im.size == (1920, 1080)
            assert im.mode == "RGB"

    def test_card_renders_solid_field_when_wordmark_empty(self, tmp_path):
        from PIL import Image
        from services.video_renderers.shot_list_renderer import _render_brand_card

        out = str(tmp_path / "card_blank.png")
        ok = _render_brand_card(output_path=out, width=1080, height=1920, wordmark="")

        assert ok is True
        with Image.open(out) as im:
            assert im.size == (1080, 1920)

    def test_card_survives_font_load_failure(self, tmp_path, monkeypatch):
        """Even if every font path fails, the solid navy field still saves."""
        from PIL import Image, ImageFont
        from services.video_renderers import shot_list_renderer as slr

        def _boom(*a, **k):
            raise OSError("no fonts")

        monkeypatch.setattr(ImageFont, "truetype", _boom)
        monkeypatch.setattr(ImageFont, "load_default", _boom)

        out = str(tmp_path / "card_nofont.png")
        ok = slr._render_brand_card(output_path=out, width=640, height=360, wordmark="X")

        assert ok is True
        with Image.open(out) as im:
            assert im.size == (640, 360)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestRenderBrandCard -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_render_brand_card'`

- [ ] **Step 3: Implement `_render_brand_card` + `_load_card_font`**

Insert after `_render_generative_clip` (before `_render_one_shot`, ~line 440):

```python
# Brand palette (dark-techno set, video-director SKILL.md STYLE POLICY).
_CARD_FIELD_RGB = (10, 26, 47)    # #0A1A2F deep navy
_CARD_WORDMARK_RGB = (34, 211, 238)  # #22D3EE cyan


def _load_card_font(size: int):
    """Best-effort TrueType wordmark font, falling back to PIL's bundled
    bitmap default. ``load_default`` never raises, so the card always has a
    usable font — the wordmark degrades in quality, never in guarantee."""
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_brand_card(
    *, output_path: str, width: int, height: int, wordmark: str,
) -> bool:
    """Rung-3 of the fallback ladder: a guaranteed branded still card.

    Pure PIL — no network, no GPU, no ffmpeg — so a shot slot is never empty
    even in a total image-gen + Pexels outage. Returns a PNG the compositor
    consumes like any image-gen still. Two-tier: a centered wordmark when a
    font + draw succeed, else a text-less solid navy field (the draw is wrapped
    so a decoration failure never breaks the save). Returns True when a PNG is
    on disk.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (max(1, width), max(1, height)), _CARD_FIELD_RGB)
    try:
        text = (wordmark or "").strip()
        if text:
            draw = ImageDraw.Draw(img)
            font = _load_card_font(size=max(24, width // 18))
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                ((width - tw) / 2, (height - th) / 2),
                text, fill=_CARD_WORDMARK_RGB, font=font,
            )
    except Exception as exc:  # noqa: BLE001 — decoration must not break the floor
        logger.debug("[SHOT_LIST] card wordmark draw failed, using solid field: %s", exc)
    try:
        img.save(output_path, format="PNG")
    except OSError as exc:
        logger.warning("[SHOT_LIST] card save failed for %s: %s", output_path, exc)
        return False
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestRenderBrandCard -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_list_renderer.py src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -F - <<'EOF'
feat(video): guaranteed branded-card renderer (rung-3 fallback floor)

Pure-PIL still (navy field + centered wordmark) that physically cannot fail,
consumed by the compositor like an image-gen still. The floor of the
never-drop-a-shot fallback ladder.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 2: Card-only backfill ladder — the "never drop" core

Fill any shot with no clip via the card, tag the rung, thread it through the audit and the result counts, and integrate into `render_shot_list` before the drop filter. Cross-family substitution (rung 2) is added in Task 3 — this task ships the guarantee with the card alone.

**Files:**

- Modify: `src/cofounder_agent/services/video_renderers/shot_list_renderer.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Consumes: `_render_brand_card` (Task 1), `_ShotState`, `ShotRenderResult`, `_log_shot_audit`, `emit_finding`.
- Produces:
  - `_ShotState.rung: str = "primary"`
  - `async def _backfill_pass(states, *, render_kwargs, site_config, post_id, width, height, wordmark) -> None`
  - `def _card_enabled(site_config) -> bool`
  - `def _emit_shot_fallback_finding(*, shot, post_id, rung) -> None`
  - `ShotListRenderResult.shots_substituted: int = 0`, `ShotListRenderResult.shots_carded: int = 0`
  - `_log_shot_audit(..., rung: str = "primary")`
  - setting `video_fallback_card_enabled` (default `true`)

- [ ] **Step 1: Write the failing tests**

Add to `test_shot_list_renderer.py`:

```python
class TestBackfillPass:
    """A failed shot is filled with a card, not dropped."""

    def _failed_state(self, idx=0, source="image_gen"):
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import _ShotState, ShotRenderResult

        shot = Shot(idx=idx, duration_s=6.0, intent="establish topic",
                    source=source, prompt="isometric 3d, empty server hall",
                    narration_offset_s=0.0)
        res = ShotRenderResult(idx=idx, source=source, success=False,
                               error="image-gen render returned no image")
        return _ShotState(shot=shot, result=res, is_reused=False)

    @pytest.mark.asyncio
    async def test_failed_shot_filled_with_card(self, tmp_path):
        from services.video_renderers.shot_list_renderer import _backfill_pass

        st = self._failed_state()
        render_kwargs = dict(work_dir=tmp_path, image_gen_url="", site_config=None,
                             http_client_factory=None, pexels_key="", orientation="landscape",
                             post_id="p1")

        await _backfill_pass([st], render_kwargs=render_kwargs, site_config=None,
                             post_id="p1", width=1920, height=1080, wordmark="Glad Labs")

        assert st.result.success is True
        assert st.result.clip_path.endswith("_card.png")
        assert st.rung == "card"
        assert st.result.duration_s == 6.0  # timeline slot preserved

    @pytest.mark.asyncio
    async def test_card_disabled_leaves_shot_dropped(self, tmp_path):
        from unittest.mock import MagicMock
        from services.video_renderers.shot_list_renderer import _backfill_pass

        sc = MagicMock()
        sc.get.return_value = "false"  # video_fallback_card_enabled=false
        st = self._failed_state()
        render_kwargs = dict(work_dir=tmp_path, image_gen_url="", site_config=sc,
                             http_client_factory=None, pexels_key="", orientation="landscape",
                             post_id="p1")

        await _backfill_pass([st], render_kwargs=render_kwargs, site_config=sc,
                             post_id="p1", width=1920, height=1080, wordmark="Glad Labs")

        assert st.result.success is False
        assert st.rung == "dropped"

    @pytest.mark.asyncio
    async def test_successful_shot_untouched(self, tmp_path):
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import (
            _ShotState, ShotRenderResult, _backfill_pass)

        shot = Shot(idx=1, duration_s=5.0, intent="x", source="pexels",
                    query="server room", narration_offset_s=6.0)
        st = _ShotState(shot=shot,
                        result=ShotRenderResult(idx=1, source="pexels", success=True,
                                                clip_path="/tmp/real.mp4", duration_s=5.0),
                        is_reused=False)
        render_kwargs = dict(work_dir=tmp_path, image_gen_url="", site_config=None,
                             http_client_factory=None, pexels_key="", orientation="landscape",
                             post_id="p1")

        await _backfill_pass([st], render_kwargs=render_kwargs, site_config=None,
                             post_id="p1", width=1920, height=1080, wordmark="Glad Labs")

        assert st.result.clip_path == "/tmp/real.mp4"
        assert st.rung == "primary"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestBackfillPass -v`
Expected: FAIL — `ImportError: cannot import name '_backfill_pass'`

- [ ] **Step 3a: Add the `rung` field to `_ShotState`**

In `shot_list_renderer.py`, `_ShotState` (ends ~line 132), add:

```python
    attempts: int = 0  # regen rounds spent on this shot
    rung: str = "primary"  # fill provenance: primary | substitute | card | dropped
```

- [ ] **Step 3b: Add the card-enabled gate + finding + backfill pass**

Insert after `_render_brand_card` (from Task 1):

```python
def _card_enabled(site_config: Any) -> bool:
    """Master switch for the guaranteed-card rung (default on). Off ⇒ a shot
    with no clip drops as before (legacy behaviour, for A/B / never-card)."""
    if site_config is None:
        return True
    return str(
        site_config.get("video_fallback_card_enabled", "true") or "true",
    ).strip().lower() in ("true", "1", "yes")


def _emit_shot_fallback_finding(*, shot: Shot, post_id: str, rung: str) -> None:
    """Advisory (info) finding when a shot was filled by the fallback ladder
    rather than its primary source. Info severity ⇒ lands in audit_log (the
    Findings/Pipeline dashboards read it) but is below the router floor, so it
    does NOT page — the 'mostly cards = outage' escalation is the gate's job."""
    emit_finding(
        source="shot_list_renderer",
        kind="shot_fallback",
        title=f"shot {shot.idx} ({shot.source}) filled via {rung}",
        body=(
            f"shot {shot.idx} ({shot.source}) produced no clip from its primary "
            f"source; the fallback ladder filled the slot via '{rung}' so the "
            f"timeline stays whole. Many cards in one render signal an "
            f"image-gen/Pexels outage. Advisory only."
        ),
        severity="info",
        dedup_key=f"shot_fallback:{post_id}:{shot.idx}",
        extra={"shot_idx": shot.idx, "source": shot.source, "rung": rung},
    )


async def _backfill_pass(
    states: list[_ShotState],
    *,
    render_kwargs: dict[str, Any],
    site_config: Any,
    post_id: str,
    width: int,
    height: int,
    wordmark: str,
) -> None:
    """Fill every shot that produced no clip so the slot is never empty (never
    drop a shot). Card-only ladder in this task; the cross-family substitute
    (rung 2) is inserted here in Task 3. Sets ``st.rung`` and preserves each
    shot's ``duration_s`` so the concat still sums to the plan.
    """
    card_ok = _card_enabled(site_config)
    work_dir: Path = render_kwargs["work_dir"]
    for st in states:
        if st.result.success and st.result.clip_path:
            continue  # primary / holdover / substitute already filled the slot
        # (Task 3 inserts the cross-family Pexels substitute here.)
        if card_ok:
            card_path = str(work_dir / f"shot_{st.shot.idx:02d}_card.png")
            if _render_brand_card(
                output_path=card_path, width=width, height=height, wordmark=wordmark,
            ):
                st.result = ShotRenderResult(
                    idx=st.shot.idx, source=st.shot.source, success=True,
                    clip_path=card_path, duration_s=st.shot.duration_s,
                )
                st.rung = "card"
                _emit_shot_fallback_finding(shot=st.shot, post_id=post_id, rung="card")
                continue
        st.rung = "dropped"
```

- [ ] **Step 3c: Thread `rung` into the audit + skip QA for backfilled shots**

In `_log_shot_audit` (line 161), add a `rung` param and include it in the JSON:

```python
async def _log_shot_audit(
    pool: Any,
    *,
    post_id: str,
    shot_result: ShotRenderResult,
    qa_score: float | None = None,
    qa_outcome: str | None = None,
    rung: str = "primary",
) -> None:
```

and in the `json.dumps({...})` payload add:

```python
                "qa_outcome": qa_outcome,
                "rung": rung,
```

In `_finalize_pass` (line 879 loop), make the FIRST branch skip QA for backfilled shots, and pass `rung` to the audit call:

```python
        if st.rung in ("substitute", "card"):
            # Backfilled slot — result is already final, no QA verdict applies.
            qa_outcome = st.rung
        elif st.is_reused:
            ...  # (unchanged)
```

and change the audit call (line 937) to:

```python
        await _log_shot_audit(
            pool, post_id=post_id, shot_result=result,
            qa_score=qa_score, qa_outcome=qa_outcome, rung=st.rung,
        )
```

- [ ] **Step 3d: Add result-count fields + integrate into `render_shot_list`**

In `ShotListRenderResult` (line 94) add:

```python
    shots_rendered: int = 0
    shots_total: int = 0
    shots_substituted: int = 0
    shots_carded: int = 0
    error: str | None = None
```

In `render_shot_list`, replace the block that runs the passes (lines 1107–1128) so backfill runs before finalize and counts are computed:

```python
    await _repair_pass(
        states, qa=qa, site_config=site_config,
        render_kwargs=render_kwargs, pool=pool,
    )
    wordmark = ""
    if site_config is not None:
        wordmark = str(site_config.get("site_name", "") or "").strip()
    await _backfill_pass(
        states, render_kwargs=render_kwargs, site_config=site_config,
        post_id=post_id, width=width, height=height, wordmark=wordmark,
    )
    shot_results = await _finalize_pass(
        states, qa=qa, pool=pool, post_id=post_id,
    )

    shots_substituted = sum(1 for st in states if st.rung == "substitute")
    shots_carded = sum(1 for st in states if st.rung == "card")

    rendered = [r for r in shot_results if r.success and r.clip_path]
    if not rendered:
        return ShotListRenderResult(
            success=False,
            shots_total=len(shot_list.shots),
            shots_rendered=0,
            shots_substituted=shots_substituted,
            shots_carded=shots_carded,
            error="no shots rendered — director output unrenderable",
        )
```

Add the two counts to BOTH remaining returns — the compositor-failed return (line 1181) and the success return (line 1191):

```python
    return ShotListRenderResult(
        success=True,
        output_path=composition.output_path,
        file_size_bytes=composition.file_size_bytes,
        duration_s=composition.duration_s,
        shots_rendered=len(rendered),
        shots_total=len(shot_list.shots),
        shots_substituted=shots_substituted,
        shots_carded=shots_carded,
    )
```

(and on the compositor-failed `ShotListRenderResult(success=False, ...)` add `shots_substituted=shots_substituted, shots_carded=shots_carded,`.)

- [ ] **Step 3e: Seed the `video_fallback_card_enabled` setting**

In `services/settings_defaults.py`, in the `DEFAULTS` dict near the other video-render keys (~line 388, after `video_render_min_shot_ratio`):

```python
    'video_render_min_shot_ratio': '0.5',
    # Master switch for the guaranteed rung-3 branded card in the shot-list
    # renderer's fallback ladder. true (default) ⇒ a shot that renders no clip
    # from any real source is filled with a branded card so the timeline stays
    # whole (never drop a shot). false ⇒ legacy behaviour (the shot drops).
    'video_fallback_card_enabled': 'true',
```

and in the owner/value_type metadata dict (~line 2367, near `video_render_min_shot_ratio`):

```python
    'video_render_min_shot_ratio': {'owner': 'media_render', 'value_type': 'float'},
    'video_fallback_card_enabled': {'owner': 'media_render', 'value_type': 'boolean'},
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestBackfillPass -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full renderer + settings test files (no regressions)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/ tests/unit/services/test_settings_defaults.py -q`
Expected: PASS (existing tests still green; audit/finalize changes didn't break the suite)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_list_renderer.py src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -F - <<'EOF'
feat(video): never-drop-a-shot backfill ladder (card floor)

A shot that renders no clip is now filled with a guaranteed branded card
instead of being silently dropped from the concat, so the visual timeline
always equals the plan. Adds per-shot rung tracking (audit + result counts),
the shot_fallback advisory finding, and the video_fallback_card_enabled switch.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 3: Cross-family Pexels substitute (rung 2)

Before falling to a card, an **image-gen-family** shot that hard-failed (server wedged) retries as real Pexels footage from a prompt-derived query. Turns the dominant failure mode (image-gen outage) into real footage instead of cards.

**Files:**

- Modify: `src/cofounder_agent/services/video_renderers/shot_list_renderer.py`
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Consumes: `_render_pexels_video`, `_render_pexels_image` (existing), `_backfill_pass` (Task 2).
- Produces: `def _pexels_query_from_shot(shot: Shot) -> str`, `async def _substitute_failed_shot(shot, *, work_dir, pexels_key, orientation, http_client_factory) -> str | None`, `_IMAGE_GEN_FAMILY` frozenset.

- [ ] **Step 1: Write the failing tests**

```python
class TestCrossFamilySubstitute:
    def test_query_strips_style_modifier(self):
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import _pexels_query_from_shot

        shot = Shot(idx=0, duration_s=6.0, intent="establish the data center",
                    source="image_kenburns",
                    prompt="isometric 3D illustration, empty server hall, cyan palette",
                    narration_offset_s=0.0)
        assert _pexels_query_from_shot(shot) == "empty server hall"

    def test_query_falls_back_to_intent_when_no_prompt(self):
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import _pexels_query_from_shot

        shot = Shot(idx=0, duration_s=6.0, intent="city skyline at night",
                    source="image_gen", prompt="", narration_offset_s=0.0)
        assert _pexels_query_from_shot(shot) == "city skyline at night"

    @pytest.mark.asyncio
    async def test_image_gen_failure_substitutes_pexels_video(self, tmp_path, monkeypatch):
        from services.video_renderers import shot_list_renderer as slr

        async def _fake_video(*, output_path, **kwargs):
            with open(output_path, "wb") as f:
                f.write(b"mp4")
            return True

        monkeypatch.setattr(slr, "_render_pexels_video", _fake_video)

        st = TestBackfillPass()._failed_state(source="image_gen")
        render_kwargs = dict(work_dir=tmp_path, image_gen_url="", site_config=None,
                             http_client_factory=None, pexels_key="KEY",
                             orientation="landscape", post_id="p1")

        await slr._backfill_pass([st], render_kwargs=render_kwargs, site_config=None,
                                 post_id="p1", width=1920, height=1080, wordmark="GL")

        assert st.rung == "substitute"
        assert st.result.success is True
        assert st.result.clip_path.endswith("_sub.mp4")

    @pytest.mark.asyncio
    async def test_pexels_source_never_substitutes_to_image_gen(self, tmp_path):
        """A missed pexels shot must go straight to the card, never image-gen
        (no-AI-humans policy)."""
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import (
            _ShotState, ShotRenderResult, _backfill_pass)

        shot = Shot(idx=0, duration_s=5.0, intent="developer at desk",
                    source="pexels", query="developer typing", narration_offset_s=0.0)
        st = _ShotState(shot=shot,
                        result=ShotRenderResult(idx=0, source="pexels", success=False,
                                                error="pexels miss at idx=0"),
                        is_reused=False)
        render_kwargs = dict(work_dir=tmp_path, image_gen_url="", site_config=None,
                             http_client_factory=None, pexels_key="KEY",
                             orientation="landscape", post_id="p1")

        await _backfill_pass([st], render_kwargs=render_kwargs, site_config=None,
                             post_id="p1", width=1920, height=1080, wordmark="GL")

        assert st.rung == "card"  # NOT substitute — pexels never routes to image-gen
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestCrossFamilySubstitute -v`
Expected: FAIL — `ImportError: cannot import name '_pexels_query_from_shot'`

- [ ] **Step 3a: Add the query helper + substitute helper**

Insert before `_backfill_pass`:

```python
# Style-modifier prefixes the director prepends to AI-source prompts
# (video-director SKILL.md STYLE POLICY); stripped to recover the subject
# nouns for a Pexels search when an image-gen shot must fall back to footage.
_STYLE_MODIFIERS = (
    "flat vector illustration", "cinematic illustration", "isometric 3d",
    "line art", "cyberpunk neon", "glassmorphism", "low poly", "watercolor",
    "pixel art", "paper cutout",
)

# Sources whose failures may substitute to Pexels. Pexels itself is excluded —
# a missed pexels shot goes straight to the card (never image-gen a human).
_IMAGE_GEN_FAMILY = frozenset({"image_gen", "image_kenburns", "generative", "wan21"})


def _pexels_query_from_shot(shot: Shot) -> str:
    """Best-effort stock-search query for an image-gen-family shot falling back
    to Pexels. Prefers the concrete prompt subject (leading style-modifier and
    trailing palette clause stripped) over the abstract intent. The image-gen
    prompt policy forbids human nouns, so the derived subject is non-human by
    contract."""
    prompt = (shot.prompt or "").strip()
    low = prompt.lower()
    for mod in _STYLE_MODIFIERS:
        if low.startswith(mod):
            prompt = prompt[len(mod):].lstrip(" ,").strip()
            break
    subject = prompt.split(",")[0].strip() if prompt else ""
    return subject or (shot.intent or "").strip()


async def _substitute_failed_shot(
    shot: Shot,
    *,
    work_dir: Path,
    pexels_key: str,
    orientation: str,
    http_client_factory: Any,
) -> str | None:
    """Rung-2 cross-family substitute: an image-gen-family shot whose render
    hard-failed retries as REAL Pexels footage (video first, then photo) from a
    prompt/intent-derived query. Returns a clip_path or None. The reverse
    direction (pexels → image-gen) is intentionally never attempted."""
    if not pexels_key:
        return None
    query = _pexels_query_from_shot(shot)
    if not query:
        return None
    video_path = str(work_dir / f"shot_{shot.idx:02d}_sub.mp4")
    if await _render_pexels_video(
        query=query, output_path=video_path, api_key=pexels_key,
        orientation=orientation, http_client_factory=http_client_factory,
    ):
        return video_path
    photo_path = str(work_dir / f"shot_{shot.idx:02d}_sub.jpg")
    if await _render_pexels_image(
        query=query, output_path=photo_path, api_key=pexels_key,
        orientation=orientation, http_client_factory=http_client_factory,
    ):
        return photo_path
    return None
```

- [ ] **Step 3b: Insert rung 2 into `_backfill_pass`**

In `_backfill_pass`, replace the `# (Task 3 inserts ...)` comment with:

```python
        if st.shot.source in _IMAGE_GEN_FAMILY:
            sub_path = await _substitute_failed_shot(
                st.shot,
                work_dir=work_dir,
                pexels_key=render_kwargs["pexels_key"],
                orientation=render_kwargs["orientation"],
                http_client_factory=render_kwargs["http_client_factory"],
            )
            if sub_path:
                st.result = ShotRenderResult(
                    idx=st.shot.idx, source=st.shot.source, success=True,
                    clip_path=sub_path, duration_s=st.shot.duration_s,
                )
                st.rung = "substitute"
                _emit_shot_fallback_finding(
                    shot=st.shot, post_id=post_id, rung="substitute",
                )
                continue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_renderer.py::TestCrossFamilySubstitute tests/unit/services/video_renderers/test_shot_list_renderer.py::TestBackfillPass -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_list_renderer.py src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -F - <<'EOF'
feat(video): cross-family Pexels substitute (fallback ladder rung 2)

An image-gen-family shot that hard-fails (server wedged) now retries as real
Pexels footage from a prompt-derived query before falling to a card, turning
the dominant image-gen-outage failure into real footage. Pexels->image-gen is
never done (no-AI-humans policy).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 4: Reframe the ship/redispatch gate onto real-source ratio

With the ladder every slot fills, so `shots_rendered/shots_total` is always 1.0 and the old gate goes dead. Re-key it to `(shots_rendered − shots_carded)/shots_total` so a mostly-card render (a real outage) still defers, while a few cards ship.

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_media_render.py:214-278`
- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_media_render_video.py`

**Interfaces:**

- Consumes: `ShotListRenderResult.shots_carded` / `.shots_rendered` / `.shots_total` (Task 2).
- Produces: setting `video_render_min_real_source_ratio` (default `0.5`); gate keyed on real-source ratio.

- [ ] **Step 1: Write the failing tests**

Add to `test_media_render_video.py` (match its existing fixture style for `render_from_state`; these assert on the returned output-key and emitted findings):

```python
class TestRealSourceGate:
    @pytest.fixture(autouse=True)
    def _no_gpu_lock(self, monkeypatch):
        # render_from_state wraps the render in `async with gpu.lock("video")`,
        # which reaches a real pg_advisory_lock on the operator PC (bootstrap
        # DSN) and flakes (see gpu-lock-unit-tests gotcha). No-op it here.
        import contextlib
        from services import gpu_scheduler

        @contextlib.asynccontextmanager
        async def _noop(*a, **k):
            yield

        monkeypatch.setattr(gpu_scheduler.gpu, "lock", _noop)

    @pytest.mark.asyncio
    async def test_mostly_cards_rejects_and_redispatches(self, monkeypatch, tmp_path):
        """8/10 shots are cards → real ratio 0.2 < 0.5 → empty output (redispatch)."""
        from modules.content.atoms import _media_render as mr

        result = _make_render_result(  # helper in this test module
            success=True, output_path=str(tmp_path / "v.mp4"),
            shots_total=10, shots_rendered=10, shots_carded=8)
        monkeypatch.setattr(mr, "render_shot_list", _async_return(result))

        out = await mr.render_from_state(_state_with_shotlist(tmp_path),
                                         shot_list_key="video_shot_list",
                                         output_key="long_video_path",
                                         narration_key="long_narration_audio_path",
                                         caption_key="long_caption_srt_path")
        assert out == {"long_video_path": ""}  # rejected

    @pytest.mark.asyncio
    async def test_few_cards_ships(self, monkeypatch, tmp_path):
        """1/10 cards → real ratio 0.9 ≥ 0.5 → ships the complete video."""
        from modules.content.atoms import _media_render as mr

        vpath = tmp_path / "v.mp4"; vpath.write_bytes(b"mp4")
        result = _make_render_result(
            success=True, output_path=str(vpath),
            shots_total=10, shots_rendered=10, shots_carded=1)
        monkeypatch.setattr(mr, "render_shot_list", _async_return(result))

        out = await mr.render_from_state(_state_with_shotlist(tmp_path),
                                         shot_list_key="video_shot_list",
                                         output_key="long_video_path",
                                         narration_key="long_narration_audio_path",
                                         caption_key="long_caption_srt_path")
        assert out == {"long_video_path": str(vpath)}  # shipped
```

Add these test helpers at the top of the class/module if not already present:

```python
def _make_render_result(**kw):
    from services.video_renderers.shot_list_renderer import ShotListRenderResult
    return ShotListRenderResult(**kw)

def _async_return(value):
    async def _fn(*a, **k):
        return value
    return _fn

def _state_with_shotlist(tmp_path):
    return {
        "video_shot_list": {"version": 1, "total_duration_s": 60.0,
                            "shots": [{"idx": 0, "duration_s": 60.0, "intent": "x",
                                       "source": "image_gen", "prompt": "p",
                                       "narration_offset_s": 0.0}],
                            "director_model": "m", "director_prompt_version": "v1",
                            "director_decided_at": "2026-07-15T00:00:00Z"},
        "long_narration_audio_path": "", "task_id": "t1",
        "site_config": None, "database_service": None,
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_media_render_video.py::TestRealSourceGate -v`
Expected: FAIL — the current gate keys off `shots_rendered < shots_total` (always false here since 10==10), so `test_mostly_cards_rejects_and_redispatches` fails (video ships instead of rejecting).

- [ ] **Step 3a: Re-key the gate in `_media_render.py`**

Replace the partial-render block (lines 214–278, the `if result.shots_rendered < result.shots_total:` gate) with a real-source-ratio gate:

```python
    # Real-source ratio gate: the fallback ladder guarantees every slot renders
    # (shots_rendered == shots_total), so a "shots dropped" ratio no longer
    # carries signal. Key off how much of the video is a rung-3 branded card:
    # a few cards (isolated misses) ship; a mostly-card render (a real image-gen
    # + Pexels outage) is rejected + redispatched to defer until infra recovers.
    real_shots = result.shots_rendered - result.shots_carded
    real_ratio = real_shots / max(result.shots_total, 1)
    min_real_ratio = (
        site_config.get_float("video_render_min_real_source_ratio", 0.5)
        if site_config is not None else 0.5
    )
    if real_ratio < min_real_ratio:
        emit_finding(
            source="media.render_video",
            kind="partial_render_rejected",
            title=(
                f"{output_key}: only {real_shots}/{result.shots_total} shots from "
                "a real source — below the real-source floor, treated as failed"
            ),
            body=(
                f"Only {real_shots} of {result.shots_total} shots for task "
                f"{task_id} came from a real source "
                f"({result.shots_carded} branded-card fill(s); real ratio "
                f"{real_ratio:.0%} < the video_render_min_real_source_ratio of "
                f"{min_real_ratio:.0%}). The render was rejected instead of "
                "shipping a mostly-card video; the media_reconciliation watchdog "
                "will re-dispatch it once image-gen/Pexels recover. Check the "
                "per-shot video_shot_rendered audit rows (rung=card) for which "
                "sources failed."
            ),
            severity="warn",
            dedup_key=f"partial_render_rejected:{task_id}:{output_key}",
            extra={
                "task_id": str(task_id or ""),
                "output_key": output_key,
                "shots_total": result.shots_total,
                "shots_carded": result.shots_carded,
                "real_ratio": real_ratio,
                "min_real_ratio": min_real_ratio,
            },
        )
        return {output_key: ""}
    if result.shots_carded or result.shots_substituted:
        # A complete-but-degraded video (some fallback fills, above the floor)
        # ships, but never silently — surface it for triage.
        emit_finding(
            source="media.render_video",
            kind="partial_render",
            title=(
                f"{output_key}: {result.shots_carded} card + "
                f"{result.shots_substituted} substitute fill(s) of "
                f"{result.shots_total} shots"
            ),
            body=(
                f"Task {task_id}: the video is complete but "
                f"{result.shots_carded} shot(s) were branded-card fills and "
                f"{result.shots_substituted} were cross-family Pexels "
                "substitutes. Check the per-shot video_shot_rendered audit rows "
                "for which primary sources failed."
            ),
            severity="warn",
            dedup_key=f"partial_render:{task_id}:{output_key}",
            extra={
                "task_id": str(task_id or ""),
                "output_key": output_key,
                "shots_total": result.shots_total,
                "shots_carded": result.shots_carded,
                "shots_substituted": result.shots_substituted,
            },
        )

    return {output_key: result.output_path or ""}
```

- [ ] **Step 3b: Seed the new setting + deprecate the old one**

In `services/settings_defaults.py` `DEFAULTS`, next to `video_fallback_card_enabled`:

```python
    # Below this fraction of real-source (non-card) shots, a render is rejected
    # + redispatched rather than shipping a mostly-card video (a real image-gen
    # + Pexels outage). Supersedes video_render_min_shot_ratio (now inert — the
    # fallback ladder guarantees a full render, so a shots-dropped ratio is
    # always 1.0). Left seeded, not deleted, to avoid settings reseed-drift.
    'video_render_min_real_source_ratio': '0.5',
```

and in the metadata dict:

```python
    'video_render_min_real_source_ratio': {'owner': 'media_render', 'value_type': 'float'},
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_media_render_video.py -v`
Expected: PASS (new gate tests green; existing `_media_render` tests still green — update any that asserted the old `shots_rendered < shots_total` message to the new real-source finding).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_media_render.py src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/atoms/test_media_render_video.py
git commit -F - <<'EOF'
feat(video): re-key the render gate to a real-source ratio

The fallback ladder makes shots_rendered==shots_total always, so the old
partial-render gate went dead. Re-key it to (rendered - carded)/total: a few
cards ship, a mostly-card render (real outage) is rejected + redispatched.
Adds video_render_min_real_source_ratio; leaves the old key inert.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 5: Verify end-to-end, docs, and live proof

Full-suite regression, services-doc freshness, live in-process proof (not a prod dispatch), and mark the spec implemented.

**Files:**

- Modify: `docs/superpowers/specs/2026-07-15-never-drop-a-shot-fallback-ladder-design.md` (status)
- Possibly regenerated: `docs/reference/services.md`

- [ ] **Step 1: Full touched-suite regression**

Run:

```bash
cd src/cofounder_agent && poetry run pytest \
  tests/unit/services/video_renderers/ \
  tests/unit/services/atoms/test_media_render_video.py \
  tests/unit/services/test_settings_defaults.py -q
```

Expected: PASS, 0 failures.

- [ ] **Step 2: services.md freshness gate**

`shot_list_renderer.py` and `_media_render.py` already exist and their module-docstring first lines are unchanged, so the doc likely doesn't move — but the gate is non-blocking-yet-required-in-original-commit, so verify:

```bash
cd src/cofounder_agent && python ../../scripts/regen-services-doc.py && git diff --stat docs/reference/services.md
```

If it shows a diff, `git add docs/reference/services.md` and amend it into Task 4's commit or a new `docs:` commit. If no diff, nothing to do.

- [ ] **Step 3: Live in-process proof (force the card rung)**

Prove a complete, correctly-timed MP4 with cards standing in for failed shots — in-process inside the prefect-worker, NOT a prod dispatch (re-dispatch can re-upload to YouTube). Run this one-off script via the worker container's env:

```python
# scratchpad/verify_backfill.py — run inside prefect-worker
import asyncio, tempfile
from schemas.video_shot_list import Shot, VideoShotList
from services.video_renderers.shot_list_renderer import render_shot_list

async def main():
    shots = [
        Shot(idx=0, duration_s=6.0, intent="opener", source="image_gen",
             prompt="isometric 3d, empty server hall", narration_offset_s=0.0),
        Shot(idx=1, duration_s=6.0, intent="beat two", source="image_kenburns",
             prompt="cyberpunk neon, glowing circuits", narration_offset_s=6.0),
    ]
    sl = VideoShotList(version=1, total_duration_s=12.0, shots=shots,
                       director_model="m", director_prompt_version="v1",
                       director_decided_at="2026-07-15T00:00:00Z")
    out = f"{tempfile.gettempdir()}/verify_backfill.mp4"
    # Dead image-gen URL + no pexels key ⇒ both shots forced to the card rung.
    res = await render_shot_list(
        post_id="verify", shot_list=sl, audio_path="", output_path=out,
        image_gen_url="http://127.0.0.1:1/dead", site_config=None, pool=None,
        width=1920, height=1080)
    print("success=", res.success, "carded=", res.shots_carded,
          "rendered=", res.shots_rendered, "path=", res.output_path)

asyncio.run(main())
```

Run (copy the script in, exec it, then ffprobe the output from inside the container):

```bash
docker cp scratchpad/verify_backfill.py poindexter-prefect-worker:/tmp/verify_backfill.py
docker exec -w /app/src/cofounder_agent poindexter-prefect-worker \
  python /tmp/verify_backfill.py
docker exec poindexter-prefect-worker \
  ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/verify_backfill.mp4
```

Expected: `success= True carded= 2 rendered= 2`, and ffprobe duration ≈ 12.0s (both slots present, timeline whole). If the print shows `carded= 0`, the image-gen URL wasn't dead-ended — confirm nothing is listening on the dead port.

- [ ] **Step 4: Mark the spec implemented + commit**

Edit the spec header: `**Status:** design — approved 2026-07-15` → `**Status:** implemented 2026-07-15 (PR #2633)`.

```bash
git add docs/superpowers/specs/2026-07-15-never-drop-a-shot-fallback-ladder-design.md
git commit -F - <<'EOF'
docs(video): mark never-drop-a-shot spec implemented

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

- [ ] **Step 5: Push and update the PR**

```bash
git push
```

The draft PR #2633 updates automatically. Mark it ready for review once CI is green.

---

## Fast-follows (separate specs — not in this plan)

1. **Short-script length** — the narrated short script runs ~4× the 15–45s clamp (`audio_duration_mismatch` avg ratio 3.86), so the short lane keeps a frozen tail even with complete shots. Fix at the script (`_estimate_short_duration` / the short-script generator), not by rescaling the video.
2. **Image-gen wedge root cause** — the recurring host-port/boot-window unavailability that drives the mass shot failures the ladder now absorbs.

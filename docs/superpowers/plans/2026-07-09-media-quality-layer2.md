# Media Quality Layer 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the binary `0.0|1.0` media `quality_score` with a real 0–100 semantic signal — video (shot-fidelity aggregate + topic-match vision check) and podcast (full-episode Whisper transcription + LLM faithfulness judge).

**Architecture:** Layer 2 slots into the two functions that already compute the operator-visible media score — `media_quality_service.evaluate_video()` / `evaluate_podcast()` — running only after Layer 1 passes. Each signal is a focused, fail-soft helper; a composition step folds them into a 0–100 score and stamps `layer2_status`. Advisory-first: low sub-scores emit findings but never auto-reject.

**Tech Stack:** Python 3.13, asyncio, asyncpg, ffmpeg/ffprobe (baked into worker image), LiteLLM via `dispatch_complete`, Speaches faster-whisper via `caption_providers`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-media-quality-layer2-design.md`

## Global Constraints

- **Scale:** `quality_score` is 0–100 (unifies with the text pipeline). Layer-1 hard-fail → `0`. Layer-1 pass + Layer-2 unavailable/disabled → `100`. Layer-1 pass + computed → 0–100 composite.
- **Fail-soft, never raise:** any Layer-2 miss (no model, dispatch raise, no transcript, no frame, master switch off) → that signal is `"unavailable"`, emits NO finding, and the composite falls back to `100` on a Layer-1 pass. A Layer-2 exception must never fail the seed.
- **Advisory-first:** Layer 2 informs the score + emits advisory findings (`severity="warn"`, dedup-keyed by post + signal). It does NOT auto-reject.
- **Fall back to `100`, never lower**, when Layer 2 can't compute — a passing file must not regress just because a model was offline. Always stamp `quality_signals.layer2_status` (`"scored"` / `"unavailable"` / `"disabled"`).
- **All tunables in `app_settings`**, `media.*` prefix, seeded in `settings_defaults.py`. Empty model key = operator-disabled (skip + unavailable), not a crash (`feedback_no_silent_defaults`).
- **`max_tokens ≥ 1024`** on every `qa_vision_model` (qwen3-vl) dispatch — its `<think>` trace shares the budget and empties the answer at 300 (PR #2224 lesson).
- **Backcompat:** `site_config` is optional (`= None`) at every layer; None → Layer-2 skipped (bare-100 pass), existing callers/tests unaffected.
- **Reuse (build only the edge):** `dispatch_complete`, `caption_providers.get_caption_provider`, `emit_finding`, `media_quality_service._probe_duration` / `_run_argv`. Do NOT import from `modules/content/` — `media_quality_service` is substrate.
- TDD, one behavior per test, frequent commits, linear history (no merge commits).
- Run tests with the worktree Poetry env: `cd src/cofounder_agent && poetry run pytest <path> -v`.

## File Structure

| File                                                                                             | Responsibility                                                                                                                            |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `services/media_quality_service.py` (modify)                                                     | All Layer-2 helpers + integration into `evaluate_video`/`evaluate_podcast`.                                                               |
| `services/media_approval_service.py` (modify)                                                    | Thread `site_config` through `record_pending` → `_evaluate_and_notify` → `evaluate_*`; fix the Discord score display for the 0–100 scale. |
| `services/jobs/media_distribute.py`, `podcast_distribute.py`, `media_reconciliation.py` (modify) | Pass `site_config` (from `config["_site_config"]`) into `record_pending`.                                                                 |
| `services/settings_defaults.py` (modify)                                                         | Seed the 7 `media.*` Layer-2 keys.                                                                                                        |
| `services/migrations/<ts>_rescale_media_quality_score.py` (create)                               | One-shot backfill of historical binary scores ×100.                                                                                       |
| `infrastructure/grafana/dashboards/pipeline.json` (modify)                                       | "Media Quality — Layer 2" panels.                                                                                                         |
| `tests/unit/services/test_media_quality_service.py` (modify)                                     | Layer-2 unit tests (fail-soft matrix, score math, aggregate, findings).                                                                   |
| `tests/unit/services/test_settings_defaults.py` (modify)                                         | Assert the new keys seed.                                                                                                                 |

---

### Task 1: Seed the Layer-2 settings defaults

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: seven `app_settings` keys read by every later task — `media.layer2.enabled`, `media.video.topic_match_model`, `media.video.topic_match_frames`, `media.video.topic_match_min`, `media.video.shot_fidelity_min`, `media.podcast.faithfulness_model`, `media.podcast.faithfulness_min`.

- [ ] **Step 1: Write the failing test**

Add to `test_settings_defaults.py`:

```python
def test_media_layer2_defaults_seeded():
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["media.layer2.enabled"] == "true"
    assert DEFAULTS["media.video.topic_match_frames"] == "3"
    assert DEFAULTS["media.video.topic_match_min"] == "50"
    assert DEFAULTS["media.video.shot_fidelity_min"] == "60"
    assert DEFAULTS["media.podcast.faithfulness_min"] == "60"
    # Model keys default empty → resolved from qa_vision_model / ragas_judge_model
    # at read time (empty = "not separately configured"), per the spec.
    assert DEFAULTS["media.video.topic_match_model"] == ""
    assert DEFAULTS["media.podcast.faithfulness_model"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_media_layer2_defaults_seeded -v`
Expected: FAIL with `KeyError: 'media.layer2.enabled'`.

- [ ] **Step 3: Add the defaults**

In `settings_defaults.py`, inside the `DEFAULTS` dict (add a grouped block near the other `media.*` keys):

```python
    # --- Media Quality Layer 2 (semantic scoring) — spec 2026-07-09 -----------
    "media.layer2.enabled": "true",
    # Empty → resolved from qa_vision_model at read time (see media_quality_service).
    "media.video.topic_match_model": "",
    "media.video.topic_match_frames": "3",
    "media.video.topic_match_min": "50",
    "media.video.shot_fidelity_min": "60",
    # Empty → resolved from ragas_judge_model at read time.
    "media.podcast.faithfulness_model": "",
    "media.podcast.faithfulness_min": "60",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_media_layer2_defaults_seeded -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(media-qa): seed Layer 2 settings defaults"
```

---

### Task 2: Shot-fidelity aggregate helper

**Files:**

- Modify: `src/cofounder_agent/services/media_quality_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_media_quality_service.py`

**Interfaces:**

- Consumes: `db` (asyncpg Pool/Connection with `.fetch`).
- Produces: `async def _aggregate_shot_qa_scores(db, post_id: str) -> dict` returning
  `{"shot_fidelity": float | None, "shot_coverage": float, "scored": int, "total": int}`.
  `shot_fidelity` is the mean `qa_score` over scored, non-reused rows, or `None` when `scored == 0`.

- [ ] **Step 1: Write the failing test**

Add to `test_media_quality_service.py`:

```python
@pytest.mark.asyncio
async def test_aggregate_shot_qa_scores_excludes_reused_and_unscored():
    from services import media_quality_service

    rows = [
        {"qa_score": 90.0, "outcome": "accepted"},
        {"qa_score": 70.0, "outcome": "regenerated"},
        {"qa_score": None, "outcome": "unscored"},        # excluded
        {"qa_score": 95.0, "outcome": "skipped_reused"},  # excluded
    ]

    class _DB:
        async def fetch(self, *_a, **_k):
            return rows

    out = await media_quality_service._aggregate_shot_qa_scores(_DB(), "post-1")
    assert out["shot_fidelity"] == 80.0          # mean(90, 70)
    assert out["scored"] == 2
    assert out["total"] == 4
    assert out["shot_coverage"] == 0.5           # 2 / 4


@pytest.mark.asyncio
async def test_aggregate_shot_qa_scores_none_when_no_scored_rows():
    from services import media_quality_service

    class _DB:
        async def fetch(self, *_a, **_k):
            return [{"qa_score": None, "outcome": "unscored"}]

    out = await media_quality_service._aggregate_shot_qa_scores(_DB(), "post-1")
    assert out["shot_fidelity"] is None
    assert out["scored"] == 0
    assert out["total"] == 1
    assert out["shot_coverage"] == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k aggregate_shot_qa -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_aggregate_shot_qa_scores'`.

- [ ] **Step 3: Implement the helper**

Add to `media_quality_service.py` (after `_probe_silence_ratio`, before `_file_size`):

```python
_SHOT_EXCLUDE_OUTCOMES = frozenset({"unscored", "skipped_reused"})


async def _aggregate_shot_qa_scores(db: Any, post_id: str) -> dict[str, Any]:
    """Aggregate the per-shot vision scores shot_list_renderer already wrote.

    Reads the ``video_shot_rendered`` audit rows for ``post_id`` and averages
    the ``qa_score`` values over shots that were actually scored (excludes
    ``unscored`` / ``skipped_reused`` outcomes). ``shot_coverage`` is the
    fraction of shots scored — an informational confidence indicator, NOT a
    weight on the composite. Returns ``shot_fidelity=None`` when nothing was
    scored (caller relies on the topic-match signal alone).
    """
    rows = await db.fetch(
        """
        SELECT (details->>'qa_score')::float AS qa_score,
               details->>'qa_outcome'        AS outcome
        FROM audit_log
        WHERE event_type = 'video_shot_rendered'
          AND details->>'post_id' = $1
        """,
        post_id,
    )
    total = len(rows)
    scored_vals = [
        r["qa_score"] for r in rows
        if r["qa_score"] is not None
        and (r["outcome"] or "") not in _SHOT_EXCLUDE_OUTCOMES
    ]
    scored = len(scored_vals)
    fidelity = (sum(scored_vals) / scored) if scored else None
    coverage = (scored / total) if total else 0.0
    return {
        "shot_fidelity": fidelity,
        "shot_coverage": coverage,
        "scored": scored,
        "total": total,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k aggregate_shot_qa -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/media_quality_service.py src/cofounder_agent/tests/unit/services/test_media_quality_service.py
git commit -m "feat(media-qa): shot-fidelity aggregate from video_shot_rendered audit rows"
```

---

### Task 3: Video topic-match helper (vision, one call per video)

**Files:**

- Modify: `src/cofounder_agent/services/media_quality_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_media_quality_service.py`

**Interfaces:**

- Consumes: `dispatch_complete` (mocked), `_run_argv`/`_probe_duration` (existing).
- Produces:
  - `async def _extract_frame_at(file_path: str, at_s: float) -> bytes | None`
  - `async def _score_video_topic_match(db, file_path: str, title: str, site_config: Any) -> float | None` — extracts N evenly-spaced frames, sends them in ONE `dispatch_complete` call, returns a 0–100 score or `None` (fail-soft).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_topic_match_scores_and_uses_thinking_safe_budget(tmp_path, monkeypatch):
    from services import media_quality_service as m

    async def _fake_frame(file_path, at_s):
        return b"\x89PNG-fake"
    monkeypatch.setattr(m, "_extract_frame_at", _fake_frame)
    monkeypatch.setattr(m, "_probe_duration", AsyncMock(return_value=30.0))

    dispatch = AsyncMock(return_value=SimpleNamespace(text='{"score": 82}'))
    monkeypatch.setattr(m, "dispatch_complete", dispatch, raising=False)

    sc = _SC({"media.video.topic_match_model": "ollama/qwen3-vl:30b",
              "media.video.topic_match_frames": "3"})
    score = await m._score_video_topic_match(_DBStub(), "/v.mp4", "GPU undervolting", sc)

    assert score == 82.0
    _, kwargs = dispatch.call_args
    assert kwargs["max_tokens"] >= 1024          # #2224 regression pin


@pytest.mark.asyncio
async def test_topic_match_unavailable_when_no_model(monkeypatch):
    from services import media_quality_service as m
    sc = _SC({"media.video.topic_match_model": ""})   # + qa_vision_model empty
    score = await m._score_video_topic_match(_DBStub(), "/v.mp4", "t", sc)
    assert score is None


@pytest.mark.asyncio
async def test_topic_match_fail_soft_on_dispatch_raise(tmp_path, monkeypatch):
    from services import media_quality_service as m
    async def _fake_frame(file_path, at_s):
        return b"png"
    monkeypatch.setattr(m, "_extract_frame_at", _fake_frame)
    monkeypatch.setattr(m, "_probe_duration", AsyncMock(return_value=30.0))
    monkeypatch.setattr(m, "dispatch_complete",
                        AsyncMock(side_effect=RuntimeError("ollama down")), raising=False)
    sc = _SC({"media.video.topic_match_model": "ollama/qwen3-vl:30b"})
    assert await m._score_video_topic_match(_DBStub(), "/v.mp4", "t", sc) is None
```

Add these module-level test helpers near the top of the test file if not already present:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock


class _DBStub:
    async def fetch(self, *_a, **_k): return []
    async def fetchrow(self, *_a, **_k): return None
    async def execute(self, *_a, **_k): return None


class _SC:
    """Sync .get site_config stub (mirrors the real SiteConfig.get contract)."""
    def __init__(self, cfg): self._c = cfg
    def get(self, k, d=None): return self._c.get(k, d)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k topic_match -v`
Expected: FAIL — `_score_video_topic_match` undefined.

- [ ] **Step 3: Implement the helpers**

Add near the top of `media_quality_service.py`, after the existing imports:

```python
from services.llm_providers.dispatcher import dispatch_complete
```

Add the constants near `_DEFAULT_THRESHOLDS`:

```python
# qwen3-vl's <think> trace shares the token budget; 1024 keeps the small JSON
# answer from being starved (PR #2224 lesson — same as shot_vision_qa).
_LAYER2_VISION_MAX_TOKENS = 1024
_TOPIC_MATCH_PROMPT = (
    "You are grading whether a video still belongs in an article titled "
    "{title!r}. Consider subject, style, and on-topic-ness. Reply with ONLY a "
    'JSON object: {{"score": <0-100 integer>}}. 100 = perfectly on-topic, '
    "0 = unrelated."
)
```

Add the helpers (after `_aggregate_shot_qa_scores`):

```python
async def _extract_frame_at(file_path: str, at_s: float) -> bytes | None:
    """Extract ONE frame at ``at_s`` seconds as PNG bytes. None on any failure.

    Reuses the module's ``_run_argv`` (no shell) and the fast ``-ss`` seek
    pattern. Fail-soft: missing ffmpeg / failed extract / missing output → None.
    """
    if not shutil.which("ffmpeg"):
        return None
    out = os.path.join(
        tempfile.gettempdir(),
        f"mediaqa_topic_{os.getpid()}_{abs(hash((file_path, at_s))) % 10**8}.png",
    )
    try:
        rc, _, _ = await _run_argv(
            ["ffmpeg", "-y", "-hide_banner", "-nostats",
             "-ss", f"{max(0.0, at_s):.3f}", "-i", file_path,
             "-frames:v", "1", out],
        )
    except Exception:  # noqa: BLE001 — fail-soft
        return None
    if rc != 0:
        return None
    try:
        with open(out, "rb") as f:
            return f.read() or None
    except OSError:
        return None
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def _resolve_topic_match_model(site_config: Any) -> str:
    """media.video.topic_match_model, falling back to qa_vision_model. '' → skip."""
    raw = (site_config.get("media.video.topic_match_model", "") or "").strip()
    if raw:
        return raw
    return (site_config.get("qa_vision_model", "") or "").strip()


async def _score_video_topic_match(
    db: Any, file_path: str, title: str, site_config: Any,
) -> float | None:
    """Sample N frames from the composed video and vision-score topic match.

    One ``dispatch_complete`` call carrying all N frames → one 0–100 score.
    Returns None (fail-soft) on: no model, no duration, no extractable frame,
    dispatch error, or unparseable response.
    """
    import base64
    import json as _json
    import re as _re

    model = _resolve_topic_match_model(site_config)
    if not model:
        return None
    try:
        n = int(site_config.get("media.video.topic_match_frames", "3") or "3")
    except (TypeError, ValueError):
        n = 3
    n = max(1, n)

    duration = await _probe_duration(file_path)
    if not duration or duration <= 0:
        return None

    # Even interior timestamps: duration*i/(n+1) for i in 1..n.
    frames_b64: list[str] = []
    for i in range(1, n + 1):
        at_s = duration * i / (n + 1)
        png = await _extract_frame_at(file_path, at_s)
        if png:
            frames_b64.append(base64.b64encode(png).decode("ascii"))
    if not frames_b64:
        return None

    content: list[dict[str, Any]] = [
        {"type": "text", "text": _TOPIC_MATCH_PROMPT.format(title=title or "this topic")},
    ]
    for b64 in frames_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    pool = getattr(db, "pool", db)
    try:
        completion = await dispatch_complete(
            pool, [{"role": "user", "content": content}], model,
            tier="standard", phase="media_qa_topic_match",
            temperature=0.2, max_tokens=_LAYER2_VISION_MAX_TOKENS, timeout_s=150.0,
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("[media_quality] topic-match dispatch failed: %s", exc)
        return None

    m = _re.search(r'"score"\s*:\s*([\d.]+)', text)
    if not m:
        return None
    try:
        return max(0.0, min(100.0, float(m.group(1))))
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k topic_match -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/media_quality_service.py src/cofounder_agent/tests/unit/services/test_media_quality_service.py
git commit -m "feat(media-qa): video topic-match vision signal (thinking-safe budget)"
```

---

### Task 4: Video Layer-2 runner + integrate into `evaluate_video`

**Files:**

- Modify: `src/cofounder_agent/services/media_quality_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_media_quality_service.py`

**Interfaces:**

- Consumes: `_aggregate_shot_qa_scores`, `_score_video_topic_match`, `emit_finding`.
- Produces:
  - `async def _run_video_layer2(db, post_id: str, file_path: str, site_config: Any) -> dict` returning `{"layer2_status": str, "layer2_score": float | None, "shot_fidelity", "shot_coverage", "topic_match"}`, emitting advisory findings internally.
  - `evaluate_video(db, post_id, file_path, *, medium="video", site_config=None)` now writes a 0–100 score.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_video_layer2_composite_is_mean_of_signals(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={"shot_fidelity": 80.0, "shot_coverage": 1.0, "scored": 5, "total": 5}))
    monkeypatch.setattr(m, "_score_video_topic_match", AsyncMock(return_value=60.0))
    monkeypatch.setattr(m, "_fetch_post_title", AsyncMock(return_value="T"))
    out = await m._run_video_layer2(_DBStub(), "p", "/v.mp4", _SC({"media.layer2.enabled": "true"}))
    assert out["layer2_score"] == 70.0            # mean(80, 60)
    assert out["layer2_status"] == "scored"


@pytest.mark.asyncio
async def test_video_layer2_unavailable_when_no_signals(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={"shot_fidelity": None, "shot_coverage": 0.0, "scored": 0, "total": 0}))
    monkeypatch.setattr(m, "_score_video_topic_match", AsyncMock(return_value=None))
    monkeypatch.setattr(m, "_fetch_post_title", AsyncMock(return_value="T"))
    out = await m._run_video_layer2(_DBStub(), "p", "/v.mp4", _SC({"media.layer2.enabled": "true"}))
    assert out["layer2_score"] is None
    assert out["layer2_status"] == "unavailable"


@pytest.mark.asyncio
async def test_video_layer2_disabled_by_master_switch(monkeypatch):
    from services import media_quality_service as m
    agg = AsyncMock()
    monkeypatch.setattr(m, "_aggregate_shot_qa_scores", agg)
    out = await m._run_video_layer2(_DBStub(), "p", "/v.mp4", _SC({"media.layer2.enabled": "false"}))
    assert out["layer2_status"] == "disabled"
    agg.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_layer2_low_fidelity_emits_finding(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={"shot_fidelity": 20.0, "shot_coverage": 1.0, "scored": 4, "total": 4}))
    monkeypatch.setattr(m, "_score_video_topic_match", AsyncMock(return_value=80.0))
    monkeypatch.setattr(m, "_fetch_post_title", AsyncMock(return_value="T"))
    captured = []
    monkeypatch.setattr(m, "emit_finding", lambda **kw: captured.append(kw))
    await m._run_video_layer2(_DBStub(), "p", "/v.mp4",
        _SC({"media.layer2.enabled": "true", "media.video.shot_fidelity_min": "60"}))
    assert any(f["kind"] == "video_shot_fidelity_low" for f in captured)


@pytest.mark.asyncio
async def test_evaluate_video_pass_writes_0_100_composite(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_probe_duration", AsyncMock(return_value=45.0))
    monkeypatch.setattr(m, "_file_size", lambda p: 5_000_000)
    monkeypatch.setattr(m, "_run_video_layer2", AsyncMock(return_value={
        "layer2_status": "scored", "layer2_score": 73.0,
        "shot_fidelity": 80.0, "shot_coverage": 1.0, "topic_match": 66.0}))
    monkeypatch.setattr(m, "_notify_if_pending", AsyncMock())
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value={"value": None})  # thresholds → defaults
    out = await m.evaluate_video(db, "p", "/v.mp4", medium="video", site_config=_SC({"media.layer2.enabled": "true"}))
    assert out["score"] == 73.0
    assert out["layer2_status"] == "scored"
    # The UPDATE wrote 73.0 as quality_score (0–100), not 1.0.
    update_args = [c.args for c in db.execute.await_args_list]
    assert any(73.0 in a for a in update_args)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k "video_layer2 or evaluate_video_pass" -v`
Expected: FAIL — `_run_video_layer2` / `_fetch_post_title` undefined.

- [ ] **Step 3: Implement the runner, title fetch, and integrate**

Add the shared helpers (after `_score_video_topic_match`):

```python
def _layer2_enabled(site_config: Any) -> bool:
    if site_config is None:
        return False
    return str(site_config.get("media.layer2.enabled", "true") or "true").strip().lower() in ("true", "1", "yes")


def _cfg_float(site_config: Any, key: str, default: float) -> float:
    try:
        return float(site_config.get(key, default) or default)
    except (TypeError, ValueError):
        return default


async def _fetch_post_title(db: Any, post_id: str) -> str:
    row = await db.fetchrow("SELECT title FROM posts WHERE id = $1::uuid", post_id)
    return (row["title"] if row and row["title"] else "") if row is not None else ""
```

Add the video runner:

```python
async def _run_video_layer2(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> dict[str, Any]:
    """Compute the video Layer-2 signals + composite. Fail-soft; never raises.

    Composite = mean of available signals (shot-fidelity aggregate + topic-match).
    None available → status 'unavailable', score None (caller → 100 on a pass).
    Emits advisory findings on sub-scores below their configured minima.
    """
    if not _layer2_enabled(site_config):
        return {"layer2_status": "disabled", "layer2_score": None}

    signals: dict[str, Any] = {}
    try:
        agg = await _aggregate_shot_qa_scores(db, post_id)
        signals["shot_fidelity"] = agg["shot_fidelity"]
        signals["shot_coverage"] = agg["shot_coverage"]

        title = await _fetch_post_title(db, post_id)
        topic_match = await _score_video_topic_match(db, file_path, title, site_config)
        signals["topic_match"] = topic_match

        available = [s for s in (agg["shot_fidelity"], topic_match) if s is not None]
        composite = (sum(available) / len(available)) if available else None
        signals["layer2_score"] = composite
        signals["layer2_status"] = "scored" if composite is not None else "unavailable"

        # Advisory findings (post-compose so a missing signal never fires one).
        fid_min = _cfg_float(site_config, "media.video.shot_fidelity_min", 60.0)
        tm_min = _cfg_float(site_config, "media.video.topic_match_min", 50.0)
        if agg["shot_fidelity"] is not None and agg["shot_fidelity"] < fid_min:
            emit_finding(
                source="media_quality", kind="video_shot_fidelity_low",
                title=f"video shot-fidelity {agg['shot_fidelity']:.0f} < {fid_min:.0f}",
                body=(f"post {post_id[:8]}: mean rendered-shot vision score "
                      f"{agg['shot_fidelity']:.1f} (coverage {agg['shot_coverage']:.0%}) "
                      f"below {fid_min:.0f}. Advisory."),
                severity="warn", dedup_key=f"video_shot_fidelity_low:{post_id}",
                extra={"post_id": post_id, "shot_fidelity": agg["shot_fidelity"],
                       "coverage": agg["shot_coverage"], "threshold": fid_min})
        if topic_match is not None and topic_match < tm_min:
            emit_finding(
                source="media_quality", kind="video_topic_mismatch",
                title=f"video topic-match {topic_match:.0f} < {tm_min:.0f}",
                body=(f"post {post_id[:8]}: composed-video frames scored "
                      f"{topic_match:.1f} for topic relevance, below {tm_min:.0f}. Advisory."),
                severity="warn", dedup_key=f"video_topic_mismatch:{post_id}",
                extra={"post_id": post_id, "topic_match": topic_match, "threshold": tm_min})
    except Exception as exc:  # noqa: BLE001 — Layer 2 must never fail the eval
        logger.warning("[media_quality] video Layer 2 raised (fail-soft): %s", exc)
        signals.setdefault("layer2_status", "unavailable")
        signals.setdefault("layer2_score", None)
    return signals
```

Now integrate into `evaluate_video`. Locate the pass branch (the `else:` around line 328, `SET quality_score = $3` writing `score` = `1.0`). Change the signature and the pass-branch scoring:

- Signature: `async def evaluate_video(db, post_id, file_path, *, medium="video", site_config=None):`
- Replace the `score = 0.0 if failures else 1.0` line with:

```python
    if failures:
        score = 0.0
    else:
        layer2 = await _run_video_layer2(db, post_id, file_path, site_config)
        signals.update({k: v for k, v in layer2.items() if k != "layer2_score"})
        signals["layer2_status"] = layer2.get("layer2_status", "unavailable")
        l2_score = layer2.get("layer2_score")
        score = float(l2_score) if l2_score is not None else 100.0
    signals["layer1_failures"] = failures
    signals["score"] = score
```

(The existing `signals["layer1_failures"] = failures` / `signals["score"] = score` lines above the DB writes are now set here — delete the old duplicate assignments so they aren't set twice.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k "video_layer2 or evaluate_video" -v`
Expected: PASS. Then run the whole file to catch regressions:
Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -v`
Expected: PASS (existing tests may need their pass-branch expectation updated from `1.0` to `100.0` — update any that assert the old binary pass value).

- [ ] **Step 5: Update the module docstring**

In the module docstring, change the "Layer 2 (future PR)" block to reflect that video Layer 2 now ships (transcription/faithfulness for podcast lands in Task 6).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/media_quality_service.py src/cofounder_agent/tests/unit/services/test_media_quality_service.py
git commit -m "feat(media-qa): video Layer 2 runner + 0-100 evaluate_video"
```

---

### Task 5: Podcast faithfulness helper

**Files:**

- Modify: `src/cofounder_agent/services/media_quality_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_media_quality_service.py`

**Interfaces:**

- Consumes: `caption_providers.get_caption_provider` (mocked), `dispatch_complete` (mocked), `db.fetchrow` for `posts.content`.
- Produces: `async def _score_podcast_faithfulness(db, post_id: str, file_path: str, site_config: Any) -> tuple[float | None, str]` — `(score_or_None, reason)`; None on any fail-soft miss.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_podcast_faithfulness_scores(monkeypatch):
    from services import media_quality_service as m

    seg = SimpleNamespace(text="the episode says gpus run cooler undervolted")
    result = SimpleNamespace(success=True, segments=[seg], error=None)
    provider = SimpleNamespace(transcribe=AsyncMock(return_value=result))
    monkeypatch.setattr(m, "get_caption_provider", lambda sc: provider, raising=False)
    monkeypatch.setattr(m, "dispatch_complete",
        AsyncMock(return_value=SimpleNamespace(text='{"score": 88, "reason": "faithful"}')), raising=False)

    class _DB:
        async def fetchrow(self, *_a, **_k): return {"content": "Undervolting keeps GPUs cool."}
    sc = _SC({"media.podcast.faithfulness_model": "ollama/qwen3.6:latest"})
    score, reason = await m._score_podcast_faithfulness(_DB(), "p", "/ep.mp3", sc)
    assert score == 88.0
    assert "faithful" in reason


@pytest.mark.asyncio
async def test_podcast_faithfulness_unavailable_no_transcript(monkeypatch):
    from services import media_quality_service as m
    result = SimpleNamespace(success=False, segments=[], error="whisper down")
    provider = SimpleNamespace(transcribe=AsyncMock(return_value=result))
    monkeypatch.setattr(m, "get_caption_provider", lambda sc: provider, raising=False)

    class _DB:
        async def fetchrow(self, *_a, **_k): return {"content": "body"}
    sc = _SC({"media.podcast.faithfulness_model": "ollama/qwen3.6:latest"})
    score, _ = await m._score_podcast_faithfulness(_DB(), "p", "/ep.mp3", sc)
    assert score is None


@pytest.mark.asyncio
async def test_podcast_faithfulness_unavailable_no_model(monkeypatch):
    from services import media_quality_service as m
    sc = _SC({"media.podcast.faithfulness_model": "", "ragas_judge_model": ""})
    score, _ = await m._score_podcast_faithfulness(_DBStub(), "p", "/ep.mp3", sc)
    assert score is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k podcast_faithfulness -v`
Expected: FAIL — helper undefined.

- [ ] **Step 3: Implement**

Add the import near the top:

```python
from services.caption_providers import get_caption_provider
```

Add the constant near `_TOPIC_MATCH_PROMPT`:

```python
_FAITHFULNESS_PROMPT = (
    "Compare a podcast transcript against the source article it was generated "
    "from. Does the episode faithfully represent the article's key claims "
    "(no fabrication, no major omission, no contradiction)?\n\n"
    "SOURCE ARTICLE:\n{source}\n\nEPISODE TRANSCRIPT:\n{transcript}\n\n"
    'Reply with ONLY JSON: {{"score": <0-100 integer>, "reason": "<one sentence>"}}. '
    "100 = fully faithful, 0 = unrelated or fabricated."
)


def _resolve_faithfulness_model(site_config: Any) -> str:
    raw = (site_config.get("media.podcast.faithfulness_model", "") or "").strip()
    if raw:
        return raw
    return (site_config.get("ragas_judge_model", "") or "").strip()
```

Add the helper (after `_run_video_layer2`):

```python
async def _score_podcast_faithfulness(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> tuple[float | None, str]:
    """Transcribe the full episode + judge faithfulness vs the source post.

    Fail-soft → (None, "") on: no model, no source body, no transcript, dispatch
    error, unparseable judge response. Never raises.
    """
    import json as _json
    import re as _re

    model = _resolve_faithfulness_model(site_config)
    if not model:
        return None, ""

    row = await db.fetchrow("SELECT content FROM posts WHERE id = $1::uuid", post_id)
    source = (row["content"] if row and row["content"] else "") if row is not None else ""
    if not source.strip():
        return None, ""

    try:
        provider = get_caption_provider(site_config)
        result = await provider.transcribe(audio_path=file_path, task_id=post_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("[media_quality] podcast transcribe failed: %s", exc)
        return None, ""
    if not getattr(result, "success", False):
        return None, ""
    transcript = " ".join(
        s.text for s in (result.segments or []) if getattr(s, "text", "")
    ).strip()
    if not transcript:
        return None, ""

    prompt = _FAITHFULNESS_PROMPT.format(source=source, transcript=transcript)
    pool = getattr(db, "pool", db)
    try:
        completion = await dispatch_complete(
            pool, [{"role": "user", "content": prompt}], model,
            tier="standard", phase="media_qa_faithfulness",
            temperature=0.2, max_tokens=_LAYER2_VISION_MAX_TOKENS, timeout_s=180.0,
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("[media_quality] faithfulness dispatch failed: %s", exc)
        return None, ""

    m = _re.search(r'"score"\s*:\s*([\d.]+)', text)
    if not m:
        return None, ""
    try:
        score = max(0.0, min(100.0, float(m.group(1))))
    except (TypeError, ValueError):
        return None, ""
    rm = _re.search(r'"reason"\s*:\s*"([^"]{0,200})"', text)
    return score, (rm.group(1) if rm else "")
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k podcast_faithfulness -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/media_quality_service.py src/cofounder_agent/tests/unit/services/test_media_quality_service.py
git commit -m "feat(media-qa): podcast faithfulness signal (whisper + LLM judge)"
```

---

### Task 6: Podcast Layer-2 runner + integrate into `evaluate_podcast`

**Files:**

- Modify: `src/cofounder_agent/services/media_quality_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_media_quality_service.py`

**Interfaces:**

- Consumes: `_score_podcast_faithfulness`, `_layer2_enabled`, `_cfg_float`, `emit_finding`.
- Produces:
  - `async def _run_podcast_layer2(db, post_id, file_path, site_config) -> dict`
  - `evaluate_podcast(db, post_id, file_path, *, site_config=None)` writes a 0–100 score.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_podcast_layer2_score_is_faithfulness(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_score_podcast_faithfulness", AsyncMock(return_value=(88.0, "ok")))
    out = await m._run_podcast_layer2(_DBStub(), "p", "/ep.mp3", _SC({"media.layer2.enabled": "true"}))
    assert out["layer2_score"] == 88.0
    assert out["layer2_status"] == "scored"
    assert out["faithfulness"] == 88.0


@pytest.mark.asyncio
async def test_podcast_layer2_unavailable(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_score_podcast_faithfulness", AsyncMock(return_value=(None, "")))
    out = await m._run_podcast_layer2(_DBStub(), "p", "/ep.mp3", _SC({"media.layer2.enabled": "true"}))
    assert out["layer2_score"] is None
    assert out["layer2_status"] == "unavailable"


@pytest.mark.asyncio
async def test_podcast_layer2_low_faithfulness_emits_finding(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_score_podcast_faithfulness", AsyncMock(return_value=(30.0, "drifted")))
    captured = []
    monkeypatch.setattr(m, "emit_finding", lambda **kw: captured.append(kw))
    await m._run_podcast_layer2(_DBStub(), "p", "/ep.mp3",
        _SC({"media.layer2.enabled": "true", "media.podcast.faithfulness_min": "60"}))
    assert any(f["kind"] == "podcast_faithfulness_low" for f in captured)


@pytest.mark.asyncio
async def test_evaluate_podcast_pass_writes_0_100(monkeypatch):
    from services import media_quality_service as m
    monkeypatch.setattr(m, "_probe_duration", AsyncMock(return_value=600.0))
    monkeypatch.setattr(m, "_probe_silence_ratio", AsyncMock(return_value=0.1))
    monkeypatch.setattr(m, "_file_size", lambda p: 8_000_000)
    monkeypatch.setattr(m, "_run_podcast_layer2", AsyncMock(return_value={
        "layer2_status": "scored", "layer2_score": 88.0, "faithfulness": 88.0}))
    monkeypatch.setattr(m, "_notify_if_pending", AsyncMock())
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value={"value": None})
    out = await m.evaluate_podcast(db, "p", "/ep.mp3", site_config=_SC({"media.layer2.enabled": "true"}))
    assert out["score"] == 88.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -k "podcast_layer2 or evaluate_podcast_pass" -v`
Expected: FAIL — `_run_podcast_layer2` undefined.

- [ ] **Step 3: Implement runner + integrate**

Add the podcast runner (after `_score_podcast_faithfulness`):

```python
async def _run_podcast_layer2(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> dict[str, Any]:
    """Compute the podcast Layer-2 faithfulness signal. Fail-soft; never raises."""
    if not _layer2_enabled(site_config):
        return {"layer2_status": "disabled", "layer2_score": None}
    signals: dict[str, Any] = {}
    try:
        score, reason = await _score_podcast_faithfulness(db, post_id, file_path, site_config)
        signals["faithfulness"] = score
        signals["faithfulness_reason"] = reason
        signals["layer2_score"] = score
        signals["layer2_status"] = "scored" if score is not None else "unavailable"
        f_min = _cfg_float(site_config, "media.podcast.faithfulness_min", 60.0)
        if score is not None and score < f_min:
            emit_finding(
                source="media_quality", kind="podcast_faithfulness_low",
                title=f"podcast faithfulness {score:.0f} < {f_min:.0f}",
                body=(f"post {post_id[:8]}: episode faithfulness vs source article "
                      f"scored {score:.1f} (< {f_min:.0f}). reason: {reason}. Advisory."),
                severity="warn", dedup_key=f"podcast_faithfulness_low:{post_id}",
                extra={"post_id": post_id, "faithfulness": score, "threshold": f_min})
    except Exception as exc:  # noqa: BLE001 — Layer 2 must never fail the eval
        logger.warning("[media_quality] podcast Layer 2 raised (fail-soft): %s", exc)
        signals.setdefault("layer2_status", "unavailable")
        signals.setdefault("layer2_score", None)
    return signals
```

Integrate into `evaluate_podcast`: change the signature to
`async def evaluate_podcast(db, post_id, file_path, *, site_config=None):`
and replace `score = 0.0 if failures else 1.0` with the same pass-branch shape as Task 4:

```python
    if failures:
        score = 0.0
    else:
        layer2 = await _run_podcast_layer2(db, post_id, file_path, site_config)
        signals.update({k: v for k, v in layer2.items() if k != "layer2_score"})
        signals["layer2_status"] = layer2.get("layer2_status", "unavailable")
        l2_score = layer2.get("layer2_score")
        score = float(l2_score) if l2_score is not None else 100.0
    signals["layer1_failures"] = failures
    signals["score"] = score
```

(Delete the old duplicate `signals["layer1_failures"]` / `signals["score"]` assignments as in Task 4.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py -v`
Expected: PASS (update any legacy test still asserting the `1.0` podcast pass value to `100.0`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/media_quality_service.py src/cofounder_agent/tests/unit/services/test_media_quality_service.py
git commit -m "feat(media-qa): podcast Layer 2 runner + 0-100 evaluate_podcast"
```

---

### Task 7: Thread `site_config` through the call chain + fix Discord score display

**Files:**

- Modify: `src/cofounder_agent/services/media_approval_service.py`
- Modify: `src/cofounder_agent/services/jobs/media_distribute.py`, `podcast_distribute.py`, `media_reconciliation.py`
- Test: `src/cofounder_agent/tests/unit/services/test_media_approval_service.py`

**Interfaces:**

- Consumes: `evaluate_video`/`evaluate_podcast` now accept `site_config=None` (Tasks 4/6).
- Produces: `record_pending(db, post_id, medium, *, file_path=None, site_config=None)` and `_evaluate_and_notify(db, post_id, medium, file_path, site_config=None)` — both pass `site_config` down.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_record_pending_threads_site_config_to_evaluator(mock_db):
    import services.media_approval_service as s
    mock_db.fetchrow.return_value = None  # niche lookup → manual review branch
    eval_video = AsyncMock()
    sentinel = object()
    with patch("services.media_quality_service.evaluate_video", eval_video):
        # row re-read inside _evaluate_and_notify: pending + never evaluated
        mock_db.fetchrow.side_effect = [None, {"status": "pending", "quality_evaluated_at": None}]
        await s.record_pending(mock_db, "p", "video", file_path="/v.mp4", site_config=sentinel)
    assert eval_video.await_args.kwargs["site_config"] is sentinel
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_approval_service.py::test_record_pending_threads_site_config_to_evaluator -v`
Expected: FAIL — `record_pending` has no `site_config` kwarg (TypeError) or the evaluator isn't called with it.

- [ ] **Step 3: Thread the kwarg**

In `media_approval_service.py`:

1. `record_pending` signature → `async def record_pending(db, post_id, medium, *, file_path=None, site_config=None):`
2. Its call `await _evaluate_and_notify(db, post_id, medium, file_path)` → `await _evaluate_and_notify(db, post_id, medium, file_path, site_config)`
3. `_evaluate_and_notify` signature → `async def _evaluate_and_notify(db, post_id, medium, file_path, site_config=None):`
4. Inside it, the two evaluator calls gain `site_config=site_config`:

```python
            if medium == "podcast":
                await media_quality_service.evaluate_podcast(
                    db, post_id, file_path, site_config=site_config,
                )
            else:
                await media_quality_service.evaluate_video(
                    db, post_id, file_path, medium=medium, site_config=site_config,
                )
```

- [ ] **Step 4: Fix the Discord score display for 0–100**

In `notify_pending_for_review` (~line 457), change:

```python
    score_str = f"{float(score):.2f}" if score is not None else "—"
```

to:

```python
    score_str = f"{float(score):.0f}" if score is not None else "—"
```

- [ ] **Step 5: Thread `site_config` from the 3 job callers**

- `services/jobs/media_distribute.py:414` — the enclosing helper already has `site_config` in scope (threaded from `run` via `_deliver`). Change the `record_pending(...)` call to add `site_config=site_config`.
- `services/jobs/podcast_distribute.py:178` and `:195` — inside `run`, `sc = config.get("_site_config")` is in scope. Add `site_config=sc` to both `record_pending(...)` calls.
- `services/jobs/media_reconciliation.py:904` — add `site_config=config.get("_site_config")` to the `record_pending(...)` call.

- [ ] **Step 6: Run tests**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_approval_service.py -v`
Expected: PASS (the existing suite still green — `site_config` defaults to None so no existing call breaks).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/media_approval_service.py src/cofounder_agent/services/jobs/media_distribute.py src/cofounder_agent/services/jobs/podcast_distribute.py src/cofounder_agent/services/jobs/media_reconciliation.py src/cofounder_agent/tests/unit/services/test_media_approval_service.py
git commit -m "feat(media-qa): thread site_config to Layer 2 evaluators; 0-100 Discord display"
```

---

### Task 8: Migration — rescale historical binary scores to 0–100

**Files:**

- Create: `src/cofounder_agent/services/migrations/<generated-ts>_rescale_media_quality_score.py`

**Interfaces:**

- Consumes: nothing (pure data convergence).
- Produces: historical `media_approvals.quality_score` values rescaled `×100`.

- [ ] **Step 1: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "rescale media_approvals quality_score to 0-100"`
This prints the created path (e.g. `services/migrations/20260709_HHMMSS_rescale_media_approvals_quality_score_to_0_100.py`).

- [ ] **Step 2: Write the migration body**

Replace the generated `upgrade` body with:

```python
async def upgrade(conn) -> None:
    # Media Layer 2 moved quality_score from binary 0/1 to 0-100 (spec
    # 2026-07-09). Rescale historical rows so an old 1.0 ("passed") doesn't
    # read as 1/100. All pre-migration rows are on the 0/1 scale; the <= 1.0
    # guard makes a re-run a no-op (new-scale scores are > 1 except a genuine
    # catastrophic score, which this runs before any of exist).
    await conn.execute(
        """
        UPDATE media_approvals
        SET quality_score = quality_score * 100
        WHERE quality_score IS NOT NULL AND quality_score <= 1.0
        """
    )
```

Keep whatever no-op `downgrade` the generator produced (or a `pass`).

- [ ] **Step 3: Lint the migration**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_lint.py`
Expected: passes (no collisions, correct runner interface).

- [ ] **Step 4: Verify the rescale logic with a targeted test**

Create `tests/unit/services/migrations/test_rescale_media_quality_score.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_rescale_multiplies_binary_scores(monkeypatch):
    import importlib, glob, os
    # Load the migration module by glob (timestamp prefix varies).
    path = glob.glob(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "services", "migrations", "*rescale_media_approvals_quality_score*.py"))[0]
    spec = importlib.util.spec_from_file_location("mig_rescale", path)
    mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)

    executed = {}
    class _Conn:
        async def execute(self, sql, *a):
            executed["sql"] = " ".join(sql.split())
    await mig.upgrade(_Conn())
    assert "quality_score * 100" in executed["sql"]
    assert "<= 1.0" in executed["sql"]
```

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/migrations/test_rescale_media_quality_score.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/*rescale_media_approvals_quality_score*.py src/cofounder_agent/tests/unit/services/migrations/test_rescale_media_quality_score.py
git commit -m "feat(media-qa): migration — rescale historical media quality_score to 0-100"
```

---

### Task 9: Grafana — "Media Quality — Layer 2" row

**Files:**

- Modify: `infrastructure/grafana/dashboards/pipeline.json`

**Interfaces:**

- Consumes: `media_approvals.quality_score` / `quality_signals`, `audit_log` findings.
- Produces: three panels on the Pipeline board's media section.

- [ ] **Step 1: Locate the media row anchor**

Run: `grep -n "Media Approval" infrastructure/grafana/dashboards/pipeline.json`
Note the `gridPos.y` of that row so the new panels append below it.

- [ ] **Step 2: Add three panels**

Add these panel objects to the `panels` array (bump `y` to sit under the media row; give each a fresh integer `id` not already used in the file). Use the existing Postgres datasource `uid` already present in other panels in this file.

Panel A — score distribution (timeseries, `quality_score` avg by medium):

```
rawSql: SELECT date_trunc('hour', decided_at) AS "time", medium,
        ROUND(AVG(quality_score)::numeric,1) AS avg_score
        FROM media_approvals
        WHERE quality_score IS NOT NULL AND $__timeFilter(decided_at)
        GROUP BY 1,2 ORDER BY 1
```

Panel B — Layer-2 unavailable rate (stat, % of scored rows falling back to bare-100):

```
rawSql: SELECT ROUND(100.0 * COUNT(*) FILTER (
          WHERE quality_signals->>'layer2_status' = 'unavailable')
          / NULLIF(COUNT(*),0), 1)::float AS value
        FROM media_approvals
        WHERE quality_signals ? 'layer2_status'
```

Panel C — advisory findings by kind (table, last 24h):

```
rawSql: SELECT details->>'kind' AS kind, COUNT(*) AS n
        FROM audit_log
        WHERE event_type = 'finding'
          AND details->>'kind' IN ('video_topic_mismatch','video_shot_fidelity_low','podcast_faithfulness_low')
          AND timestamp > now() - interval '24 hours'
        GROUP BY 1 ORDER BY 2 DESC
```

- [ ] **Step 3: Validate the dashboard JSON**

Run: `cd src/cofounder_agent && python -c "import json,sys; json.load(open(r'../../infrastructure/grafana/dashboards/pipeline.json')); print('valid json')"`
Expected: `valid json`. (If the repo has a dashboard-lint CI script, run it too.)

- [ ] **Step 4: Commit**

```bash
git add infrastructure/grafana/dashboards/pipeline.json
git commit -m "feat(media-qa): Grafana Media Quality Layer 2 row"
```

---

### Task 10: Full-suite regression + lint gate

**Files:** none (verification only).

- [ ] **Step 1: Run the touched suites**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_media_quality_service.py tests/unit/services/test_media_approval_service.py tests/unit/services/test_settings_defaults.py tests/unit/services/migrations/ -v`
Expected: all PASS.

- [ ] **Step 2: Ruff**

Run: `cd src/cofounder_agent && poetry run ruff check services/media_quality_service.py services/media_approval_service.py services/jobs/media_distribute.py services/jobs/podcast_distribute.py services/jobs/media_reconciliation.py`
Expected: All checks passed.

- [ ] **Step 3: Migrations smoke (fresh-DB safety)**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_smoke.py`
Expected: passes.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin claude/media-quality-layer2
gh pr create --title "feat(media-qa): quality Layer 2 — video + podcast semantic scoring (#2129)" --body "<summary + test plan; closes the video/podcast half of #2129>"
```

---

## Self-Review

**Spec coverage:**

- 0–100 rescale → Tasks 4/6 (evaluators) + Task 8 (backfill). ✓
- Video Signal A (shot-fidelity) → Task 2. ✓
- Video Signal B (topic-match) → Task 3, composed in Task 4. ✓
- Podcast faithfulness → Task 5, composed in Task 6. ✓
- Score-composition table (hard-fail 0 / unavailable 100 / computed composite) → Tasks 4 & 6 pass-branch. ✓
- Advisory-first findings (3 kinds, warn, dedup) → Tasks 4 & 6. ✓
- Fail-soft matrix → Tasks 3, 5 (+ runner wrappers in 4, 6). ✓
- Settings (7 keys) → Task 1. ✓
- `max_tokens ≥ 1024` pin → Task 3. ✓
- Only-after-Layer-1-pass → Tasks 4 & 6 (runner called only in the `else` branch). ✓
- site_config threading, optional-with-skip → Task 7. ✓
- Discord 0–100 display → Task 7. ✓
- Grafana row (dist / unavailable-rate / findings) → Task 9. ✓
- Migration backfill → Task 8. ✓
- Reuse callouts (dispatch_complete / get_caption_provider / emit_finding / \_run_argv / \_probe_duration) → Tasks 2,3,5. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code; every test step shows real assertions. ✓

**Type consistency:** `_aggregate_shot_qa_scores` returns the same dict shape consumed in Task 4; `_score_video_topic_match` / `_score_podcast_faithfulness` return types match their runners' expectations; `_run_video_layer2`/`_run_podcast_layer2` both emit `layer2_status`/`layer2_score` consumed identically in the two evaluators; `site_config` kwarg name consistent across Tasks 4/6/7. ✓

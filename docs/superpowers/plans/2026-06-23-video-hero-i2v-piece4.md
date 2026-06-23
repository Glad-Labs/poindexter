# Video Quality Piece 4 — Wan 2.2 TI2V-5B image-to-video hero renderer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing Wan render seam from Wan 2.1 1.3B text-to-video to Wan 2.2 TI2V-5B **image-to-video** that animates the shot's stylized SDXL still, with a still+Ken-Burns fallback and a swappable-model DB seam — per video-quality spec §3.3.

**Architecture:** The wan render seam is already built, deployed, and live (`scripts/wan-server.py` sidecar + `Wan21Provider` + `shot_list_renderer._render_wan21_clip`). Piece 4 is a model-tier / i2v / fallback **upgrade** on a working seam, not a from-scratch build. It splits into a **code-half** (Tasks 1–6, pure-code + TDD, no GPU) and an **infra-half deploy runbook** (Tasks 7–9, GPU + host-stability gated). The two halves are decoupled by a forward-compatible request contract: the current `GenerateRequest` pydantic model ignores unknown fields, so the code-half's new `image_b64` field is silently dropped by the live T2V server until the server-half lands — **the code-half deploys with zero regression** and `generative` shots render as T2V (init image ignored) with the improved still fallback until i2v is enabled.

**Tech Stack:** Python 3.13, pydantic v2, FastAPI sidecar, diffusers (`WanPipeline` → `WanImageToVideoPipeline`), Wan-AI/Wan2.2-TI2V-5B (Apache-2.0), pytest. DB-config via `settings_defaults.py` + `SiteConfig` DI.

## Global Constraints

- **Backcompat required** — `wan21` shot source must keep rendering; `generative` is added as the new canonical alias (old shot lists + in-flight tasks carry `wan21`). Both route to the same renderer path.
- **No hardcoded values** — `generative_video_model`, `video_hero_shots_max` are `app_settings` keys with defaults in `settings_defaults.py`, never literals in production code.
- **Fail loud, no silent defaults** — a `generative` shot with no `prompt` fails validation (it's the i2v conditioning prompt); a render miss emits a finding, never silently drops.
- **Models commercial-licensed** — Wan-AI/Wan2.2-TI2V-5B is Apache-2.0 (verified on the model card); keep the `license` metadata field accurate.
- **DB-configurable prompts** — director guidance lives in `skills/content/video-director/SKILL.md` (UnifiedPromptManager YAML), not inline.
- **Test env:** venv `poindexter-backend--eW9-khR-py3.12` (`Scripts/python.exe`); run `PYTHONPATH=<worktree>/src/cofounder_agent <py> -m pytest <test> -q -p no:cacheprovider --no-header`.
- **Branch:** `feat/video-hero-i2v` off `origin/main`. TDD, commit per task. PR when CI green.

---

## File Structure

**Code-half (Tasks 1–6):**

- `src/cofounder_agent/schemas/video_shot_list.py` — add `"generative"` to the `ShotSource` Literal; treat it like `wan21`/`sdxl` in the prompt-required + human-token validators. **Owns:** the director↔renderer source contract.
- `src/cofounder_agent/services/video_providers/wan2_1.py` — `fetch()` + `_generate_to_path()` accept an optional init image (`image_path` → base64 `image_b64` in the POST body); resolve `generative_video_model` for the model label/metadata. **Owns:** the worker→wan-server HTTP contract.
- `src/cofounder_agent/services/video_renderers/shot_list_renderer.py` — `generative`/`wan21` shots render the SDXL still first (i2v conditioning), animate it, and on miss fall back to the still PNG (compositor Ken-Burns it) + a `hero_render_fallback` finding; a hero-shot cap downgrades excess `generative` shots to `sdxl_kenburns`. **Owns:** per-shot render orchestration + fallback.
- `src/cofounder_agent/services/settings_defaults.py` — `generative_video_model`, `video_hero_shots_max` defaults. **Owns:** DB-config defaults.
- `src/cofounder_agent/skills/content/video-director/SKILL.md` — rename `wan21` → `generative` in the allowed-source list + guidance; describe it as "animate a stylized brand still" and the per-video hero cap. **Owns:** director prompt.

**Infra-half (Tasks 7–9, deploy runbook):**

- `scripts/wan-server.py` — default `MODEL_ID` → `Wan-AI/Wan2.2-TI2V-5B-Diffusers`; load `WanImageToVideoPipeline`; `GenerateRequest` gains optional `image_b64`; `_generate_blocking` decodes it and passes `image=` for i2v (else stays T2V). **Owns:** the GPU inference server.
- `Dockerfile.wan` / `docker-compose*.yml` — model bump, any new VRAM env. **Owns:** the baked wan container.

**Tests:**

- `src/cofounder_agent/tests/unit/schemas/test_video_shot_list.py`
- `src/cofounder_agent/tests/unit/services/video_providers/test_wan2_1.py`
- `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py`

---

# PART A — Code-half (TDD now, no GPU)

### Task 1: Add `generative` source alias to the shot schema

**Files:**

- Modify: `src/cofounder_agent/schemas/video_shot_list.py:32-38` (the `ShotSource` Literal) and `:113-144` (the validators)
- Test: `src/cofounder_agent/tests/unit/schemas/test_video_shot_list.py`

**Interfaces:**

- Produces: `ShotSource` now includes `"generative"`; a `Shot(source="generative", prompt=...)` validates, `source="generative"` with no prompt raises `ValueError`. `wan21` behaviour is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# in test_video_shot_list.py
import pytest
from pydantic import ValidationError
from schemas.video_shot_list import Shot


def _shot(**kw):
    base = dict(idx=0, duration_s=5.0, intent="hero", source="generative",
               prompt="a glowing neon GPU die, cyberpunk", narration_offset_s=0.0)
    base.update(kw)
    return Shot(**base)


def test_generative_source_accepted_with_prompt():
    s = _shot()
    assert s.source == "generative"
    assert s.prompt


def test_generative_source_requires_prompt():
    with pytest.raises(ValidationError):
        _shot(prompt=None)


def test_wan21_source_still_accepted_backcompat():
    s = _shot(source="wan21")
    assert s.source == "wan21"
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src/cofounder_agent <py> -m pytest src/cofounder_agent/tests/unit/schemas/test_video_shot_list.py -q -p no:cacheprovider`
Expected: FAIL — `generative` not a valid `ShotSource` literal (ValidationError on the accepted-case test).

- [ ] **Step 3: Implement**

In `video_shot_list.py`, add `generative` to the Literal (keep `wan21` for backcompat):

```python
ShotSource = Literal[
    "sdxl",            # Static SDXL image, held for ``duration_s``
    "sdxl_kenburns",   # SDXL image + Ken Burns zoom/pan animation
    "pexels",          # Pexels stock video clip (real footage)
    "generative",      # Hero shot: animate the stylized SDXL still (Wan i2v)
    "wan21",           # DEPRECATED alias of ``generative`` (legacy shot lists)
    "holdover",        # Cross-fade transition from prior shot (no asset)
]
```

Then in `_validate_source_inputs`, add `"generative"` everywhere `"wan21"` appears in a source tuple — there are three such tuples (the prompt-required check, the human-token scan). Replace each `("sdxl", "sdxl_kenburns", "wan21")` with `("sdxl", "sdxl_kenburns", "wan21", "generative")`.

- [ ] **Step 4: Run to verify pass**

Run the same pytest command. Expected: PASS (3 new + all existing).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/schemas/video_shot_list.py src/cofounder_agent/tests/unit/schemas/test_video_shot_list.py
git commit -m "feat(video): add 'generative' hero shot source (wan21 backcompat alias)"
```

---

### Task 2: Provider sends the init image for i2v

**Files:**

- Modify: `src/cofounder_agent/services/video_providers/wan2_1.py` — `Wan21Provider.fetch` (`:102-222`), `_generate_to_path` (`:288-373`)
- Test: `src/cofounder_agent/tests/unit/services/video_providers/test_wan2_1.py`

**Interfaces:**

- Consumes: `config["image_path"]` (str, optional) — local path to the SDXL still PNG; `config["_site_config"]` for `generative_video_model`.
- Produces: when `image_path` is set and the file exists, the `/generate` POST body carries `image_b64` (base64 of the file bytes). When absent, the body has no `image_b64` (T2V — current behaviour). The returned `VideoResult.metadata["model"]` reflects the resolved `generative_video_model`.

- [ ] **Step 1: Write the failing tests**

```python
# in test_wan2_1.py
import base64
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from services.video_providers import wan2_1


@pytest.mark.asyncio
async def test_generate_includes_image_b64_when_image_path_given(tmp_path):
    png = tmp_path / "still.png"
    png.write_bytes(b"\x89PNGfake-bytes")
    captured = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "video/mp4", "X-Elapsed-Seconds": "1"}
        content = b"MP4BYTES"

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, timeout=None):
            captured["json"] = json
            return _Resp()

    with patch.object(wan2_1.httpx, "AsyncClient", _Client):
        ok = await wan2_1._generate_to_path(
            prompt="p", negative="", output_path=str(tmp_path / "o.mp4"),
            server_url="http://x", steps=10, guidance=5.0, duration=5,
            width=832, height=480, fps=16, image_b64=base64.b64encode(png.read_bytes()).decode(),
        )
    assert ok is True
    assert captured["json"].get("image_b64") == base64.b64encode(b"\x89PNGfake-bytes").decode()


@pytest.mark.asyncio
async def test_generate_omits_image_b64_for_t2v(tmp_path):
    captured = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "video/mp4"}
        content = b"MP4"

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, timeout=None):
            captured["json"] = json
            return _Resp()

    with patch.object(wan2_1.httpx, "AsyncClient", _Client):
        await wan2_1._generate_to_path(
            prompt="p", negative="", output_path=str(tmp_path / "o.mp4"),
            server_url="http://x", steps=10, guidance=5.0, duration=5,
            width=832, height=480, fps=16,
        )
    assert "image_b64" not in captured["json"]
```

- [ ] **Step 2: Run to verify fail**

Run: `PYTHONPATH=src/cofounder_agent <py> -m pytest src/cofounder_agent/tests/unit/services/video_providers/test_wan2_1.py -q -p no:cacheprovider`
Expected: FAIL — `_generate_to_path()` has no `image_b64` parameter (TypeError).

- [ ] **Step 3: Implement**

Add `image_b64: str | None = None` to `_generate_to_path`'s keyword-only signature. In the POST body dict, conditionally include it:

```python
body = {
    "prompt": prompt,
    "negative_prompt": negative,
    "steps": steps,
    "guidance_scale": guidance,
    "duration_s": duration,
    "width": width,
    "height": height,
    "fps": fps,
    "model": "wan2.1-1.3b",
}
if image_b64:
    body["image_b64"] = image_b64
resp = await client.post(f"{server_url}/generate", json=body, timeout=300)
```

In `fetch()`, read the image path, base64-encode it (in a thread — file IO), resolve the model label, and thread `image_b64` through:

```python
image_path = str(config.get("image_path", "") or "")
image_b64: str | None = None
if image_path and os.path.exists(image_path):
    raw = await asyncio.to_thread(_read_image_bytes, image_path)
    image_b64 = base64.b64encode(raw).decode("ascii")

model_label = "wan2.1-1.3b"
if site_config is not None:
    try:
        model_label = site_config.get("generative_video_model", "") or model_label
    except Exception:
        pass
```

Add the module-level helper + imports (`import base64`):

```python
def _read_image_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()
```

Pass `image_b64=image_b64` into the `_generate_to_path(...)` call, and set `metadata["model"] = model_label` / `metadata["model_repo"] = model_label` in the `VideoResult`.

- [ ] **Step 4: Run to verify pass**

Run the same pytest command. Expected: PASS (2 new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/video_providers/wan2_1.py src/cofounder_agent/tests/unit/services/video_providers/test_wan2_1.py
git commit -m "feat(video): Wan provider sends image_b64 init frame for i2v (T2V when absent)"
```

---

### Task 3: Renderer — still-first i2v + still/Ken-Burns fallback + finding

**Files:**

- Modify: `src/cofounder_agent/services/video_renderers/shot_list_renderer.py` — `_REGENERABLE_SOURCES` (`:63`), `_render_wan21_clip` (`:296`), `_render_one_shot` (`:335`, the `wan21` branch `:459`)
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Consumes: `_render_sdxl_image(prompt, output_path, sdxl_url, http_client_factory, render_timeout) -> bool` (existing), `Wan21Provider` via `image_path` (Task 2), `emit_finding(...)` (existing import).
- Produces: for `source in ("generative", "wan21")`, `_render_one_shot` returns a `ShotRenderResult` whose `clip_path` is the animated `.mp4` on success, or the SDXL still `.png` on i2v-miss (with `_emit_hero_fallback_finding` called). `_REGENERABLE_SOURCES` includes `"generative"`.

- [ ] **Step 1: Write the failing tests**

```python
# in test_shot_list_renderer.py — add
import pytest
from services.video_renderers import shot_list_renderer as slr
from schemas.video_shot_list import Shot


@pytest.mark.asyncio
async def test_generative_shot_animates_sdxl_still(tmp_path, monkeypatch):
    shot = Shot(idx=0, duration_s=5.0, intent="hero", source="generative",
                prompt="neon GPU die", narration_offset_s=0.0)

    async def fake_sdxl(*, prompt, output_path, **kw):
        open(output_path, "wb").write(b"PNG"); return True
    async def fake_clip(*, prompt, output_path, image_path, duration_s, site_config):
        assert image_path and image_path.endswith(".png")
        open(output_path, "wb").write(b"MP4"); return True

    monkeypatch.setattr(slr, "_render_sdxl_image", fake_sdxl)
    monkeypatch.setattr(slr, "_render_generative_clip", fake_clip)
    res = await slr._render_one_shot(
        shot, prior_clip=None, work_dir=tmp_path, sdxl_url="http://x",
        site_config=None, http_client_factory=None)
    assert res.success and res.clip_path.endswith(".mp4")


@pytest.mark.asyncio
async def test_generative_shot_falls_back_to_still_on_clip_miss(tmp_path, monkeypatch):
    shot = Shot(idx=1, duration_s=5.0, intent="hero", source="generative",
                prompt="neon GPU die", narration_offset_s=0.0)
    findings = []

    async def fake_sdxl(*, prompt, output_path, **kw):
        open(output_path, "wb").write(b"PNG"); return True
    async def fake_clip(**kw):
        return False  # i2v miss

    monkeypatch.setattr(slr, "_render_sdxl_image", fake_sdxl)
    monkeypatch.setattr(slr, "_render_generative_clip", fake_clip)
    monkeypatch.setattr(slr, "emit_finding", lambda **kw: findings.append(kw))
    res = await slr._render_one_shot(
        shot, prior_clip=None, work_dir=tmp_path, sdxl_url="http://x",
        site_config=None, http_client_factory=None)
    assert res.success and res.clip_path.endswith(".png")  # still, KB'd by compositor
    assert any(f.get("kind") == "hero_render_fallback" for f in findings)


def test_generative_is_regenerable():
    assert "generative" in slr._REGENERABLE_SOURCES
```

- [ ] **Step 2: Run to verify fail**

Run: `PYTHONPATH=src/cofounder_agent <py> -m pytest src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py -q -p no:cacheprovider`
Expected: FAIL — `_render_generative_clip` doesn't exist; `generative` not in `_REGENERABLE_SOURCES`; the `wan21`-only branch doesn't handle `generative`.

- [ ] **Step 3: Implement**

Add `"generative"` to `_REGENERABLE_SOURCES`:

```python
_REGENERABLE_SOURCES = frozenset({"sdxl", "sdxl_kenburns", "wan21", "generative"})
```

Rename/extend the clip helper to take an init image (keep the old name as a thin wrapper for any other caller, or just rename — grep shows the only caller is `_render_one_shot`):

```python
async def _render_generative_clip(
    *, prompt: str, output_path: str, image_path: str | None,
    duration_s: int, site_config: Any,
) -> bool:
    """Render one hero clip (Wan i2v when ``image_path`` set, else T2V)."""
    from services.video_providers.wan2_1 import Wan21Provider
    provider = Wan21Provider()
    try:
        results = await provider.fetch(prompt, {
            "output_path": output_path,
            "duration_s": min(duration_s, _WAN21_MAX_DURATION_S),
            "image_path": image_path or "",
            "_site_config": site_config,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOT_LIST] generative render raised for %s: %s",
                       os.path.basename(output_path), exc)
        return False
    return bool(results) and bool(results[0].file_path) and os.path.exists(results[0].file_path)  # type: ignore[arg-type]
```

Add the finding helper:

```python
def _emit_hero_fallback_finding(*, shot: Shot, post_id: str) -> None:
    emit_finding(
        source="shot_list_renderer", kind="hero_render_fallback",
        title=f"hero shot {shot.idx} fell back to still (Ken Burns)",
        body=(f"shot {shot.idx} (generative) — i2v render produced no clip; "
              f"used the stylized SDXL still with Ken Burns motion instead."),
        severity="warn",
        dedup_key=f"hero_render_fallback:{post_id}:{shot.idx}",
        extra={"shot_idx": shot.idx, "source": shot.source},
    )
```

Replace the `if source == "wan21":` branch (`:459-487`) with a still-first i2v block handling both tokens. `_render_one_shot` needs `post_id` for the finding — thread it through `render_kwargs` (it's already in scope in `render_shot_list`); add `post_id: str = ""` to `_render_one_shot`'s signature and pass it from `render_kwargs`:

```python
if source in ("generative", "wan21"):
    if not shot.prompt:
        return ShotRenderResult(idx=shot.idx, source=source, success=False,
                                error=f"{source} shot missing prompt")
    still_path = str(work_dir / f"shot_{shot.idx:02d}.png")
    render_timeout = (site_config.get_int("image_render_timeout_seconds", 240)
                      if site_config is not None else 240)
    still_ok = await _render_sdxl_image(
        prompt=shot.prompt, output_path=still_path, sdxl_url=sdxl_url,
        http_client_factory=http_client_factory, render_timeout=render_timeout)
    if not still_ok:
        return ShotRenderResult(idx=shot.idx, source=source, success=False,
                                error="generative shot: SDXL still render failed")
    clip_path = str(work_dir / f"shot_{shot.idx:02d}.mp4")
    clip_ok = await _render_generative_clip(
        prompt=shot.prompt, output_path=clip_path, image_path=still_path,
        duration_s=int(shot.duration_s), site_config=site_config)
    if clip_ok:
        return ShotRenderResult(idx=shot.idx, source=source, success=True,
                                clip_path=clip_path, duration_s=shot.duration_s)
    # i2v miss → fall back to the still (compositor Ken-Burns it) + finding.
    _emit_hero_fallback_finding(shot=shot, post_id=post_id)
    return ShotRenderResult(idx=shot.idx, source=source, success=True,
                            clip_path=still_path, duration_s=shot.duration_s)
```

Add `post_id=post_id` to the `render_kwargs` dict in `render_shot_list` (`:782-789`) and `post_id: str = ""` to `_render_one_shot`'s kwargs.

- [ ] **Step 4: Run to verify pass**

Run the same pytest command. Expected: PASS (3 new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_list_renderer.py src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -m "feat(video): hero shots render SDXL still then i2v-animate; still+KenBurns fallback + finding"
```

---

### Task 4: Cap hero shots at `video_hero_shots_max`

**Files:**

- Modify: `src/cofounder_agent/services/video_renderers/shot_list_renderer.py` — `render_shot_list` (`:781`, before `_render_pass`)
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py`

**Interfaces:**

- Produces: a module-level `_cap_hero_shots(shots: list[Shot], max_hero: int) -> list[Shot]` that returns a new list where, past the first `max_hero` `generative`/`wan21` shots, excess hero shots are rewritten to `source="sdxl_kenburns"` (a still+KB cousin that keeps the same prompt). Called in `render_shot_list` before the render pass with `max_hero = site_config.get_int("video_hero_shots_max", 3)`.

- [ ] **Step 1: Write the failing test**

```python
def test_cap_hero_shots_downgrades_excess_to_kenburns():
    from schemas.video_shot_list import Shot
    mk = lambda i, src: Shot(idx=i, duration_s=4.0, intent="x", source=src,
                             prompt="neon die", narration_offset_s=0.0)
    shots = [mk(0, "generative"), mk(1, "generative"), mk(2, "pexels"),
             mk(3, "generative"), mk(4, "generative")]
    # cap at 2 generatives: idx 0,1 stay; idx 3,4 become sdxl_kenburns
    out = slr._cap_hero_shots(shots, 2)
    assert [s.source for s in out] == [
        "generative", "generative", "pexels", "sdxl_kenburns", "sdxl_kenburns"]
```

(`mk` uses `query=None` — fine, pexels needs a query; give it one: `Shot(..., source="pexels", prompt=None, query="gpu")`. Adjust the pexels shot in the test to pass `query="gpu", prompt=None`.)

- [ ] **Step 2: Run to verify fail**

Run: `PYTHONPATH=src/cofounder_agent <py> -m pytest src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py::test_cap_hero_shots_downgrades_excess_to_kenburns -q -p no:cacheprovider`
Expected: FAIL — `_cap_hero_shots` not defined.

- [ ] **Step 3: Implement**

```python
_HERO_SOURCES = frozenset({"generative", "wan21"})


def _cap_hero_shots(shots: list[Shot], max_hero: int) -> list[Shot]:
    """Keep at most ``max_hero`` hero (generative/wan21) shots; downgrade the
    rest to ``sdxl_kenburns`` (the still+Ken-Burns cousin, same prompt). The
    hero render is the most expensive + failure-prone source, so the director
    over-asking shouldn't blow the GPU budget (spec §3.3)."""
    if max_hero < 0:
        return list(shots)
    out: list[Shot] = []
    seen = 0
    for s in shots:
        if s.source in _HERO_SOURCES:
            seen += 1
            if seen > max_hero:
                out.append(s.model_copy(update={"source": "sdxl_kenburns"}))
                continue
        out.append(s)
    return out
```

In `render_shot_list`, before `states = await _render_pass(...)`:

```python
max_hero = site_config.get_int("video_hero_shots_max", 3) if site_config is not None else 3
capped_shots = _cap_hero_shots(shot_list.shots, max_hero)
```

and pass `capped_shots` to `_render_pass(capped_shots, ...)` (replacing `shot_list.shots`). Leave the `shots_total` counts reading `len(shot_list.shots)` unchanged.

- [ ] **Step 4: Run to verify pass**

Run the same pytest command (full file). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_list_renderer.py src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py
git commit -m "feat(video): cap hero shots at video_hero_shots_max, downgrade excess to sdxl_kenburns"
```

---

### Task 5: Settings defaults

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (near the existing video keys)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py` (if a defaults-presence test exists; else fold into Task 4's file)

**Interfaces:**

- Produces: `DEFAULTS["generative_video_model"] == "Wan-AI/Wan2.2-TI2V-5B"`, `DEFAULTS["video_hero_shots_max"] == "3"`.

- [ ] **Step 1: Write the failing test**

```python
def test_piece4_video_defaults_present():
    from services.settings_defaults import DEFAULTS
    assert DEFAULTS["generative_video_model"] == "Wan-AI/Wan2.2-TI2V-5B"
    assert DEFAULTS["video_hero_shots_max"] == "3"
```

- [ ] **Step 2: Run to verify fail**

Run: `PYTHONPATH=src/cofounder_agent <py> -m pytest <path>::test_piece4_video_defaults_present -q -p no:cacheprovider`
Expected: FAIL — KeyError.

- [ ] **Step 3: Implement**

Add to the `DEFAULTS` dict, with comment block, near other `video_*` keys:

```python
    # Piece 4 (#video-pipeline-workstream) — Wan 2.2 TI2V-5B image-to-video
    # hero renderer. ``generative_video_model`` is the swappable model seam
    # (14B / LTX later) read by the Wan provider + wan-server; keep it the
    # ``Wan-AI/...`` repo id. ``video_hero_shots_max`` caps the per-video
    # generative-shot budget (each is a heavy GPU render); excess hero shots
    # downgrade to sdxl_kenburns.
    "generative_video_model": "Wan-AI/Wan2.2-TI2V-5B",
    "video_hero_shots_max": "3",
```

- [ ] **Step 4: Run to verify pass** — same command, Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py <test file>
git commit -m "feat(video): seed generative_video_model + video_hero_shots_max defaults"
```

---

### Task 6: Director prompt — rename `wan21` → `generative`

**Files:**

- Modify: `src/cofounder_agent/skills/content/video-director/SKILL.md` (lines ~73, 98, 105, 109, 122, 139–147, 162–170)
- Test: none (prompt doc) — but run any existing director-prompt assertion test if present (`grep -rl video-director src/cofounder_agent/tests`).

- [ ] **Step 1: Edit the SKILL.md** — replace the `"wan21"` source bullet (line ~73) with:

```markdown
- "generative": AI hero shot — a short clip that **animates a stylized SDXL
  still** (image-to-video). Use sparingly for the 2–3 highest-impact beats
  that benefit from motion (a pan across an abstract scene, a slow push-in).
  Capped per video; excess generative shots are auto-downgraded to a
  Ken-Burns still, so reserve it for genuine hero moments.
```

Replace every other `wan21` mention (`sdxl / sdxl_kenburns / wan21` enumerations at ~98, 105, 109, 122, 145, 147; the "First and last shots MUST NOT be wan21" rule at ~142; the mix guidance at ~139–140) with `generative`. Keep the rule "First and last shots MUST NOT be 'generative' — its artifacts are most visible at cut-in/cut-out."

- [ ] **Step 2: Verify no stray `wan21`** — Run: `grep -n wan21 src/cofounder_agent/skills/content/video-director/SKILL.md` → expect no output.

- [ ] **Step 3: Run any director-prompt test** — `PYTHONPATH=src/cofounder_agent <py> -m pytest <grep result> -q` (skip if none).

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/skills/content/video-director/SKILL.md
git commit -m "feat(video): director emits 'generative' hero source (was wan21); brand-still i2v guidance"
```

---

### Task 7 (code-half close-out): full-suite green + ruff + PR

- [ ] **Step 1** — Run the four touched suites + the video schema suite together:

```bash
PYTHONPATH=src/cofounder_agent <py> -m pytest \
  src/cofounder_agent/tests/unit/schemas/test_video_shot_list.py \
  src/cofounder_agent/tests/unit/services/video_providers/test_wan2_1.py \
  src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_renderer.py \
  src/cofounder_agent/tests/unit/services/video_renderers/test_shot_vision_qa.py \
  -q -p no:cacheprovider --no-header
```

Expected: all PASS.

- [ ] **Step 2** — `ruff check` the four touched source files; fix any lint.
- [ ] **Step 3** — `python scripts/regen-services-doc.py` (no new files here, but `_cap_hero_shots`/renames change docstrings — regen keeps `docs/reference/services.md` green for the regen-services-doc CI check). Commit if it changes.
- [ ] **Step 4** — Push `feat/video-hero-i2v`, open PR against `main`. Title: `feat(video): Wan i2v hero renderer code-half — generative source + still fallback (Piece 4)`. Body notes the infra-half (Tasks 8–9) is the deploy runbook below, and that the code-half deploys non-regressing (live server ignores `image_b64`, renders T2V). Merge when CI green.

---

# PART B — Infra-half deploy runbook (GPU + host-stability gated)

> Do these only when the host is stable (no recent Docker/WSL crash — see the post-crash boot-window gotcha in `project_video_pipeline_workstream`). The code-half (Part A) is already live and non-regressing before this runs.

### Task 8: wan-server.py — TI2V-5B image-to-video

**Files:** `scripts/wan-server.py`, `Dockerfile.wan`, `docker-compose*.yml`

1. **Confirm the diffusers API first.** Wan 2.2 TI2V-5B is recent — verify the exact pipeline class + call signature before editing (HF model card `Wan-AI/Wan2.2-TI2V-5B-Diffusers` + diffusers docs). Expected: `WanImageToVideoPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16)`, call `pipe(image=<PIL>, prompt=..., num_frames=..., ...)`. The TI2V-5B model does **both** T2V and i2v through one checkpoint; `image=None` falls back to T2V.
2. **`MODEL_ID` default** → `Wan-AI/Wan2.2-TI2V-5B-Diffusers` (still env-overridable via `WAN_MODEL_ID`).
3. **`_load_pipeline_blocking`** → load `WanImageToVideoPipeline` instead of `WanPipeline`. Keep `enable_attention_slicing()`. Update the `/health` `model` / `model_display_name` / `model_id` strings.
4. **`GenerateRequest`** — add `image_b64: str | None = Field(default=None)`. (Pydantic ignores it today; declaring it makes the server consume it.)
5. **`_generate_blocking`** — when `image_b64` is set: `base64.b64decode` → `PIL.Image.open(BytesIO(...)).convert("RGB")` → resize to (width, height) → pass `image=img` to the pipeline. When absent, omit `image=` (T2V). Pass `image_b64` from `generate()` into `_generate_blocking`.
6. **VRAM check** — TI2V-5B at bf16 is ~10–12 GB weights + video-latent activations. On the 32 GB card with Ollama evicted via `gpu.lock`, it must fit; watch `/health` `vram_used_mb` on first render. If OOM, add `pipe.enable_model_cpu_offload()` (trades speed for VRAM) behind a `WAN_CPU_OFFLOAD` env.
7. **Bump the HTTP timeout note** — 5B is slower than 1.3B; the provider's `_HTTP_TIMEOUT` (900 s) already covers it.

### Task 9: download + rebuild + e2e (GPU)

1. Pre-fetch weights so the first render doesn't pay a 10-min cold download mid-pipeline: `huggingface-cli download Wan-AI/Wan2.2-TI2V-5B-Diffusers` (or let the container lazy-load once and warm the HF cache volume).
2. **Rebuild the baked container** (wan is baked, not bind-mounted): `bash scripts/start-stack.sh build poindexter-wan-server` (or `docker compose up -d --build poindexter-wan-server`), then recreate.
3. **Health:** `curl :9840/health` → `model_id` shows TI2V-5B, `status` idle/ready, `gpu_available true`.
4. **Set the model key live:** `set_setting generative_video_model Wan-AI/Wan2.2-TI2V-5B` (matches the default; explicit on prod).
5. **e2e:** trigger a fresh content run (`create_post`, brand niche) so the director emits `generative` hero shots → publish → media render. Confirm in worker logs: `GPU acquired model=shot_list_render owner=video`, a `generative` shot rendering an `.mp4` (not a still fallback), and `vram_used_mb` within budget. If the wan render misses, confirm the **still+Ken-Burns fallback** produced a frame + a `hero_render_fallback` finding (Findings dashboard).
6. **Verify** per the `verify` skill — drive the real media pipeline, capture the rendered MP4 (a hero shot visibly _moves_ vs a static still), screenshot/clip as evidence.

---

## Self-Review

**Spec §3.3 coverage:**

- "Renderer: wan-server, weights → Wan2.2-TI2V-5B, image-to-video animate the SDXL still" → Tasks 2 (provider sends still), 3 (renderer renders still first + animates), 8 (server i2v). ✅
- "`video_hero_shots_max` (default 3) caps count" → Task 4 + Task 5. ✅
- "hero render failure → fall back to the still (Ken Burns), emit a finding" → Task 3 (`hero_render_fallback`). ✅
- "Seam: `wan_server_url` + a `generative_video_model` DB key" → `wan_server_url` already resolved in the provider; `generative_video_model` → Tasks 2 + 5. ✅
- "`shot.source == 'generative'` routes to the generative renderer" → Tasks 1 (schema) + 3 (dispatch) + 6 (director). ✅

**Placeholder scan:** none — every code step shows the code.

**Type consistency:** `_render_generative_clip(*, prompt, output_path, image_path, duration_s, site_config) -> bool` used identically in Task 3 def + `_render_one_shot` call. `_cap_hero_shots(shots, max_hero) -> list[Shot]` consistent in Task 4. `image_b64` is the field name across provider (Task 2), server (Task 8). `generative_video_model` / `video_hero_shots_max` keys identical across Tasks 2/4/5/9.

**Non-regression check:** the live wan-server's `GenerateRequest` ignores unknown fields → code-half (`image_b64` in the body) is dropped, server renders T2V, `generative` shots still produce clips. The still-first change means even pre-i2v, a `generative` shot now has a proper SDXL still to fall back to (strict improvement over the old holdover). ✅

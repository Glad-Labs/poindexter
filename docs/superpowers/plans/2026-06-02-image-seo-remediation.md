# Image-SEO Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make gladlabs.io image alt text accurately describe the real rendered image (via local qwen3-vl vision through the dispatcher), fix it forward in the pipeline and backfill all existing posts, and close 6 other SEO gaps the audit found.

**Architecture:** One shared `image_captioner.py` (vision via `dispatch_complete`, post-processed by the existing `alt_text.sanitize_alt_text`, GPU-locked, fail-soft) consumed by both a new `stage.caption_images` pipeline node and a `--mode` backfill orchestrator. Production reads static JSON from R2, so every DB write is followed by `static_export_service.export_post(slug)`.

**Tech Stack:** Python 3 / FastAPI / asyncpg / LangGraph graph_def / Ollama qwen3-vl:30b via LiteLLM dispatcher / Next.js 15 (web/public-site) / pytest.

Spec: [docs/superpowers/specs/2026-06-02-image-seo-remediation-design.md](../specs/2026-06-02-image-seo-remediation-design.md)

---

## File structure

**Backend (create):**

- `src/cofounder_agent/services/image_captioner.py` — shared vision-caption lib
- `src/cofounder_agent/services/stages/caption_images.py` — pipeline node
- `src/cofounder_agent/services/migrations/<ts>_seed_vision_alt_settings.py` — seed `vision_alt_*` app_settings
- `src/cofounder_agent/services/migrations/<ts>_reseed_canonical_blog_caption_images.py` — re-seed graph_def (version 3)
- `scripts/backfill-image-alt-vision.py` — backfill orchestrator (`--mode {alt,seo-desc,titles}`)
- Tests: `tests/unit/services/test_image_captioner.py`, `tests/unit/services/stages/test_caption_images.py`, `tests/unit/services/test_dev_diary_seo.py`, `tests/unit/services/test_publish_title_sanitizer.py`

**Backend (modify):**

- `src/cofounder_agent/services/canonical_blog_spec.py` — add caption_images node + edges
- `src/cofounder_agent/plugins/registry.py:~644` — register `CaptionImagesStage`
- `src/cofounder_agent/services/pipeline_templates/__init__.py` — add `generate_seo_metadata` node to `dev_diary`
- `src/cofounder_agent/services/publish_service.py` — title-suffix sanitizer before insert
- `src/cofounder_agent/services/static_export_service.py` — emit `featured_image_alt`

**Frontend (modify):**

- `web/public-site/lib/seo.js` — `buildSEOTitle` brand-suffix-when-fits + `lib/seo.test.js`
- `web/public-site/lib/posts.ts` — add `featured_image_alt?` to `Post`
- `web/public-site/app/posts/[slug]/page.tsx` — `og:image` alt = `featured_image_alt || title`
- `web/public-site/app/legal/{privacy,terms,cookie-policy,data-requests}/page.*` — add canonical
- `web/public-site/app/{about,posts,archive/[page]}` + `app/layout.js` — default og:image

---

## Phase 1 — Vision captioner (foundation)

### Task 1: `image_captioner.py` shared lib

**Files:**

- Create: `src/cofounder_agent/services/image_captioner.py`
- Test: `src/cofounder_agent/tests/unit/services/test_image_captioner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_image_captioner.py
import base64
import pytest
from unittest.mock import AsyncMock, patch

from services.image_captioner import caption_image


class _Result:
    def __init__(self, text): self.text = text


@pytest.mark.asyncio
async def test_caption_image_happy_path_strips_image_of_prefix():
    png = base64.b64encode(b"\x89PNG\r\n").decode()
    with patch("services.image_captioner._fetch_b64", AsyncMock(return_value=png)), \
         patch("services.image_captioner.dispatch_complete",
               AsyncMock(return_value=_Result("Image of a teal glass cube on blueprint paper."))) as disp:
        alt = await caption_image(
            image_url="https://r2/x.png", topic="CAD", budget=120,
            site_config=None, pool=object(),
        )
    # dispatch_complete called with an image content block
    msgs = disp.call_args.kwargs["messages"]
    assert isinstance(msgs[0]["content"], list)
    assert any(p.get("type") == "image_url" for p in msgs[0]["content"])
    # sanitized: no "Image of" prefix, within budget
    assert not alt.lower().startswith("image of")
    assert len(alt) <= 120


@pytest.mark.asyncio
async def test_caption_image_fail_soft_returns_none_on_fetch_error():
    with patch("services.image_captioner._fetch_b64", AsyncMock(return_value=None)):
        alt = await caption_image(
            image_url="https://r2/x.png", topic="CAD", budget=120,
            site_config=None, pool=object(),
        )
    assert alt is None


@pytest.mark.asyncio
async def test_caption_image_fail_soft_on_dispatch_error():
    png = base64.b64encode(b"x").decode()
    with patch("services.image_captioner._fetch_b64", AsyncMock(return_value=png)), \
         patch("services.image_captioner.dispatch_complete",
               AsyncMock(side_effect=RuntimeError("ollama down"))):
        alt = await caption_image(
            image_url="https://r2/x.png", topic="CAD", budget=120,
            site_config=None, pool=object(),
        )
    assert alt is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_captioner.py -q`
Expected: FAIL — `ModuleNotFoundError: services.image_captioner`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cofounder_agent/services/image_captioner.py
"""Vision-based alt-text captioner — describes the ACTUAL image.

Single source of truth for image alt generation, shared by the
``stage.caption_images`` pipeline node and ``scripts/backfill-image-alt-vision.py``
(mirrors how ``services.alt_text`` is shared by the stage + the #84 backfill).

Routes the vision call through ``dispatch_complete`` (provider-swappable,
Matt's directive) using OpenAI-style image content blocks, which the active
litellm provider forwards to the local qwen3-vl Ollama model. The raw model
output is post-processed by ``alt_text.sanitize_alt_text`` so every existing
guard (token strip, mid-word, image-gen-prompt-shape) still applies. Fail-soft:
returns None on any error so callers keep the prior alt — a backfill must
never blank or degrade a post.
"""
from __future__ import annotations

import base64
import logging

import httpx

from services.alt_text import sanitize_alt_text
from services.llm_providers.dispatcher import dispatch_complete

logger = logging.getLogger(__name__)

_DEFAULT_VISION_MODEL = "qwen3-vl:30b"


def _prompt(budget: int) -> str:
    return (
        "Write alt text for this image. Describe ONLY what is actually visible "
        "— factual, concise, one sentence, under {b} characters. Do NOT begin "
        "with 'image of' or 'photo of'. Do NOT invent details that aren't "
        "visible."
    ).format(b=budget)


async def _fetch_b64(image_url: str) -> str | None:
    """Download an image and base64-encode it. None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as c:
            r = await c.get(image_url)
        if r.status_code != 200:
            logger.warning("image_captioner: GET %s -> %s", image_url, r.status_code)
            return None
        return base64.b64encode(r.content).decode()
    except Exception as e:  # noqa: BLE001 — fail-soft
        logger.warning("image_captioner: fetch failed for %s: %s", image_url, e)
        return None


async def caption_image(
    *,
    image_url: str | None = None,
    image_b64: str | None = None,
    topic: str,
    budget: int,
    site_config,
    pool,
    model: str | None = None,
    task_id: str | None = None,
) -> str | None:
    """Return accurate alt text for an image, or None (caller keeps prior alt).

    Provide either ``image_url`` (downloaded here) or ``image_b64``.
    """
    if image_b64 is None:
        if not image_url:
            return None
        image_b64 = await _fetch_b64(image_url)
        if image_b64 is None:
            return None

    vmodel = model or (
        site_config.get("vision_alt_model", _DEFAULT_VISION_MODEL)
        if site_config is not None else _DEFAULT_VISION_MODEL
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": _prompt(budget)},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ],
    }]

    # GPU coordination — qwen3-vl is ~19.6 GB; serialize against image-gen/writer.
    from services.gpu_scheduler import gpu
    try:
        async with gpu.lock("ollama", model=vmodel, task_id=task_id, phase="caption_image"):
            result = await dispatch_complete(
                pool=pool, messages=messages, model=vmodel,
                tier="standard", task_id=task_id, phase="caption_image",
                temperature=0.2, max_tokens=120,
            )
    except Exception as e:  # noqa: BLE001 — fail-soft
        logger.warning("image_captioner: vision call failed: %s", e)
        return None

    raw = (getattr(result, "text", "") or "").strip()
    if "</think>" in raw:
        raw = raw.split("</think>", 1)[1].strip()
    if not raw:
        return None
    return sanitize_alt_text(raw, budget=budget, topic=topic)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_captioner.py -q`
Expected: PASS (3 passed). Note: `sanitize_alt_text` lowercases-checks the "Image of" prefix via its `IMAGE:`/`FIGURE:` strip only — if the prefix assert fails, the test reveals we must add an "image of"/"photo of" prefix strip to the captioner (see Step 5b).

- [ ] **Step 5b (if needed): strip "image of"/"photo of" prefix before sanitize**

If `test_..._strips_image_of_prefix` fails, add before the `return`:

```python
    import re as _re
    raw = _re.sub(r"^(?:an?\s+)?(?:image|photo|picture)\s+of\s+", "", raw, flags=_re.IGNORECASE).strip()
    raw = raw[:1].upper() + raw[1:] if raw else raw
```

- [ ] **Step 6: Smoke-test the real dispatcher path** (one-off, not committed)

Run a throwaway script that calls `caption_image` against the worker env (or `docker exec poindexter-worker python -c "..."`) on one real R2 image URL to confirm litellm→ollama forwards the image to qwen3-vl. Confirm a sensible caption returns. If litellm does NOT forward images for the ollama route, fall back inside `caption_image` to a direct Ollama `/api/chat` `images=[b64]` call guarded behind the same gpu.lock (documented; still the only place that knows the transport).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/image_captioner.py src/cofounder_agent/tests/unit/services/test_image_captioner.py
git commit -m "feat(seo): vision-based image alt captioner via dispatcher (qwen3-vl)"
```

### Task 2: Seed `vision_alt_*` settings migration

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_seed_vision_alt_settings.py`

- [ ] **Step 1: Generate the migration file**

Run: `python scripts/new-migration.py "seed vision alt settings"`
Then replace its body:

```python
"""Seed vision alt-text settings (image_captioner)."""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

_SEEDS = [
    ("vision_alt_enabled", "true"),
    ("vision_alt_model", "qwen3-vl:30b"),
]

async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value in _SEEDS:
            await conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO NOTHING",
                key, value,
            )
    logger.info("seed_vision_alt_settings: applied")

async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            [k for k, _ in _SEEDS],
        )
```

- [ ] **Step 2: Lint the migration**

Run: `python scripts/ci/migrations_lint.py`
Expected: PASS (no collisions, runner interface present).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/migrations/*seed_vision_alt_settings.py
git commit -m "feat(seo): seed vision_alt_enabled + vision_alt_model settings"
```

---

## Phase 2 — Pipeline forward-fix

### Task 3: `CaptionImagesStage`

**Files:**

- Create: `src/cofounder_agent/services/stages/caption_images.py`
- Test: `src/cofounder_agent/tests/unit/services/stages/test_caption_images.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/stages/test_caption_images.py
import pytest
from unittest.mock import AsyncMock, patch
from services.stages.caption_images import CaptionImagesStage


@pytest.mark.asyncio
async def test_caption_images_rewrites_inline_alt_and_featured():
    content = '<img src="https://r2/a.png" alt="A photo of a person at a desk" width="1024" height="1024" loading="lazy" />'
    ctx = {
        "content": content, "topic": "Async APIs",
        "featured_image_url": "https://r2/feat.png",
        "featured_image_alt": "Async APIs — AI generated illustration",
        "site_config": None, "database_service": None,
    }
    async def fake_caption(*, image_url, **kw):
        return "Monitor showing circuit schematic, backlit keyboard" if "a.png" in image_url \
               else "Abstract cyan and navy geometric cover"
    with patch("services.stages.caption_images.caption_image", AsyncMock(side_effect=fake_caption)):
        res = await CaptionImagesStage().execute(ctx, {})
    assert res.ok
    new_content = res.context_updates["content"]
    assert 'alt="Monitor showing circuit schematic, backlit keyboard"' in new_content
    assert "a photo of a person" not in new_content.lower()
    assert res.context_updates["featured_image_alt"] == "Abstract cyan and navy geometric cover"


@pytest.mark.asyncio
async def test_caption_images_failsoft_keeps_prior_alt():
    content = '<img src="https://r2/a.png" alt="original alt" />'
    ctx = {"content": content, "topic": "X", "site_config": None}
    with patch("services.stages.caption_images.caption_image", AsyncMock(return_value=None)):
        res = await CaptionImagesStage().execute(ctx, {})
    assert 'alt="original alt"' in res.context_updates["content"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_caption_images.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the stage**

```python
# src/cofounder_agent/services/stages/caption_images.py
"""CaptionImagesStage — re-caption inline + featured images with vision.

Runs after source_featured_image (so all images exist). Reads every
``<img alt="...">`` in the draft and replaces the alt with a pixel-accurate
caption from ``services.image_captioner``. Fail-soft per image: a None
caption leaves the existing alt untouched. Also re-captions the featured
image into ``featured_image_alt``. No-op when ``vision_alt_enabled`` is false.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from plugins.stage import StageResult
from services.alt_text import _IMG_ALT_RE  # (<img...alt=")(value)(")
from services.image_captioner import caption_image

logger = logging.getLogger(__name__)
_IMG_SRC_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.IGNORECASE)


class CaptionImagesStage:
    name = "caption_images"
    description = "Re-caption inline + featured images with qwen3-vl (accurate alt)"
    timeout_seconds = 600
    halts_on_failure = False

    async def execute(self, context: dict[str, Any], config: dict[str, Any]) -> StageResult:
        site_config = context.get("site_config")
        if site_config is not None:
            raw = str(site_config.get("vision_alt_enabled", "true") or "true").strip().lower()
            if raw not in ("true", "1", "yes", "on"):
                return StageResult(ok=True, detail="vision_alt_enabled=false", metrics={"skipped": True})

        topic = context.get("topic", "")
        content = context.get("content", "") or ""
        pool = getattr(site_config, "_pool", None) if site_config is not None else None
        budget = site_config.get_int("alt_text_budget", 120) if site_config is not None else 120
        task_id = context.get("task_id")
        updates: dict[str, Any] = {}
        n = 0

        # Inline <img> tags: caption each src, swap its alt in place.
        async def _recaption_tag(m: re.Match) -> str:
            nonlocal n
            tag = m.group(0)
            src_m = _IMG_SRC_RE.search(tag)
            if not src_m:
                return tag
            new_alt = await caption_image(
                image_url=src_m.group(1), topic=topic, budget=budget,
                site_config=site_config, pool=pool, task_id=task_id,
            )
            if not new_alt:
                return tag  # fail-soft: keep prior alt
            n += 1
            return _IMG_ALT_RE.sub(lambda a: f"{a.group(1)}{new_alt}{a.group(3)}", tag, count=1)

        # re.sub has no async; iterate matches manually.
        out, last = [], 0
        for m in re.finditer(r"<img\b[^>]*>", content, re.IGNORECASE):
            out.append(content[last:m.start()])
            out.append(await _recaption_tag(m))
            last = m.end()
        out.append(content[last:])
        new_content = "".join(out)
        if new_content != content:
            updates["content"] = new_content
            db = context.get("database_service")
            if db is not None and task_id:
                await db.update_task(task_id=task_id, updates={"content": new_content})
        else:
            updates["content"] = content

        # Featured image.
        feat_url = context.get("featured_image_url")
        if feat_url:
            feat_alt = await caption_image(
                image_url=feat_url, topic=topic, budget=budget,
                site_config=site_config, pool=pool, task_id=task_id,
            )
            if feat_alt:
                updates["featured_image_alt"] = feat_alt

        return StageResult(ok=True, detail=f"{n} inline captioned",
                           context_updates=updates, metrics={"inline_captioned": n})
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_caption_images.py -q`
Expected: PASS (2 passed). If the inline-rewrite test fails on the manual finditer, debug the `_recaption_tag` swap — the `_IMG_ALT_RE` group indices are (1)=`<img...alt="`, (2)=value, (3)=`"`.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/stages/caption_images.py src/cofounder_agent/tests/unit/services/stages/test_caption_images.py
git commit -m "feat(seo): caption_images pipeline stage (vision alt for inline + featured)"
```

### Task 4: Register stage + wire into graph_def + re-seed migration

**Files:**

- Modify: `src/cofounder_agent/plugins/registry.py` (core samples stage list, ~line 644)
- Modify: `src/cofounder_agent/services/canonical_blog_spec.py`
- Create: `src/cofounder_agent/services/migrations/<ts>_reseed_canonical_blog_caption_images.py`

- [ ] **Step 1: Register the stage in core samples**

In `plugins/registry.py`, after the `source_featured_image` tuple (~line 644) add:

```python
        ("stages", "services.stages.caption_images", "CaptionImagesStage"),
```

- [ ] **Step 2: Add node + rewire edges in `canonical_blog_spec.py`**

In `nodes`, after the `source_featured_image` node add:

```python
        {"id": "caption_images", "atom": "stage.caption_images"},
```

In `edges`, replace `{"from": "source_featured_image", "to": "qa_critic"}` with:

```python
        {"from": "source_featured_image", "to": "caption_images"},
        {"from": "caption_images", "to": "qa_critic"},
```

- [ ] **Step 3: Write the build-validates test**

```python
# tests/unit/services/test_canonical_blog_spec_caption.py
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as S

def test_caption_images_node_wired_between_featured_and_qa():
    ids = [n["id"] for n in S["nodes"]]
    assert "caption_images" in ids
    edges = {(e["from"], e["to"]) for e in S["edges"]}
    assert ("source_featured_image", "caption_images") in edges
    assert ("caption_images", "qa_critic") in edges
    assert ("source_featured_image", "qa_critic") not in edges
```

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_canonical_blog_spec_caption.py -q`
Expected: PASS.

- [ ] **Step 4: Re-seed migration (graph_def version 3)**

Run: `python scripts/new-migration.py "reseed canonical_blog caption_images"`
Body (mirror `20260602_023250_seed_canonical_blog_graph_def.py`, bump version to 3):

```python
"""Re-seed canonical_blog graph_def with the caption_images node (version 3)."""
from __future__ import annotations
import json, logging
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF
logger = logging.getLogger(__name__)

async def up(pool) -> None:
    payload = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $1::jsonb, version = 3, updated_at = NOW()
             WHERE slug = 'canonical_blog'
            """,
            payload,
        )
    logger.info("reseed_canonical_blog_caption_images: applied")

async def down(pool) -> None:
    logger.info("reseed_canonical_blog_caption_images: no-op down (forward-only reseed)")
```

- [ ] **Step 5: Lint migrations + run the architect validation**

Run: `python scripts/ci/migrations_lint.py`
Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/ -k "pipeline_architect or canonical_blog or graph" -q`
Expected: PASS — `build_graph_from_spec`/`compose` still validate (stage.\* nodes are transparent to the I/O contract).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/plugins/registry.py src/cofounder_agent/services/canonical_blog_spec.py src/cofounder_agent/services/migrations/*reseed_canonical_blog_caption_images.py src/cofounder_agent/tests/unit/services/test_canonical_blog_spec_caption.py
git commit -m "feat(seo): wire caption_images node into canonical_blog graph_def"
```

---

## Phase 3 — dev_diary meta descriptions (issue #2 forward)

### Task 5: Add `generate_seo_metadata` to the dev_diary template

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_templates/__init__.py` (the `dev_diary` factory)
- Test: `src/cofounder_agent/tests/unit/services/test_dev_diary_seo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_dev_diary_seo.py
from services.pipeline_templates import dev_diary

def test_dev_diary_includes_generate_seo_metadata_node():
    g = dev_diary(pool=None)  # factory builds a StateGraph
    node_names = set(getattr(g, "nodes", {}).keys()) if hasattr(g, "nodes") else set()
    assert "generate_seo_metadata" in node_names
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_dev_diary_seo.py -q`
Expected: FAIL — node absent. (If `dev_diary`'s signature differs, read the factory first and adjust the call; the assertion target is "generate_seo_metadata is a node".)

- [ ] **Step 3: Insert the node**

In the `dev_diary` factory, register the existing `generate_seo_metadata` stage between `narrate_bundle` and `source_featured_image`, following the same `get_core_samples()` stage-lookup + `make_stage_node` pattern the factory already uses for `verify_task`/`source_featured_image`/`finalize_task`. Add `"generate_seo_metadata"` to `nodes_added` in that position so the linear `zip` edges thread it.

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_dev_diary_seo.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_templates/__init__.py src/cofounder_agent/tests/unit/services/test_dev_diary_seo.py
git commit -m "fix(seo): dev_diary template now generates SEO metadata (meta description)"
```

---

## Phase 4 — Frontend fixes (#5, #6, #7) + featured-alt wiring (A4)

### Task 6: `buildSEOTitle` — brand suffix only when it fits

**Files:**

- Modify: `web/public-site/lib/seo.js:25-40`
- Test: `web/public-site/lib/seo.test.js`

- [ ] **Step 1: Add failing tests**

```js
// in lib/seo.test.js
import { buildSEOTitle } from './seo';
test('appends brand when total <= 60', () => {
  expect(buildSEOTitle('Short Title')).toBe('Short Title | Glad Labs');
});
test('drops brand suffix when title alone is long', () => {
  const t =
    'A Very Long Title That Already Pushes Right Up Against The Limit Here';
  expect(buildSEOTitle(t)).toBe(t);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web/public-site && npm test -- seo.test.js`
Expected: FAIL — current code uses `| Blog`.

- [ ] **Step 3: Implement**

Replace `buildSEOTitle` body:

```js
export function buildSEOTitle(title, siteName = SITE_NAME) {
  const candidate = `${title} | ${siteName}`;
  return candidate.length <= 60 ? candidate : title;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web/public-site && npm test -- seo.test.js`
Expected: PASS. (Check no other caller relies on the old `suffix`/2nd-arg signature — `getPostBySlug` page uses `buildSEOTitle(title)`.)

- [ ] **Step 5: Commit**

```bash
git add web/public-site/lib/seo.js web/public-site/lib/seo.test.js
git commit -m "fix(seo): buildSEOTitle appends brand only when title stays <=60 chars"
```

### Task 7: Legal-page canonicals (#6)

**Files:** `web/public-site/app/legal/{privacy,terms,cookie-policy,data-requests}/page.*`

- [ ] **Step 1:** Read one legal page to learn its metadata export shape.
- [ ] **Step 2:** Add to each page's `metadata` (or `generateMetadata`):

```ts
alternates: { canonical: `${SITE_URL}/legal/privacy` },  // path per page
```

- [ ] **Step 3:** Verify locally: `cd web/public-site && npm run build` then curl the rendered page (or grep the built output) for `rel="canonical"`. Or after deploy, re-run `.audit/seo_audit.py` and confirm "missing canonical" drops by 4.
- [ ] **Step 4: Commit**

```bash
git add web/public-site/app/legal
git commit -m "fix(seo): add canonical URLs to legal pages"
```

### Task 8: Static-page default og:image (#7)

**Files:** `web/public-site/app/layout.js` (root default) + verify `/about`, `/posts`, `/archive/[page]` inherit.

- [ ] **Step 1:** In `app/layout.js` `metadata`, add a default `openGraph.images: [{ url: `${SITE_URL}/og-image.jpg`, width: 1200, height: 630 }]` so pages without their own og:image inherit it.
- [ ] **Step 2:** Confirm `/og-image.jpg` exists in `web/public-site/public/`. If not, note it and use the existing default referenced in `page.tsx` (`/og-image.jpg`).
- [ ] **Step 3:** `npm run build`; re-audit confirms "missing og:image" → 0.
- [ ] **Step 4: Commit**

```bash
git add web/public-site/app/layout.js
git commit -m "fix(seo): site-default og:image for static/listing pages"
```

### Task 9: Emit + consume `featured_image_alt` (A4)

**Files:**

- Modify: `src/cofounder_agent/services/static_export_service.py` (both `_fetch_*` queries + emitted dict)
- Modify: `web/public-site/lib/posts.ts` (`Post` interface)
- Modify: `web/public-site/app/posts/[slug]/page.tsx:92-99`

- [ ] **Step 1:** In `_fetch_post_by_slug` and `_fetch_published_posts`, add to the SELECT: `p.metadata->>'featured_image_alt' AS featured_image_alt`, and include `"featured_image_alt"` in the emitted JSON dict for each post.
- [ ] **Step 2:** In `lib/posts.ts` `Post`, add `featured_image_alt?: string;`.
- [ ] **Step 3:** In `page.tsx` `generateMetadata`, change the OG image `alt: post.title` to `alt: post.featured_image_alt || post.title`.
- [ ] **Step 4:** Add/extend a static-export test asserting `featured_image_alt` is present in the per-slug payload (mirror `tests/unit/services/test_static_export_service.py`). Run it.
- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/static_export_service.py web/public-site/lib/posts.ts web/public-site/app/posts/[slug]/page.tsx src/cofounder_agent/tests/unit/services/test_static_export_service.py
git commit -m "feat(seo): export featured_image_alt and use it for og:image:alt"
```

---

## Phase 5 — Publish-time title-suffix guard (#4 forward)

### Task 10: Strip batch/debug suffixes before a title is persisted

**Files:**

- Modify: `src/cofounder_agent/services/publish_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_publish_title_sanitizer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_publish_title_sanitizer.py
from services.publish_service import sanitize_published_title

def test_strips_batch_suffix():
    assert sanitize_published_title(
        "How embeddings rank similarity (2026-05-11 15:33 overnight B #1)"
    ) == "How embeddings rank similarity"

def test_strips_batch_letter_hash():
    assert sanitize_published_title(
        "Indie Game Updates (2026-05-11 17:48 batch C #5)"
    ) == "Indie Game Updates"

def test_leaves_clean_title_untouched():
    assert sanitize_published_title("FastAPI Async Patterns") == "FastAPI Async Patterns"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_publish_title_sanitizer.py -q`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement + call at insert time**

Add to `publish_service.py`:

```python
import re as _re_title

_TITLE_SUFFIX_RE = _re_title.compile(
    r"\s*\((?:\d{4}-\d{2}-\d{2}[^)]*?"
    r"(?:batch\s+[A-Za-z]\s*#\d+|overnight[^)]*|#\d+)|[^)]*#\d+)\)\s*$",
    _re_title.IGNORECASE,
)

def sanitize_published_title(title: str | None) -> str:
    if not title:
        return title or ""
    return _TITLE_SUFFIX_RE.sub("", title).strip()
```

Then in `publish_post_from_task`, where the post `title` is resolved before the INSERT, wrap it: `title = sanitize_published_title(title)`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_publish_title_sanitizer.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/publish_service.py src/cofounder_agent/tests/unit/services/test_publish_title_sanitizer.py
git commit -m "fix(seo): strip batch/debug suffixes from titles at publish time"
```

---

## Phase 6 — Backfill orchestrator

### Task 11: `scripts/backfill-image-alt-vision.py`

**Files:**

- Create: `scripts/backfill-image-alt-vision.py`

- [ ] **Step 1: Implement** (modeled on `scripts/backfill-alt-text.py`; reuses `image_captioner`, `static_export_service`, and the publish title sanitizer)

Key behaviors (no new test file — exercised via `--dry-run` against prod in Phase 7; the underlying units are already tested):

- DB-URL resolution + `--dry-run` + `--post-id` + `--limit` identical to `backfill-alt-text.py`.
- `--mode alt` (default): for each published post with `<img>` (and `pipeline_versions`), and NOT already stamped `metadata->>'alt_vision_backfilled_at'` (unless `--force`): re-caption every inline `<img>` via `caption_image`, re-caption featured into `metadata.featured_image_alt`, write `posts.content`/`metadata`, stamp `alt_vision_backfilled_at=now()`, then `await export_post(pool, slug, site_config=sc)`.
- `--mode seo-desc`: for the 27 dev_diary/`seo_description IS NULL` posts, run the SEO generator (`services.seo_content_generator`) over `content` → set `seo_description` (+ `excerpt`) → export.
- `--mode titles`: apply `sanitize_published_title` to contaminated `posts.title` rows → export.
- Must build a real `SiteConfig` with a `_pool` (needed by `caption_image` → `dispatch_complete` and by `export_post`). Reuse the bootstrap the other scripts use (`brain.bootstrap` / `SiteConfig.load(pool)`), or run inside the worker container: `docker exec poindexter-worker python scripts/backfill-image-alt-vision.py --dry-run`.

- [ ] **Step 2: Syntax/import check**

Run: `python -c "import ast; ast.parse(open('scripts/backfill-image-alt-vision.py').read())"`
Expected: no output (valid). Full run happens in Phase 7.

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill-image-alt-vision.py
git commit -m "feat(seo): vision alt-text + seo-desc + title backfill orchestrator"
```

---

## Phase 7 — Run backfill + verify (after code merged & worker deployed)

### Task 12: Execute backfill against prod and verify

- [ ] **Step 1:** Deploy: update the bind-mounted checkout + `docker restart poindexter-worker`; verify the host file changed and re-check (Windows/Docker file-lock race — see [[worker-deploy-bind-mount]]). Run migrations (`vision_alt_settings`, `reseed_canonical_blog_caption_images`).
- [ ] **Step 2:** Dry-run alt mode and READ the proposed diffs:
      `docker exec poindexter-worker python scripts/backfill-image-alt-vision.py --mode alt --dry-run --limit 3`
      Confirm captions are accurate vs. the images (spot-check 2-3 against the URLs).
- [ ] **Step 3:** Live alt backfill: `docker exec poindexter-worker python scripts/backfill-image-alt-vision.py --mode alt`
- [ ] **Step 4:** `--mode seo-desc` (dry-run → live) and `--mode titles` (dry-run → live).
- [ ] **Step 5:** Re-run the audit: `python .audit/seo_audit.py`. Expected: missing-meta-desc → ~0, alt accuracy fixed, titles clean, canonicals/og:image → 0. Spot-check one live per-slug R2 JSON (`curl $STATIC_URL/posts/<slug>.json`) shows corrected inline alts.
- [ ] **Step 6:** Confirm on the live site (ISR/tag revalidation) that a sample post's inline images carry the new alts.

---

## Self-review notes

- **Spec coverage:** A1→Task1, A2→Tasks3-4, A3→Task11, A4→Task9, B1→Tasks5+11, B2→Tasks10+11, B3→Task6, C1→Task7, C2→Task8. All 7 issues + featured-alt covered.
- **Idempotency:** backfill marker `alt_vision_backfilled_at` (Task 11) + `--force`.
- **Dispatcher unknown:** Task 1 Step 6 smoke-test resolves whether litellm forwards images to ollama; fallback documented behind `caption_image`.
- **Light-import migrations:** both migrations import only `canonical_blog_spec` (pure data) / stdlib — safe for migrations-smoke ([[migrations-smoke-light-env]]).
- **Prettier/markdown:** re-read committed `.md` (glob/`*` tokens) per [[prettier-hook-mangles-markdown-globs]].

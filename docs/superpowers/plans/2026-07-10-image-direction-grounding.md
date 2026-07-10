# Content-Grounded Image Direction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make blog images match the article by letting the writer (Sonnet) nominate every image from full context, demoting the local model to phrasing a given subject in a rotated style, and cycling styles without clustering.

**Architecture:** The writer places `[IMAGE: subject]` markers + one `[HERO-IMAGE: subject]`. `content.plan_image_markers` extracts the hero, numbers the inline markers, and uses them (writer-primary); when a draft has none it falls back to the image decision agent, which now reads section **body** text. The featured stage grounds its prompt on the hero subject and picks styles least-recently-used. The prompt-builder model moves from `phi4:14b` to `gemma-4-31B-it-qat`.

**Tech Stack:** Python 3.12, FastAPI worker, LangGraph pipeline atoms/stages, Ollama (local models), SDXL/Z-Image via the image-gen HTTP server, Postgres (`app_settings`, `posts`), pytest.

## Global Constraints

- **Rebase first.** Before any task, rebase this branch onto `origin/main` **after** `claude/cli-scheduling-time-parse-ff68b6` (rebuild-images) merges. The three image atoms are consumed by `ImageRebuildService`; Task 8 guards their I/O against that consumer.
- **Atom I/O is a contract** (Task 8 enforces): `content.plan_image_markers.run` returns `{content, image_plans, featured_image_plan?}` (+ additive `featured_image_subject`); `content.generate_images.run` returns `image_results[{num, url, alt_text, source}]` with `source ∈ {"image_gen","pexels","none"}`; `content.inject_images.run` accepts `{content, image_results, task_id, database_service}` → `{content}`; `_try_image_gen`/`_try_pexels` signatures unchanged; injected inline HTML stays `<img …>` + optional `<figcaption>`.
- **No invented counts in prompts** (`feedback_no_hardcoded_lengths_in_prompts`): the writer prompt must not state a number of images; the cap lives in `writer_max_inline_images` (default `3`), enforced in code.
- **Settings are DB-first, seeder is `ON CONFLICT DO NOTHING`**: changing a default in `settings_defaults.py` does NOT update an existing prod row. The live model-pin flip is an explicit rollout step (Task 9).
- **Model IDs carry the exact Ollama tag**: `ollama/gemma-4-31B-it-qat:latest`.
- Run backend tests from `src/cofounder_agent`: `poetry run pytest <path> -q`.
- Commit after every green task. Conventional-commit messages. Co-author trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `services/settings_defaults.py` — add 2 keys, change 2 model pins, add METADATA (Task 1).
- `skills/content/blog-generation/SKILL.md` — writer places markers (Task 2).
- `modules/content/atoms/_writer_markers.py` — **new** pure helpers: hero extraction + inline numbering (Task 3).
- `modules/content/atoms/content_plan_image_markers.py` — wire normalization + surface `featured_image_subject` (Task 3).
- `services/image_decision_agent.py` — body-fed section extraction (Task 4).
- `skills/content/image-generation/SKILL.md` — `image.decision` excerpt note; `image.featured_image` `{subject}` field (Tasks 4, 5).
- `modules/content/stages/source_featured_image.py` — subject grounding (Task 5) + LRU style selection (Task 6).
- `services/self_review.py` + `modules/content/stages/quality_evaluation.py` — preserve/ignore markers (Task 7).
- `tests/unit/modules/content/test_image_atom_contracts.py` — **new** contract guard (Task 8).
- Docs + live-settings rollout (Task 9).

---

### Task 1: Settings — new caps + model swap

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (DEFAULTS near line 224-226; METADATA near line 1958)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: `DEFAULTS["writer_max_inline_images"] == "3"`, `DEFAULTS["image_decision_section_body_chars"] == "500"`, `DEFAULTS["inline_image_prompt_model"] == "ollama/gemma-4-31B-it-qat:latest"`, `DEFAULTS["model_role_image_decision"] == "ollama/gemma-4-31B-it-qat:latest"`.

- [ ] **Step 1: Write the failing test**

Add to `test_settings_defaults.py`:

```python
def test_image_direction_defaults_present():
    """Writer places images; local prompt-builder is gemma-4-31B (feedback: cloud=writer only)."""
    from services.settings_defaults import DEFAULTS, METADATA

    assert DEFAULTS["writer_max_inline_images"] == "3"
    assert DEFAULTS["image_decision_section_body_chars"] == "500"
    assert DEFAULTS["inline_image_prompt_model"] == "ollama/gemma-4-31B-it-qat:latest"
    assert DEFAULTS["model_role_image_decision"] == "ollama/gemma-4-31B-it-qat:latest"
    assert METADATA["writer_max_inline_images"]["value_type"] == "integer"
    assert METADATA["image_decision_section_body_chars"]["value_type"] == "integer"
    assert METADATA["inline_image_prompt_model"]["value_type"] == "model"
    assert METADATA["model_role_image_decision"]["value_type"] == "model"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_image_direction_defaults_present -q`
Expected: FAIL (`KeyError: 'writer_max_inline_images'` and the model asserts on the old `llama3:latest`/`qwen3:8b`).

- [ ] **Step 3: Edit DEFAULTS**

In `settings_defaults.py`, change the two existing model lines (currently `'inline_image_prompt_model': 'llama3:latest',` and `'model_role_image_decision': 'qwen3:8b',`) and add the two caps next to them:

```python
    'inline_image_prompt_model': 'ollama/gemma-4-31B-it-qat:latest',
    'local_llm_api_url': 'http://localhost:11434',
    'model_role_image_decision': 'ollama/gemma-4-31B-it-qat:latest',
    # Writer places [IMAGE:]/[HERO-IMAGE:] markers; this caps how many inline
    # images survive normalization (feedback_no_hardcoded_lengths_in_prompts —
    # the prompt states no number, the cap lives here).
    'writer_max_inline_images': '3',
    # Body chars per section fed to the image decision agent fallback so its
    # picks are grounded in content, not just heading titles.
    'image_decision_section_body_chars': '500',
```

- [ ] **Step 4: Add METADATA entries**

In the `METADATA` dict (near the LLM-model-selection block, ~line 1958), add:

```python
    'inline_image_prompt_model': {'owner': 'image_pipeline', 'value_type': 'model'},
    'model_role_image_decision': {'owner': 'image_decision_agent', 'value_type': 'model'},
    'writer_max_inline_images': {'owner': 'plan_image_markers', 'value_type': 'integer'},
    'image_decision_section_body_chars': {'owner': 'image_decision_agent', 'value_type': 'integer'},
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_image_direction_defaults_present -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(images): seed writer-image caps + gemma-4-31B prompt-builder pins

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Writer contract — the writer places image markers

**Files:**

- Modify: `src/cofounder_agent/skills/content/blog-generation/SKILL.md` (the `IMPORTANT OUTPUT RULES` block, ~lines 59-65)
- Test: `src/cofounder_agent/tests/unit/services/test_blog_generation_skill.py` (create)

**Interfaces:**

- Produces: the writer skill body instructs `[IMAGE: …]` + one `[HERO-IMAGE: …]` placement and no longer forbids image placeholders.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/test_blog_generation_skill.py`:

```python
from pathlib import Path


def _skill_body() -> str:
    p = Path(__file__).resolve()
    root = next(a for a in p.parents if (a / "skills").is_dir())
    return (root / "skills/content/blog-generation/SKILL.md").read_text(encoding="utf-8")


def test_writer_skill_instructs_image_markers():
    body = _skill_body()
    assert "[IMAGE:" in body
    assert "[HERO-IMAGE:" in body
    # The old suppression rule must be gone.
    assert "Do NOT include image descriptions" not in body
    # No invented count (feedback_no_hardcoded_lengths_in_prompts).
    assert "exactly 3 images" not in body.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_blog_generation_skill.py -q`
Expected: FAIL (`[IMAGE:` not present; suppression rule still there).

- [ ] **Step 3: Edit the writer SKILL.md**

Replace the four image-suppression bullets in the `IMPORTANT OUTPUT RULES` block with placement rules:

```markdown
- Place images where a visual genuinely helps the reader. Mark each with `[IMAGE: <a concrete, specific subject drawn from THIS section — an object, diagram, scene, or visual metaphor>]` on its own line. Describe the SUBJECT only — not an art style, not a camera or render instruction. Only where it adds real value; skip code-heavy or very short sections. Do not state or aim for a fixed number.
- Add exactly one hero image as the FIRST line of the article: `[HERO-IMAGE: <a concrete subject that represents the whole post>]`.
- Never depict identifiable people, faces, hands, or any text/words in an image subject — the brand style is objects, hardware, diagrams, and environments.
- Do NOT leave empty markdown brackets like " []" at the end of a sentence. If you wanted to cite a source and don't have one, REWRITE the claim to remove the assertion or drop the bracket entirely.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_blog_generation_skill.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/skills/content/blog-generation/SKILL.md src/cofounder_agent/tests/unit/services/test_blog_generation_skill.py
git commit -m "feat(images): writer places [IMAGE:]/[HERO-IMAGE:] markers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Marker normalization + hero extraction

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/_writer_markers.py`
- Modify: `src/cofounder_agent/modules/content/atoms/content_plan_image_markers.py`
- Test: `src/cofounder_agent/tests/unit/modules/content/test_writer_markers.py` (create)

**Interfaces:**

- Produces: `extract_hero_subject(content: str) -> tuple[str, str | None]` (returns content with the hero line removed, and the hero subject or None); `number_inline_markers(content: str, max_inline: int) -> str` (converts `[IMAGE: x]` → `[IMAGE-N: x]` in order, drops markers beyond `max_inline`). `content.plan_image_markers.run` adds `featured_image_subject` to its result when a hero marker was present.
- Consumes: `writer_max_inline_images` (Task 1).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/modules/content/test_writer_markers.py`:

```python
from modules.content.atoms._writer_markers import (
    extract_hero_subject,
    number_inline_markers,
)


def test_extract_hero_pulls_subject_and_strips_line():
    content = "[HERO-IMAGE: a branching token tree]\n\n# Intro\n\nBody."
    new, hero = extract_hero_subject(content)
    assert hero == "a branching token tree"
    assert "[HERO-IMAGE:" not in new
    assert new.lstrip().startswith("# Intro")


def test_extract_hero_none_when_absent():
    new, hero = extract_hero_subject("# Intro\n\nBody.")
    assert hero is None
    assert new == "# Intro\n\nBody."


def test_number_inline_markers_sequential():
    content = "A\n[IMAGE: draft model]\nB\n[IMAGE: verify step]\n"
    out = number_inline_markers(content, max_inline=3)
    assert "[IMAGE-1: draft model]" in out
    assert "[IMAGE-2: verify step]" in out


def test_number_inline_markers_caps_and_strips_extras():
    content = "[IMAGE: a]\n[IMAGE: b]\n[IMAGE: c]\n[IMAGE: d]\n"
    out = number_inline_markers(content, max_inline=2)
    assert "[IMAGE-1: a]" in out and "[IMAGE-2: b]" in out
    assert "[IMAGE-3:" not in out
    assert "[IMAGE: c]" not in out and "[IMAGE: d]" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_writer_markers.py -q`
Expected: FAIL (`ModuleNotFoundError: _writer_markers`).

- [ ] **Step 3: Implement the helper module**

Create `modules/content/atoms/_writer_markers.py`:

```python
"""Pure helpers for writer-placed image markers.

The writer (blog-generation SKILL.md) emits `[IMAGE: subject]` inline and one
`[HERO-IMAGE: subject]` first line. These functions extract the hero and number
the inline markers into the `[IMAGE-N: …]` form the rest of the pipeline parses.
No I/O — trivially unit-testable.
"""
from __future__ import annotations

import re

_HERO_RE = re.compile(r"^[ \t]*\[HERO-IMAGE:\s*([^\]]*)\][ \t]*\n?", re.IGNORECASE | re.MULTILINE)
_UNNUMBERED_RE = re.compile(r"\[IMAGE:\s*([^\]]*)\]", re.IGNORECASE)


def extract_hero_subject(content: str) -> tuple[str, str | None]:
    """Return (content_without_hero_line, hero_subject_or_None). First match wins."""
    m = _HERO_RE.search(content)
    if not m:
        return content, None
    subject = (m.group(1) or "").strip()
    stripped = _HERO_RE.sub("", content, count=1)
    return stripped, (subject or None)


def number_inline_markers(content: str, max_inline: int) -> str:
    """Convert `[IMAGE: x]` → `[IMAGE-N: x]` in document order; drop markers past max_inline."""
    counter = {"n": 0}

    def _sub(match: re.Match[str]) -> str:
        counter["n"] += 1
        if counter["n"] > max_inline:
            return ""  # strip extras beyond the cap
        desc = (match.group(1) or "").strip()
        return f"[IMAGE-{counter['n']}: {desc}]"

    return _UNNUMBERED_RE.sub(_sub, content)
```

- [ ] **Step 4: Run helper test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_writer_markers.py -q`
Expected: PASS

- [ ] **Step 5: Write the failing atom-integration test**

Add to `tests/unit/modules/content/test_writer_markers.py`:

```python
import pytest
from services.site_config import SiteConfig


@pytest.mark.asyncio
async def test_plan_image_markers_surfaces_hero_and_uses_writer_markers():
    from modules.content.atoms import content_plan_image_markers

    sc = SiteConfig(initial_config={"writer_max_inline_images": "3"})
    state = {
        "content": "[HERO-IMAGE: a token tree]\n\n# Draft\n\nText.\n[IMAGE: a draft model]\nMore.",
        "topic": "speculative decoding",
        "site_config": sc,
    }
    out = await content_plan_image_markers.run(state)
    assert out["featured_image_subject"] == "a token tree"
    assert "[HERO-IMAGE:" not in out["content"]
    # writer-primary: the marker was numbered and parsed, decision agent NOT called.
    assert any(p["desc"] == "a draft model" for p in out["image_plans"])
    assert "[IMAGE-1: a draft model]" in out["content"]
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_writer_markers.py::test_plan_image_markers_surfaces_hero_and_uses_writer_markers -q`
Expected: FAIL (`featured_image_subject` absent; hero line still present).

- [ ] **Step 7: Wire normalization into the atom**

In `content_plan_image_markers.py`, after the `content_text` empty-guard and before the VRAM guard, add normalization; carry `hero_subject` into the result. Replace the body of `run` from the `content_text` read through the result assembly:

```python
    content_text = (state.get("content") or "").strip()
    if not content_text:
        return {}

    topic = state.get("topic", "")
    category = state.get("category", "technology")
    site_config = state.get("site_config")

    # Writer-placed markers (blog-generation SKILL.md): extract the hero, number
    # the inline markers, enforce the cap. No markers → the fallback below runs.
    from modules.content.atoms._writer_markers import (
        extract_hero_subject,
        number_inline_markers,
    )
    max_inline = site_config.get_int("writer_max_inline_images", 3) if site_config is not None else 3
    content_text, hero_subject = extract_hero_subject(content_text)
    content_text = number_inline_markers(content_text, max_inline)

    # VRAM guard: unload writer LLM before image-gen may load.
    try:
        from services.llm_providers.ollama_unload import maybe_unload_writer_before_image_gen
        await maybe_unload_writer_before_image_gen(
            site_config=site_config,
            stage_label="content.plan_image_markers",
        )
    except Exception as exc:
        logger.debug("[content.plan_image_markers] VRAM guard skipped: %s", exc)

    placeholders = _PLACEHOLDER_RE.findall(content_text)
    stages = state.get("stages") or {}

    if not placeholders:
        from modules.content.stages.replace_inline_images import _plan_and_inject_placeholders
        content_text, plan = await _plan_and_inject_placeholders(
            content_text, topic, category, site_config=site_config,
        )
        if plan is not None and plan.get("agent_error"):
            stages["2c_image_agent_error"] = plan["agent_error"]
        if plan is not None and plan.get("featured_image_plan"):
            result_extra = {"featured_image_plan": plan["featured_image_plan"]}
        else:
            result_extra: dict[str, Any] = {}  # type: ignore[no-redef]
        placeholders = _PLACEHOLDER_RE.findall(content_text)
    else:
        result_extra = {}

    image_plans = [{"num": num, "desc": desc.strip()} for num, desc in placeholders]

    result: dict[str, Any] = {"content": content_text, "image_plans": image_plans}
    if hero_subject:
        result["featured_image_subject"] = hero_subject
    if result_extra:
        result.update(result_extra)
    if stages:
        result["stages"] = stages
    return result
```

Also add `featured_image_subject` to `ATOM_META.produces`:

```python
    produces=("content", "image_plans", "featured_image_subject"),
```

- [ ] **Step 8: Run the atom test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_writer_markers.py -q`
Expected: PASS (all four helper tests + the atom test).

- [ ] **Step 9: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_writer_markers.py src/cofounder_agent/modules/content/atoms/content_plan_image_markers.py src/cofounder_agent/tests/unit/modules/content/test_writer_markers.py
git commit -m "feat(images): extract hero + number writer image markers in plan_image_markers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Body-fed decision agent (fallback)

**Files:**

- Modify: `src/cofounder_agent/services/image_decision_agent.py` (section extraction, ~lines 129-152)
- Modify: `src/cofounder_agent/skills/content/image-generation/SKILL.md` (`image.decision` block)
- Test: `src/cofounder_agent/tests/unit/services/test_image_decision_prompt.py`

**Interfaces:**

- Consumes: `image_decision_section_body_chars` (Task 1).
- Produces: `plan_images` builds `section_list` entries as `title` + a body excerpt; JSON output shape (`featured` + `inline[]`) unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/services/test_image_decision_prompt.py`:

```python
def test_section_list_includes_body_excerpt(monkeypatch):
    """The decision agent grounds on section body, not just headings."""
    import services.image_decision_agent as ida

    captured = {}

    class _PM:
        def get_prompt(self, key, **kw):
            captured.update(kw)
            return "PROMPT"

    monkeypatch.setattr(ida, "get_prompt_manager", lambda: _PM())
    content = "## Draft phase\n\nA small draft model proposes tokens cheaply.\n\n## Verify phase\n\nThe big model checks them."
    sections = ida._extract_sections(content, body_chars=200)
    section_list = ida._render_section_list(sections)
    assert "Draft phase" in section_list
    assert "small draft model proposes tokens" in section_list
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_decision_prompt.py::test_section_list_includes_body_excerpt -q`
Expected: FAIL (`_extract_sections` / `_render_section_list` don't exist).

- [ ] **Step 3: Extract helpers + feed body in `image_decision_agent.py`**

Add module-level helpers and use them in `plan_images`. Insert near the top (after imports):

```python
def _extract_sections(content: str, *, body_chars: int) -> list[dict]:
    """Sections as {level, title, excerpt}. Real H2/H3 first; bold-text pseudo-
    headings (title-only) as the fallback so we never lose structure."""
    matches = list(re.finditer(r'^(#{2,3})\s+(.+)$', content, re.MULTILINE))
    sections: list[dict] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        excerpt = " ".join(content[start:end].split())[:body_chars]
        sections.append({"level": len(m.group(1)), "title": m.group(2).strip(), "excerpt": excerpt})
    if sections:
        return sections
    bold = re.findall(r'^\*\*(.{1,80}?)\*\*\s*$', content, re.MULTILINE)
    return [{"level": 2, "title": t.strip(), "excerpt": ""} for t in bold if t.strip()]


def _render_section_list(sections: list[dict]) -> str:
    lines = []
    for i, s in enumerate(sections):
        lines.append(f"  {i+1}. {s['title']}")
        if s.get("excerpt"):
            lines.append(f"     excerpt: {s['excerpt']}")
    return "\n".join(lines)
```

In `plan_images`, replace the heading-extraction + `section_list` block (the `headings = re.findall(...)` through `section_list = "\n".join(...)`) with:

```python
    body_chars = int(_sc.get("image_decision_section_body_chars", 500) or 500)
    sections = _extract_sections(content, body_chars=body_chars)
    if not sections:
        logger.info("[IMAGE_AGENT] No sections found — skipping image planning")
        return ImagePlanResult()
    section_list = _render_section_list(sections)
```

- [ ] **Step 4: Update the `image.decision` skill to use the excerpts**

In `skills/content/image-generation/SKILL.md`, in the `image.decision` block, change the `SECTIONS:` line and add an instruction so the model grounds on excerpts:

```text
SECTIONS (title + a short excerpt of the actual text):
{section_list}
```

And append to `RULES`:

```text
6. Ground each image's subject in the section's excerpt — depict what that section actually discusses, not a generic {category} image.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_decision_prompt.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/image_decision_agent.py src/cofounder_agent/skills/content/image-generation/SKILL.md src/cofounder_agent/tests/unit/services/test_image_decision_prompt.py
git commit -m "feat(images): feed section body to the image decision agent fallback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Featured image grounded on the hero subject

**Files:**

- Modify: `src/cofounder_agent/modules/content/stages/source_featured_image.py` (`execute`, `_try_image_gen_featured`, `_build_image_gen_prompt`, `_resolve_image_prompt`)
- Modify: `src/cofounder_agent/skills/content/image-generation/SKILL.md` (`image.featured_image` — `{topic}` → `{subject}`)
- Test: `src/cofounder_agent/tests/unit/services/test_featured_image_relevance.py`

**Interfaces:**

- Consumes: `featured_image_subject` (Task 3), `featured_image_plan` (decision-agent path).
- Produces: `_build_image_gen_prompt(subject, on_style_picked, style_tracker, *, site_config, platform)` — first param renamed `topic`→`subject`; `image.featured_image` prompt renders with `subject=`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/services/test_featured_image_relevance.py`:

```python
def test_featured_subject_precedence():
    from modules.content.stages.source_featured_image import _resolve_featured_subject

    # writer hero wins over decision plan and topic
    ctx = {"topic": "T", "featured_image_subject": "hero subj",
           "featured_image_plan": {"prompt": "plan subj"}}
    assert _resolve_featured_subject(ctx) == "hero subj"
    # decision plan when no hero
    assert _resolve_featured_subject({"topic": "T", "featured_image_plan": {"prompt": "plan subj"}}) == "plan subj"
    # topic as last resort
    assert _resolve_featured_subject({"topic": "T"}) == "T"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_featured_image_relevance.py::test_featured_subject_precedence -q`
Expected: FAIL (`_resolve_featured_subject` undefined).

- [ ] **Step 3: Add the resolver + thread the subject**

In `source_featured_image.py`, add a module-level helper:

```python
def _resolve_featured_subject(context: dict[str, Any]) -> str:
    """Featured-image subject: writer hero > decision-agent plan > topic."""
    hero = (context.get("featured_image_subject") or "").strip()
    if hero:
        return hero
    plan = context.get("featured_image_plan") or {}
    plan_prompt = (plan.get("prompt") if isinstance(plan, dict) else "") or ""
    if plan_prompt.strip():
        return plan_prompt.strip()
    return context.get("topic", "")
```

In `execute`, where it calls `_try_image_gen_featured(...)`, pass the resolved subject as a new `subject=` kwarg (keep `existing_prompt` as the verbatim override):

```python
            gen_image = await _try_image_gen_featured(
                subject=_resolve_featured_subject(context),
                existing_prompt=context.get("featured_image_prompt", ""),
                task_id=task_id,
                on_style_picked=_on_style_picked_sync,
                style_tracker=style_tracker,
                site_config=site_config,
                platform=platform,
            )
```

Change `_try_image_gen_featured` to accept `subject` (rename its `topic` param) and pass it through to `_build_image_gen_prompt`:

```python
async def _try_image_gen_featured(
    subject: str,
    existing_prompt: str,
    task_id: str | None,
    on_style_picked: Any,
    style_tracker: Any,
    *,
    site_config: Any = None,
    platform: Any = None,
) -> GeneratedImage | None:
    ...
        img_gen_prompt = existing_prompt
        if not img_gen_prompt:
            img_gen_prompt = await _build_image_gen_prompt(
                subject, on_style_picked, style_tracker,
                site_config=site_config, platform=platform,
            )
```

Change `_build_image_gen_prompt`'s first param `topic`→`subject`, and its `_resolve_image_prompt` call to pass `subject=`:

```python
async def _build_image_gen_prompt(
    subject: str,
    on_style_picked: Any,
    style_tracker: Any,
    *,
    site_config: Any = None,
    platform: Any = None,
) -> str:
    ...
    img_prompt = _resolve_image_prompt(
        "image.featured_image",
        subject=subject,
        style=chosen_style,
        style_tags=style_tags,
    )
```

Update `_resolve_image_prompt`'s fallback to read `subject` (it already checks `kwargs.get("topic")`; add `subject`):

```python
        subject = kwargs.get("subject") or kwargs.get("topic") or kwargs.get("search_query") or ""
```

- [ ] **Step 4: Update the `image.featured_image` skill**

In `skills/content/image-generation/SKILL.md`, `image.featured_image` block: change `Article topic: {topic}` to `Image subject: {subject}` and adjust the first line to depict the subject:

```text
Write a single Stable Diffusion XL image prompt for a magazine-style editorial cover illustration.

Image subject: {subject}
Art style: {style} — {style_tags}

Depict the given subject concretely (a recognizable object, place, or visual metaphor), rendered fully in the "{style}" art style. Commit to that style's medium, palette, and composition. Do NOT default to a generic glowing-circuit board or abstract floating-data backdrop, and do not lock every image to teal/cyan. Faceless silhouettes are fine; no identifiable faces, no hands, no text or words in the image.

Output ONLY the image prompt, 1-2 sentences, nothing else.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_featured_image_relevance.py -q`
Expected: PASS

- [ ] **Step 6: Run the stage's existing tests to check the rename didn't break callers**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/ -q -k source_featured_image`
Expected: PASS (fix any test that constructed `_try_image_gen_featured(topic=...)` / `_build_image_gen_prompt(topic, ...)` to use the new `subject` param).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/modules/content/stages/source_featured_image.py src/cofounder_agent/skills/content/image-generation/SKILL.md src/cofounder_agent/tests/unit/services/test_featured_image_relevance.py
git commit -m "feat(images): ground the featured image on the writer's hero subject

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Least-recently-used style rotation

**Files:**

- Modify: `src/cofounder_agent/modules/content/stages/source_featured_image.py` (`_build_image_gen_prompt` selection; add `_load_style_last_used`, `_select_style_lru`)
- Test: `src/cofounder_agent/tests/unit/services/test_image_style_lru.py` (create)

**Interfaces:**

- Produces: `_select_style_lru(styles, last_used, mem_recent, rng=random) -> tuple[str, str]` — pure selection; picks the least-recently-used style (never-used first), excluding the in-memory recent set, random tie-break.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/test_image_style_lru.py`:

```python
import random

from modules.content.stages.source_featured_image import _select_style_lru

STYLES = [("flat_vector", "t1"), ("pixel_art", "t2"), ("silhouette", "t3")]


def test_never_used_style_wins():
    # pixel_art & silhouette never used; flat_vector used recently.
    last_used = {"flat_vector": "2026-07-10T00:00:00"}
    chosen, _ = _select_style_lru(STYLES, last_used, set(), rng=random.Random(0))
    assert chosen in ("pixel_art", "silhouette")


def test_oldest_used_wins_when_all_used():
    last_used = {
        "flat_vector": "2026-07-10T00:00:00",
        "pixel_art": "2026-07-01T00:00:00",   # oldest
        "silhouette": "2026-07-05T00:00:00",
    }
    chosen, _ = _select_style_lru(STYLES, last_used, set(), rng=random.Random(0))
    assert chosen == "pixel_art"


def test_in_memory_recent_excluded():
    last_used = {}  # all never-used
    chosen, _ = _select_style_lru(STYLES, last_used, {"pixel_art", "silhouette"}, rng=random.Random(0))
    assert chosen == "flat_vector"


def test_full_cycle_before_repeat():
    """Greedily selecting + recording last_used cycles the whole pool before repeating."""
    last_used: dict[str, str] = {}
    picks = []
    for i in range(len(STYLES)):
        chosen, _ = _select_style_lru(STYLES, last_used, set(), rng=random.Random(i))
        picks.append(chosen)
        last_used[chosen] = f"2026-07-10T00:00:0{i}"
    assert sorted(picks) == sorted(s[0] for s in STYLES)  # each used once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_style_lru.py -q`
Expected: FAIL (`_select_style_lru` undefined).

- [ ] **Step 3: Implement selection + the DB loader**

In `source_featured_image.py`, add:

```python
def _select_style_lru(
    styles: list[tuple[str, str]],
    last_used: dict[str, str],
    mem_recent: set[str],
    *,
    rng: Any = random,
) -> tuple[str, str]:
    """Pick the least-recently-used style. Never-used (absent from last_used) is
    oldest. Exclude the in-memory recent set unless that would leave nothing.
    Random tie-break among equally-old styles. #image-zimage-and-variety."""
    candidates = [s for s in styles if s[0] not in mem_recent] or styles
    # "" sorts before any ISO timestamp → never-used styles are treated as oldest.
    oldest_key = min(last_used.get(s[0], "") for s in candidates)
    tied = [s for s in candidates if last_used.get(s[0], "") == oldest_key]
    return rng.choice(tied)


async def _load_style_last_used(site_config: Any = None) -> dict[str, str]:
    """Map style-name → most-recent published_at (ISO) for LRU rotation.
    Reads featured_image_data->>'image_style' (fallback metadata->>'image_style')."""
    if site_config is None:
        return {}
    _QUERY = """
        SELECT COALESCE(featured_image_data->>'image_style', metadata->>'image_style') AS style,
               MAX(published_at) AS last_used
        FROM posts WHERE status = 'published'
          AND COALESCE(featured_image_data->>'image_style', metadata->>'image_style') IS NOT NULL
        GROUP BY 1
    """
    pool = getattr(site_config, "_pool", None)
    if pool is None:
        return {}
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_QUERY)
        return {r["style"]: (r["last_used"].isoformat() if r["last_used"] else "") for r in rows if r["style"]}
    except Exception:
        return {}
```

Then in `_build_image_gen_prompt`, replace the selection block (`recent = await _load_recent_published_styles(...)` through `chosen_style, style_tags = random.choice(available)`) with:

```python
    last_used = await _load_style_last_used(site_config)
    mem_recent = set(style_tracker.recent())
    chosen_style, style_tags = _select_style_lru(styles, last_used, mem_recent)
    style_tracker.record(chosen_style)
    on_style_picked(chosen_style)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_style_lru.py -q`
Expected: PASS

- [ ] **Step 5: Run the stage's variety/config tests**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_image_variety_config.py -q`
Expected: PASS (adjust any test that asserted `random.choice`-based selection to the LRU behavior).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/stages/source_featured_image.py src/cofounder_agent/tests/unit/services/test_image_style_lru.py
git commit -m "feat(images): least-recently-used featured style rotation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Preserve markers through the early pipeline stages

**Files:**

- Modify: `src/cofounder_agent/services/self_review.py` (the revise prompt)
- Modify: `src/cofounder_agent/modules/content/stages/quality_evaluation.py` (strip markers before scoring)
- Test: `src/cofounder_agent/tests/unit/services/test_marker_survival.py` (create)

**Interfaces:**

- Consumes: the `[IMAGE-N: …]` / `[IMAGE: …]` / `[HERO-IMAGE: …]` marker forms.
- Produces: `quality_evaluation` scores on marker-stripped text; `self_review` revise prompt instructs verbatim marker preservation.

- [ ] **Step 1: Locate the two edit points**

Run: `cd src/cofounder_agent && grep -n "get_prompt\|revise\|def self_review_and_revise" services/self_review.py | head` and `grep -n "content\b\|len(\|def " modules/content/stages/quality_evaluation.py | head`.
Note the revise-prompt construction in `self_review.py` and where `quality_evaluation.py` reads `content` for scoring.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/services/test_marker_survival.py`:

```python
from modules.content.stages.quality_evaluation import _strip_image_markers


def test_strip_image_markers_removes_all_forms():
    text = "[HERO-IMAGE: x]\n# H\nBody [IMAGE: a] and [IMAGE-2: b] end."
    out = _strip_image_markers(text)
    assert "IMAGE" not in out
    assert "Body" in out and "end." in out
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_marker_survival.py -q`
Expected: FAIL (`_strip_image_markers` undefined).

- [ ] **Step 4: Add the stripper and use it in the scorer**

In `quality_evaluation.py`, add and apply before any length/pattern scoring of `content`:

```python
import re as _re

_MARKER_RE = _re.compile(r"\[(?:HERO-)?IMAGE(?:-\d+)?:[^\]]*\]", _re.IGNORECASE)


def _strip_image_markers(text: str) -> str:
    """Remove writer image markers so they don't skew word-count / pattern scores."""
    return _MARKER_RE.sub("", text)
```

At the top of the stage's scoring path, score `_strip_image_markers(content_text)` rather than the raw content.

- [ ] **Step 5: Instruct the reviser to preserve markers**

In `services/self_review.py`, in the revise-prompt string (the instruction that tells the writer to return the corrected article), append a preservation clause:

```text
Preserve any [IMAGE: ...], [IMAGE-N: ...], and [HERO-IMAGE: ...] markers exactly as they appear — do not remove, renumber, reword, or relocate them.
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_marker_survival.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/self_review.py src/cofounder_agent/modules/content/stages/quality_evaluation.py src/cofounder_agent/tests/unit/services/test_marker_survival.py
git commit -m "fix(images): preserve image markers through self-review + scoring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Contract tests guarding the rebuild-images consumer

**Files:**

- Test: `src/cofounder_agent/tests/unit/modules/content/test_image_atom_contracts.py` (create)

**Interfaces:**

- Consumes: the three atoms + `_try_image_gen`/`_try_pexels` signatures. Guards the Global-Constraints contract so a future edit can't silently break `ImageRebuildService`.

- [ ] **Step 1: Write the contract tests**

Create `tests/unit/modules/content/test_image_atom_contracts.py`:

```python
import inspect

import pytest
from services.site_config import SiteConfig


def test_try_image_gen_signature_stable():
    from modules.content.stages.replace_inline_images import _try_image_gen, _try_pexels

    p = inspect.signature(_try_image_gen).parameters
    assert list(p)[:3] == ["num", "search_query", "topic"]
    assert {"site_config", "task_id", "platform"} <= set(p)
    assert list(inspect.signature(_try_pexels).parameters)[:3] == ["search_query", "topic", "image_service"]


@pytest.mark.asyncio
async def test_plan_image_markers_contract_shape():
    from modules.content.atoms import content_plan_image_markers

    sc = SiteConfig(initial_config={"writer_max_inline_images": "3"})
    # Marker-free body → fallback path; must still return the contract keys
    # (ImageRebuildService calls with exactly these state keys).
    out = await content_plan_image_markers.run(
        {"content": "# H\n\nplain body, no markers", "topic": "t", "site_config": sc}
    )
    assert set(out) >= {"content", "image_plans"}
    assert isinstance(out["image_plans"], list)


def test_generate_images_result_shape_documented():
    """generate_images must keep per-item source ∈ {image_gen,pexels,none} — the
    rebuild fail-loud gate keys on `source == 'image_gen'`."""
    src = inspect.getsource(
        __import__("modules.content.atoms.content_generate_images", fromlist=["run"]).run
    )
    assert '"source"' in src or "'source'" in src
```

- [ ] **Step 2: Run to verify they pass against current atoms**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_image_atom_contracts.py -q`
Expected: PASS (this is a guard; it should be green on the post-Task-3/5/6 tree).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/modules/content/test_image_atom_contracts.py
git commit -m "test(images): pin atom I/O contract consumed by ImageRebuildService

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Docs + live-settings rollout

**Files:**

- Modify: `docs/architecture/anti-hallucination.md` (or the image doc) — note the writer-nominates-images flow.
- Modify: `CLAUDE.md` — update the image-pipeline narrative (writer places markers; decision agent is body-fed fallback; LRU rotation; gemma-4-31B).
- Rollout: live `app_settings` model-pin update on prod.

- [ ] **Step 1: Update docs**

In the image/anti-hallucination doc and the CLAUDE.md content-pipeline section, replace the "decision agent plans every image from headings" description with: the writer places `[IMAGE:]`/`[HERO-IMAGE:]` markers; `plan_image_markers` numbers them (writer-primary) and falls back to the body-fed decision agent; the featured image is grounded on the hero subject; styles rotate least-recently-used; the local prompt-builder is `gemma-4-31B-it-qat`.

- [ ] **Step 2: Commit docs**

```bash
git add docs CLAUDE.md
git commit -m "docs(images): writer-nominated, content-grounded image direction

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 3: Flip the live model pins on prod (post-merge, operator-confirmed)**

After the PR merges, apply the two model pins to the running DB (fresh installs already get them from `settings_defaults.py`; the boot seeder will NOT overwrite existing rows). Confirm with the operator first, then via the poindexter MCP:

```
set_setting("inline_image_prompt_model", "ollama/gemma-4-31B-it-qat:latest")
set_setting("model_role_image_decision", "ollama/gemma-4-31B-it-qat:latest")
```

Verify: `get_setting` returns the new values, and that `gemma-4-31B-it-qat:latest` is present in `ollama list` on the worker host.

- [ ] **Step 4: Verify end-to-end on one draft**

Generate one `canonical_blog` draft (or `poindexter tasks rebuild-images <task_id>` on an existing draft) and confirm: the hero and inline images depict article-specific subjects, and the chosen featured style differs from the previous few posts. Spot-check `qa.vision` (advisory) relevance scores in the QA Rails dashboard.

---

## Self-Review

**Spec coverage:**

- §1 Writer contract → Task 2. ✓
- §2 Marker normalization + hero extraction → Task 3. ✓
- §3 Body-fed decision agent → Task 4. ✓
- §4 Featured subject grounding → Task 5. ✓
- §5 LRU style rotation → Task 6. ✓
- §6 Model swap + skill wording → Task 1 (pins) + Tasks 4/5 (skill wording). ✓
- Integration risk (markers in early stages) → Task 7. ✓
- Rebuild-images contract preservation → Task 8. ✓
- Data/state new keys → Task 1. ✓
- Docs + rollout → Task 9. ✓
- Out-of-scope (vision hard-gate, featured-path unify, persistence) — correctly absent.

**Placeholder scan:** No TBD/TODO; every code step shows real code; Task 7 Step 1 is a locate-step whose subsequent edits are concrete.

**Type consistency:** `_resolve_featured_subject(context)`, `_select_style_lru(styles, last_used, mem_recent, *, rng)`, `_load_style_last_used(site_config)`, `extract_hero_subject`/`number_inline_markers`, `_extract_sections`/`_render_section_list`, `_strip_image_markers` — names/signatures consistent across the tasks that define and call them. `_build_image_gen_prompt`/`_try_image_gen_featured` first param renamed `topic`→`subject` consistently (Task 5); `image.featured_image` skill switched `{topic}`→`{subject}` to match the `subject=` kwarg.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-10-image-direction-grounding.md`.

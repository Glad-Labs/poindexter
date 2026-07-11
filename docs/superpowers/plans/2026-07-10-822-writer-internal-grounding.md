# Writer-side internal-grounding consumer (#822) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the `two_pass_writer` to inject the internal-grounding match (already threaded into task metadata by the #822 discovery half) into the draft prompt as a soft, fail-open "PRIOR WORK" framing anchor.

**Architecture:** An additive optional prompt section in `_draft_node`, fed by a match read in `generate_content.py` and threaded through `two_pass_writer.run()` — mirroring the existing `context_bundle`/`research_context`/`writer_prompt_override` optional-context precedents in the same writer. The anchor is eligibility-gated on the writer's existing `writer_rag_source_filter`, scrubbed fail-closed, and degrades to "no anchor" on every failure path.

**Tech Stack:** Python 3.12, LangGraph (the writer state machine), asyncpg/pgvector, pytest (`pytest.mark.asyncio`), DB-backed `app_settings` via `SiteConfig`.

**Spec:** `docs/superpowers/specs/2026-07-10-822-writer-internal-grounding-design.md`

## Global Constraints

- **Fail-open everywhere; scrub fail-closed.** No path may raise into the writer graph. The preview passes `scrub_rag_text` before entering the prompt; a scrub error drops the anchor.
- **Eligibility rides the writer's existing corpus knob.** Inject only when `grounding_ref["source_table"]` ∈ `_resolve_snippet_source_filter(site_config)` (default `("posts",)`).
- **Own kill switch:** `writer_internal_grounding_enabled` (default `true`), independent of the discovery-side `niche_external_grounding_enabled`.
- **Inline section wording (v1)** — the two variants live inline in `_draft_node`/its helper, like the sibling `GROUND TRUTH`/`SOURCES` sections. Not a `UnifiedPromptManager` key.
- **Anchor is appended AFTER the `SOURCES` section** so authoritative grounding precedes the soft nudge.
- **Required anchor copy clauses:** "use where genuinely relevant / in our own voice / ignore if the connection is thin / never parrot or copy verbatim / (posts) link the exact URL, never a fake one".
- **`embeddings.source_id` for a post is `str(posts.id)`** → URL lookup uses `WHERE id::text = $1` (type-safe for any `posts.id` column type).
- **Commits:** conventional-commit style, end every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Linear history; all work on branch `claude/822-writer-internal-grounding`.
- **No migration** — this feature adds one `app_settings` key (seeded in `settings_defaults.py`), no schema DDL.

---

### Task 1: Settings default — `writer_internal_grounding_enabled`

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (DEFAULTS dict + METADATA dict)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: the app_setting key `writer_internal_grounding_enabled` (string `'true'`, boolean type), read later by `_internal_grounding_enabled(site_config)` in Task 2.

- [ ] **Step 1: Write the failing test**

Add to `test_settings_defaults.py`:

```python
def test_writer_internal_grounding_default_present():
    from services.settings_defaults import DEFAULTS, METADATA
    assert DEFAULTS["writer_internal_grounding_enabled"] == "true"
    meta = METADATA["writer_internal_grounding_enabled"]
    assert meta["value_type"] == "boolean"
    assert meta["owner"] == "two_pass_writer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_writer_internal_grounding_default_present -v`
Expected: FAIL with `KeyError: 'writer_internal_grounding_enabled'`.

- [ ] **Step 3: Add the DEFAULTS entry**

In `settings_defaults.py`, in the `DEFAULTS` dict, next to the other `writer_*` writer knobs (search for `'writer_disable_thinking'` or `'writer_length_expansion_enabled'` and place adjacent):

```python
    # Writer-side prior-work anchor (#822 consumer half): when a grounded
    # external topic carries an internal match, _draft_node injects it as a
    # soft "PRIOR WORK" framing section. Independent of the discovery-side
    # niche_external_grounding_enabled. Eligibility rides writer_rag_source_filter.
    'writer_internal_grounding_enabled': 'true',
```

- [ ] **Step 4: Add the METADATA entry**

In the `METADATA` dict (search for an existing `'writer_'` metadata entry or add near the `topic_grounding`-owned block):

```python
    'writer_internal_grounding_enabled': {
        'owner': 'two_pass_writer', 'value_type': 'boolean',
    },
```

- [ ] **Step 5: Run test to verify it passes + the file's own guard tests still pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py -q`
Expected: PASS (including the qa-key-span guard — the new key is not a `qa_` key, so the span is unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "$(cat <<'EOF'
feat(settings): writer_internal_grounding_enabled default (#822)

Master switch for the writer-side prior-work anchor. Independent of the
discovery-side niche_external_grounding_enabled.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The section builder + helpers (pure logic)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` (add three module-level functions near `_resolve_writer_think`, ~line 1053)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py`

**Interfaces:**

- Consumes: `_resolve_snippet_source_filter(site_config)` (existing, returns `list[str]` allowlist, default `["posts"]`); `services.rag_scrub.scrub_rag_text(str) -> str` (existing, raises on failure); `writer_internal_grounding_enabled` (Task 1).
- Produces:
  - `_internal_grounding_enabled(site_config) -> bool`
  - `async _resolve_post_url(pool, post_id: str, site_config) -> str | None`
  - `async _build_internal_grounding_section(ig: dict, *, site_config, pool) -> tuple[str, str | None]` — returns `(section_text, source_table)` when injected, else `("", None)`. Consumed by `_draft_node` in Task 3.

- [ ] **Step 1: Write the failing tests**

Add to `test_two_pass_writer.py` (top-level, after the existing helpers):

```python
def _grounding_site_config(*, enabled="true", source_filter="", site_url=""):
    """SiteConfig stub for the prior-work anchor tests."""
    sc = MagicMock()
    values = {
        "writer_internal_grounding_enabled": enabled,
        "writer_rag_source_filter": source_filter,
        "rag_source_filter": "",
        "site_url": site_url,
    }
    sc.get = MagicMock(side_effect=lambda key, default="": values.get(key, default))
    return sc


def _grounding_pool(*, post_row=None):
    """Pool whose conn.fetchrow returns post_row (for the posts URL lookup)."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=post_row)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


async def test_grounding_section_post_eligible_has_preview_and_url(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "posts", "source_id": "42",
          "preview": "How we cut VRAM spill on a single GPU.", "similarity": 0.71}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(site_url="https://www.gladlabs.io"),
        pool=_grounding_pool(post_row={"slug": "vram-spill"}),
    )
    assert src == "posts"
    assert "PRIOR WORK" in section
    assert "How we cut VRAM spill on a single GPU." in section
    assert "https://www.gladlabs.io/posts/vram-spill" in section


async def test_grounding_section_ineligible_source_returns_empty(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "memory", "source_id": "9", "preview": "ops note", "similarity": 0.8}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(),  # default filter = posts only
        pool=_grounding_pool(),
    )
    assert section == ""
    assert src is None


async def test_grounding_section_nonpost_eligible_is_framing_only(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "claude_sessions", "source_id": "s1",
          "preview": "we debugged the checkpoint poisoning", "similarity": 0.66}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(source_filter="posts,claude_sessions"),
        pool=_grounding_pool(),
    )
    assert src == "claude_sessions"
    assert "PRIOR WORK" in section
    assert "we debugged the checkpoint poisoning" in section
    assert "/posts/" not in section  # no link for a non-post source


async def test_grounding_section_scrub_failure_returns_empty(monkeypatch):
    def _boom(_t):
        raise RuntimeError("scrub down")
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", _boom)
    ig = {"source_table": "posts", "source_id": "42", "preview": "x", "similarity": 0.9}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(),
        pool=_grounding_pool(post_row={"slug": "s"}),
    )
    assert section == ""
    assert src is None


async def test_grounding_section_post_url_relative_when_no_site_url(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "posts", "source_id": "7", "preview": "prev", "similarity": 0.7}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(site_url=""),
        pool=_grounding_pool(post_row={"slug": "rel-slug"}),
    )
    assert "/posts/rel-slug" in section
    assert "http" not in section


async def test_grounding_section_empty_preview_returns_empty(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "posts", "source_id": "42", "preview": "   ", "similarity": 0.9}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(), pool=_grounding_pool(post_row={"slug": "s"}),
    )
    assert section == ""
    assert src is None


def test_internal_grounding_enabled_flag(monkeypatch):
    assert two_pass._internal_grounding_enabled(_grounding_site_config(enabled="false")) is False
    assert two_pass._internal_grounding_enabled(_grounding_site_config(enabled="true")) is True
    assert two_pass._internal_grounding_enabled(None) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer.py -k grounding -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_build_internal_grounding_section'`.

- [ ] **Step 3: Implement the helpers**

In `two_pass_writer.py`, add after `_resolve_writer_think` (~line 1053):

```python
# ---------------------------------------------------------------------------
# Prior-work anchor (#822 writer-consumer half).
#
# The discovery-side ranker (topic_batch_service, #2266) threads the single
# most-related internal item for a grounded external topic into the task
# metadata as {source_table, source_id, preview, similarity}. _draft_node turns
# it into an OPTIONAL soft "PRIOR WORK" section — a framing anchor, never a
# forced source. Fail-open at every step (→ no section); scrub FAIL-CLOSED (the
# grounding corpus includes non-publishable memory / claude_sessions rows).


def _internal_grounding_enabled(site_config: Any = None) -> bool:
    """Master switch for the writer-side prior-work anchor
    (``writer_internal_grounding_enabled``, default true). Off → no anchor.
    Mirrors ``_expansion_enabled``'s fail-safe default-on posture."""
    if site_config is None:
        return True
    try:
        raw = site_config.get("writer_internal_grounding_enabled", "true")
        return str(raw).lower() in ("true", "1", "yes")
    except Exception:  # noqa: BLE001 — defensive against test stubs
        return True


async def _resolve_post_url(pool: Any, post_id: str, site_config: Any) -> str | None:
    """Resolve a published post's public URL from its id.

    Returns ``{site_url}/posts/{slug}`` (the form ai_content_generator uses to
    present internal links to this same writer), falling back to the relative
    ``/posts/{slug}`` when ``site_url`` is unset. None on any failure (blank id,
    no pool, no row, query error) → the caller uses the framing-only variant.
    ``embeddings.source_id`` for a post is ``str(posts.id)``, so the lookup
    casts ``id::text`` to stay type-safe for any posts.id column type. Never
    raises."""
    if not post_id or pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT slug FROM posts WHERE id::text = $1", post_id,
            )
    except Exception as exc:  # noqa: BLE001 — URL is best-effort, never fatal
        logger.warning("[two_pass] post-url lookup failed (%s) — framing-only", exc)
        return None
    slug = (row.get("slug") if row else None) or ""
    if not slug:
        return None
    try:
        site_url = (site_config.get("site_url", "") if site_config else "") or ""
    except Exception:  # noqa: BLE001
        site_url = ""
    site_url = site_url.rstrip("/")
    return f"{site_url}/posts/{slug}" if site_url else f"/posts/{slug}"


async def _build_internal_grounding_section(
    ig: dict[str, Any], *, site_config: Any, pool: Any,
) -> tuple[str, str | None]:
    """Compose the optional "PRIOR WORK" prompt section from a grounding match.

    Returns ``(section_text, source_table)`` when an anchor is injected, else
    ``("", None)``. Additive and non-raising: every failing check returns
    ``("", None)``.

    Order: eligibility (source_table ∈ writer_rag_source_filter) → scrub
    FAIL-CLOSED → post-URL resolution (posts only) → compose linkable /
    non-linkable variant.
    """
    source_table = str(ig.get("source_table") or "")
    if not source_table:
        return "", None
    # Eligibility rides the writer's existing corpus allowlist (default
    # ('posts',)) so ops content stays out of the public writer prompt unless
    # the operator broadens writer_rag_source_filter to include it.
    if source_table not in _resolve_snippet_source_filter(site_config):
        return "", None
    # Scrub FAIL-CLOSED — never inject unscrubbed operator text.
    from services.rag_scrub import scrub_rag_text
    try:
        preview = scrub_rag_text(str(ig.get("preview") or "")).strip()
    except Exception as exc:  # noqa: BLE001 — leak-safe: drop the anchor
        logger.warning(
            "[two_pass] internal_grounding preview scrub failed (%s) — no anchor", exc,
        )
        return "", None
    if not preview:
        return "", None
    url = None
    if source_table == "posts":
        url = await _resolve_post_url(pool, str(ig.get("source_id") or ""), site_config)
    if url:
        section = (
            "PRIOR WORK (something we've already published that this topic "
            "connects to — if there's a genuine throughline, open on it or weave "
            "the connection into your take in our own voice, and link it inline "
            "using the exact URL below. If the connection is thin, ignore this "
            "entirely: do NOT force it, and never copy the text below verbatim):"
            f"\n\n\"{preview}\"\n{url}"
        )
    else:
        section = (
            "PRIOR WORK (an internal note or past experience of ours that this "
            "topic connects to — if there's a genuine throughline, draw on it to "
            "ground your take in first-hand experience, in our own voice. Don't "
            "quote it, don't force the connection, and don't name internal tools "
            "or systems. If the connection is thin, ignore this entirely):"
            f"\n\n\"{preview}\""
        )
    return section, source_table
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer.py -k grounding -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/two_pass_writer.py src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py
git commit -m "$(cat <<'EOF'
feat(writer): prior-work anchor section builder (#822)

_build_internal_grounding_section composes the optional soft "PRIOR WORK"
prompt section from a grounding match: eligibility gated on
writer_rag_source_filter, scrubbed fail-closed, posts get an inline URL.
Pure helper; wired into _draft_node in the next commit.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire into `_State` + `_draft_node` + `run()` (+ observability)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` (`_State` ~line 222; `_draft_node` ~line 599-612; `run()` initial state ~line 1762 and return dict ~line 1827)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py`

**Interfaces:**

- Consumes: `_internal_grounding_enabled`, `_build_internal_grounding_section` (Task 2).
- Produces: `two_pass_writer.run(..., internal_grounding=<dict|None>)` accepted kwarg; `run()` return dict gains `internal_grounding_anchor_injected: bool` and `internal_grounding_source_table: str | None`. Consumed by `generate_content.py` in Task 4.

- [ ] **Step 1: Write the failing integration tests**

Add to `test_two_pass_writer.py`:

```python
async def test_internal_grounding_post_injected_into_draft_prompt(monkeypatch):
    """A grounded post match reaches the writer's draft prompt as a PRIOR WORK
    section, after SOURCES, with the resolved /posts/<slug> link."""
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    monkeypatch.setattr("services.topic_ranking.embed_text",
                        lambda text, *, site_config=None: [0.0] * 768)
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(post_row={"slug": "vram-spill"}),
        site_config=_grounding_site_config(site_url="https://www.gladlabs.io"),
        research_context="Source A: something (https://example.com).",
        internal_grounding={"source_table": "posts", "source_id": "42",
                            "preview": "How we cut VRAM spill.", "similarity": 0.7},
    )
    instr = captured["instructions"]
    assert "PRIOR WORK" in instr
    assert "How we cut VRAM spill." in instr
    assert "https://www.gladlabs.io/posts/vram-spill" in instr
    # Appended AFTER the SOURCES section.
    assert instr.index("SOURCES") < instr.index("PRIOR WORK")
    assert result["internal_grounding_anchor_injected"] is True
    assert result["internal_grounding_source_table"] == "posts"


async def test_internal_grounding_absent_leaves_prompt_unchanged(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    monkeypatch.setattr("services.topic_ranking.embed_text",
                        lambda text, *, site_config=None: [0.0] * 768)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(), site_config=_grounding_site_config(),
    )
    assert "PRIOR WORK" not in captured["instructions"]
    assert result["internal_grounding_anchor_injected"] is False


async def test_internal_grounding_disabled_flag_no_section(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "draft"
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    monkeypatch.setattr("services.topic_ranking.embed_text",
                        lambda text, *, site_config=None: [0.0] * 768)
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(post_row={"slug": "s"}),
        site_config=_grounding_site_config(enabled="false", site_url="https://x.io"),
        internal_grounding={"source_table": "posts", "source_id": "1",
                            "preview": "p", "similarity": 0.9},
    )
    assert "PRIOR WORK" not in captured["instructions"]
    assert result["internal_grounding_anchor_injected"] is False


async def test_internal_grounding_ineligible_source_no_section(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "draft"
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    monkeypatch.setattr("services.topic_ranking.embed_text",
                        lambda text, *, site_config=None: [0.0] * 768)
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(), site_config=_grounding_site_config(),  # posts-only
        internal_grounding={"source_table": "memory", "source_id": "9",
                            "preview": "ops", "similarity": 0.9},
    )
    assert "PRIOR WORK" not in captured["instructions"]
    assert result["internal_grounding_anchor_injected"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer.py -k "internal_grounding and (injected or absent or disabled or ineligible)" -v`
Expected: FAIL (`run()` doesn't accept `internal_grounding` / result lacks the new keys / `PRIOR WORK` never injected).

- [ ] **Step 3: Add the `_State` fields**

In `two_pass_writer.py`, inside `class _State` (after the `target_length: int` field ~line 276), add:

```python
    # Prior-work anchor (#822) — the internal match threaded from
    # generate_content.py via run(). {source_table, source_id, preview,
    # similarity} or empty. Plain dict → msgpack-clean for the checkpointer.
    internal_grounding: dict[str, Any]
    # Observability set by _draft_node: did an anchor actually reach the prompt,
    # and from which corpus type. Surfaced up through run() to the caller stage.
    internal_grounding_injected: bool
    internal_grounding_source_table: str | None
```

- [ ] **Step 4: Inject the section in `_draft_node`**

In `_draft_node`, the `site_config` and `pool` are fetched at ~lines 599-600 (right before the `generate_with_context` call). Insert this block immediately AFTER `pool = _POOL_REGISTRY.get(state["pool_thread"])` and BEFORE `draft = await generate_with_context(`:

```python
    # Prior-work anchor (#822) — optional soft framing section appended AFTER
    # the SOURCES block. Fail-open: any issue → no section, prompt unchanged.
    ig = state.get("internal_grounding") or {}
    ig_injected = False
    ig_source_table: str | None = None
    if _internal_grounding_enabled(site_config) and ig:
        section, ig_source_table = await _build_internal_grounding_section(
            ig, site_config=site_config, pool=pool,
        )
        if section:
            instruction = f"{instruction}\n\n---\n\n{section}"
            ig_injected = True
```

Then change the node's return (currently `return {**state, "draft": draft}`) to:

```python
    return {
        **state,
        "draft": draft,
        "internal_grounding_injected": ig_injected,
        "internal_grounding_source_table": ig_source_table,
    }
```

- [ ] **Step 5: Seed the kwarg in `run()` initial state + return the observability fields**

In `run()`, add to the `initial: _State` dict (after `"target_length": ...` ~line 1770):

```python
            "internal_grounding": (
                kw.get("internal_grounding")
                if isinstance(kw.get("internal_grounding"), dict) else {}
            ),
```

And in the `run()` return dict (after `"prompt_template_version": ...` ~line 1861), add:

```python
            # Prior-work anchor observability (#822) — did an anchor reach the
            # draft prompt this run, and from which corpus type (None = no anchor).
            "internal_grounding_anchor_injected": bool(
                final.get("internal_grounding_injected", False)
            ),
            "internal_grounding_source_table": final.get(
                "internal_grounding_source_table"
            ),
```

Also document the new kwarg in the `run()` docstring's "Kwargs that flow into graph state" list:

```python
    - ``internal_grounding`` — the {source_table, source_id, preview,
      similarity} match threaded from generate_content.py (#822). When set and
      eligible, _draft_node appends a soft "PRIOR WORK" anchor section.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer.py -q`
Expected: PASS (all existing writer tests + the new grounding + integration tests).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/two_pass_writer.py src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py
git commit -m "$(cat <<'EOF'
feat(writer): inject prior-work anchor into the draft prompt (#822)

_draft_node appends the grounding match as a soft PRIOR WORK section after
SOURCES, gated on writer_internal_grounding_enabled + eligibility. run()
accepts internal_grounding= and surfaces anchor_injected/source_table for
observability. Fail-open: no match / disabled / ineligible / scrub-fail →
byte-identical prompt.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Read + thread the match in `generate_content.py`

**Files:**

- Modify: `src/cofounder_agent/modules/content/stages/generate_content.py` (new `_read_internal_grounding` after `_read_context_bundle` ~line 698; call + pass in `_generate_via_two_pass_atom` ~lines 853 and 885; metrics dict ~line 932)
- Test: `src/cofounder_agent/tests/unit/services/stages/test_generate_content.py`

**Interfaces:**

- Consumes: `two_pass_writer.run(internal_grounding=...)` and its `internal_grounding_anchor_injected`/`internal_grounding_source_table` return keys (Task 3). The match lives at `pipeline_versions.stage_data.metadata.internal_grounding` (written by the #822 discovery half).
- Produces: `GenerateContentStage._read_internal_grounding(database_service, task_id) -> dict | None`.

- [ ] **Step 1: Write the failing test**

Add to `test_generate_content.py`:

```python
class _GroundingReadPoolConn:
    def __init__(self, stage_data):
        self._stage_data = stage_data
    async def fetchrow(self, *_args):
        if self._stage_data is _RAISE:
            raise RuntimeError("db down")
        return {"stage_data": self._stage_data} if self._stage_data is not None else None

class _GroundingReadPool:
    def __init__(self, stage_data):
        self._stage_data = stage_data
    def acquire(self):
        sd = self._stage_data
        class _Ctx:
            async def __aenter__(self_):
                return _GroundingReadPoolConn(sd)
            async def __aexit__(self_, *_exc):
                return None
        return _Ctx()

class _GroundingReadDb:
    def __init__(self, stage_data):
        self.pool = _GroundingReadPool(stage_data)

_RAISE = object()


async def test_read_internal_grounding_returns_match():
    match = {"source_table": "posts", "source_id": "42", "preview": "p", "similarity": 0.7}
    db = _GroundingReadDb({"metadata": {"internal_grounding": match}})
    got = await GenerateContentStage()._read_internal_grounding(db, "task-1")
    assert got == match


async def test_read_internal_grounding_none_when_absent():
    db = _GroundingReadDb({"metadata": {"angle": "x"}})  # no internal_grounding key
    assert await GenerateContentStage()._read_internal_grounding(db, "task-1") is None


async def test_read_internal_grounding_none_on_error():
    db = _GroundingReadDb(_RAISE)
    assert await GenerateContentStage()._read_internal_grounding(db, "task-1") is None
```

(If `GenerateContentStage` and `pytest.mark.asyncio` are not already imported/marked at module scope in this file, add `pytestmark = pytest.mark.asyncio` near the top or mark each test — check the file header first; the existing async stage tests confirm the marker is already configured.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_generate_content.py -k read_internal_grounding -v`
Expected: FAIL with `AttributeError: 'GenerateContentStage' object has no attribute '_read_internal_grounding'`.

- [ ] **Step 3: Implement `_read_internal_grounding`**

In `generate_content.py`, add immediately after `_read_context_bundle` (ends ~line 698):

```python
    async def _read_internal_grounding(
        self, database_service: Any, task_id: str,
    ) -> dict[str, Any] | None:
        """Pull ``stage_data.metadata.internal_grounding`` from the version-1 row.

        Set by ``topic_batch_service._handoff_to_pipeline`` on grounded external
        winners (#822 discovery half) — the single most-related internal item
        for the topic, shape ``{source_table, source_id, preview, similarity}``.
        None for internal / ungrounded winners, manual tasks, and dev_diary.

        Reads the raw version-1 ``pipeline_versions.stage_data`` directly (not
        via ``get_task()``, whose ModelConverter can reshape ``metadata``),
        mirroring ``_read_context_bundle``. ``metadata`` is one of the keys the
        content_tasks_update_redirect trigger JSONB-merges, so it survives the
        writer's later upsert. Fail-open: any error → None (no anchor).
        """
        try:
            pool = getattr(database_service, "pool", None)
            if pool is None:
                return None
            import json as _json
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT stage_data FROM pipeline_versions "
                    "WHERE task_id = $1 ORDER BY version ASC LIMIT 1",
                    str(task_id),
                )
            if not row:
                return None
            sd = row["stage_data"]
            if isinstance(sd, str):
                try:
                    sd = _json.loads(sd)
                except Exception:
                    return None
            if not isinstance(sd, dict):
                return None
            meta = sd.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = _json.loads(meta)
                except Exception:
                    return None
            ig = meta.get("internal_grounding") if isinstance(meta, dict) else None
            if isinstance(ig, str):
                try:
                    ig = _json.loads(ig)
                except Exception:
                    return None
            return ig if isinstance(ig, dict) else None
        except Exception as e:
            logger.warning(
                "Failed to read internal_grounding for task %s: %s — "
                "falling back to no anchor",
                task_id, e,
            )
            return None
```

- [ ] **Step 4: Thread it into `_generate_via_two_pass_atom`**

After the `context_bundle = await self._read_context_bundle(...)` call (~line 853), add:

```python
        # Prior-work anchor (#822) — the internal match the discovery ranker
        # threaded onto this task. None for internal/ungrounded/manual tasks.
        # Threaded to the writer so _draft_node can open on it (soft, fail-open).
        internal_grounding = await self._read_internal_grounding(
            database_service, task_id,
        )
```

In the `two_pass_writer.run(...)` call (~line 885), add the kwarg alongside `context_bundle=context_bundle`:

```python
                internal_grounding=internal_grounding,
```

- [ ] **Step 5: Record the observability fields on `metrics`**

After the two-pass extras loop (`for k in ("external_lookups", "revision_loops", "loop_capped"): ...` ~line 932), add:

```python
        # Prior-work anchor observability (#822) — surface whether the writer
        # actually opened on a grounded internal thread, and from which source
        # type, so the #822 pair is measurable end to end.
        if result.get("internal_grounding_anchor_injected") is not None:
            metrics["internal_grounding_anchor_injected"] = result.get(
                "internal_grounding_anchor_injected"
            )
            metrics["internal_grounding_source_table"] = result.get(
                "internal_grounding_source_table"
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/stages/test_generate_content.py -q`
Expected: PASS (the three new read tests + all existing stage tests, which exercise the two_pass path and must still pass with the added kwarg).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/modules/content/stages/generate_content.py src/cofounder_agent/tests/unit/services/stages/test_generate_content.py
git commit -m "$(cat <<'EOF'
feat(content): read + thread internal_grounding to the writer (#822)

GenerateContentStage reads stage_data.metadata.internal_grounding (mirrors
_read_context_bundle) and threads it into two_pass_writer.run(), and records
anchor_injected/source_table on the writer metrics. Closes the #822 pair:
the discovery half's threaded match now reaches the draft prompt.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Docs + full verification

**Files:**

- Modify: `docs/architecture/niches-and-rag-modes.md` (extend the existing "External-candidate internal grounding (#822)" subsection)

- [ ] **Step 1: Extend the #822 docs subsection**

Open `docs/architecture/niches-and-rag-modes.md`, find the `External-candidate internal grounding (#822)` subsection (added by the discovery-half PR), and append a paragraph describing the writer-consumer half:

```markdown
**Writer consumer (#822 half 2).** When a grounded external topic reaches the
`canonical_blog` writer, `GenerateContentStage._read_internal_grounding` reads
the threaded match from `stage_data.metadata.internal_grounding` and passes it
into `two_pass_writer.run(internal_grounding=…)`. `_draft_node` renders it as a
soft **PRIOR WORK** section appended after the `SOURCES` block — a framing
anchor the writer is told to use only where there's a genuine throughline, in
our own voice, and never to parrot. Eligibility rides the writer's existing
`writer_rag_source_filter` (default `posts`-only, so ops content stays out of
the public prompt unless opted in); a `posts` match gets an inline
`/posts/<slug>` link. Governed by `writer_internal_grounding_enabled` (default
`true`); fail-open (no match / disabled / ineligible / scrub failure →
byte-identical prompt) and scrub fail-closed on the preview.
```

- [ ] **Step 2: Commit the docs**

```bash
git add docs/architecture/niches-and-rag-modes.md
git commit -m "$(cat <<'EOF'
docs(rag): document the #822 writer-side prior-work anchor

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Run the full affected test surface**

Run:

```bash
cd src/cofounder_agent && poetry run pytest \
  tests/unit/services/atoms/test_two_pass_writer.py \
  tests/unit/services/stages/test_generate_content.py \
  tests/unit/services/test_settings_defaults.py -q
```

Expected: all PASS, 0 failures.

- [ ] **Step 4: Lint the touched Python**

Run: `cd src/cofounder_agent && poetry run ruff check modules/content/atoms/two_pass_writer.py modules/content/stages/generate_content.py services/settings_defaults.py`
Expected: no new violations (fix any that the change introduced).

- [ ] **Step 5: Finish the branch**

Invoke **superpowers:finishing-a-development-branch** — verify tests, then push `claude/822-writer-internal-grounding` and open a PR titled `feat: writer opens on the internal thread an external topic connects to (#822)` with a body summarizing the four commits + linking the spec. Report the PR URL.

## Self-Review

**1. Spec coverage:**

- Read helper (spec §1) → Task 4. ✓
- Thread into run()/\_State (spec §2) → Task 3. ✓
- `_draft_node` section builder, scrub, eligibility, URL, two variants (spec §3) → Task 2 (builder) + Task 3 (wire). ✓
- Setting `writer_internal_grounding_enabled` (spec §4) → Task 1. ✓
- Observability fields (spec §5) → Task 3 (return) + Task 4 (metrics). ✓
- Fail-open matrix (spec error-handling) → covered by Task 2 tests (ineligible/scrub-fail/empty-preview/URL-fail) + Task 3 tests (absent/disabled). ✓
- Testing list (spec §Testing) → Tasks 1-4 test steps. ✓
- Docs → Task 5. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows the assertion.

**3. Type consistency:** `_build_internal_grounding_section` returns `tuple[str, str | None]` in Task 2 and is consumed that way in Task 3's `_draft_node` (`section, ig_source_table = await ...`). `run()` return keys `internal_grounding_anchor_injected` / `internal_grounding_source_table` match between Task 3 (producer) and Task 4 (`metrics` consumer). The `internal_grounding` kwarg name is consistent across `run()` (Task 3) and the `_generate_via_two_pass_atom` call (Task 4). ✓

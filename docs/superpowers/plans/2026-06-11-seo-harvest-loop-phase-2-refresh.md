# SEO Harvest Loop — Phase 2 (`seo_refresh`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the harvest loop — take a high-`gap_score` row from `seo_opportunities` and actually re-optimize an existing published post's title/meta (no body change), gated on operator approval, then re-publish + propagate.

**Architecture:** A dedicated `seo_refresh` graph_def (stored in `pipeline_templates`, compiled by `pipeline_architect.build_graph_from_spec`, run by `TemplateRunner`) — NOT a `task_type` branch on `canonical_blog`. It composes 3 new atoms (`content.load_existing_post`, `seo.optimize_metadata`, `content.republish_post`) plus reused atoms (`content.check_title_originality`, `qa.programmatic`, `qa.aggregate`, `atoms.approval_gate`). The refresh task is an ordinary `pipeline_tasks` row carrying the target `post_id` in metadata; the entry atom hydrates `PipelineState` from the `posts` row instead of generating a draft.

**Tech Stack:** Python 3.12 / asyncpg / LangGraph (graph_def path) / pytest. All settings DB-backed (`app_settings`). Tracked as Glad-Labs/poindexter#763 (epic #762).

**Tracking issue:** https://github.com/Glad-Labs/poindexter/issues/763

---

## Decisions locked before implementation (veto-able)

1. **#763 before #764**, optimizer is **query-optional** — uses `target_query` when present, falls back to the post's topic/primary keyword when empty (current Phase-1 reality). No rework when #764 lands.
2. **Default scope `meta_only`** — update `seo_title` + `seo_description` (+ `seo_keywords`) only. **Never touch `posts.content`.** Deeper scopes (`meta_and_intro`, `full`) are future, opt-in, and ADD atoms — they never branch existing ones.
3. **Approval-first** — `pipeline_gate_seo_refresh_gate` seeded **`true`** (unlike `draft_gate`, which is off). Re-publishing a live post pauses for sign-off. Auto-publish graduation (Lock 2) is deferred to a later task behind `seo.refresh.auto_publish_after_clean_runs`.
4. **Two milestones.** Milestone A (Tasks 0–8) ships the hand-triggerable refresh machinery; Milestone B (Tasks 9–11) ships auto-enqueue + outcome measurement + Grafana. Each is a PR.
5. **`post_id` reuses the existing `PipelineState.post_id` channel**, carried as the `posts.id` UUID **string** (the TypedDict's `int` annotation is not runtime-enforced — LangGraph checks key presence, not value type).

## Already in place (do NOT rebuild)

- `seo_opportunities` table **already has** `baseline_position` / `baseline_ctr` / `outcome_position` / `outcome_ctr` / `outcome_measured_at` (Phase-1 migration reserved them). **No schema change for outcome tracking.**
- Settings **already seeded** in `settings_defaults.py`: `seo.refresh.enabled='false'`, `seo.refresh.outcome_measure_after_days='14'`, `seo.query_ingestion.enabled='false'`, plus all analyzer thresholds.
- `atoms.approval_gate` is reused verbatim (config-seeded `gate_name`). `_seo_common.py` provides the LLM-call-with-retry + programmatic fallbacks the new optimizer reuses.
- Republish propagation helpers exist: `static_export_service.export_post(pool, slug, *, site_config)` and `revalidation_service.trigger_isr_revalidate(slug, *, site_config)`.

## Hard constraints (the compiler enforces these)

- `pipeline_architect._validate_graph_schema` raises `ValueError` at seed/compile time if any atom `requires`/`produces` key is **not declared in `PipelineState.__annotations__`** (the #753 guard). New channels MUST be added to `PipelineState` (Task 1).
- `pipeline_architect._validate_spec` reachability treats every `PipelineState` key as seedable from initial state — that is exactly why `content.load_existing_post` can `requires=("post_id",)` and pass.
- A NULL `template_slug` fails loud (`feedback_no_silent_defaults`). The enqueue path sets `template_slug='seo_refresh'` explicitly.
- Migrations run in the light migrations-smoke env: a migration may import only light modules. The spec module (Task 6) is pure-data (typing only), so the seed migration can import it — mirror `services/canonical_blog_spec.py`.

---

# Milestone A — the refresh machinery (hand-triggerable, default-off)

### Task 0: De-risk the entry seam FIRST (spike)

> Design §9 #1: graph-entry-from-an-existing-post is the one novel seam. Prove it end-to-end with a 2-node graph before building the real atoms. This task is a throwaway spike — its asserts move into Tasks 2/5/6; delete the temp graph at the end.

**Files:**

- Test: `src/cofounder_agent/tests/unit/seo/test_seo_refresh_entry_seam_spike.py`

- [ ] **Step 1: Write the failing seam test** — a 2-node spec (`load → END`) with a stub atom that just echoes `post_id`, compiled + invoked through the real `build_graph_from_spec`, asserting `post_id` flows from `initial_state` into the node.

```python
import pytest
from services.pipeline_architect import build_graph_from_spec, _validate_spec

def test_minimal_post_id_entry_spec_validates():
    spec = {
        "name": "seo_refresh_spike",
        "entry": "load",
        "nodes": [{"id": "load", "atom": "content.check_title_originality"}],
        "edges": [{"from": "load", "to": "END"}],
    }
    # check_title_originality requires keys already in PipelineState, so a
    # one-node spec must pass reachability — proving PipelineState keys are
    # treated as seedable initial state (the basis for post_id entry).
    ok, errors = _validate_spec(spec)
    assert ok, errors
```

- [ ] **Step 2: Run it** — `cd src/cofounder_agent && poetry run pytest tests/unit/seo/test_seo_refresh_entry_seam_spike.py -v`. Expected: PASS (the registry must be discovered — if it errors with "atom not in catalog", call `atom_registry.discover()` in a fixture first).

- [ ] **Step 3: Manually confirm the dispatcher seam (no code, document finding)** — read `services/tasks_db.py::add_task` end-to-end and confirm WHERE per-task `metadata` is persisted (the docstring says `metadata`/`result`/`task_metadata` land in `pipeline_versions.stage_data` JSONB at version=1). Record in the test file's module docstring: the exact column + read path `process_content_generation_task` must use in Task 5 to surface `post_id`. This is the seam Task 5 implements.

- [ ] **Step 4: Commit the spike** — `git add tests/unit/seo/test_seo_refresh_entry_seam_spike.py && git commit -F-` with body documenting the confirmed seam.

---

### Task 1: Declare new `PipelineState` channels

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` (the `PipelineState` TypedDict, after the existing `post_slug`/`task_metadata` block ~line 543)
- Test: `src/cofounder_agent/tests/unit/seo/test_pipeline_state_seo_refresh_keys.py`

- [ ] **Step 1: Write the failing test**

```python
from services.template_runner import PipelineState

def test_seo_refresh_channels_declared():
    keys = set(PipelineState.__annotations__)
    for k in ("target_query", "seo_opportunity_id", "seo_refresh_scope"):
        assert k in keys, f"{k} must be declared so build_graph_from_spec accepts it"
```

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/test_pipeline_state_seo_refresh_keys.py -v`. Expected: FAIL (KeyError-style assert).

- [ ] **Step 3: Add the channels** — insert into `PipelineState` (these are last-value channels; no reducer needed):

```python
    # SEO Harvest Loop Phase 2 (#763): the seo_refresh graph hydrates these
    # from the source posts row + its seo_opportunities row at entry. Declared
    # so build_graph_from_spec's #753 schema gate accepts the new atoms'
    # requires/produces (undeclared keys raise ValueError at seed time).
    target_query: str            # the GSC query the refresh optimizes toward ('' = page-level only)
    seo_opportunity_id: str      # seo_opportunities.id (uuid) — stamped on republish for outcome tracking
    seo_refresh_scope: str       # 'meta_only' | 'meta_and_intro' | 'full' (default meta_only)
```

- [ ] **Step 4: Run** — same command. Expected: PASS.

- [ ] **Step 5: Commit** — `git add services/template_runner.py tests/unit/seo/test_pipeline_state_seo_refresh_keys.py && git commit -F-` "feat(seo): declare seo_refresh PipelineState channels (#763)".

---

### Task 2: `content.load_existing_post` atom (the novel entry seam)

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/content_load_existing_post.py`
- Test: `src/cofounder_agent/tests/unit/seo/test_content_load_existing_post.py`

- [ ] **Step 1: Write the failing test** — hydrate from a fake pool returning one posts row + one opportunity row.

```python
import pytest
from modules.content.atoms import content_load_existing_post as atom

class _Conn:
    def __init__(self, post, opp):
        self._post, self._opp = post, opp
    async def fetchrow(self, sql, *args):
        return self._post if "FROM posts" in sql else self._opp
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class _Pool:
    def __init__(self, post, opp): self._c = _Conn(post, opp)
    def acquire(self): return self._c

class _DB:
    def __init__(self, pool): self.pool = pool

@pytest.mark.asyncio
async def test_hydrates_content_title_and_target_query():
    post = {"id": "11111111-1111-1111-1111-111111111111", "title": "Old Title",
            "slug": "old-title", "content": "# Body\n\nUnchanged.",
            "seo_title": "Old SEO", "seo_description": "old desc",
            "seo_keywords": "a, b", "tag_ids": []}
    opp = {"id": "22222222-2222-2222-2222-222222222222",
           "target_query": "best gpu 2026", "current_position": 7.2, "ctr": 0.004}
    state = {"post_id": post["id"], "database_service": _DB(_Pool(post, opp))}
    out = await atom.run(state)
    assert out["content"] == "# Body\n\nUnchanged."     # body carried, never regenerated
    assert out["title"] == "Old Title"
    assert out["post_slug"] == "old-title"
    assert out["target_query"] == "best gpu 2026"
    assert out["seo_opportunity_id"] == opp["id"]
    assert out["topic"] == "Old Title"                  # topic seeds the optimizer fallback

def test_atom_meta_keys_subset_of_pipeline_state():
    from services.template_runner import PipelineState
    keys = set(PipelineState.__annotations__)
    assert set(atom.ATOM_META.produces) <= keys
    assert set(atom.ATOM_META.requires) <= keys
```

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/test_content_load_existing_post.py -v`. Expected: FAIL (module missing).

- [ ] **Step 3: Implement the atom**

```python
"""content.load_existing_post — hydrate PipelineState from an existing posts row.

The seo_refresh graph's entry atom. Unlike canonical_blog's generate_draft,
this atom does NOT call an LLM — it reads the already-published post (body
carried unchanged) plus its seo_opportunities row (target query + baseline
metrics) so the downstream seo.optimize_metadata atom can re-optimize the
title/meta toward the query. Read-only; mutates nothing.

Issue: Glad-Labs/poindexter#763 (epic #762).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.load_existing_post",
    type="atom",
    version="1.0.0",
    description=(
        "Hydrate pipeline state from an existing posts row (+ its "
        "seo_opportunities row) for the seo_refresh graph. No LLM, no writes."
    ),
    inputs=(
        FieldSpec(name="post_id", type="str", description="posts.id (uuid) to refresh"),
        FieldSpec(name="database_service", type="object", description="DB service"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="existing body (carried unchanged)"),
        FieldSpec(name="title", type="str", description="current post title"),
        FieldSpec(name="topic", type="str", description="optimizer fallback keyword source"),
        FieldSpec(name="post_slug", type="str", description="post URL slug"),
        FieldSpec(name="seo_title", type="str", description="current seo_title"),
        FieldSpec(name="seo_description", type="str", description="current seo_description"),
        FieldSpec(name="seo_keywords", type="str", description="current seo_keywords csv"),
        FieldSpec(name="target_query", type="str", description="GSC query to optimize toward"),
        FieldSpec(name="seo_opportunity_id", type="str", description="seo_opportunities.id"),
        FieldSpec(name="tags", type="list", description="post tags (slugs/ids)"),
    ),
    requires=("post_id",),
    produces=(
        "content", "title", "topic", "post_slug", "seo_title", "seo_description",
        "seo_keywords", "target_query", "seo_opportunity_id", "tags",
    ),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)

_POST_SQL = """
SELECT id, title, slug, content, seo_title, seo_description, seo_keywords, tag_ids
  FROM posts WHERE id = $1::uuid
"""
_OPP_SQL = """
SELECT id, target_query, current_position, ctr
  FROM seo_opportunities
 WHERE post_id = $1::uuid
 ORDER BY gap_score DESC
 LIMIT 1
"""


async def run(state: dict[str, Any]) -> dict[str, Any]:
    post_id = state.get("post_id")
    pool = getattr(state.get("database_service"), "pool", None)
    if not post_id or pool is None:
        raise RuntimeError(
            "content.load_existing_post: post_id + database_service are required "
            f"(post_id={post_id!r}, pool={'set' if pool else 'None'})"
        )

    async with pool.acquire() as conn:
        post = await conn.fetchrow(_POST_SQL, str(post_id))
        if post is None:
            raise RuntimeError(f"content.load_existing_post: no posts row id={post_id!r}")
        opp = await conn.fetchrow(_OPP_SQL, str(post_id))

    title = post["title"] or ""
    out: dict[str, Any] = {
        "content": post["content"] or "",   # carried verbatim — meta_only never edits body
        "title": title,
        "topic": title,                     # optimizer keyword fallback when target_query == ''
        "post_slug": post["slug"] or "",
        "seo_title": post["seo_title"] or "",
        "seo_description": post["seo_description"] or "",
        "seo_keywords": post["seo_keywords"] or "",
        "tags": list(post["tag_ids"] or []),
        "target_query": (opp["target_query"] if opp else "") or "",
        "seo_opportunity_id": str(opp["id"]) if opp else "",
    }
    logger.info(
        "[content.load_existing_post] hydrated post %s (slug=%s, target_query=%r)",
        post_id, out["post_slug"], out["target_query"],
    )
    return out


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run** — same command. Expected: PASS.

- [ ] **Step 5: Commit** — `git add modules/content/atoms/content_load_existing_post.py tests/unit/seo/test_content_load_existing_post.py && git commit -F-` "feat(seo): content.load_existing_post atom — hydrate state from a posts row (#763)".

---

### Task 3: `seo.optimize_metadata` atom + `SeoMetadataOptimizer`

> The query-aware optimizer. Reuses `_seo_common.run_seo_llm` (LLM-call-with-retry) + the programmatic fallbacks. The optimizer is a standalone callable so the deferred generation-time rewire (design §2 follow-up) can reuse it.

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/seo_optimize_metadata.py`
- Test: `src/cofounder_agent/tests/unit/seo/test_seo_optimize_metadata.py`
- Prompt: register `atoms.seo.optimize_metadata` default in the prompt YAML (see Step 3a)

- [ ] **Step 1: Write the failing test** — query-aware path + query-empty fallback + LLM-failure degradation.

```python
import pytest
from modules.content.atoms import seo_optimize_metadata as atom

@pytest.mark.asyncio
async def test_optimizes_toward_target_query(monkeypatch):
    async def fake_llm(state, key, **kw):
        assert kw["target_query"] == "best gpu 2026"   # query threaded into the prompt
        return '{"title": "Best GPU 2026: Tested", "description": "We benchmarked every card."}'
    monkeypatch.setattr(atom.sc, "run_seo_llm", fake_llm)
    state = {"content": "body", "title": "Old", "topic": "Old", "target_query": "best gpu 2026",
             "seo_title": "Old SEO", "seo_description": "old", "site_config": object()}
    out = await atom.run(state)
    assert out["seo_title"].startswith("Best GPU 2026")
    assert len(out["seo_description"]) <= 160
    assert out["stages"]["seo_metadata_optimized"] is True

@pytest.mark.asyncio
async def test_query_empty_falls_back_to_topic(monkeypatch):
    seen = {}
    async def fake_llm(state, key, **kw):
        seen["primary_keyword"] = kw["target_query"] or kw["primary_keyword"]
        return '{"title": "T", "description": "D"}'
    monkeypatch.setattr(atom.sc, "run_seo_llm", fake_llm)
    state = {"content": "b", "title": "Old", "topic": "GPU guide", "target_query": "",
             "tags": ["gpu"], "site_config": object()}
    await atom.run(state)
    assert seen["primary_keyword"] in ("gpu", "GPU guide")   # falls back, never empties

@pytest.mark.asyncio
async def test_llm_failure_preserves_existing_meta(monkeypatch):
    async def boom(*a, **k): raise RuntimeError("ollama down")
    monkeypatch.setattr(atom.sc, "run_seo_llm", boom)
    state = {"content": "b", "title": "Old", "topic": "Old", "target_query": "q",
             "seo_title": "KEEP ME", "seo_description": "KEEP DESC", "site_config": object()}
    out = await atom.run(state)
    # meta_only safety: a failed optimization must NOT blank the live post's meta
    assert out["seo_title"] == "KEEP ME"
    assert out["seo_description"] == "KEEP DESC"
```

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/test_seo_optimize_metadata.py -v`. Expected: FAIL (module missing).

- [ ] **Step 3: Implement** — note the failure-mode difference from `generate_all_metadata`: on LLM failure the refresh optimizer **keeps the existing live meta** (never degrades a published post to a programmatic guess).

````python
"""seo.optimize_metadata — query-aware re-optimization of an existing post's
title + meta description (the seo_refresh graph's optimizer).

Differs from seo.generate_all_metadata in three ways:
  1. Targets a specific GSC query (state['target_query']) when present, falling
     back to the post's topic/primary keyword when empty (Phase-1 page-level data).
  2. Optimizes for CTR on an ALREADY-RANKING page — preserve intent, sharpen the
     hook. The body (state['content']) is read-only here (meta_only scope).
  3. On LLM/parse failure it KEEPS the existing live meta rather than degrading to
     a programmatic guess — a failed refresh must never worsen a published post.

Exposes SeoMetadataOptimizer as a standalone callable so the deferred
generation-time rewire (design §2) can reuse one implementation.

Issue: Glad-Labs/poindexter#763 (epic #762).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from modules.content.atoms import _seo_common as sc
from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from utils.title_utils import derive_seo_title

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="seo.optimize_metadata",
    type="atom",
    version="1.0.0",
    description=(
        "Re-optimize an existing post's seo_title + seo_description toward a "
        "target GSC query for CTR. Keeps existing meta on LLM failure. "
        "Body is read-only (meta_only scope)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="existing body (read-only)"),
        FieldSpec(name="topic", type="str", description="fallback keyword when target_query empty"),
        FieldSpec(name="target_query", type="str", description="GSC query to optimize toward", required=False),
        FieldSpec(name="seo_title", type="str", description="current seo_title (kept on failure)", required=False),
        FieldSpec(name="seo_description", type="str", description="current meta (kept on failure)", required=False),
    ),
    outputs=(
        FieldSpec(name="seo_title", type="str", description="<=60 char optimized title"),
        FieldSpec(name="seo_description", type="str", description="<=160 char optimized meta"),
        FieldSpec(name="stages", type="dict", description="sets seo_metadata_optimized"),
    ),
    requires=("content",),
    produces=("seo_title", "seo_description", "stages"),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)


def _extract_json(text: str) -> dict[str, Any] | None:
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fence.group(1) if fence else text
    for chunk in (candidate, text):
        chunk = chunk.strip()
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        brace = re.search(r"\{[\s\S]*\}", chunk)
        if brace:
            try:
                parsed = json.loads(brace.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    return None


async def optimize(state: dict[str, Any]) -> tuple[str, str]:
    """SeoMetadataOptimizer core — returns (seo_title, seo_description).

    Reusable by both the seo_refresh graph and (deferred) the generation path.
    Raises on persistent LLM/parse failure; the atom wrapper catches + keeps
    the existing meta.
    """
    target_query = (state.get("target_query") or "").strip()
    tags = state.get("tags") or []
    fallback_kw = (tags[0] if tags else state.get("topic")) or state.get("topic") or ""
    primary_keyword = target_query or fallback_kw

    raw = await sc.run_seo_llm(
        state,
        "atoms.seo.optimize_metadata",
        target_query=target_query,
        primary_keyword=primary_keyword,
        current_title=state.get("seo_title") or state.get("title") or "",
        current_description=state.get("seo_description") or "",
        content=sc.content_excerpt(state.get("content") or ""),
        max_attempts=ATOM_META.retry.max_attempts,
        backoff_s=ATOM_META.retry.backoff_s,
    )
    parsed = _extract_json(raw)
    if parsed is None:
        raise ValueError(f"seo.optimize_metadata: unparseable LLM output: {raw[:160]!r}")

    raw_title = str(parsed.get("title") or "").strip()
    raw_desc = str(parsed.get("description") or "").strip()
    title = derive_seo_title(sc.clean_oneline(raw_title), max_len=60) if raw_title else (
        state.get("seo_title") or state.get("title") or ""
    )
    desc = sc.clamp_words(raw_desc, 160) if raw_desc else (state.get("seo_description") or "")
    return title, desc


async def run(state: dict[str, Any]) -> dict[str, Any]:
    stages = dict(state.get("stages") or {})
    if state.get("site_config") is None:
        return {}
    try:
        title, desc = await optimize(state)
    except Exception as exc:  # noqa: BLE001 — keep existing meta; never blank a live post
        sc.degraded("optimize_metadata", exc)
        title = state.get("seo_title") or state.get("title") or ""
        desc = state.get("seo_description") or ""
    stages["seo_metadata_optimized"] = True
    return {"seo_title": title, "seo_description": desc, "stages": stages}


__all__ = ["ATOM_META", "run", "optimize"]
````

- [ ] **Step 3a: Register the prompt default** — add an `atoms.seo.optimize_metadata` key to the SEO prompt YAML (same file the other `atoms.seo.*` keys live in; find via `grep -rl "atoms.seo.generate_all_metadata" src/cofounder_agent/**/prompts*`). The prompt instructs: "Rewrite the title and meta description to win the click for `{target_query}` (primary keyword `{primary_keyword}`). Preserve the page's intent; do not invent facts not in the excerpt. Return ONLY JSON `{\"title\": ..., \"description\": ...}`." Per `feedback_prompts_must_be_db_configurable`, the YAML default is the floor; runtime overrides land in `prompt_templates`. No hardcoded length numbers in the prompt copy (`feedback_no_hardcoded_lengths_in_prompts`) — the 60/160 clamps live in code.

- [ ] **Step 4: Run** — `poetry run pytest tests/unit/seo/test_seo_optimize_metadata.py -v`. Expected: PASS.

- [ ] **Step 5: Commit** — `git add modules/content/atoms/seo_optimize_metadata.py tests/unit/seo/test_seo_optimize_metadata.py <prompt yaml> && git commit -F-` "feat(seo): seo.optimize_metadata atom + SeoMetadataOptimizer (#763)".

---

### Task 4: `content.republish_post` atom (terminal — mutates the live post, gated)

> Updates `posts` meta-only, re-exports to R2, fires ISR revalidation, and stamps the opportunity baseline. This is the ONE content-mutating unit — guard it. It runs only AFTER `atoms.approval_gate` passes, so reaching it means the operator approved (or auto-publish graduated).

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/content_republish_post.py`
- Test: `src/cofounder_agent/tests/unit/seo/test_content_republish_post.py`

- [ ] **Step 1: Write the failing test** — assert (a) only meta columns are written, never `content`; (b) `export_post` + `trigger_isr_revalidate` are awaited with the slug; (c) the opportunity row is stamped `status='refreshed'` + baseline captured.

```python
import pytest
from modules.content.atoms import content_republish_post as atom

@pytest.mark.asyncio
async def test_republish_updates_meta_exports_and_stamps(monkeypatch):
    calls = {"update": None, "export": None, "reval": None, "stamp": None}

    class _Conn:
        async def execute(self, sql, *args):
            if "UPDATE posts" in sql:
                calls["update"] = (sql, args)
                assert "content" not in sql.lower().split("set", 1)[1]  # body untouched
            elif "UPDATE seo_opportunities" in sql:
                calls["stamp"] = args
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class _Pool:
        def acquire(self): return _Conn()
    class _DB:
        pool = _Pool()

    async def fake_export(pool, slug, *, site_config): calls["export"] = slug; return True
    async def fake_reval(slug, *, site_config): calls["reval"] = slug; return True
    monkeypatch.setattr(atom, "export_post", fake_export)
    monkeypatch.setattr(atom, "trigger_isr_revalidate", fake_reval)

    state = {
        "post_id": "11111111-1111-1111-1111-111111111111",
        "post_slug": "old-title", "seo_title": "New SEO", "seo_description": "New desc",
        "seo_keywords": "a, b", "seo_opportunity_id": "22222222-2222-2222-2222-222222222222",
        "database_service": _DB(), "site_config": object(),
    }
    out = await atom.run(state)
    assert calls["export"] == "old-title"
    assert calls["reval"] == "old-title"
    assert out["status"] == "refreshed"
    assert calls["stamp"] is not None     # baseline + status stamped
```

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/test_content_republish_post.py -v`. Expected: FAIL (module missing).

- [ ] **Step 3: Implement** — import the public helpers at module top so the test can monkeypatch them by name.

```python
"""content.republish_post — terminal atom of the seo_refresh graph.

Applies the optimized meta to the live post (meta_only: title/seo_title/
seo_description/seo_keywords — NEVER content), re-exports the static JSON to R2,
fires ISR revalidation (per prod-content-propagation-r2-isr — a DB update alone
does NOT reach the live site), and stamps the seo_opportunities row with the
pre-refresh baseline + status='refreshed' for later outcome measurement.

Only reached after atoms.approval_gate passes, so execution implies sign-off.

Issue: Glad-Labs/poindexter#763 (epic #762).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.revalidation_service import trigger_isr_revalidate
from services.static_export_service import export_post

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.republish_post",
    type="atom",
    version="1.0.0",
    description=(
        "Apply optimized meta to a live post (meta_only), re-export to R2, "
        "revalidate ISR, and stamp the seo_opportunities baseline. Gated — runs "
        "only after approval_gate."
    ),
    inputs=(
        FieldSpec(name="post_id", type="str", description="posts.id (uuid)"),
        FieldSpec(name="post_slug", type="str", description="post slug for export/revalidate"),
        FieldSpec(name="seo_title", type="str", description="optimized title"),
        FieldSpec(name="seo_description", type="str", description="optimized meta"),
        FieldSpec(name="database_service", type="object", description="DB service"),
        FieldSpec(name="site_config", type="object", description="SiteConfig"),
    ),
    outputs=(
        FieldSpec(name="status", type="str", description="'refreshed' on success"),
    ),
    requires=("post_id", "post_slug"),
    produces=("status",),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("db_write", "r2_export", "isr_revalidate"),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)

_UPDATE_POST_SQL = """
UPDATE posts
   SET seo_title       = $2,
       seo_description  = $3,
       seo_keywords     = $4,
       updated_at       = NOW()
 WHERE id = $1::uuid
"""
_STAMP_OPP_SQL = """
UPDATE seo_opportunities
   SET status            = 'refreshed',
       baseline_position = current_position,
       baseline_ctr      = ctr
 WHERE id = $1::uuid
"""
# Baseline is stamped self-referentially from the opportunity's own current
# metrics — no need to thread current_position/ctr through PipelineState.


async def run(state: dict[str, Any]) -> dict[str, Any]:
    post_id = state.get("post_id")
    slug = state.get("post_slug") or ""
    db = state.get("database_service")
    pool = getattr(db, "pool", None)
    site_config = state.get("site_config")
    if not post_id or not slug or pool is None:
        raise RuntimeError(
            f"content.republish_post: post_id+post_slug+pool required "
            f"(post_id={post_id!r}, slug={slug!r})"
        )

    seo_title = state.get("seo_title") or ""
    seo_description = state.get("seo_description") or ""
    seo_keywords = state.get("seo_keywords") or ""

    async with pool.acquire() as conn:
        await conn.execute(_UPDATE_POST_SQL, str(post_id), seo_title, seo_description, seo_keywords)
        opp_id = state.get("seo_opportunity_id")
        if opp_id:
            await conn.execute(_STAMP_OPP_SQL, str(opp_id))

    # Propagation — a DB update alone does NOT reach the live site (R2 JSON +
    # ISR). Await inline (export) per the cancelled-bg-task lesson in publish_service.
    exported = await export_post(pool, slug, site_config=site_config)
    revalidated = await trigger_isr_revalidate(slug, site_config=site_config)
    logger.info(
        "[content.republish_post] post=%s slug=%s exported=%s revalidated=%s",
        post_id, slug, exported, revalidated,
    )
    return {"status": "refreshed"}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run** — same command. Expected: PASS.

- [ ] **Step 5: Commit** — `git add modules/content/atoms/content_republish_post.py tests/unit/seo/test_content_republish_post.py && git commit -F-` "feat(seo): content.republish_post atom — meta_only update + R2/ISR propagation (#763)".

---

### Task 5: Entry-seam helper — surface `post_id`/`target_query` into initial_state

> Mirror the existing `_load_template_slug` / `_load_niche_slug` pattern. Use the metadata read-path confirmed in Task 0 Step 3.

**Files:**

- Modify: `src/cofounder_agent/services/content_router_service.py` (add `_load_task_metadata`; call it in `process_content_generation_task` after the `niche_slug` block ~line 338)
- Test: `src/cofounder_agent/tests/unit/seo/test_content_router_seo_refresh_seam.py`

- [ ] **Step 1: Write the failing test** — a fake db whose metadata read returns `{post_id, seo_opportunity_id, target_query, seo_refresh_scope}`; assert they land in the result dict handed to `TemplateRunner.run`.

```python
import pytest
from services import content_router_service as crs

@pytest.mark.asyncio
async def test_load_task_metadata_surfaces_post_id(monkeypatch):
    async def fake_meta(db, task_id):
        return {"post_id": "abc", "seo_opportunity_id": "opp1",
                "target_query": "best gpu", "seo_refresh_scope": "meta_only"}
    monkeypatch.setattr(crs, "_load_task_metadata", fake_meta)
    captured = {}
    async def fake_run(self, slug, state, **kw): captured.update(state);
    # ... assemble a minimal process_content_generation_task call with a fake
    # database_service + monkeypatched TemplateRunner.run = fake_run + slug='seo_refresh'
    # assert captured["post_id"] == "abc" and captured["target_query"] == "best gpu"
```

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/test_content_router_seo_refresh_seam.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement `_load_task_metadata`** (read from the column confirmed in Task 0 — shown here against `pipeline_versions.stage_data`; adjust to the confirmed path) and merge into `result`:

```python
async def _load_task_metadata(database_service: DatabaseService, task_id: str) -> dict[str, Any]:
    """Read per-task metadata (post_id / seo_opportunity_id / target_query /
    seo_refresh_scope) for graph templates that hydrate from an existing row.

    Mirrors _load_template_slug / _load_niche_slug. Returns {} on miss so the
    canonical_blog path is unaffected (it carries no such metadata).
    """
    keys = ("post_id", "seo_opportunity_id", "target_query", "seo_refresh_scope")
    try:
        async with database_service.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT stage_data FROM pipeline_versions "
                "WHERE task_id = $1 ORDER BY version DESC LIMIT 1",
                str(task_id),
            )
    except Exception as exc:
        logger.warning("[BG-TASK] task metadata lookup failed for %s: %s", task_id, exc)
        return {}
    data = raw if isinstance(raw, dict) else {}
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else data
    return {k: meta[k] for k in keys if isinstance(meta, dict) and meta.get(k) not in (None, "")}
```

Then after the `niche_slug` block:

```python
    # seo_refresh + future graph templates carry hydration metadata (post_id,
    # target_query, ...) in the task row; surface it onto initial_state so the
    # entry atom (content.load_existing_post) can read it. Empty for canonical_blog.
    for k, v in (await _load_task_metadata(database_service, task_id)).items():
        result[k] = v
```

- [ ] **Step 4: Run** — same command. Expected: PASS. Also run the existing content_router tests to confirm no regression: `poetry run pytest tests/unit -k content_router -q`.

- [ ] **Step 5: Commit** — `git add services/content_router_service.py tests/unit/seo/test_content_router_seo_refresh_seam.py && git commit -F-` "feat(seo): surface graph-template hydration metadata into initial_state (#763)".

---

### Task 6: `seo_refresh` graph_def spec + seed migration + validation test

**Files:**

- Create: `src/cofounder_agent/services/seo_refresh_spec.py` (pure-data, typing-only — mirrors `canonical_blog_spec.py`)
- Create: `src/cofounder_agent/services/migrations/<UTC>_seed_seo_refresh_graph_def.py` (generate with `python scripts/new-migration.py "seed seo_refresh graph_def"`)
- Test: `src/cofounder_agent/tests/unit/seo/test_seo_refresh_spec.py`

- [ ] **Step 1: Write the failing test** — assert the spec passes BOTH `_validate_spec` (reachability/DAG) and `build_graph_from_spec` (the #753 schema gate). This is the design's acceptance criterion #1.

```python
import pytest
from services.seo_refresh_spec import SEO_REFRESH_GRAPH_DEF
from services.pipeline_architect import _validate_spec, build_graph_from_spec

def test_spec_passes_reachability_and_schema():
    ok, errors = _validate_spec(SEO_REFRESH_GRAPH_DEF)
    assert ok, errors
    # build_graph_from_spec raises ValueError on undeclared produces/requires (#753)
    g = build_graph_from_spec(SEO_REFRESH_GRAPH_DEF, pool=None)
    assert g is not None

def test_body_mutating_atoms_absent():
    atoms = {n["atom"] for n in SEO_REFRESH_GRAPH_DEF["nodes"]}
    assert "content.generate_draft" not in atoms   # never regenerate body
    assert "content.load_existing_post" in atoms
    assert "content.republish_post" in atoms
```

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/test_seo_refresh_spec.py -v`. Expected: FAIL (spec module missing). NOTE: registry must be discovered first — add an autouse fixture calling `atom_registry.discover()` (copy from an existing atom/graph test, e.g. `tests/unit/**/test_canonical_blog_spec*`).

- [ ] **Step 3: Write the spec** (linear chain; gate config seeds `gate_name`):

```python
"""seo_refresh pipeline as a static graph_def (SEO Harvest Loop Phase 2, #763).

Pure data — NO imports beyond typing — so the seed migration imports just this
dict (migrations-smoke light env). Mirrors canonical_blog_spec.py. Hydrates from
an existing post (content.load_existing_post) instead of generating a draft,
re-optimizes meta toward the target query, runs a minimal QA subset, pauses for
operator approval (approval-first), then republishes meta_only.
"""
from __future__ import annotations

from typing import Any

SEO_REFRESH_GRAPH_DEF: dict[str, Any] = {
    "name": "seo_refresh",
    "description": "Re-optimize an existing post's title/meta toward its target query (meta_only), gated on approval.",
    "entry": "load_post",
    "nodes": [
        {"id": "load_post", "atom": "content.load_existing_post"},
        {"id": "optimize_meta", "atom": "seo.optimize_metadata"},
        {"id": "check_title_originality", "atom": "content.check_title_originality"},
        {"id": "qa_programmatic", "atom": "qa.programmatic"},
        {"id": "qa_aggregate", "atom": "qa.aggregate"},
        {"id": "refresh_gate", "atom": "atoms.approval_gate",
         "config": {"gate_name": "seo_refresh_gate"}},
        {"id": "republish", "atom": "content.republish_post"},
    ],
    "edges": [
        {"from": "load_post", "to": "optimize_meta"},
        {"from": "optimize_meta", "to": "check_title_originality"},
        {"from": "check_title_originality", "to": "qa_programmatic"},
        {"from": "qa_programmatic", "to": "qa_aggregate"},
        {"from": "qa_aggregate", "to": "refresh_gate"},
        {"from": "refresh_gate", "to": "republish"},
        {"from": "republish", "to": "END"},
    ],
}

__all__ = ["SEO_REFRESH_GRAPH_DEF"]
```

> If `test_spec_passes_reachability_and_schema` fails on a `requires` key produced by neither an upstream atom nor PipelineState (e.g. `qa.aggregate` needing `qa_rail_reviews` which `qa.programmatic` produces — verify), reorder or drop the offending QA node. The minimum viable gate is `check_title_originality` alone; `qa.programmatic`+`qa.aggregate` are included for the audit trail and are removable if they over-constrain a meta-only edit. Record the final QA subset in the spec docstring.

- [ ] **Step 4: Write the seed migration** — mirror `20260602_023250_seed_canonical_blog_graph_def.py`:

```python
"""Seed the seo_refresh graph_def into pipeline_templates (active). Light imports."""
from __future__ import annotations
import json, logging
from services.seo_refresh_spec import SEO_REFRESH_GRAPH_DEF
logger = logging.getLogger(__name__)

_SQL = """
INSERT INTO pipeline_templates (slug, name, description, version, active, graph_def, created_by)
VALUES ('seo_refresh', 'seo_refresh', $1, 1, true, $2::jsonb, 'migration')
ON CONFLICT (slug) DO UPDATE
   SET graph_def = EXCLUDED.graph_def, description = EXCLUDED.description, updated_at = NOW()
"""

async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SQL, SEO_REFRESH_GRAPH_DEF["description"], json.dumps(SEO_REFRESH_GRAPH_DEF))
    logger.info("Seeded seo_refresh graph_def")

async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pipeline_templates WHERE slug = 'seo_refresh'")
```

- [ ] **Step 5: Run** — spec test PASS; then migration lint + smoke: `python scripts/ci/migrations_lint.py` and `python scripts/ci/migrations_smoke.py`. Expected: both pass (the migration imports only the pure-data spec).

- [ ] **Step 6: Commit** — `git add services/seo_refresh_spec.py services/migrations/<file> tests/unit/seo/test_seo_refresh_spec.py && git commit -F-` "feat(seo): seed seo_refresh graph_def + spec (#763)".

---

### Task 7: Settings — scope, gate-on, graduation threshold

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (the `# ----- SEO Harvest Loop (Phase 1) -----` block ~line 631)
- Test: `src/cofounder_agent/tests/unit/seo/test_seo_refresh_settings.py`

- [ ] **Step 1: Write the failing test**

```python
from services.settings_defaults import DEFAULTS

def test_seo_refresh_settings_seeded():
    assert DEFAULTS["seo.refresh.scope"] == "meta_only"
    assert DEFAULTS["pipeline_gate_seo_refresh_gate"] == "true"   # approval-first
    assert "seo.refresh.auto_publish_after_clean_runs" in DEFAULTS
    assert DEFAULTS["seo.refresh.enabled"] == "false"             # still default-off
```

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/test_seo_refresh_settings.py -v`. Expected: FAIL.

- [ ] **Step 3: Add the keys** (rename the Phase-1 comment to cover Phase 2):

```python
    'seo.refresh.scope': 'meta_only',
    # Approval-first: the refresh_gate is ON (re-publishing a live post pauses
    # for sign-off), unlike draft_gate which ships off. Lock 2 graduation flips
    # to auto-publish once the trailing clean-run count is met.
    'pipeline_gate_seo_refresh_gate': 'true',
    'seo.refresh.auto_publish_after_clean_runs': '5',
```

- [ ] **Step 4: Run** — PASS.

- [ ] **Step 5: Commit** — `git add services/settings_defaults.py tests/unit/seo/test_seo_refresh_settings.py && git commit -F-` "feat(seo): seed seo_refresh scope + approval-first gate + graduation threshold (#763)".

---

### Task 8: End-to-end graph test + docs (Milestone A close)

**Files:**

- Test: `src/cofounder_agent/tests/unit/seo/test_seo_refresh_graph_e2e.py`
- Docs: `docs/architecture/seo-harvest-loop.md` (new — or extend the design doc with an "implementation" section), + CLAUDE.md graph-def node count note
- Delete: the Task 0 spike test file (its asserts now live in Tasks 2/5/6)

- [ ] **Step 1: Write an in-memory e2e test** — seed a fake posts row + opportunity, build the graph via `build_graph_from_spec`, drive it with `gate` disabled (so it runs to `republish` without pausing), assert the post's meta updated + `export_post`/`trigger_isr_revalidate` fired + opportunity stamped `refreshed`. (Gate-enabled interrupt/resume is covered by the existing approval_gate tests — reference them rather than re-testing LangGraph.)

- [ ] **Step 2: Run** — `poetry run pytest tests/unit/seo/ -v`. Expected: all PASS.

- [ ] **Step 3: Write the runbook doc** — `docs/architecture/seo-harvest-loop.md`: the 7-node `seo_refresh` chain, the meta_only invariant, the approval-first gate + `poindexter pipeline resume <task_id>`, the manual one-post validation procedure (enqueue a `seo_refresh` task by hand for one striking-distance post, approve, verify the live SERP title changed). Add the `seo_refresh` graph_def (7 nodes) to the CLAUDE.md pipeline-templates narrative.

- [ ] **Step 4: Run the full backend suite for the touched area** — `cd src/cofounder_agent && poetry run pytest tests/unit -q -k "seo or content_router or pipeline_state or template_runner"`. Expected: green.

- [ ] **Step 5: Commit + open the Milestone A PR** — `git add ...; git rm tests/unit/seo/test_seo_refresh_entry_seam_spike.py; git commit -F-` then open a PR titled "feat(seo): SEO Harvest Loop Phase 2A — seo_refresh graph (hand-triggerable, default-off) (#763)". CI green = merge per `feedback_ci_is_the_review_gate`.

> **Validate on ONE real post before Milestone B.** With the PR merged, hand-enqueue a single `seo_refresh` task for a top page-1-push opportunity, approve at the gate, and confirm the live title/meta changed (R2 + ISR). Only then wire auto-enqueue.

---

# Milestone B — autonomy (auto-enqueue + outcome + Grafana)

### Task 9: Auto-enqueue from the analyzer (gated on `seo.refresh.enabled`)

**Files:**

- Create: `src/cofounder_agent/services/seo/enqueue_refreshes.py`
- Modify: `src/cofounder_agent/services/jobs/run_seo_opportunity_analyzer.py` (call the enqueuer after upsert, gated)
- Test: `src/cofounder_agent/tests/unit/seo/test_enqueue_refreshes.py`

- [ ] **Step 1: Write the failing test** — given N `open` opportunities and `seo.refresh.enabled=true`, assert it creates `pipeline_tasks` rows with `template_slug='seo_refresh'` + metadata `{post_id, seo_opportunity_id, target_query, seo_refresh_scope}`, flips those opportunities to `status='queued'`, and respects a per-run cap (`seo.refresh.max_per_run`, default e.g. 3). With `enabled=false`, asserts zero enqueues.

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement `enqueue_refreshes`** — select top-N `open` `page1_push`/`striking_distance` rows by `gap_score`, create each task via `TasksDatabase.add_task({...})` with explicit `template_slug='seo_refresh'`, `topic=<post title or target_query>`, `task_type='seo_refresh'`, `status='pending'`, and `metadata={...}`. **Verify `add_task` honors an explicit `template_slug`** (it defaults from `app_settings.default_template_slug`); if it ignores the passed value, add a `template_slug` passthrough — small, in-scope. Flip enqueued opportunities to `status='queued'`. Add `seo.refresh.max_per_run` to `settings_defaults.py` (default `'3'`).

- [ ] **Step 4: Wire into the analyzer job** — after `upsert_opportunities`, if `sc.get_bool("seo.refresh.enabled", False)`, call `enqueue_refreshes(pool, sc)` and add its count to the `JobResult` metrics.

- [ ] **Step 5: Run + commit** — Expected: PASS. Commit "feat(seo): auto-enqueue seo_refresh tasks from the analyzer (gated) (#763)".

---

### Task 10: Refresh-outcome measurement

> Closes design §2c — N days after a refresh, re-read GSC position/CTR and record the delta vs the stamped baseline.

**Files:**

- Create: `src/cofounder_agent/services/jobs/run_seo_refresh_outcome.py`
- Create: `src/cofounder_agent/services/seo/outcome.py` (delta computation, pure + tested)
- Test: `src/cofounder_agent/tests/unit/seo/test_seo_refresh_outcome.py`

- [ ] **Step 1: Write the failing test** — pure delta function: given baseline (pos 7.2, ctr 0.004) + latest snapshot (pos 4.1, ctr 0.018), assert position/CTR deltas computed correctly and `outcome_measured_at` set; a row younger than `seo.refresh.outcome_measure_after_days` is skipped.

- [ ] **Step 2–4: Implement** — a job selecting `seo_opportunities` rows where `status='refreshed'` AND `outcome_measured_at IS NULL` AND `baseline` stamped ≥ `outcome_measure_after_days` ago; join the latest `post_performance` snapshot; write `outcome_position`/`outcome_ctr`/`outcome_measured_at`. Register the job in the scheduler (mirror `RunSeoOpportunityAnalyzerJob`). Commit.

---

### Task 11: Grafana — refresh outcomes panel

**Files:**

- Modify: `infrastructure/grafana/dashboards/seo-harvest.json` (the Phase-1 board)
- Validate: `python scripts/ci/grafana_panels_lint.py` (or the repo's panel linter)

- [ ] **Step 1: Add panels** — (a) refresh queue depth (`seo_opportunities` `status='queued'`), (b) refreshed count over time, (c) **position/CTR delta after refresh** (baseline vs outcome — the panel that proves the loop), (d) a live table of refreshed posts with delta. Repo JSON is the source of truth (30s provision reload); 960px-portrait-friendly per `feedback_grafana_everything`.

- [ ] **Step 2: Validate + commit** — run the panel linter, commit "feat(seo): refresh-outcome panels on the SEO Harvest board (#763)", open the Milestone B PR.

---

## Self-review checklist (run before handing off each milestone)

- **Spec coverage:** design §3 Phase 2a (graph) → Tasks 2/3/4/6; §2b approval+graduation → Tasks 4/7 (+ deferred auto-publish); §2c outcome → Task 10; §5 data model → reuses existing columns (Task 4 stamps them); §6 config → Task 7; §7 observability → Task 11; §8 testing → every task is TDD; §9 #1 entry seam → Task 0 (first). ✅
- **meta_only invariant:** `content.republish_post` UPDATE touches no `content` column (Task 4 test asserts it); `content.load_existing_post` carries `content` verbatim. ✅
- **#753 schema gate:** every new `requires`/`produces` key (`target_query`, `seo_opportunity_id`, `seo_refresh_scope`, plus reused `post_id`/`post_slug`/`status`/`seo_*`/`content`/`title`/`topic`/`tags`/`stages`) is declared in `PipelineState` (Task 1). ✅
- **Fail-loud:** load/republish atoms raise on missing `post_id`/`pool`; no silent defaults. ✅
- **No paid APIs / DB-first config:** optimizer routes through `_seo_common.run_seo_llm` (local tier `budget`); every threshold/scope/gate is an `app_settings` key. ✅

## Execution handoff

(Filled in by the writing-plans skill's handoff step.)

# Atom Cutover — Plan 4: graph_def cutover seam for `canonical_blog`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author `canonical_blog` as a static `graph_def` spec (the 13 coarse stages as `stage.<name>` nodes + the Plan-3 `qa.*` rail atoms replacing the monolithic `cross_model_qa` stage), seed it into the `pipeline_templates` DB table (active, new version), and route `TemplateRunner.run` to PREFER the graph_def path (compiled by `build_graph_from_spec`) when `app_settings.pipeline_use_graph_def` is true AND an active graph_def row exists for the slug — otherwise the legacy Python `TEMPLATES` factory. **Flag default FALSE → the change is dormant on prod until an operator flips it** (Plan 5 does the canary + flip).

**Architecture:** A pure-data module holds `CANONICAL_BLOG_GRAPH_DEF` (no heavy imports, so a migration can import just the dict). A new `load_active_graph_def(pool, slug)` reader in `services/pipeline_templates/__init__.py` returns the active row's `graph_def` (or `None` — missing row, unreadable, or empty `{}`). `TemplateRunner.run` gains a small branch BEFORE the legacy factory lookup: when the flag is on and a graph_def loads, build the graph via `build_graph_from_spec(graph_def, pool=…, record_sink=records)` (which returns the same uncompiled `StateGraph` over the same `PipelineState`); everything downstream — `_partition_state_and_services`, checkpointer, `graph.compile`, `ainvoke`, `ok`/`halted_at`, `capability_outcomes.record_run`, `TemplateRunSummary` — is IDENTICAL. A migration seeds the spec (`active=true`, `version=2`) and the `pipeline_use_graph_def='false'` flag.

**Why sequential rails (not parallel fan-out):** the qa.\* rails are `parallelizable=True`, but this cutover wires them as a LINEAR chain (`qa.critic → qa.deepeval → qa.guardrails → qa.ragas → qa.aggregate`) for correctness/robustness — it sidesteps LangGraph fan-in/superstep edge cases on the live runtime path. Each rail still appends to the `operator.add`-reduced `qa_rail_reviews` channel; `qa.aggregate` reads the merged list. Parallel fan-out is a safe later optimization once the graph_def path is proven.

**Tech Stack:** Python 3.13, the existing `pipeline_architect.build_graph_from_spec` + `_validate_spec` (Plan 1), `atom_registry.discover` (surfaces stages as `stage.<name>` atoms with `requires=()/produces=()`, and the Plan-3 `qa.*` atoms), `template_runner.TemplateRunner` (`self._site_config.get_bool`), `pipeline_templates` DB table (`slug/version/active/graph_def`), asyncpg, pytest (`asyncio_mode="auto"`; pool-stub unit tests, no live DB/Ollama).

**Spec:** `docs/superpowers/specs/2026-06-01-canonical-blog-atom-cutover-design.md` (§ "Cutover mechanism (flag-gated, DB-config)" + D1/D2/D6).

**Conventions:** run tests from `src/cofounder_agent` with the main venv python (worktrees have no poetry env):
`"<main-venv-python>" -m pytest <relative/test/path> -p no:cacheprovider > test_out.txt 2>&1` then **Read `test_out.txt` back** (Windows stdout buffers; never treat empty as success; delete `test_out.txt`, don't commit it). cwd = the worktree's `src/cofounder_agent`. Linear commits, commit after each green task, end every message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Do NOT push/PR/merge — the controller integrates.

### Reference facts (verified — don't re-derive)

- The cutover seam is `services/template_runner.py::TemplateRunner.run`, lines 709-722 (factory lookup → `graph = factory(pool=…, record_sink=records)`). Everything after line 722 is path-agnostic.
- `build_graph_from_spec(spec, *, pool, record_sink) -> StateGraph` (`pipeline_architect.py:571`) returns an UNcompiled `StateGraph(PipelineState)`; it TRUSTS the spec (does NOT call `_validate_spec` — the caller/seed path must validate). `stage.<name>` and `qa.<name>` nodes both resolve through the registry after `discover()`.
- Stages are surfaced as `stage.<name>` atoms with `requires=()`/`produces=()` (`atom_registry._stage_to_atom_meta`) — so `stage.*` nodes pass Plan-1 validation trivially.
- `_CANONICAL_BLOG_ORDER` (the live legacy order) is **14** stages: `verify_task, generate_content, writer_self_review, resolve_internal_link_placeholders, quality_evaluation, url_validation, replace_inline_images, source_featured_image, cross_model_qa, generate_seo_metadata, generate_media_scripts, generate_video_shot_list, capture_training_data, finalize_task`. All 14 stage names are registered in `plugins/registry.py:_SAMPLES`. The graph_def replaces `cross_model_qa` with the qa.\* rail chain.
- `SiteConfig.get_bool(key, default=False) -> bool` (`site_config.py:213`); `TemplateRunner` holds `self._site_config`.
- `pipeline_templates` columns: `id, slug (unique), name, description, version, active, graph_def (jsonb default '{}'), created_by, created_at, updated_at`. No existing reader — Plan 4 adds one. The writer pattern (`cache_template`) uses `INSERT … ON CONFLICT (slug) DO UPDATE SET graph_def = EXCLUDED.graph_def`.
- asyncpg returns `jsonb` as a `str` by default (needs `json.loads`); handle both `str` and already-decoded `dict`.

---

### Task 1: author + validate the `canonical_blog` graph_def

A pure-data module holding the spec, with a test that proves it (a) passes Plan-1's `_validate_spec` and (b) compiles via `build_graph_from_spec(...).compile()` (every `stage.*` + `qa.*` node resolves through the registry). This is the strongest possible proof the spec is runnable before it ever touches the live path.

**Files:**

- Create: `src/cofounder_agent/services/pipeline_templates/canonical_blog_spec.py`
- Test: `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Validate + compile the canonical_blog graph_def (atom-cutover Plan 4, #355)."""

from __future__ import annotations

import pytest

from services import pipeline_architect
from services.atom_registry import discover
from services.pipeline_templates.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF


@pytest.mark.unit
class TestCanonicalBlogSpec:
    def test_shape(self):
        spec = CANONICAL_BLOG_GRAPH_DEF
        assert spec["name"] == "canonical_blog"
        assert spec["entry"] == "verify_task"
        node_atoms = {n["atom"] for n in spec["nodes"]}
        # The five qa.* atoms replace cross_model_qa.
        assert {"qa.critic", "qa.deepeval", "qa.guardrails", "qa.ragas", "qa.aggregate"} <= node_atoms
        # No legacy monolithic QA stage node.
        assert "stage.cross_model_qa" not in node_atoms
        # The 13 surviving coarse stages are present as stage.* nodes.
        for s in (
            "verify_task", "generate_content", "writer_self_review",
            "resolve_internal_link_placeholders", "quality_evaluation",
            "url_validation", "replace_inline_images", "source_featured_image",
            "generate_seo_metadata", "generate_media_scripts",
            "generate_video_shot_list", "capture_training_data", "finalize_task",
        ):
            assert f"stage.{s}" in node_atoms, s

    def test_passes_plan1_validator(self):
        discover()  # surfaces stage.* + registers qa.* atoms (idempotent)
        ok, errors = pipeline_architect._validate_spec(CANONICAL_BLOG_GRAPH_DEF)
        assert ok is True, errors

    def test_compiles_via_build_graph_from_spec(self):
        discover()
        graph = pipeline_architect.build_graph_from_spec(
            CANONICAL_BLOG_GRAPH_DEF, pool=None, record_sink=[],
        )
        # compile() resolves entry/edges/nodes — proves every stage.* and
        # qa.* node has a registered callable and the wiring is valid.
        compiled = graph.compile()
        assert compiled is not None
```

- [ ] **Step 2: Run, verify it fails**

Run: `"<venv-python>" -m pytest tests/unit/services/test_canonical_blog_spec.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `services.pipeline_templates.canonical_blog_spec` does not exist.

- [ ] **Step 3: Create `services/pipeline_templates/canonical_blog_spec.py`**

```python
"""The canonical_blog pipeline as a static graph_def spec (atom-cutover #355).

Pure data — NO imports beyond typing — so a migration can import just this
dict without pulling in LangGraph / template_runner. This is the authoritative
spec; the Plan-4 migration seeds ``json.dumps(CANONICAL_BLOG_GRAPH_DEF)`` into
``pipeline_templates.graph_def`` and ``TemplateRunner`` compiles it via
``build_graph_from_spec`` when ``pipeline_use_graph_def`` is on.

Structure: the 13 coarse stages that survive the granularity refactor are
``stage.<name>`` nodes (resolved through the surfaced-stage registry);
the monolithic ``cross_model_qa`` stage is replaced by the Plan-3 qa.* rail
atoms wired as a LINEAR chain (qa.critic → qa.deepeval → qa.guardrails →
qa.ragas → qa.aggregate). Each rail appends to the operator.add-reduced
``qa_rail_reviews`` channel; qa.aggregate combines them into the gate
decision (and halts the graph on reject). Sequential (not parallel fan-out)
for cutover robustness — the rails are parallelizable, so a future spec can
fan them out once the graph_def path is proven.
"""

from __future__ import annotations

from typing import Any

CANONICAL_BLOG_GRAPH_DEF: dict[str, Any] = {
    "name": "canonical_blog",
    "description": (
        "Canonical blog pipeline (atom-composed): 13 coarse stages + the "
        "qa.* rail block replacing cross_model_qa."
    ),
    "entry": "verify_task",
    "nodes": [
        {"id": "verify_task", "atom": "stage.verify_task"},
        {"id": "generate_content", "atom": "stage.generate_content"},
        {"id": "writer_self_review", "atom": "stage.writer_self_review"},
        {"id": "resolve_internal_link_placeholders", "atom": "stage.resolve_internal_link_placeholders"},
        {"id": "quality_evaluation", "atom": "stage.quality_evaluation"},
        {"id": "url_validation", "atom": "stage.url_validation"},
        {"id": "replace_inline_images", "atom": "stage.replace_inline_images"},
        {"id": "source_featured_image", "atom": "stage.source_featured_image"},
        # qa.* rail block (replaces the cross_model_qa stage) — linear chain.
        {"id": "qa_critic", "atom": "qa.critic"},
        {"id": "qa_deepeval", "atom": "qa.deepeval"},
        {"id": "qa_guardrails", "atom": "qa.guardrails"},
        {"id": "qa_ragas", "atom": "qa.ragas"},
        {"id": "qa_aggregate", "atom": "qa.aggregate"},
        {"id": "generate_seo_metadata", "atom": "stage.generate_seo_metadata"},
        {"id": "generate_media_scripts", "atom": "stage.generate_media_scripts"},
        {"id": "generate_video_shot_list", "atom": "stage.generate_video_shot_list"},
        {"id": "capture_training_data", "atom": "stage.capture_training_data"},
        {"id": "finalize_task", "atom": "stage.finalize_task"},
    ],
    "edges": [
        {"from": "verify_task", "to": "generate_content"},
        {"from": "generate_content", "to": "writer_self_review"},
        {"from": "writer_self_review", "to": "resolve_internal_link_placeholders"},
        {"from": "resolve_internal_link_placeholders", "to": "quality_evaluation"},
        {"from": "quality_evaluation", "to": "url_validation"},
        {"from": "url_validation", "to": "replace_inline_images"},
        {"from": "replace_inline_images", "to": "source_featured_image"},
        {"from": "source_featured_image", "to": "qa_critic"},
        {"from": "qa_critic", "to": "qa_deepeval"},
        {"from": "qa_deepeval", "to": "qa_guardrails"},
        {"from": "qa_guardrails", "to": "qa_ragas"},
        {"from": "qa_ragas", "to": "qa_aggregate"},
        {"from": "qa_aggregate", "to": "generate_seo_metadata"},
        {"from": "generate_seo_metadata", "to": "generate_media_scripts"},
        {"from": "generate_media_scripts", "to": "generate_video_shot_list"},
        {"from": "generate_video_shot_list", "to": "capture_training_data"},
        {"from": "capture_training_data", "to": "finalize_task"},
        {"from": "finalize_task", "to": "END"},
    ],
}

__all__ = ["CANONICAL_BLOG_GRAPH_DEF"]
```

- [ ] **Step 4: Run, verify it passes**

Run: `"<venv-python>" -m pytest tests/unit/services/test_canonical_blog_spec.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all 3 tests pass. (If `test_passes_plan1_validator` fails, read the `errors` list it prints — a `FIX node …` message names the unmet requirement; the most likely cause is a typo in a `stage.*` name not matching a registered stage, or a qa.\* atom not discovered.)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_templates/canonical_blog_spec.py src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py
git commit -m "feat(pipeline): canonical_blog graph_def spec (#355)"
```

---

### Task 2: `load_active_graph_def` reader

The DB reader `TemplateRunner` calls to fetch the active graph_def for a slug. Best-effort (a read failure → `None` → legacy fallback, never a crash). Treats a missing row / empty `{}` / node-less spec as "no graph_def".

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_templates/__init__.py` — add `load_active_graph_def` + re-export `CANONICAL_BLOG_GRAPH_DEF`
- Test: `src/cofounder_agent/tests/unit/services/test_load_active_graph_def.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for load_active_graph_def (atom-cutover Plan 4, #355).
Pool stub — no live DB."""

from __future__ import annotations

import json

import pytest

from services.pipeline_templates import load_active_graph_def


class _Conn:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, sql, *args):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _Pool:
    def __init__(self, row):
        self._conn = _Conn(row)

    def acquire(self):
        return _Acquire(self._conn)


@pytest.mark.unit
class TestLoadActiveGraphDef:
    async def test_returns_parsed_dict_from_str_jsonb(self):
        spec = {"name": "x", "entry": "a", "nodes": [{"id": "a", "atom": "qa.aggregate"}], "edges": []}
        pool = _Pool({"graph_def": json.dumps(spec)})
        out = await load_active_graph_def(pool, "canonical_blog")
        assert out == spec

    async def test_returns_dict_when_already_decoded(self):
        spec = {"name": "x", "nodes": [{"id": "a", "atom": "q"}]}
        pool = _Pool({"graph_def": spec})
        out = await load_active_graph_def(pool, "canonical_blog")
        assert out == spec

    async def test_no_row_returns_none(self):
        pool = _Pool(None)
        assert await load_active_graph_def(pool, "canonical_blog") is None

    async def test_empty_graph_def_returns_none(self):
        # The column default is '{}' — a node-less spec is treated as absent.
        pool = _Pool({"graph_def": "{}"})
        assert await load_active_graph_def(pool, "canonical_blog") is None

    async def test_none_pool_returns_none(self):
        assert await load_active_graph_def(None, "canonical_blog") is None

    async def test_unparseable_json_returns_none(self):
        pool = _Pool({"graph_def": "{not json"})
        assert await load_active_graph_def(pool, "canonical_blog") is None
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/test_load_active_graph_def.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `load_active_graph_def` not exported from `services.pipeline_templates`.

- [ ] **Step 3: Add to `services/pipeline_templates/__init__.py`**

At the TOP of the file, ensure `json` and a module `logger` are available. If the module already imports `logging`/has a `logger`, reuse it; otherwise add near the other imports:

```python
import json
import logging

logger = logging.getLogger(__name__)
```

Add the re-export near the other imports (so a migration can also do `from services.pipeline_templates import CANONICAL_BLOG_GRAPH_DEF`):

```python
from services.pipeline_templates.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF
```

Add the reader function (anywhere at module level, e.g. just above the `TEMPLATES` dict):

```python
async def load_active_graph_def(pool: Any, slug: str) -> dict[str, Any] | None:
    """Return the active ``graph_def`` for ``slug`` from ``pipeline_templates``,
    or ``None`` when there's no active row, the row is unreadable, or the
    spec is empty/node-less (the column default ``'{}'``).

    Best-effort: a DB error degrades to ``None`` (the runner falls back to the
    legacy Python factory) rather than failing the run.
    """
    if pool is None or not slug:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT graph_def FROM pipeline_templates "
                "WHERE slug = $1 AND active = true "
                "ORDER BY version DESC LIMIT 1",
                slug,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pipeline_templates] load_active_graph_def(%r) failed: %s", slug, exc)
        return None
    if not row:
        return None
    raw = row["graph_def"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[pipeline_templates] graph_def for %r is not valid JSON", slug)
            return None
    if not isinstance(raw, dict) or not raw.get("nodes"):
        return None
    return raw
```

Ensure `Any` is imported (the module already uses `Callable[..., StateGraph]`, so `from typing import Any` is likely present — add it to the existing typing import if not).

- [ ] **Step 4: Run, verify they pass**

Run: `"<venv-python>" -m pytest tests/unit/services/test_load_active_graph_def.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all 6 tests pass.

- [ ] **Step 5: Regression — pipeline_templates still imports cleanly**

Run: `"<venv-python>" -m pytest tests/unit/services/test_canonical_blog_spec.py tests/unit/services/test_template_runner_state_partition.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass (the new import + reader don't break module load or the existing runner tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/pipeline_templates/__init__.py src/cofounder_agent/tests/unit/services/test_load_active_graph_def.py
git commit -m "feat(pipeline): load_active_graph_def reader for pipeline_templates (#355)"
```

---

### Task 3: route `TemplateRunner.run` through the graph_def path (flag-gated)

The surgical cutover seam. BEFORE the legacy factory lookup, when `pipeline_use_graph_def` is on AND a graph_def loads, build the graph via `build_graph_from_spec`; otherwise the legacy factory runs unchanged. Flag default FALSE.

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` — `TemplateRunner.run`, lines 709-722
- Test: `src/cofounder_agent/tests/unit/services/test_template_runner_graphdef_routing.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Routing tests for the graph_def cutover seam in TemplateRunner.run
(atom-cutover Plan 4, #355). Mirrors the harness in
test_template_runner_state_partition.py: a trivial StateGraph + a
SiteConfig with the Postgres checkpointer off (→ MemorySaver), pool=None."""

from __future__ import annotations

import pytest
from langgraph.graph import END, StateGraph

from services import pipeline_architect
from services.pipeline_templates import TEMPLATES
from services.site_config import SiteConfig
from services.template_runner import PipelineState, TemplateRunner


def _trivial_graph() -> StateGraph:
    g: StateGraph = StateGraph(PipelineState)

    async def _noop(state, config=None):
        return {}

    g.add_node("noop", _noop)
    g.set_entry_point("noop")
    g.add_edge("noop", END)
    return g


def _runner(flag: bool) -> TemplateRunner:
    return TemplateRunner(
        pool=None,
        checkpointer_dsn=None,
        site_config=SiteConfig(initial_config={
            "template_runner_use_postgres_checkpointer": "false",
            "pipeline_use_graph_def": "true" if flag else "false",
        }),
    )


@pytest.mark.unit
class TestGraphDefRouting:
    async def test_flag_off_uses_legacy_factory(self, monkeypatch):
        calls = {"factory": 0, "build": 0}

        def fake_factory(*, pool, record_sink=None):
            calls["factory"] += 1
            return _trivial_graph()

        def fake_build(spec, *, pool, record_sink=None):
            calls["build"] += 1
            return _trivial_graph()

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)
        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)

        summary = await _runner(False).run("canonical_blog", {"task_id": "t1"})
        assert summary.ok is True
        assert calls["factory"] == 1
        assert calls["build"] == 0

    async def test_flag_on_with_graph_def_uses_build(self, monkeypatch):
        calls = {"factory": 0, "build": 0}

        def fake_factory(*, pool, record_sink=None):
            calls["factory"] += 1
            return _trivial_graph()

        def fake_build(spec, *, pool, record_sink=None):
            calls["build"] += 1
            return _trivial_graph()

        async def fake_load(pool, slug):
            return {"name": slug, "entry": "noop", "nodes": [{"id": "noop", "atom": "x"}], "edges": []}

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)
        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)
        # The runner does `from services.pipeline_templates import load_active_graph_def`
        # lazily inside run(); patch it on that module so the lazy import resolves
        # to the fake at call time.
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "load_active_graph_def", fake_load)

        summary = await _runner(True).run("canonical_blog", {"task_id": "t2"})
        assert summary.ok is True
        assert calls["build"] == 1
        assert calls["factory"] == 0

    async def test_flag_on_no_graph_def_falls_back_to_factory(self, monkeypatch):
        calls = {"factory": 0, "build": 0}

        def fake_factory(*, pool, record_sink=None):
            calls["factory"] += 1
            return _trivial_graph()

        def fake_build(spec, *, pool, record_sink=None):
            calls["build"] += 1
            return _trivial_graph()

        async def fake_load(pool, slug):
            return None  # no active graph_def row

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)
        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "load_active_graph_def", fake_load)

        summary = await _runner(True).run("canonical_blog", {"task_id": "t3"})
        assert summary.ok is True
        assert calls["factory"] == 1
        assert calls["build"] == 0
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/test_template_runner_graphdef_routing.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: `test_flag_on_with_graph_def_uses_build` FAILs (legacy code always uses the factory → `calls["build"] == 0`). The flag-off + fallback tests may already pass.

- [ ] **Step 3: Apply the routing change**

In `services/template_runner.py::TemplateRunner.run`, replace this exact block (lines 709-722):

```python
        # Lazy import to avoid module-load cycle: pipeline_templates.__init__
        # imports adapters from here, here imports from there → cycle if
        # done at top level.
        from services.pipeline_templates import TEMPLATES

        factory = TEMPLATES.get(template_slug)
        if factory is None:
            raise KeyError(
                f"unknown template_slug={template_slug!r}; "
                f"registered={sorted(TEMPLATES)}"
            )

        records: list[TemplateRunRecord] = []
        graph: StateGraph = factory(pool=self._pool, record_sink=records)
```

with:

```python
        # Lazy import to avoid module-load cycle: pipeline_templates.__init__
        # imports adapters from here, here imports from there → cycle if
        # done at top level.
        from services.pipeline_templates import TEMPLATES, load_active_graph_def

        records: list[TemplateRunRecord] = []

        # Cutover seam (#355 Plan 4): prefer the DB-stored graph_def
        # (compiled by build_graph_from_spec) when the operator has enabled
        # it AND an active graph_def row exists for this slug; otherwise fall
        # back to the legacy Python TEMPLATES factory. Flag default False →
        # dormant until an operator flips pipeline_use_graph_def. Everything
        # downstream (partition / checkpointer / compile / ainvoke / outcome)
        # is identical for both paths.
        graph: StateGraph | None = None
        if self._site_config.get_bool("pipeline_use_graph_def", False):
            graph_def = await load_active_graph_def(self._pool, template_slug)
            if graph_def:
                from services.pipeline_architect import build_graph_from_spec
                logger.info(
                    "[template_runner] graph_def path for slug=%r "
                    "(pipeline_use_graph_def on)", template_slug,
                )
                graph = build_graph_from_spec(
                    graph_def, pool=self._pool, record_sink=records,
                )

        if graph is None:
            factory = TEMPLATES.get(template_slug)
            if factory is None:
                raise KeyError(
                    f"unknown template_slug={template_slug!r}; "
                    f"registered={sorted(TEMPLATES)}"
                )
            graph = factory(pool=self._pool, record_sink=records)
```

(`logger` is already module-level in `template_runner.py`. The `build_graph_from_spec` import is INSIDE the branch — lazy, only paid when the graph_def path runs.)

- [ ] **Step 4: Run, verify they pass**

Run: `"<venv-python>" -m pytest tests/unit/services/test_template_runner_graphdef_routing.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all 3 tests pass.

- [ ] **Step 5: Regression — the existing runner tests still pass**

Run: `"<venv-python>" -m pytest tests/unit/services/test_template_runner_state_partition.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass (the legacy path is unchanged when the flag is off, which is the default the existing tests construct).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/tests/unit/services/test_template_runner_graphdef_routing.py
git commit -m "feat(pipeline): route TemplateRunner through graph_def when enabled (#355)"
```

---

### Task 4: migration — seed the canonical_blog graph_def + the cutover flag

Seeds the spec into `pipeline_templates` (`active=true`, `version=2`) and the `pipeline_use_graph_def='false'` flag into `app_settings` (visible/tunable; Plan 5 flips it per-niche then globally). Idempotent. The migration imports the pure-data spec module (no heavy deps).

**Files:**

- Create: `src/cofounder_agent/services/migrations/<generated-timestamp>_seed_canonical_blog_graph_def.py` (generate with the helper)

- [ ] **Step 1: Generate the migration file**

Run (cwd = worktree root): `python scripts/new-migration.py "seed canonical blog graph_def"` (use the venv python). Note the printed path.

- [ ] **Step 2: Replace the generated file's body**

```python
"""Migration: seed canonical_blog graph_def + pipeline_use_graph_def flag

ISSUE: Glad-Labs/poindexter#355 (atom-cutover Plan 4)

Seeds the static graph_def spec for canonical_blog into pipeline_templates
(active=true, version=2) — the 13 coarse stages as stage.* nodes + the qa.*
rail atoms replacing the monolithic cross_model_qa stage. Also seeds
app_settings.pipeline_use_graph_def='false' — the master cutover flag.

DORMANT on prod: with the flag false, TemplateRunner ignores the seeded
graph_def and keeps running the legacy Python TEMPLATES factory. Plan 5
flips the flag (per-niche canary, then globally) and then deletes the
legacy factory.

Idempotent: ON CONFLICT (slug) DO UPDATE for the template row, ON CONFLICT
(key) DO NOTHING for the flag (keeps an operator-tuned value).
"""

from __future__ import annotations

import json
import logging

from services.pipeline_templates.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    payload = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pipeline_templates
              (slug, name, description, version, active, graph_def, created_by)
            VALUES ('canonical_blog', 'Canonical Blog',
                    'Atom-composed canonical_blog pipeline (#355)',
                    2, true, $1::jsonb, 'migration')
            ON CONFLICT (slug) DO UPDATE
              SET graph_def   = EXCLUDED.graph_def,
                  name        = EXCLUDED.name,
                  description = EXCLUDED.description,
                  version     = EXCLUDED.version,
                  active      = EXCLUDED.active,
                  updated_at  = NOW()
            """,
            payload,
        )
        # Master cutover flag — default false so the seeded graph_def stays
        # dormant until Plan 5 flips it. ON CONFLICT keeps an operator value.
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO NOTHING",
            "pipeline_use_graph_def", "false",
        )
        logger.info("Migration seed_canonical_blog_graph_def: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        # Drop the seeded template row + the flag (only if still false).
        await conn.execute(
            "DELETE FROM pipeline_templates WHERE slug = 'canonical_blog' AND created_by = 'migration'"
        )
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'pipeline_use_graph_def' AND value = 'false'"
        )
        logger.info("Migration seed_canonical_blog_graph_def down: reverted")
```

- [ ] **Step 3: Lint the migration (static)**

Run (cwd = worktree root): `"<venv-python>" scripts/ci/migrations_lint.py`
Expected: exits 0.

- [ ] **Step 4: Sanity-check the spec is importable + serializable (no live DB needed)**

Run (cwd = `src/cofounder_agent`):
`"<venv-python>" -c "import json; from services.pipeline_templates.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as s; print('nodes', len(s['nodes'])); json.dumps(s)" > test_out.txt 2>&1` then read `test_out.txt`.
Expected: prints `nodes 18` (13 stage._ + 5 qa._), no exception. (The migration's fresh-DB application is validated by the `migrations-smoke` CI job.)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/<generated-timestamp>_seed_canonical_blog_graph_def.py
git commit -m "feat(pipeline): seed canonical_blog graph_def + cutover flag (#355)"
```

---

## Self-review notes

- **Spec coverage:** implements the spec's "Cutover mechanism (flag-gated, DB-config)" steps 1–2 (author + seed the graph_def; runner prefers it when the flag is on + an active row exists, else the legacy factory). Step 3 (canary, flip the default, DELETE the legacy factory) is Plan 5. D1 (cut over to atoms), D2 (static graph_def in DB), D6 (everything DB-configurable) honored. **Flag default FALSE → the live pipeline is unchanged on merge** (the seeded graph_def is dormant).
- **Surgical + reversible:** the runner change is one added branch BEFORE the unchanged legacy lookup; every downstream step (`_partition_state_and_services`, checkpointer, `compile`, `ainvoke`, `ok`/`halted_at`, `capability_outcomes.record_run`, `TemplateRunSummary`) is identical for both paths because `build_graph_from_spec` returns the same `StateGraph(PipelineState)` shape a factory does. `load_active_graph_def` is best-effort (read failure → `None` → legacy fallback), so a malformed/missing row can never break a run.
- **Validation before the live path:** Task 1 proves `CANONICAL_BLOG_GRAPH_DEF` passes Plan-1's `_validate_spec` AND compiles via `build_graph_from_spec(...).compile()` — every `stage.*` + `qa.*` node resolves through the registry. This is the safety net the spec's "a pipeline is a JSON file" relies on.
- **Sequential rails (documented choice):** the qa.\* rails run as a linear chain in the graph_def (not parallel fan-out) for cutover robustness on the live runtime path; they still each append to the `operator.add`-reduced `qa_rail_reviews` channel and `qa.aggregate` reads the merged list. Parallel fan-out is a later optimization (the rails are `parallelizable=True`).
- **Type consistency:** `load_active_graph_def(pool, slug: str) -> dict | None`; the runner branch assigns `graph: StateGraph | None`; `build_graph_from_spec(spec, *, pool, record_sink) -> StateGraph`; flag read via `self._site_config.get_bool("pipeline_use_graph_def", False)`. The migration imports `CANONICAL_BLOG_GRAPH_DEF` (pure data) and `json.dumps` it into `graph_def jsonb`.
- **No placeholders:** every step has concrete code + run command + expected output. The only runtime-variable is the migration's generated timestamp filename (Task 4 Step 1 prints it).
- **Blast radius:** one new pure-data module, one new reader fn + a re-export in `pipeline_templates/__init__.py`, one added branch in `TemplateRunner.run`, one additive migration (idempotent, seeds the flag false). With the flag off (default), production behavior is byte-identical to before this PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

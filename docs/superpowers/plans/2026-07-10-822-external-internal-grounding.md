# External Topic Candidate Internal-Grounding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Softly penalize external topic candidates that have no first-party grounding in the operator's own corpus, so popular-but-ungrounded headlines stop winning batch slots on popularity alone (Glad-Labs/poindexter#822).

**Architecture:** A new `services/topic_grounding.py` runs one pgvector nearest-neighbor query (reusing #820's `embedding <=> vec` pattern) to find the operator's best content-bearing match for a candidate's already-computed embedding. `TopicBatchService._embed_and_pre_rank` multiplies an ungrounded external candidate's pre-rank score by a soft-penalty factor so the penalty propagates through the whole funnel (top-N cut, effective-score sort, LLM final-score). The match that justified a grounded win is persisted on the candidate row and threaded into the pipeline handoff metadata (data-only; no writer-prompt consumer in this plan).

**Tech Stack:** Python 3.13, asyncpg, pgvector, FastAPI service layer, pytest (`pytest.mark.asyncio(loop_scope="session")`), Grafana (postgres datasource).

**Spec:** `docs/superpowers/specs/2026-07-10-822-external-internal-grounding-design.md`

## Global Constraints

- **Async-everywhere.** All DB access is `async`; never block the event loop.
- **Fail-open for grounding** (deliberate exception to fail-loud): any query error, empty vector, or empty corpus returns `grounded=True` — infra trouble must never penalize a candidate or sink the sweep. Mirrors the dedup/empty-batch guards in `run_sweep`.
- **DB-first config.** All four tunables live in `services/settings_defaults.py` (`DEFAULTS` value + METADATA entry), NEVER in a migration file. Read via `SiteConfig` getters.
- **Grounding corpus is content-bearing only:** `post_history`→`posts`, `decision_log`/`memory_file`→`memory`, `claude_session`→`claude_sessions`. `audit_event`/`brain_knowledge` are intentionally excluded so a status row can never manufacture grounding.
- **Reuse existing patterns:** pgvector passed in text form `"[" + ",".join(...) + "]"`; test pools stubbed with the `_FakeConn`/`_FakeAcquireCtx`/`_FakePool` shape from `test_internal_rag_source.py`.
- **Worktree discipline:** work in the worktree at `.claude/worktrees/zealous-cohen-92c6da` on branch `claude/rag-engine-internal-sources-ade3f1`. NEVER `cd` to the main checkout (`C:\Users\mattm\glad-labs-website`) — it sits on `main`. Commit to the feature branch only.
- **Before running any test:** the worktree venv must be installed — from `<worktree>/src/cofounder_agent` run `poetry install --no-root` once. An un-installed worktree venv silently falls through to stray global packages and produces fake failures.
- **Commits:** conventional-commit messages, linear history, frequent commits. End each message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Settings defaults for the four grounding tunables

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (DEFAULTS block near line 548; METADATA block near line 2085)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: four `app_settings` keys readable via `SiteConfig`: `niche_external_grounding_enabled` (bool, default `true`), `niche_external_grounding_source_kinds` (csv, default `post_history,decision_log,memory_file,claude_session`), `niche_external_grounding_threshold` (float, default `0.55`), `niche_external_grounding_penalty_factor` (float, default `0.6`).

- [ ] **Step 1: Write the failing test**

Add to `test_settings_defaults.py`:

```python
def test_external_grounding_defaults_present():
    from services.settings_defaults import DEFAULTS, METADATA

    assert DEFAULTS["niche_external_grounding_enabled"] == "true"
    assert (
        DEFAULTS["niche_external_grounding_source_kinds"]
        == "post_history,decision_log,memory_file,claude_session"
    )
    assert DEFAULTS["niche_external_grounding_threshold"] == "0.55"
    assert DEFAULTS["niche_external_grounding_penalty_factor"] == "0.6"

    for key, vtype in [
        ("niche_external_grounding_enabled", "boolean"),
        ("niche_external_grounding_source_kinds", "csv"),
        ("niche_external_grounding_threshold", "float"),
        ("niche_external_grounding_penalty_factor", "float"),
    ]:
        assert METADATA[key]["owner"] == "topic_grounding"
        assert METADATA[key]["value_type"] == vtype
```

Note: confirm the METADATA dict is named `METADATA` and is importable (it is referenced at module scope near line 2085). If the module exposes it under a different name, match that name in both the test and Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_external_grounding_defaults_present -v`
Expected: FAIL with `KeyError: 'niche_external_grounding_enabled'`.

- [ ] **Step 3: Add the defaults**

In the `DEFAULTS` dict, immediately after the `niche_internal_rag_kind_weights` block (ends ~line 548), add:

```python
    # External-candidate internal grounding (poindexter#822): softly
    # penalize a popular external topic that has no first-party material in
    # our own corpus, so it can't win a batch slot on popularity alone.
    'niche_external_grounding_enabled': 'true',
    # Content-bearing corpus only — a status/ops row must never manufacture
    # grounding. Kinds map to embeddings.source_table via topic_grounding.
    'niche_external_grounding_source_kinds': (
        'post_history,decision_log,memory_file,claude_session'
    ),
    # Cosine similarity >= this counts as grounded. PROVISIONAL — calibrate
    # from a real sweep's logged _grounding distribution before trusting it.
    'niche_external_grounding_threshold': '0.55',
    # Soft-penalty multiplier applied to an ungrounded external candidate's
    # pre-rank score (1.0 = no penalty).
    'niche_external_grounding_penalty_factor': '0.6',
```

In the METADATA dict, after the `niche_internal_rag_kind_weights` metadata entry (~line 2085), add:

```python
    'niche_external_grounding_enabled': {
        'owner': 'topic_grounding', 'value_type': 'boolean',
    },
    'niche_external_grounding_source_kinds': {
        'owner': 'topic_grounding', 'value_type': 'csv',
    },
    'niche_external_grounding_threshold': {
        'owner': 'topic_grounding', 'value_type': 'float',
    },
    'niche_external_grounding_penalty_factor': {
        'owner': 'topic_grounding', 'value_type': 'float',
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_external_grounding_defaults_present -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(topics): seed external-grounding settings defaults (#822)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `services/topic_grounding.py` — the grounding unit

**Files:**

- Create: `src/cofounder_agent/services/topic_grounding.py`
- Test: `src/cofounder_agent/tests/unit/services/test_topic_grounding.py`

**Interfaces:**

- Consumes: `SiteConfig` (reads `niche_external_grounding_source_kinds`, `niche_external_grounding_threshold`); an asyncpg-style `pool` whose `.acquire()` yields a conn with `.fetchrow(query, *args)`.
- Produces:
  - `@dataclass GroundingMatch(source_table: str, source_id: str, preview: str, similarity: float)`
  - `@dataclass GroundingResult(similarity: float | None, grounded: bool, match: GroundingMatch | None)`
  - `async def internal_grounding(pool, candidate_vec: list[float], *, site_config: SiteConfig) -> GroundingResult`

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/test_topic_grounding.py`:

```python
"""Tests for topic_grounding — internal-corpus grounding of external topics."""

import pytest

from services.site_config import SiteConfig
from services.topic_grounding import (
    GroundingMatch,
    GroundingResult,
    internal_grounding,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeConn:
    def __init__(self, row, *, raise_on_fetch=False):
        self._row = row
        self._raise = raise_on_fetch
        self.last_args = None

    async def fetchrow(self, query, *args):
        self.last_args = (query, args)
        if self._raise:
            raise RuntimeError("boom")
        return self._row


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, row=None, *, raise_on_fetch=False):
        self.conn = _FakeConn(row, raise_on_fetch=raise_on_fetch)
        self.acquired = False

    def acquire(self):
        self.acquired = True
        return _FakeAcquireCtx(self.conn)


def _cfg(**over):
    base = {
        "niche_external_grounding_source_kinds": "post_history,claude_session",
        "niche_external_grounding_threshold": "0.55",
    }
    base.update(over)
    return SiteConfig(initial_config=base)


async def test_grounded_when_similarity_above_threshold():
    row = {
        "source_table": "posts", "source_id": "p1",
        "text_preview": "we shipped X", "similarity": 0.80,
    }
    pool = _FakePool(row)
    res = await internal_grounding(pool, [0.1, 0.2], site_config=_cfg())
    assert isinstance(res, GroundingResult)
    assert res.grounded is True
    assert res.similarity == pytest.approx(0.80)
    assert isinstance(res.match, GroundingMatch)
    assert res.match.source_table == "posts"
    assert res.match.source_id == "p1"


async def test_ungrounded_when_similarity_below_threshold():
    row = {
        "source_table": "posts", "source_id": "p1",
        "text_preview": "unrelated", "similarity": 0.20,
    }
    res = await internal_grounding(_FakePool(row), [0.1], site_config=_cfg())
    assert res.grounded is False
    assert res.similarity == pytest.approx(0.20)
    assert res.match is not None  # match is still returned for observability


async def test_empty_vector_fails_open_without_query():
    pool = _FakePool(row=None)
    res = await internal_grounding(pool, [], site_config=_cfg())
    assert res.grounded is True
    assert res.similarity is None
    assert res.match is None
    assert pool.acquired is False  # never touched the DB


async def test_query_error_fails_open():
    pool = _FakePool(row=None, raise_on_fetch=True)
    res = await internal_grounding(pool, [0.1], site_config=_cfg())
    assert res.grounded is True
    assert res.similarity is None
    assert res.match is None


async def test_empty_corpus_fails_open():
    # No matching rows (fresh install) -> fetchrow returns None -> grounded.
    res = await internal_grounding(_FakePool(row=None), [0.1], site_config=_cfg())
    assert res.grounded is True
    assert res.similarity is None
    assert res.match is None


async def test_unknown_source_kinds_are_skipped_and_fail_open():
    # Only ops kinds configured -> they map to nothing -> empty table list.
    cfg = _cfg(niche_external_grounding_source_kinds="audit_event,brain_knowledge")
    pool = _FakePool(row=None)
    res = await internal_grounding(pool, [0.1], site_config=cfg)
    assert res.grounded is True
    assert pool.acquired is False  # no valid tables -> no query


async def test_query_targets_only_configured_content_tables():
    row = {
        "source_table": "claude_sessions", "source_id": "s1",
        "text_preview": "session", "similarity": 0.9,
    }
    pool = _FakePool(row)
    await internal_grounding(pool, [0.1], site_config=_cfg())
    _query, args = pool.conn.last_args
    # args[1] is the text[] of source_tables passed to ANY($2)
    assert set(args[1]) == {"posts", "claude_sessions"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_grounding.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.topic_grounding'`.

- [ ] **Step 3: Write the module**

Create `src/cofounder_agent/services/topic_grounding.py`:

```python
"""Internal-corpus grounding for external topic candidates (poindexter#822).

An external topic (hackernews / devto / web_search) is a popularity signal.
Before it wins a batch slot, ask: does the operator's OWN corpus already have
material on this? If not, we'd be paraphrasing someone else's reporting — the
zero-new-value rewrite the system positions against. This module answers that
question with a single pgvector nearest-neighbor query against the
content-bearing slice of the embeddings table, reusing the same
``embedding <=> vec`` primitive internal_rag_source uses.

Fail-open by construction: any error, empty vector, or empty corpus returns
``grounded=True`` so infra trouble never penalizes a candidate and never sinks
the sweep (same posture as the dedup / empty-batch guards in run_sweep).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

# source_kind -> embeddings.source_table. Content-bearing kinds ONLY — the
# ops-noise kinds (audit_event->audit, brain_knowledge->brain) are
# deliberately absent so a status row can't manufacture grounding. Subset of
# internal_rag_source's table_map, kept local so the two concerns stay
# independent.
_KIND_TO_TABLE: dict[str, str] = {
    "post_history": "posts",
    "decision_log": "memory",
    "memory_file": "memory",
    "claude_session": "claude_sessions",
}

_PREVIEW_MAX_CHARS = 500


@dataclass
class GroundingMatch:
    source_table: str
    source_id: str
    preview: str
    similarity: float


@dataclass
class GroundingResult:
    similarity: float | None   # best cosine similarity; None on fail-open
    grounded: bool             # similarity >= threshold (True on fail-open)
    match: GroundingMatch | None


def _resolve_source_tables(site_config: SiteConfig) -> list[str]:
    raw = site_config.get(
        "niche_external_grounding_source_kinds",
        "post_history,decision_log,memory_file,claude_session",
    ) or ""
    tables: list[str] = []
    for kind in (k.strip() for k in raw.split(",")):
        if not kind:
            continue
        table = _KIND_TO_TABLE.get(kind)
        if table is None:
            logger.warning(
                "[topic_grounding] unsupported source_kind %r — skipping "
                "(content-bearing kinds only)", kind,
            )
            continue
        if table not in tables:
            tables.append(table)
    return tables


async def internal_grounding(
    pool, candidate_vec: list[float], *, site_config: SiteConfig,
) -> GroundingResult:
    """Return the best internal-corpus match for ``candidate_vec`` and whether
    it clears ``niche_external_grounding_threshold``.

    ``candidate_vec`` is the embedding already computed for goal pre-ranking —
    no second embed call.
    """
    threshold = site_config.get_float("niche_external_grounding_threshold", 0.55)

    if not candidate_vec:
        return GroundingResult(similarity=None, grounded=True, match=None)

    tables = _resolve_source_tables(site_config)
    if not tables:
        return GroundingResult(similarity=None, grounded=True, match=None)

    try:
        # pgvector has no asyncpg codec — pass the vector in its text form
        # (pattern: internal_rag_source._fetch_recent_snippets / embeddings_db).
        vec_str = "[" + ",".join(str(v) for v in candidate_vec) + "]"
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT source_table, source_id, text_preview,
                       1 - (embedding <=> $1::vector) AS similarity
                  FROM embeddings
                 WHERE source_table = ANY($2::text[])
                 ORDER BY embedding <=> $1::vector
                 LIMIT 1
                """,
                vec_str, tables,
            )
    except Exception:
        logger.warning(
            "[topic_grounding] grounding query failed — fail-open (grounded)",
            exc_info=True,
        )
        return GroundingResult(similarity=None, grounded=True, match=None)

    if row is None:
        # Empty corpus (fresh install): nothing to ground against. Fail-open
        # so a brand-new operator isn't penalized into an empty batch.
        return GroundingResult(similarity=None, grounded=True, match=None)

    sim = float(row["similarity"])
    match = GroundingMatch(
        source_table=row["source_table"],
        source_id=str(row["source_id"]),
        preview=(row["text_preview"] or "")[:_PREVIEW_MAX_CHARS],
        similarity=sim,
    )
    return GroundingResult(similarity=sim, grounded=sim >= threshold, match=match)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_grounding.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_grounding.py src/cofounder_agent/tests/unit/services/test_topic_grounding.py
git commit -m "feat(topics): internal_grounding query unit for external candidates (#822)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire the soft penalty into `_embed_and_pre_rank`

**Files:**

- Modify: `src/cofounder_agent/services/topic_ranking.py` (the `ScoredCandidate` dataclass, ~line 162)
- Modify: `src/cofounder_agent/services/topic_batch_service.py` (`_embed_and_pre_rank`, ~lines 680-754)
- Test: `src/cofounder_agent/tests/unit/services/test_topic_batch_service.py`

**Interfaces:**

- Consumes: `internal_grounding`, `GroundingMatch` from Task 2; `SiteConfig` getters `get_bool`/`get_float`.
- Produces: `ScoredCandidate.grounding_match: GroundingMatch | None = None`; external candidates carry a `grounding_match`; `score_breakdown["_grounding"]` holds the similarity; an aggregated `external_topic_ungrounded` finding per penalized sweep.

- [ ] **Step 1: Write the failing tests**

Add to `test_topic_batch_service.py` (match the file's existing import style and any `TopicBatchService` fixture; if it constructs the service as `TopicBatchService(pool, site_config=...)`, mirror that). These tests monkeypatch `internal_grounding` at the point of use — `services.topic_batch_service.internal_grounding` — and `services.topic_ranking.embed_text` so no real DB/Ollama is needed:

```python
async def test_ungrounded_external_gets_penalty(monkeypatch):
    from services import topic_batch_service as tbs
    from services.topic_grounding import GroundingResult
    from services.site_config import SiteConfig

    async def fake_embed(text, *, site_config):
        return [0.1, 0.2, 0.3]

    async def fake_grounding(pool, vec, *, site_config):
        return GroundingResult(similarity=0.1, grounded=False, match=None)

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)

    cfg = SiteConfig(initial_config={
        "niche_external_grounding_enabled": "true",
        "niche_external_grounding_penalty_factor": "0.5",
        "niche_top_n_per_pool": "5",
    })
    svc = tbs.TopicBatchService(_pool_stub(), site_config=cfg)
    # A niche with one goal so weighted_cosine_score returns a positive score.
    monkeypatch.setattr(svc, "_niche_svc", _niche_svc_with_one_goal())

    ext, _int = await svc._embed_and_pre_rank(
        _niche(), [{"data": {"id": "e1", "title": "Popular Thing", "summary": "s"}}], [],
    )
    assert len(ext) == 1
    # score was multiplied by 0.5; _grounding similarity recorded
    assert ext[0].score_breakdown["_grounding"] == pytest.approx(0.1)
    assert ext[0].grounding_match is None


async def test_grounded_external_no_penalty_and_match_stashed(monkeypatch):
    from services import topic_batch_service as tbs
    from services.topic_grounding import GroundingMatch, GroundingResult
    from services.site_config import SiteConfig

    async def fake_embed(text, *, site_config):
        return [0.1, 0.2, 0.3]

    match = GroundingMatch("posts", "p1", "we shipped X", 0.9)

    async def fake_grounding(pool, vec, *, site_config):
        return GroundingResult(similarity=0.9, grounded=True, match=match)

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)

    cfg = SiteConfig(initial_config={
        "niche_external_grounding_enabled": "true",
        "niche_external_grounding_penalty_factor": "0.5",
        "niche_top_n_per_pool": "5",
    })
    svc = tbs.TopicBatchService(_pool_stub(), site_config=cfg)
    monkeypatch.setattr(svc, "_niche_svc", _niche_svc_with_one_goal())

    ext, _int = await svc._embed_and_pre_rank(
        _niche(), [{"data": {"id": "e1", "title": "Grounded Thing", "summary": "s"}}], [],
    )
    assert ext[0].grounding_match is match
    assert ext[0].score_breakdown["_grounding"] == pytest.approx(0.9)


async def test_grounding_disabled_is_noop(monkeypatch):
    from services import topic_batch_service as tbs
    from services.site_config import SiteConfig

    async def fake_embed(text, *, site_config):
        return [0.1, 0.2, 0.3]

    called = {"n": 0}

    async def fake_grounding(pool, vec, *, site_config):
        called["n"] += 1
        raise AssertionError("must not be called when disabled")

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)

    cfg = SiteConfig(initial_config={
        "niche_external_grounding_enabled": "false",
        "niche_top_n_per_pool": "5",
    })
    svc = tbs.TopicBatchService(_pool_stub(), site_config=cfg)
    monkeypatch.setattr(svc, "_niche_svc", _niche_svc_with_one_goal())

    ext, _int = await svc._embed_and_pre_rank(
        _niche(), [{"data": {"id": "e1", "title": "Thing", "summary": "s"}}], [],
    )
    assert called["n"] == 0
    assert "_grounding" not in ext[0].score_breakdown


async def test_internal_candidates_never_grounding_penalized(monkeypatch):
    from services import topic_batch_service as tbs
    from services.topic_grounding import GroundingResult
    from services.site_config import SiteConfig

    async def fake_embed(text, *, site_config):
        return [0.1, 0.2, 0.3]

    async def fake_grounding(pool, vec, *, site_config):
        raise AssertionError("grounding must not run for internal candidates")

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)

    cfg = SiteConfig(initial_config={
        "niche_external_grounding_enabled": "true",
        "niche_top_n_per_pool": "5",
    })
    svc = tbs.TopicBatchService(_pool_stub(), site_config=cfg)
    monkeypatch.setattr(svc, "_niche_svc", _niche_svc_with_one_goal())

    # one internal candidate (InternalCandidate-shaped dict with distilled_*)
    _ext, intr = await svc._embed_and_pre_rank(
        _niche(), [], [{"data": _internal_candidate("Our Retro", "why we did it")}],
    )
    assert intr and "_grounding" not in intr[0].score_breakdown
```

Reuse or add the small local helpers `_pool_stub()`, `_niche()`, `_niche_svc_with_one_goal()`, `_internal_candidate(...)` following whatever fixtures the file already defines for `_embed_and_pre_rank` tests (there are existing pre-rank tests — model these on them; `_niche_svc_with_one_goal` returns an object whose `get_goals(niche_id)` async-returns a one-element list of a `NicheGoal(goal_type=..., weight_pct=100)`, and `goal_vector_for` is monkeypatched or the goal_type resolves through `_resolve_goal_descriptions`). If the existing tests already have a `_embed_and_pre_rank` harness, extend that instead of duplicating.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_batch_service.py -k grounding -v`
Expected: FAIL — `ScoredCandidate` has no `grounding_match`, and `topic_batch_service` has no `internal_grounding` symbol.

- [ ] **Step 3a: Add the field to `ScoredCandidate`**

In `src/cofounder_agent/services/topic_ranking.py`, extend the dataclass (~line 162). Import `GroundingMatch` lazily inside a `TYPE_CHECKING` block to avoid an import cycle (topic_grounding imports nothing from topic_ranking, so a direct import is also safe — prefer the direct import for simplicity):

```python
from services.topic_grounding import GroundingMatch


@dataclass
class ScoredCandidate:
    id: str
    title: str
    summary: str | None
    embedding_score: float
    llm_score: float | None = None
    score_breakdown: dict[str, float] | None = None
    grounding_match: GroundingMatch | None = None
```

- [ ] **Step 3b: Wire grounding into `_embed_and_pre_rank`**

In `src/cofounder_agent/services/topic_batch_service.py`, add a module-level import near the other service imports:

```python
from services.topic_grounding import internal_grounding
```

Change the `score_one` closure to also return the vec, and read the grounding config once before the loops. Replace the closure + external loop (~lines 680-716) so it reads:

```python
        grounding_enabled = self._site_config.get_bool(
            "niche_external_grounding_enabled", True,
        )
        penalty_factor = self._site_config.get_float(
            "niche_external_grounding_penalty_factor", 0.6,
        )

        async def score_one(
            text: str, decay: float,
        ) -> tuple[float, dict[str, float], list[float]]:
            if not text or not text.strip():
                return 0.0, {g.goal_type: 0.0 for g in goals}, []
            vec = await embed_text(text, site_config=self._site_config)
            raw, breakdown = weighted_cosine_score(vec, goal_vecs, goals)
            return apply_decay(score=raw, decay_factor=decay), breakdown, vec

        penalized: list[tuple[str, float]] = []  # (title, similarity)
        ext_scored: list[ScoredCandidate] = []
        for item in external:
            if isinstance(item, dict) and "row" in item:
                row = item["row"]
            elif isinstance(item, dict) and "data" in item:
                row = item["data"]
            else:
                row = item
            assert row is not None
            text = (row.get("title") or "") + " " + (row.get("summary") or "")
            decay = item.get("decay_factor", 1.0) if isinstance(item, dict) else 1.0
            score, breakdown, vec = await score_one(text, decay)

            grounding_match = None
            if grounding_enabled and vec:
                g = await internal_grounding(
                    self._pool, vec, site_config=self._site_config,
                )
                if not g.grounded:
                    score *= penalty_factor
                    penalized.append(
                        (row.get("title") or "Untitled", g.similarity or 0.0),
                    )
                breakdown["_grounding"] = (
                    g.similarity if g.similarity is not None else 1.0
                )
                grounding_match = g.match

            ext_scored.append(
                ScoredCandidate(
                    id=str(row.get("id") or row.get("source_ref") or text[:40]),
                    title=row.get("title") or "Untitled",
                    summary=row.get("summary"),
                    embedding_score=score,
                    score_breakdown=breakdown,
                    grounding_match=grounding_match,
                )
            )
```

Update the internal loop's `score_one` unpack (~line 736) — it now returns three values; the vec is ignored:

```python
            score, breakdown, _vec = await score_one(text, decay)
```

Finally, immediately before the sort (`ext_scored.sort(...)`, ~line 749), emit the aggregated finding:

```python
        if penalized:
            emit_finding(
                source="topic_batch_service",
                kind="external_topic_ungrounded",
                title=(
                    f"{len(penalized)} external candidate(s) penalized for "
                    f"missing internal grounding (niche {niche.slug})"
                ),
                body="\n".join(
                    f"- {title!r}: similarity={sim:.3f}"
                    for title, sim in penalized
                ),
                severity="info",
                dedup_key=f"external-grounding:{niche.slug}",
                extra={
                    "stage": "pre_rank",
                    "niche_slug": niche.slug,
                    "penalized": [
                        {"title": t[:200], "similarity": s}
                        for t, s in penalized
                    ],
                },
            )
```

(`emit_finding` is already imported at line 50; `apply_decay`, `weighted_cosine_score`, `embed_text` are already used in this method.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_batch_service.py -k grounding -v`
Expected: PASS. Then run the whole file to catch the 3-tuple unpack breaking any existing pre-rank test: `poetry run pytest tests/unit/services/test_topic_batch_service.py -q`. Fix any existing test that unpacked `score_one`'s old 2-tuple (search the test file for `score_one`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_ranking.py src/cofounder_agent/services/topic_batch_service.py src/cofounder_agent/tests/unit/services/test_topic_batch_service.py
git commit -m "feat(topics): soft-penalize ungrounded external candidates at pre-rank (#822)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Migration — `topic_candidates.grounding_ref` column

**Files:**

- Create: `src/cofounder_agent/services/migrations/<generated>_add_topic_candidates_grounding_ref.py`

**Interfaces:**

- Produces: nullable `topic_candidates.grounding_ref jsonb` column.

- [ ] **Step 1: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "add topic_candidates grounding_ref column"`
This creates a timestamped stub `services/migrations/YYYYMMDD_HHMMSS_add_topic_candidates_grounding_ref.py` with an `async def up(pool)` skeleton.

- [ ] **Step 2: Fill in the migration body**

Replace the generated file's body with (keep the generated timestamp/filename):

```python
"""Add topic_candidates.grounding_ref — the internal-corpus match that
justified a grounded external topic candidate (poindexter#822). Nullable:
internal candidates and ungrounded/fail-open external candidates carry NULL.
No-op on fresh installs where the baseline already includes the column;
real add on prod. stdlib-only so migrations-smoke applies it without a boot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE topic_candidates "
            "ADD COLUMN IF NOT EXISTS grounding_ref jsonb"
        )
    logger.info("add_topic_candidates_grounding_ref up: column ready")
```

- [ ] **Step 3: Lint + smoke the migration**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_lint.py && python scripts/ci/migrations_smoke.py`
Expected: both pass (lint: no collisions / valid runner interface; smoke: applies cleanly against a fresh DB).

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/migrations/
git commit -m "feat(db): add topic_candidates.grounding_ref column (#822)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Persist the match + thread it into the handoff (data-only)

**Files:**

- Modify: `src/cofounder_agent/services/topic_batch_service.py` (`_write_batch` external INSERT ~line 815; `CandidateView` dataclass ~line 72; `show_batch` external loop ~line 882; `_handoff_to_pipeline` metadata ~line 1173)
- Test: `src/cofounder_agent/tests/unit/services/test_topic_batch_service.py`

**Interfaces:**

- Consumes: `ScoredCandidate.grounding_match` (Task 3); the `grounding_ref` column (Task 4).
- Produces: `CandidateView.grounding_ref: dict | None`; `pipeline_versions.stage_data.metadata.internal_grounding` populated on the winning task.

- [ ] **Step 1: Write the failing test**

Add to `test_topic_batch_service.py` a test that drives the handoff metadata. Model it on the existing `_handoff_to_pipeline` / resolve tests in the file (they already stub the pool insert). The assertion:

```python
async def test_handoff_threads_internal_grounding_metadata(monkeypatch):
    # A winning external CandidateView carrying grounding_ref should thread it
    # into stage_data.metadata.internal_grounding. Reuse the file's existing
    # handoff harness that captures the pipeline_tasks / pipeline_versions
    # insert payload; assert on the metadata dict.
    from services.topic_batch_service import CandidateView

    winner = CandidateView(
        id="e1", kind="external", title="Grounded Thing", summary="angle",
        score=1.0, decay_factor=1.0, effective_score=1.0, rank_in_batch=1,
        operator_rank=1, operator_edited_topic=None, operator_edited_angle=None,
        score_breakdown={"_grounding": 0.9},
        grounding_ref={"source_table": "posts", "source_id": "p1",
                       "preview": "we shipped X", "similarity": 0.9},
    )
    captured = await _run_handoff_capture(winner)  # helper per existing tests
    assert captured["metadata"]["internal_grounding"] == {
        "source_table": "posts", "source_id": "p1",
        "preview": "we shipped X", "similarity": 0.9,
    }


async def test_handoff_internal_grounding_none_when_absent(monkeypatch):
    from services.topic_batch_service import CandidateView
    winner = CandidateView(
        id="i1", kind="internal", title="Our Retro", summary="why",
        score=1.0, decay_factor=1.0, effective_score=1.0, rank_in_batch=1,
        operator_rank=1, operator_edited_topic=None, operator_edited_angle=None,
        score_breakdown={}, grounding_ref=None,
    )
    captured = await _run_handoff_capture(winner)
    assert captured["metadata"]["internal_grounding"] is None
```

Implement `_run_handoff_capture` by following the existing resolve/handoff test that captures the insert (search the file for `_handoff_to_pipeline` or `stage_data` in tests). If no such harness exists yet, build a minimal one: a `_FakePool` whose conn `.fetchrow`/`.execute` record the `stage_data` argument, then call `svc._handoff_to_pipeline(winner, _niche(), uuid4())` and return the parsed `stage_data`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_batch_service.py -k "handoff and grounding or internal_grounding" -v`
Expected: FAIL — `CandidateView.__init__` got an unexpected keyword `grounding_ref`.

- [ ] **Step 3a: Add the `CandidateView` field**

In `topic_batch_service.py`, extend the `CandidateView` dataclass (~line 92, after `score_breakdown`):

```python
    score_breakdown: dict[str, float]
    grounding_ref: dict | None = None
```

- [ ] **Step 3b: Persist in `_write_batch`**

Add `from dataclasses import asdict` to the module imports if not present. In the external-candidate INSERT (~lines 815-831), add the `grounding_ref` column + a parameter:

```python
                        await conn.execute(
                            """
                            INSERT INTO topic_candidates
                              (batch_id, niche_id, source_name, source_ref, title, summary,
                               score, score_breakdown, rank_in_batch, decay_factor,
                               grounding_ref)
                            VALUES ($1, $2, 'external', $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb)
                            """,
                            batch_row["id"],
                            niche.id,
                            c.id,
                            c.title,
                            c.summary,
                            c.llm_score or 0,
                            _json(c.score_breakdown or {}),
                            rank_in_batch,
                            1.0,
                            _json(asdict(c.grounding_match)) if c.grounding_match else None,
                        )
```

- [ ] **Step 3c: Read it back in `show_batch`**

In the `show_batch` external-rows loop (~line 882), populate the new field:

```python
                    score_breakdown=_loads(r["score_breakdown"]) or {},
                    grounding_ref=_loads(r["grounding_ref"]),
```

(The internal-rows loop leaves `grounding_ref` unset → defaults to `None`.)

- [ ] **Step 3d: Thread into the handoff metadata**

In `_handoff_to_pipeline`, add one key to the `stage_data["metadata"]` dict (~line 1185, after `"niche_slug"`):

```python
                "niche_slug": niche.slug,
                # The internal match that justified a grounded external topic
                # (poindexter#822). Data-only: a follow-up wires the writer
                # prompt to open on it. None for internal / ungrounded winners.
                "internal_grounding": winner.grounding_ref,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_batch_service.py -q`
Expected: PASS (new handoff tests + no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_batch_service.py src/cofounder_agent/tests/unit/services/test_topic_batch_service.py
git commit -m "feat(topics): persist + thread internal-grounding match to handoff (#822)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Grafana panel — external candidates penalized for missing grounding

**Files:**

- Modify: the Pipeline dashboard JSON under `infrastructure/grafana/dashboards/` (the file whose `title` is "Pipeline" / uid contains `pipeline`)

**Interfaces:**

- Consumes: `audit_log` rows where `event_type='finding'` and `details->>'kind'='external_topic_ungrounded'` (emitted in Task 3).

- [ ] **Step 1: Locate the board + a sibling finding panel**

Run: `grep -rl '"title": "Pipeline"' infrastructure/grafana/dashboards/` to find the file. Open it and find an existing panel that queries `audit_log` for `event_type='finding'` (the Findings board — `infrastructure/grafana/dashboards/findings.json` — has these; copy its `datasource` object and panel shape). Note the postgres datasource `uid`.

- [ ] **Step 2: Add the panel**

Append a new panel object to the Pipeline board's `panels` array with a fresh unique `id` and a `gridPos` that doesn't overlap (put it at the bottom — max existing `y` + height). Use the postgres datasource `uid` copied in Step 1. Panel:

```json
{
  "type": "timeseries",
  "title": "External topics penalized — no internal grounding (#822)",
  "description": "Count of external topic candidates soft-penalized per sweep for lacking first-party corpus grounding. High/rising = a niche whose external feed has drifted from what we actually have experience in.",
  "datasource": { "type": "postgres", "uid": "<POSTGRES_UID_FROM_STEP_1>" },
  "fieldConfig": {
    "defaults": { "custom": { "drawStyle": "bars", "fillOpacity": 60 } },
    "overrides": []
  },
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": "<MAX_Y_PLUS_HEIGHT>" },
  "targets": [
    {
      "refId": "A",
      "format": "time_series",
      "rawSql": "SELECT date_trunc('hour', created_at) AS time, COUNT(*) AS penalized_candidates FROM audit_log WHERE event_type = 'finding' AND details->>'kind' = 'external_topic_ungrounded' AND $__timeFilter(created_at) GROUP BY 1 ORDER BY 1"
    }
  ]
}
```

Confirm the `audit_log` timestamp column is `created_at` (check a sibling finding panel's SQL in `findings.json`; if the board uses a different column name for the finding rows, match it).

- [ ] **Step 3: Validate the JSON**

Run: `python -c "import json,glob,sys; [json.load(open(f, encoding='utf-8')) for f in glob.glob('infrastructure/grafana/dashboards/*.json')]; print('all dashboards parse')"`
Expected: `all dashboards parse` (no `JSONDecodeError`).

- [ ] **Step 4: Commit**

```bash
git add infrastructure/grafana/dashboards/
git commit -m "feat(grafana): panel for external topics penalized without grounding (#822)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Docs — record the grounding signal in the discovery/RAG docs

**Files:**

- Modify: `docs/architecture/niches-and-rag-modes.md` (or the closest topic-discovery doc; if a dedicated topic-discovery doc exists, prefer it)

**Interfaces:** none (documentation).

- [ ] **Step 1: Add a section**

Add a short section documenting the external-grounding signal: what it does (soft-penalize ungrounded external candidates at pre-rank), the corpus (posts + memory + claude_sessions), the four `app_settings` and their defaults, the fail-open posture, the `external_topic_ungrounded` finding, and a pointer to the spec (`docs/superpowers/specs/2026-07-10-822-external-internal-grounding-design.md`). Note the `threshold=0.55` calibration caveat.

- [ ] **Step 2: Commit**

```bash
git add docs/architecture/niches-and-rag-modes.md
git commit -m "docs(topics): document external-candidate internal grounding (#822)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Full topic + settings test slice green: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_grounding.py tests/unit/services/test_topic_batch_service.py tests/unit/services/test_settings_defaults.py -q`
- [ ] Type check: `npm run type:check` (mypy clean on the changed files)
- [ ] Migration smoke green: `cd src/cofounder_agent && python scripts/ci/migrations_smoke.py`
- [ ] Update the draft PR #2266 body: check off the status boxes; flip the PR out of draft (`gh pr ready 2266`) once CI is green.
- [ ] Note the calibration follow-up on the PR: run one real sweep, read the `_grounding` distribution from `topic_candidates.score_breakdown`, and tune `niche_external_grounding_threshold` off the observed knee before relying on the default `0.55`.

## Self-Review notes (author)

- **Spec coverage:** grounding unit (Task 2) ✓; pre-rank multiplier + internal-untouched + finding (Task 3) ✓; migration (Task 4) ✓; persist + read-back + handoff data-only (Task 5) ✓; settings (Task 1) ✓; observability finding (Task 3) + panel (Task 6) ✓; calibration caveat (final verification) ✓; docs (Task 7) ✓.
- **Type consistency:** `GroundingResult(similarity, grounded, match)` and `GroundingMatch(source_table, source_id, preview, similarity)` are used identically in Tasks 2/3/5; `internal_grounding(pool, candidate_vec, *, site_config)` signature matches the monkeypatch in Task 3; `score_one` returns a 3-tuple everywhere it's unpacked.
- **Ordering:** Task 3 depends on Tasks 1+2; Task 5 depends on Tasks 3+4; Task 6 depends on Task 3's finding kind. Build in numeric order.

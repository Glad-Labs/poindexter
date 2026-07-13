# Dev.to Selective Syndication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. (subagent-driven-development is disabled in this repo — subagents bill at full API rates; execute inline.) Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the already-live `CrosspostToDevtoJob` so only posts in an operator-chosen niche allowlist scoring ≥ a configurable quality floor syndicate to Dev.to, instead of every published post.

**Architecture:** Change is confined to one job + two settings. The candidate query joins the existing `posts.metadata->>'pipeline_task_id' = pipeline_tasks.task_id` seam to reach `pipeline_tasks.niche_slug` and the newest `pipeline_versions.quality_score`, and filters on both. Posts with no task fall out of the inner join (fail-closed). An empty allowlist short-circuits to a no-op. Publish posture (`devto_publish_immediately=true`) is unchanged.

**Tech Stack:** Python 3.12 async, asyncpg, pytest / pytest-asyncio, existing `plugins.job.JobResult`, `services.site_config.SiteConfig`, `utils.findings.emit_finding`.

## Global Constraints

- New `app_settings` keys go in `services/settings_defaults.py` (`DEFAULTS` dict, idempotent boot seed) — **never** a migration file (`feedback_seed_data_in_baseline`).
- Settings are DB-configurable with sensible defaults; required-but-missing config fails loud via `emit_finding`, never a silent default (`feedback_no_silent_defaults`).
- OSS default `devto_syndicate_niches=""` (opt-in). Matt's real allowlist (AI/ML + dev_diary slugs) is set operator-side, not in the public seed (`project_oss_vs_operator_model_defaults`).
- `quality_score` is on a 0–100 scale. Default floor `80`.
- Every change ships tests + doc updates (`feedback_docs_and_tests_default`).
- Run unit tests with the main checkout's poetry venv python and `-o addopts=""` (fresh worktree has no venv; skips `--forked`) — `reference_run_worktree_tests`.

---

### Task 1: Settings defaults + parse helpers

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py:1339-1342` (Devto section)
- Modify: `src/cofounder_agent/services/jobs/crosspost_to_devto.py` (add module-level helpers)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_crosspost_to_devto_job.py`

**Interfaces:**

- Produces: `_parse_niches(raw: str) -> list[str]` (CSV → stripped, lowercased, de-duped, order-preserving list; empty/`""` → `[]`). `_parse_min_quality(raw: str) -> float` (raises `ValueError` on unparseable input). Both used by Task 2's `run()`.

- [ ] **Step 1: Write the failing tests** (append to the test module)

```python
from services.jobs.crosspost_to_devto import _parse_niches, _parse_min_quality


class TestParseHelpers:
    def test_parse_niches_splits_strips_lowercases_dedupes(self):
        assert _parse_niches(" AI-ML, dev_diary ,ai-ml,, GAMING ") == [
            "ai-ml", "dev_diary", "gaming",
        ]

    def test_parse_niches_empty_is_empty_list(self):
        assert _parse_niches("") == []
        assert _parse_niches("  ,  , ") == []

    def test_parse_min_quality_ok(self):
        assert _parse_min_quality("80") == 80.0
        assert _parse_min_quality("72.5") == 72.5

    def test_parse_min_quality_bad_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _parse_min_quality("high")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/services/jobs/test_crosspost_to_devto_job.py::TestParseHelpers -q -o addopts=""` (from `src/cofounder_agent`, using the main-checkout venv python)
Expected: FAIL with `ImportError: cannot import name '_parse_niches'`.

- [ ] **Step 3: Add the helpers** to `crosspost_to_devto.py` (module level, after the imports/logger)

```python
def _parse_niches(raw: str) -> list[str]:
    """Parse the ``devto_syndicate_niches`` CSV into a normalized slug list.

    Strips, lowercases, de-dupes (order-preserving). Empty / whitespace-only
    input yields ``[]`` — the caller treats that as "syndicate nothing".
    """
    seen: dict[str, None] = {}
    for part in (raw or "").split(","):
        slug = part.strip().lower()
        if slug:
            seen.setdefault(slug, None)
    return list(seen)


def _parse_min_quality(raw: str) -> float:
    """Parse ``devto_syndicate_min_quality`` (0–100). Raises ValueError on junk
    so the caller can fail loud instead of silently syndicating everything."""
    return float(raw)
```

- [ ] **Step 4: Add the two settings** to `settings_defaults.py`, replacing the Devto block at line 1339:

```python
    # ----- Devto / external publishing -----
    'devto_api_base': 'https://dev.to/api',
    # Selective syndication (spec 2026-07-12): only posts whose niche is in
    # this CSV allowlist AND whose quality_score >= the floor cross-post to
    # Dev.to. OSS default is empty = syndicate nothing (opt-in); operators set
    # their allowlist via `poindexter settings set` / the operator overlay.
    'devto_syndicate_niches': '',
    'devto_syndicate_min_quality': '80',
    # (mastodon_instance_url removed 2026-06-29 — the legacy direct Mastodon
    #  adapter is retired; Mastodon-via-Postiz uses postiz_integration_id_mastodon.)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/services/jobs/test_crosspost_to_devto_job.py::TestParseHelpers -q -o addopts=""`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/services/jobs/crosspost_to_devto.py src/cofounder_agent/tests/unit/services/jobs/test_crosspost_to_devto_job.py
git commit -m "feat(devto): add selective-syndication settings + parse helpers"
```

---

### Task 2: Gate the candidate query + empty-allowlist short-circuit

**Files:**

- Modify: `src/cofounder_agent/services/jobs/crosspost_to_devto.py` (module SQL constant + `run()` body + docstring)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_crosspost_to_devto_job.py`

**Interfaces:**

- Consumes: `_parse_niches`, `_parse_min_quality` (Task 1); `config["_site_config"]` (a `SiteConfig`-like object exposing `.get(key, default)`).
- Produces: `run()` now issues `conn.fetch(_CANDIDATE_SQL, batch_size, niches, min_quality)`; short-circuits (no fetch) when the allowlist is empty; no-ops loud when the quality floor is unparseable.

- [ ] **Step 1: Write the failing tests.** Add a shared config helper + new cases to `TestRun`:

```python
class _FakeSiteConfig:
    def __init__(self, values: dict[str, str]):
        self._v = values
    def get(self, key, default=None):
        return self._v.get(key, default)


def _cfg(niches: str = "ai-ml,dev_diary", min_quality: str = "0", **extra):
    """Config dict with a site_config that PERMITS the fetch (non-empty
    allowlist, floor 0). Extra kwargs merge into the top-level job config."""
    return {"_site_config": _FakeSiteConfig({
        "devto_syndicate_niches": niches,
        "devto_syndicate_min_quality": min_quality,
    }), **extra}


class TestGate:
    @pytest.mark.asyncio
    async def test_empty_allowlist_short_circuits_no_fetch(self):
        pool, conn = _make_pool([])
        svc = _patched_svc()
        with patch("services.devto_service.DevToCrossPostService", return_value=svc):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, _cfg(niches=""))
        assert result.ok is True
        assert result.changes_made == 0
        assert "no syndication niches" in result.detail.lower()
        conn.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_sql_and_params(self):
        pool, conn = _make_pool([])
        svc = _patched_svc()
        with patch("services.devto_service.DevToCrossPostService", return_value=svc):
            job = CrosspostToDevtoJob()
            await job.run(pool, _cfg(niches="ai-ml,dev_diary", min_quality="80"))
        sql = conn.fetch.call_args.args[0]
        assert "pipeline_tasks" in sql and "niche_slug" in sql
        assert "pipeline_versions" in sql and "quality_score" in sql
        assert conn.fetch.call_args.args[2] == ["ai-ml", "dev_diary"]
        assert conn.fetch.call_args.args[3] == 80.0

    @pytest.mark.asyncio
    async def test_bad_min_quality_fails_loud_noop(self):
        pool, conn = _make_pool([])
        svc = _patched_svc()
        emit = MagicMock()
        with patch("services.devto_service.DevToCrossPostService", return_value=svc), \
             patch("services.jobs.crosspost_to_devto.emit_finding", new=emit):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, _cfg(min_quality="high"))
        assert result.ok is True
        assert result.changes_made == 0
        conn.fetch.assert_not_awaited()
        emit.assert_called_once()
```

Then **thread `_cfg()` into every existing `TestRun` case that expects a fetch** — mechanical: replace the config arg in each `await job.run(pool, X)` that currently expects a candidate fetch. Exact transforms:

- `test_no_candidates_returns_ok`: `job.run(pool, {})` → `job.run(pool, _cfg())`
- `test_successful_crosspost_counts`: `job.run(pool, {})` → `job.run(pool, _cfg())`
- `test_mixed_success_and_failure`: `job.run(pool, {})` → `job.run(pool, _cfg())`
- `test_batch_size_passthrough`: `job.run(pool, {"batch_size": 10})` → `job.run(pool, _cfg(batch_size=10))` (assertion `args[1] == 10` stays valid — batch_size is still `$1`)
- `test_gitea_opt_in_when_errors`: `job.run(pool, {"file_gitea_issue": True})` → `job.run(pool, _cfg(file_gitea_issue=True))`
- `test_gitea_default_is_opt_out`: `job.run(pool, {})` → `job.run(pool, _cfg())`
- `test_fetch_failure_returns_not_ok`: `job.run(pool, {})` → `job.run(pool, _cfg())`
- `test_candidate_query_excludes_terminal_devto_status`: `job.run(pool, {})` → `job.run(pool, _cfg())`
- `test_already_exists_url_counts_as_success_not_error`: `job.run(pool, {})` → `job.run(pool, _cfg())`
- **Do NOT touch** `test_missing_api_key_skips_work` / `test_api_key_lookup_failure_returns_not_ok` — those short-circuit on the API key before the niche gate, so they keep `{}`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/services/jobs/test_crosspost_to_devto_job.py -q -o addopts=""`
Expected: FAIL — `TestGate` cases fail (no short-circuit / old query / no emit), and the threaded existing cases fail until `run()` reads the gate.

- [ ] **Step 3: Rewrite the candidate SQL + `run()`.** Replace the inline query string and the fetch/config block in `crosspost_to_devto.py`.

Add the module-level constant:

```python
_CANDIDATE_SQL = """
    SELECT p.id, p.title, p.slug
    FROM posts p
    JOIN pipeline_tasks pt
      ON p.metadata->>'pipeline_task_id' = pt.task_id
    LEFT JOIN LATERAL (
        SELECT pv.quality_score
        FROM pipeline_versions pv
        WHERE pv.task_id = pt.task_id
        ORDER BY pv.version DESC
        LIMIT 1
    ) pv ON TRUE
    WHERE p.status = 'published'
      AND (p.metadata IS NULL
           OR p.metadata->>'devto_url' IS NULL
           OR p.metadata->>'devto_url' = '')
      AND COALESCE(p.metadata->>'devto_status', '') NOT IN ('gave_up', 'already_exists')
      AND pt.niche_slug = ANY($2::text[])
      AND COALESCE(pv.quality_score, 0) >= $3
    ORDER BY p.published_at DESC
    LIMIT $1
"""
```

In `run()`, after the existing api-key check and before the candidate fetch, insert:

```python
        # Selective-syndication gate (spec 2026-07-12). Read from the
        # DI'd SiteConfig; None only in bare unit tests that don't exercise
        # the gate (they never reach here — the api-key check returns first).
        sc = config.get("_site_config")
        niches = _parse_niches(sc.get("devto_syndicate_niches", "") if sc else "")
        if not niches:
            return JobResult(
                ok=True,
                detail="no syndication niches configured — skipping",
                changes_made=0,
            )
        try:
            min_quality = _parse_min_quality(
                sc.get("devto_syndicate_min_quality", "80") if sc else "80"
            )
        except ValueError as e:
            emit_finding(
                source="crosspost_to_devto",
                kind="devto_min_quality_unparseable",
                severity="warning",
                title="devto_syndicate_min_quality is not a number — syndication paused",
                body=f"Value {sc.get('devto_syndicate_min_quality')!r} is unparseable ({e}); no posts syndicated this tick.",
                dedup_key="devto_min_quality_unparseable",
            )
            return JobResult(ok=True, detail=f"min_quality unparseable: {e}", changes_made=0)
```

Then change the fetch call from the old inline SQL to:

```python
                rows = await conn.fetch(_CANDIDATE_SQL, batch_size, niches, min_quality)
```

Update the module docstring's `## Config` section to document `devto_syndicate_niches` + `devto_syndicate_min_quality` and the fail-closed / short-circuit behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/services/jobs/test_crosspost_to_devto_job.py -q -o addopts=""`
Expected: PASS (all `TestRun`, `TestGate`, `TestContract`, `TestParseHelpers`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/jobs/crosspost_to_devto.py src/cofounder_agent/tests/unit/services/jobs/test_crosspost_to_devto_job.py
git commit -m "feat(devto): gate crosspost candidates on niche allowlist + quality floor"
```

---

### Task 3: Real-Postgres filtering + fail-closed (integration_db)

**Files:**

- Create: `src/cofounder_agent/tests/integration_db/test_crosspost_devto_gate.py`

**Interfaces:**

- Consumes: the `test_pool` fixture (integration_db `conftest.py`); the finished `run()` from Task 2. Patches `services.devto_service.DevToCrossPostService` with a stub that records the post_ids it was asked to cross-post (so the test asserts _selection_, not real HTTP).

- [ ] **Step 1: Write the test** (fails until Task 2's query is correct against the real schema)

```python
"""Real-Postgres verification of the CrosspostToDevtoJob niche+quality gate.

The candidate query is the correctness core — unit tests only see the SQL
string (mocked pool). This exercises it against the migrated schema: only
allowlisted-niche posts scoring >= the floor are selected, and posts with no
pipeline_task_id are fail-closed. HTTP is stubbed; only selection is asserted.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from services.jobs.crosspost_to_devto import CrosspostToDevtoJob

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


class _FakeSiteConfig:
    def __init__(self, values):
        self._v = values
    def get(self, key, default=None):
        return self._v.get(key, default)


def _cfg():
    return {"_site_config": _FakeSiteConfig({
        "devto_syndicate_niches": "ai-ml",
        "devto_syndicate_min_quality": "80",
    })}


async def _seed_post(conn, *, slug, niche, score, with_task=True):
    """Insert a published post + (optionally) its task/version. Returns post id."""
    task_id = f"task-{slug}"
    if with_task:
        await conn.execute(
            "INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage, niche_slug) "
            "VALUES ($1, 'blog_post', $2, 'published', 'done', $3)",
            task_id, slug, niche,
        )
        await conn.execute(
            "INSERT INTO pipeline_versions (task_id, version, title, quality_score) "
            "VALUES ($1, 1, $2, $3)",
            task_id, slug, score,
        )
    meta = f'{{"pipeline_task_id": "{task_id}"}}' if with_task else "{}"
    pid = await conn.fetchval(
        "INSERT INTO posts (id, title, slug, status, content, published_at, metadata) "
        "VALUES ($1, $2, $3, 'published', 'body', now(), $4::jsonb) RETURNING id",
        uuid.uuid4(), slug, slug, meta,
    )
    return str(pid)


async def _reset(conn):
    await conn.execute("DELETE FROM posts WHERE slug LIKE 'gate-%'")
    await conn.execute("DELETE FROM pipeline_versions WHERE task_id LIKE 'task-gate-%'")
    await conn.execute("DELETE FROM pipeline_tasks WHERE task_id LIKE 'task-gate-%'")


async def test_gate_selects_only_allowlisted_high_quality(test_pool):
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            keep = await _seed_post(conn, slug="gate-keep", niche="ai-ml", score=90)
            await _seed_post(conn, slug="gate-lowscore", niche="ai-ml", score=60)
            await _seed_post(conn, slug="gate-offniche", niche="gaming", score=95)
            await _seed_post(conn, slug="gate-notask", niche="ai-ml", score=95, with_task=False)

    asked: list[str] = []
    svc = AsyncMock()
    svc._get_api_key = AsyncMock(return_value="dt_key")
    async def _cp(post_id):
        asked.append(post_id)
        return f"https://dev.to/g/{post_id}"
    svc.cross_post_by_post_id = AsyncMock(side_effect=_cp)

    with patch("services.devto_service.DevToCrossPostService", return_value=svc):
        result = await CrosspostToDevtoJob().run(test_pool, {**_cfg(), "batch_size": 50})

    assert result.ok is True
    assert asked == [keep]  # only the allowlisted, high-quality, task-backed post
```

- [ ] **Step 2: Run to verify it passes against the real DB**

Run: `python -m pytest tests/integration_db/test_crosspost_devto_gate.py -q -o addopts=""` (requires a reachable test Postgres; otherwise runs in CI's throwaway service container — `reference_migrations_smoke_hits_live_db` / integration_db harness)
Expected: PASS. (If no Postgres is reachable locally this session, note it and let CI run it — do NOT point it at prod.)

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/integration_db/test_crosspost_devto_gate.py
git commit -m "test(devto): real-PG gate filtering + fail-closed on missing task"
```

---

### Task 4: Gate-throughput metrics + docs (+ panel if a sink exists)

**Files:**

- Modify: `src/cofounder_agent/services/jobs/crosspost_to_devto.py` (two count queries + metrics + log)
- Modify: `src/cofounder_agent/tests/unit/services/jobs/test_crosspost_to_devto_job.py` (assert new metrics)
- Modify: `docs/reference/app-settings.md` (document the two settings)
- Investigate: job-metrics → Grafana sink; add Integrations panel **only if** the sink exists.

**Interfaces:**

- Consumes: `run()` (Task 2). Produces: `JobResult.metrics` now includes `gate_universe`, `gate_eligible`, `posts_skipped_by_gate`.

- [ ] **Step 1: Write the failing test** (add to `TestGate`)

```python
    @pytest.mark.asyncio
    async def test_metrics_include_gate_counts(self):
        pool, conn = _make_pool([{"id": "p1", "title": "T", "slug": "s"}])
        # fetchval returns universe then eligible on successive calls.
        conn.fetchval = AsyncMock(side_effect=[5, 3])
        svc = _patched_svc(post_return_map={"p1": "https://dev.to/g/s"})
        with patch("services.devto_service.DevToCrossPostService", return_value=svc):
            result = await CrosspostToDevtoJob().run(pool, _cfg())
        assert result.metrics["gate_universe"] == 5
        assert result.metrics["gate_eligible"] == 3
        assert result.metrics["posts_skipped_by_gate"] == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest "tests/unit/services/jobs/test_crosspost_to_devto_job.py::TestGate::test_metrics_include_gate_counts" -q -o addopts=""`
Expected: FAIL — `KeyError: 'gate_universe'`.

- [ ] **Step 3: Add the two count queries + metrics.** Add module constants:

```python
_UNIVERSE_COUNT_SQL = """
    SELECT count(*) FROM posts p
    JOIN pipeline_tasks pt ON p.metadata->>'pipeline_task_id' = pt.task_id
    WHERE p.status='published'
      AND (p.metadata IS NULL OR p.metadata->>'devto_url' IS NULL OR p.metadata->>'devto_url'='')
      AND COALESCE(p.metadata->>'devto_status','') NOT IN ('gave_up','already_exists')
"""

_ELIGIBLE_COUNT_SQL = """
    SELECT count(*) FROM posts p
    JOIN pipeline_tasks pt ON p.metadata->>'pipeline_task_id' = pt.task_id
    LEFT JOIN LATERAL (
        SELECT pv.quality_score FROM pipeline_versions pv
        WHERE pv.task_id = pt.task_id ORDER BY pv.version DESC LIMIT 1
    ) pv ON TRUE
    WHERE p.status='published'
      AND (p.metadata IS NULL OR p.metadata->>'devto_url' IS NULL OR p.metadata->>'devto_url'='')
      AND COALESCE(p.metadata->>'devto_status','') NOT IN ('gave_up','already_exists')
      AND pt.niche_slug = ANY($1::text[])
      AND COALESCE(pv.quality_score,0) >= $2
"""
```

Inside the `async with pool.acquire() as conn:` block, alongside the candidate fetch:

```python
                gate_universe = await conn.fetchval(_UNIVERSE_COUNT_SQL) or 0
                gate_eligible = await conn.fetchval(_ELIGIBLE_COUNT_SQL, niches, min_quality) or 0
```

Compute `posts_skipped_by_gate = max(0, gate_universe - gate_eligible)`, add all three to the returned `metrics=` dict, and add before the final return:

```python
        logger.info(
            "CrosspostToDevtoJob: %d eligible, %d skipped by niche/quality gate "
            "(universe %d)", gate_eligible, gate_universe - gate_eligible, gate_universe,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/services/jobs/test_crosspost_to_devto_job.py -q -o addopts=""`
Expected: PASS (all classes).

- [ ] **Step 5: Document the settings.** Add to `docs/reference/app-settings.md` (Devto / syndication area) two rows:

```markdown
| `devto_syndicate_niches` | `""` | CSV allowlist of niche slugs eligible for Dev.to cross-posting. Empty = syndicate nothing (opt-in). |
| `devto_syndicate_min_quality` | `80` | Minimum `quality_score` (0–100) a post needs to cross-post to Dev.to. |
```

- [ ] **Step 6: Investigate the Grafana sink, then branch.** Find where `JobResult.metrics` is persisted (grep for the job runner that consumes `JobResult`, and the `job_runs` table). If a metrics-bearing sink + Grafana datasource exists, add an "Dev.to syndication — eligible vs skipped" panel to the Integrations & Admin board JSON in the same PR. If no job-metrics sink reaches Grafana, the counts already land in `JobResult.metrics` + the log line — record "Grafana panel = fast-follow (no job-metrics→Grafana path today)" in the spec's Observability section and move on. Do **not** fabricate a panel data source.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/jobs/crosspost_to_devto.py src/cofounder_agent/tests/unit/services/jobs/test_crosspost_to_devto_job.py docs/reference/app-settings.md
# + the Integrations board JSON if a panel was added
git commit -m "feat(devto): emit gate-throughput metrics + document syndication settings"
```

---

## Post-implementation verification (before marking PR ready)

1. **Full job test file green:** `python -m pytest tests/unit/services/jobs/test_crosspost_to_devto_job.py -q -o addopts=""`.
2. **Integration test:** run Task 3 against a reachable test PG, or confirm CI runs it (never prod).
3. **Score-distribution dry-run (spec Verification #1):** once the DB is reachable, run the eligible-count query read-only against prod for Matt's chosen niches at `min_quality=80` — confirm it selects a sensible non-empty set of the ~110 posts. If it gates out nearly everything, lower the default before merge.
4. **Set Matt's operator allowlist:** confirm the exact AI/ML + dev_diary slugs via `niche_service.get_known_niche_slugs`, then set `devto_syndicate_niches` operator-side (not the public seed).
5. Mark PR #2415 ready for review; report the dry-run numbers.

## Self-review notes

- **Spec coverage:** gate (Task 2/3), settings (Task 1), fail-closed (Task 3), grandfathering (unchanged dedup, no task needed), observability (Task 4), tests + docs (all tasks), verification (post-impl section). ✓
- **Types:** `_parse_niches -> list[str]`, `_parse_min_quality -> float`, candidate params `(batch_size:int, niches:list[str], min_quality:float)`, count params `(niches, min_quality)` consistent across tasks. ✓
- **Panel honesty:** Task 4 Step 6 is investigate-then-branch, not a fabricated data source — matches the spec's Observability fallback. ✓

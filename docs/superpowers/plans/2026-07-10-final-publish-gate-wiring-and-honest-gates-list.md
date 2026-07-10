# Final-publish gate wiring + honest `gates list` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `final_publish_approval` into `scheduled_publisher` so an enabled gate pauses due scheduled posts before the timer promotes them, and make `poindexter gates list` show the always-on `awaiting_approval` default gate, accurate wiring, and code-defined gates.

**Architecture:** Two independent slices on the existing gate machinery. Slice 1 finishes a contract `posts_approval_service` already documents — the publisher never called `pause_post_at_gate` and never filtered parked posts out of its promote query. Slice 2 adds a hand-maintained `GATE_CATALOG` and rewrites `list_gates` to merge it with live pending counts from both gate-carrying tables plus a synthetic default-gate row. No schema migration — the `posts.awaiting_gate` columns/index already exist.

**Tech Stack:** Python 3.11 / asyncpg / Click CLI / pytest (async). Spec: `docs/superpowers/specs/2026-07-09-final-publish-gate-wiring-and-honest-gates-list-design.md`.

## Global Constraints

- **No migration.** `posts.awaiting_gate` / `gate_artifact` / `gate_paused_at` and `idx_posts_awaiting_gate` already exist in `0000_baseline.schema.sql`. Add no migration file and no new `app_settings` key (`pipeline_gate_final_publish_approval` is already seeded `off`).
- **Fail loud, never silent.** Gate/notify failures in the publisher log at WARNING and are swallowed only so they cannot poison the publish loop (per `feedback_no_silent_defaults` / `feedback_self_heal_not_suppress`).
- **Backcompat.** `list_gates` stays a list of row dicts; every row keeps the existing `gate_name` / `enabled` / `pending_count` keys. New keys are additive (`mechanism`, `wired_into`; default row adds posture keys).
- **Colorblind-safe.** Gate state is carried by the `enabled`/`disabled` text, never color alone.
- **Tests run from** `src/cofounder_agent` via `poetry run pytest`.
- **Every commit message ends with the trailer** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (shown in each commit step).
- **DB-first config, DI seams.** `list_gates` reads posture through the injected `site_config`; the publisher reads `site_config` already threaded into `run_scheduled_publisher`.

## File Structure

- `src/cofounder_agent/services/gate_machinery.py` — **modify.** Add `GateSpec` dataclass + `GATE_CATALOG` + `GATE_CATALOG_BY_NAME`. (Task 1)
- `src/cofounder_agent/services/scheduled_publisher.py` — **modify.** Add `_maybe_park_due_posts_at_gate`; call it each tick; add `AND awaiting_gate IS NULL` to the promote `UPDATE`. (Task 2)
- `src/cofounder_agent/services/approval_service.py` — **modify.** Rewrite `list_gates` (catalog merge + dual-table pending); add `_auto_publish_posture` + the default-gate row. (Tasks 3, 4)
- `src/cofounder_agent/poindexter/cli/approval.py` — **modify.** Grouped renderer in `gates_list_command`. (Task 5)
- `docs/operations/cli-reference.md` — **modify.** Update the `gates` docs. (Task 6)
- Tests: `tests/unit/services/test_gate_machinery.py` (extend), `tests/unit/services/test_scheduled_publisher.py` (extend), `tests/unit/test_approval_service.py` (extend + rewrite one test), `tests/unit/cli/test_gates_list_cli.py` (create).

---

### Task 1: `GATE_CATALOG` in `gate_machinery.py`

**Files:**

- Modify: `src/cofounder_agent/services/gate_machinery.py`
- Test: `src/cofounder_agent/tests/unit/services/test_gate_machinery.py` (extend)

**Interfaces:**

- Produces: `GateSpec` (frozen dataclass with fields `name: str`, `mechanism: str`, `wired_into: str`, `default_enabled: bool`); `GATE_CATALOG: tuple[GateSpec, ...]`; `GATE_CATALOG_BY_NAME: dict[str, GateSpec]`.

- [ ] **Step 1: Write the failing test**

Append to `src/cofounder_agent/tests/unit/services/test_gate_machinery.py`:

```python
class TestGateCatalog:
    def test_catalog_has_the_five_known_gates(self):
        from services.gate_machinery import GATE_CATALOG

        names = {g.name for g in GATE_CATALOG}
        assert names == {
            "draft_gate",
            "preview_gate",
            "seo_refresh_gate",
            "topic_decision",
            "final_publish_approval",
        }

    def test_mechanism_and_wiring_are_accurate(self):
        from services.gate_machinery import GATE_CATALOG_BY_NAME

        fpa = GATE_CATALOG_BY_NAME["final_publish_approval"]
        assert fpa.mechanism == "imperative-hold"
        assert fpa.wired_into == "scheduled_publisher"
        assert fpa.default_enabled is False

        td = GATE_CATALOG_BY_NAME["topic_decision"]
        assert td.mechanism == "imperative-hold"

        draft = GATE_CATALOG_BY_NAME["draft_gate"]
        assert draft.mechanism == "graph-node"
        assert draft.wired_into == "canonical_blog"

        seo = GATE_CATALOG_BY_NAME["seo_refresh_gate"]
        assert seo.default_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_gate_machinery.py::TestGateCatalog -q`
Expected: FAIL — `ImportError: cannot import name 'GATE_CATALOG'`.

- [ ] **Step 3: Add the catalog**

In `src/cofounder_agent/services/gate_machinery.py`, add after the imports (`from typing import Any` block) near the top:

```python
from dataclasses import dataclass
```

Then add a new section just above the `GateServiceError` class:

```python
# ---------------------------------------------------------------------------
# Gate catalog — the single source of truth for which HITL gates exist, how
# they gate, and where they fire. Consumed by approval_service.list_gates so
# `poindexter gates list` shows every known gate (even one with no setting row
# or paused task) with an honest mechanism/wiring label.
#
# mechanism follows execution context, not preference:
#   - "graph-node"      — a LangGraph interrupt() atom; can only pause a LIVE
#                         graph run (draft/preview in canonical_blog; seo_refresh).
#   - "imperative-hold" — writes the awaiting_gate columns to hold an entity
#                         OUTSIDE any live graph: topic_decision holds a task
#                         before the flow claims it (pre-graph);
#                         final_publish_approval holds a post before the
#                         scheduled_publisher timer promotes it (post-graph).
#
# New gate? Add a row here so `gates list` stays honest.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateSpec:
    """One known HITL gate: its name, mechanism, wiring, and default state."""

    name: str
    mechanism: str  # "graph-node" | "imperative-hold"
    wired_into: str  # human-readable location where it fires
    default_enabled: bool


GATE_CATALOG: tuple[GateSpec, ...] = (
    GateSpec("draft_gate", "graph-node", "canonical_blog", False),
    GateSpec("preview_gate", "graph-node", "canonical_blog", False),
    GateSpec("seo_refresh_gate", "graph-node", "seo_refresh", True),
    GateSpec("topic_decision", "imperative-hold", "topic proposals (CLI)", False),
    GateSpec("final_publish_approval", "imperative-hold", "scheduled_publisher", False),
)

GATE_CATALOG_BY_NAME: dict[str, GateSpec] = {g.name: g for g in GATE_CATALOG}
```

Add the four new names to `__all__`:

```python
__all__ = [
    "GateServiceError",
    "GateSpec",
    "GATE_CATALOG",
    "GATE_CATALOG_BY_NAME",
    "coerce_artifact",
    "ensure_gate_match",
    "resolve_reject_status",
    "iso_or_none",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_gate_machinery.py -q`
Expected: PASS (all, including the pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/gate_machinery.py src/cofounder_agent/tests/unit/services/test_gate_machinery.py
git commit -m "feat(gates): add GATE_CATALOG source of truth for known HITL gates" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire `final_publish_approval` into `scheduled_publisher`

**Files:**

- Modify: `src/cofounder_agent/services/scheduled_publisher.py`
- Test: `src/cofounder_agent/tests/unit/services/test_scheduled_publisher.py` (extend)

**Interfaces:**

- Consumes: `services.approval_service.is_gate_enabled(gate_name, site_config) -> bool`; `services.posts_approval_service.pause_post_at_gate(*, post_id, gate_name, artifact, site_config, pool, notify) -> dict`; `services.posts_approval_service.FINAL_PUBLISH_GATE == "final_publish_approval"`.
- Produces: `_maybe_park_due_posts_at_gate(pool, *, site_config) -> None` (module-private helper).

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/tests/unit/services/test_scheduled_publisher.py`:

```python
class TestFinalPublishGate:
    """final_publish_approval pauses due scheduled posts when enabled."""

    @pytest.mark.asyncio
    async def test_gate_off_does_not_park(self):
        rows = [{"id": "p-1", "slug": "s", "title": "T", "pipeline_task_id": None}]
        pool, _conn = _make_pool(rows)
        get_pool = AsyncMock(return_value=pool)
        with patch(
            "services.posts_approval_service.pause_post_at_gate",
            new=AsyncMock(),
        ) as mock_pause:
            await _run_one_iteration(get_pool)  # empty SiteConfig -> gate off
        mock_pause.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_on_parks_due_posts(self):
        rows = [{"id": "p-1", "slug": "s-1", "title": "T1", "pipeline_task_id": None}]
        pool, _conn = _make_pool(rows)
        get_pool = AsyncMock(return_value=pool)
        sc = SiteConfig(
            initial_config={"pipeline_gate_final_publish_approval": "on"}
        )
        with patch(
            "services.posts_approval_service.pause_post_at_gate",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_pause:
            task = asyncio.create_task(
                run_scheduled_publisher(get_pool, site_config=sc)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            await task
        mock_pause.assert_awaited()
        _, kwargs = mock_pause.await_args
        assert kwargs["post_id"] == "p-1"
        assert kwargs["gate_name"] == "final_publish_approval"
        assert kwargs["artifact"]["slug"] == "s-1"

    @pytest.mark.asyncio
    async def test_promote_query_excludes_parked_posts(self):
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)
        await _run_one_iteration(get_pool)  # gate off -> only the promote UPDATE
        sql = conn.fetch.call_args[0][0]
        assert "awaiting_gate IS NULL" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_scheduled_publisher.py::TestFinalPublishGate -q`
Expected: FAIL — `test_promote_query_excludes_parked_posts` fails ("awaiting_gate IS NULL" absent) and `test_gate_on_parks_due_posts` fails (pause never called).

- [ ] **Step 3: Add the park guard to the promote query**

In `src/cofounder_agent/services/scheduled_publisher.py`, edit the promote `UPDATE` WHERE clause (currently `WHERE status = 'scheduled' AND published_at <= NOW()`):

```python
                    rows = await conn.fetch("""
                        UPDATE posts
                        SET status = 'published',
                            updated_at = NOW(),
                            distributed_at = COALESCE(distributed_at, NOW())
                        WHERE status = 'scheduled' AND published_at <= NOW()
                          AND awaiting_gate IS NULL
                        RETURNING id, title, slug,
                                  metadata ->> 'pipeline_task_id' AS pipeline_task_id
                        """)
```

- [ ] **Step 4: Add the park step + helper**

In the same file, insert the park call inside the loop, right after the `if not pool: continue` guard and before `async with pool.acquire() as conn:`:

```python
                # HITL final-publish gate: when enabled, park due-but-unparked
                # scheduled posts at final_publish_approval instead of letting
                # the promote UPDATE publish them. Runs on its own connection
                # BEFORE the promote transaction (pause_post_at_gate acquires
                # its own connection). Never raises — a gate/notify failure
                # must not poison the publish loop.
                await _maybe_park_due_posts_at_gate(pool, site_config=_sc)
```

Then add the helper below `run_scheduled_publisher` (above `_revalidate_for_row`):

```python
async def _maybe_park_due_posts_at_gate(pool, *, site_config: SiteConfig) -> None:
    """Pause due scheduled posts at ``final_publish_approval`` when enabled.

    When ``pipeline_gate_final_publish_approval`` is on, every post that is
    ``scheduled`` and due (``published_at <= NOW()``) but not already parked
    (``awaiting_gate IS NULL``) is handed to
    :func:`services.posts_approval_service.pause_post_at_gate`, which sets the
    gate columns, notifies the operator, and writes the audit row. The promote
    UPDATE (which now filters ``awaiting_gate IS NULL``) then skips them until
    the operator clears the gate via ``poindexter schedule approve <post_id>``.

    Best-effort: any failure is logged at WARNING and swallowed so the publish
    loop keeps running (a crash here would wedge every subsequent due post).
    """
    from services.approval_service import is_gate_enabled
    from services.posts_approval_service import (
        FINAL_PUBLISH_GATE,
        pause_post_at_gate,
    )

    if not is_gate_enabled(FINAL_PUBLISH_GATE, site_config):
        return

    try:
        async with pool.acquire() as conn:
            due = await conn.fetch(
                """
                SELECT id::text AS id, slug, title
                  FROM posts
                 WHERE status = 'scheduled'
                   AND published_at <= NOW()
                   AND awaiting_gate IS NULL
                """
            )
    except Exception as exc:
        logger.warning(
            "[scheduled_publisher] final_publish_approval: due-post query "
            "failed (non-fatal), skipping park this tick: %s",
            exc,
        )
        return

    site_url = ""
    try:
        site_url = str(site_config.get("site_url", "") or "")
    except Exception:
        site_url = ""

    for row in due:
        post_id = row["id"]
        slug = row["slug"]
        artifact = {"slug": slug, "title": row["title"]}
        if site_url and slug:
            artifact["permalink"] = f"{site_url.rstrip('/')}/posts/{slug}"
        try:
            await pause_post_at_gate(
                post_id=post_id,
                gate_name=FINAL_PUBLISH_GATE,
                artifact=artifact,
                site_config=site_config,
                pool=pool,
                notify=True,
            )
            logger.info(
                "[scheduled_publisher] final_publish_approval: parked post %s "
                "(%s) for operator sign-off",
                post_id,
                slug,
            )
        except Exception as exc:
            logger.warning(
                "[scheduled_publisher] final_publish_approval: pause_post_at_gate "
                "failed for post %s (non-fatal): %s",
                post_id,
                exc,
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_scheduled_publisher.py -q`
Expected: PASS — the new `TestFinalPublishGate` class plus all pre-existing publisher tests (gate-off leaves `conn.fetch` called once; the guard substring is present; existing substring asserts unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/scheduled_publisher.py src/cofounder_agent/tests/unit/services/test_scheduled_publisher.py
git commit -m "feat(gates): wire final_publish_approval into scheduled_publisher" -m "When the gate is enabled, due scheduled posts pause at the gate instead of being promoted; the promote UPDATE now filters awaiting_gate IS NULL so parked posts are never auto-published." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `list_gates` — catalog merge + dual-table pending

**Files:**

- Modify: `src/cofounder_agent/services/approval_service.py` (`list_gates`)
- Test: `src/cofounder_agent/tests/unit/test_approval_service.py` (extend `FakeStore` / `FakeConnection`, rewrite one test)

**Interfaces:**

- Consumes: `services.gate_machinery.GATE_CATALOG`.
- Produces: `list_gates(*, pool, site_config=None) -> list[dict]` where each configurable-gate row now also carries `mechanism: str` and `wired_into: str`, and `pending_count` sums both `pipeline_tasks.awaiting_gate` and `posts.awaiting_gate`.

- [ ] **Step 1: Extend the fake DB for the posts pending query**

In `src/cofounder_agent/tests/unit/test_approval_service.py`, add a `posts` store to `FakeStore.__init__`:

```python
class FakeStore:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.app_settings: dict[str, dict[str, Any]] = {}
        self.posts: dict[str, dict[str, Any]] = {}
```

In `FakeConnection.fetch`, add a branch for the posts pending count (place it right after the existing `FROM pipeline_tasks` count branch):

```python
        if sql_norm.startswith(
            "SELECT awaiting_gate AS gate_name, COUNT(*) AS pending_count FROM posts"
        ):
            counts: dict[str, int] = {}
            for p in self._store.posts.values():
                g = p.get("awaiting_gate")
                if g:
                    counts[g] = counts.get(g, 0) + 1
            return [
                {"gate_name": k, "pending_count": v} for k, v in counts.items()
            ]
```

- [ ] **Step 2: Rewrite the merge test (was `test_list_gates_merges_settings_and_live_rows`)**

Replace that test body with catalog-aware assertions (subset-based so Task 4's default row doesn't break it):

```python
    async def test_list_gates_merges_catalog_settings_and_live_rows(self, fake_pool):
        # A catalog gate toggled on via settings; a live task-gate; a live
        # post-gate (final_publish_approval parks on posts).
        fake_pool.store.app_settings["pipeline_gate_topic_decision"] = {
            "value": "on", "is_active": True, "description": "",
        }
        now = datetime.now(timezone.utc)
        fake_pool.store.tasks["t-1"] = {
            "awaiting_gate": "topic_decision",
            "gate_artifact": "{}",
            "gate_paused_at": now,
        }
        fake_pool.store.posts["p-1"] = {"awaiting_gate": "final_publish_approval"}

        rows = await list_gates(pool=fake_pool)
        by_name = {r["gate_name"]: r for r in rows}

        # Every catalog gate appears even with no setting row / no paused entity.
        assert {
            "draft_gate", "preview_gate", "seo_refresh_gate",
            "topic_decision", "final_publish_approval",
        } <= set(by_name)

        # Mechanism / wiring come from the catalog.
        assert by_name["final_publish_approval"]["mechanism"] == "imperative-hold"
        assert by_name["final_publish_approval"]["wired_into"] == "scheduled_publisher"
        assert by_name["draft_gate"]["mechanism"] == "graph-node"

        # Enabled state from settings; seo_refresh_gate defaults on.
        assert by_name["topic_decision"]["enabled"] is True
        assert by_name["seo_refresh_gate"]["enabled"] is True
        assert by_name["draft_gate"]["enabled"] is False

        # Pending counts unioned across both tables.
        assert by_name["topic_decision"]["pending_count"] == 1  # from tasks
        assert by_name["final_publish_approval"]["pending_count"] == 1  # from posts
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/test_approval_service.py::TestSetGate::test_list_gates_merges_catalog_settings_and_live_rows -q`

If the test's class differs, run the whole file to locate it: `poetry run pytest tests/unit/test_approval_service.py -k list_gates -q`
Expected: FAIL — catalog gates missing / `mechanism` KeyError.

- [ ] **Step 4: Rewrite `list_gates`**

Replace the body of `list_gates` in `src/cofounder_agent/services/approval_service.py` (keep the signature and docstring intro) with:

```python
    from services.gate_machinery import GATE_CATALOG

    async with pool.acquire() as conn:
        setting_rows = await conn.fetch(
            """
            SELECT key, value, is_active
              FROM app_settings
             WHERE key LIKE $1
            """,
            f"{_GATE_SETTING_PREFIX}%",
        )
        task_live = await conn.fetch(
            """
            SELECT awaiting_gate AS gate_name, COUNT(*) AS pending_count
              FROM pipeline_tasks
             WHERE awaiting_gate IS NOT NULL
             GROUP BY awaiting_gate
            """,
        )
        post_live = await conn.fetch(
            """
            SELECT awaiting_gate AS gate_name, COUNT(*) AS pending_count
              FROM posts
             WHERE awaiting_gate IS NOT NULL
             GROUP BY awaiting_gate
            """,
        )

    gates: dict[str, dict[str, Any]] = {}

    # 1. Seed from the catalog so every known gate appears, even with no
    #    setting row or paused entity, with an honest mechanism/wiring label.
    for spec in GATE_CATALOG:
        gates[spec.name] = {
            "gate_name": spec.name,
            "enabled": spec.default_enabled,
            "mechanism": spec.mechanism,
            "wired_into": spec.wired_into,
            "setting_key": _gate_setting_key(spec.name),
            "pending_count": 0,
        }

    # 2. Overlay real enabled-state from settings; surface any settings-only
    #    gate absent from the catalog (forward-compat) as mechanism=unknown.
    for row in setting_rows:
        gate_name = row["key"][len(_GATE_SETTING_PREFIX):]
        if not gate_name:
            continue
        enabled = (
            str(row["value"]).strip().lower() in ("on", "true", "1", "yes")
            and bool(row.get("is_active", True))
        )
        entry = gates.get(gate_name)
        if entry is None:
            gates[gate_name] = {
                "gate_name": gate_name,
                "enabled": enabled,
                "mechanism": "unknown",
                "wired_into": "unknown",
                "setting_key": row["key"],
                "pending_count": 0,
            }
        else:
            entry["enabled"] = enabled
            entry["setting_key"] = row["key"]

    # 3. Add pending counts from BOTH gate-carrying tables (a gate parks on
    #    pipeline_tasks OR posts). Surface any live gate not otherwise known.
    for row in list(task_live) + list(post_live):
        gate_name = row["gate_name"]
        entry = gates.setdefault(
            gate_name,
            {
                "gate_name": gate_name,
                "enabled": False,
                "mechanism": "unknown",
                "wired_into": "unknown",
                "setting_key": _gate_setting_key(gate_name),
                "pending_count": 0,
            },
        )
        entry["pending_count"] += int(row["pending_count"])

    return sorted(gates.values(), key=lambda g: g["gate_name"])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/test_approval_service.py -k "list_gates or gate" -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/approval_service.py src/cofounder_agent/tests/unit/test_approval_service.py
git commit -m "feat(gates): list_gates merges GATE_CATALOG + dual-table pending counts" -m "Every known gate now appears with mechanism/wired_into, and pending counts sum both pipeline_tasks.awaiting_gate and posts.awaiting_gate." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `list_gates` — default `awaiting_approval` row + auto-publish posture

**Files:**

- Modify: `src/cofounder_agent/services/approval_service.py` (add `_auto_publish_posture`, prepend default row in `list_gates`)
- Test: `src/cofounder_agent/tests/unit/test_approval_service.py` (extend fake, add test)

**Interfaces:**

- Consumes: `site_config.get(key, default)`.
- Produces: `list_gates` returns `[default_row, *configurable_gates]`. `default_row` has `gate_name="awaiting_approval"`, `mechanism="default"`, `enabled=True`, `pending_count=<count of pipeline_tasks at awaiting_approval>`, plus `auto_publish_threshold: str`, `require_human_approval: str`, `armed_niches: list[str]`. New private `_auto_publish_posture(*, pool, site_config) -> dict`.

- [ ] **Step 1: Extend the fake DB for the awaiting_approval count + posture scan**

In `src/cofounder_agent/tests/unit/test_approval_service.py`, add to `FakeConnection.fetchval` (before its final `raise AssertionError`):

```python
        if sql_norm.startswith(
            "SELECT COUNT(*) FROM pipeline_tasks WHERE status = 'awaiting_approval'"
        ):
            return sum(
                1
                for t in self._store.tasks.values()
                if t.get("status") == "awaiting_approval"
            )
```

And add to `FakeConnection.fetch` (before its final `raise AssertionError`):

```python
        if sql_norm.startswith(
            "SELECT key, value FROM app_settings WHERE key LIKE '%auto_publish%'"
        ):
            return [
                {"key": k, "value": v["value"]}
                for k, v in self._store.app_settings.items()
                if "auto_publish" in k
            ]
```

- [ ] **Step 2: Write the failing test**

Append to the `list_gates` test class in `src/cofounder_agent/tests/unit/test_approval_service.py`:

```python
    async def test_list_gates_prepends_default_awaiting_approval_row(self, fake_pool):
        fake_pool.store.tasks["a-1"] = {"status": "awaiting_approval"}
        fake_pool.store.tasks["a-2"] = {"status": "awaiting_approval"}
        # An armed niche (threshold > 0 AND dry_run false) and a disarmed one.
        fake_pool.store.app_settings["dev_diary_auto_publish_threshold"] = {
            "value": "70", "is_active": True, "description": "",
        }
        fake_pool.store.app_settings["dev_diary_auto_publish_dry_run"] = {
            "value": "false", "is_active": True, "description": "",
        }
        fake_pool.store.app_settings["glad_labs_auto_publish_threshold"] = {
            "value": "80", "is_active": True, "description": "",
        }
        fake_pool.store.app_settings["glad_labs_auto_publish_dry_run"] = {
            "value": "true", "is_active": True, "description": "",
        }
        sc = _make_site_config(
            {"auto_publish_threshold": "0", "require_human_approval": "true"}
        )

        rows = await list_gates(pool=fake_pool, site_config=sc)

        default = rows[0]
        assert default["gate_name"] == "awaiting_approval"
        assert default["mechanism"] == "default"
        assert default["enabled"] is True
        assert default["pending_count"] == 2
        assert default["auto_publish_threshold"] == "0"
        assert default["require_human_approval"] == "true"
        # dev_diary armed (dry_run false); glad_labs not (dry_run true).
        assert default["armed_niches"] == ["dev_diary"]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/test_approval_service.py::TestSetGate::test_list_gates_prepends_default_awaiting_approval_row -q`

(If the class name differs, run `-k prepends_default_awaiting_approval`.)
Expected: FAIL — `rows[0]` is a configurable gate, no `awaiting_approval`.

- [ ] **Step 4: Add the posture helper**

In `src/cofounder_agent/services/approval_service.py`, add above `list_gates`:

```python
async def _auto_publish_posture(*, pool: Any, site_config: Any) -> dict[str, Any]:
    """Return the global auto-publish posture for the default-gate row.

    - ``auto_publish_threshold`` / ``require_human_approval``: the two globals
      that keep every post in ``awaiting_approval`` (both must relax for a
      post to auto-publish).
    - ``armed_niches``: niches that HAVE opted into auto-publish —
      ``<niche>_auto_publish_threshold > 0`` AND
      ``<niche>_auto_publish_dry_run == 'false'``.

    Best-effort: the armed-niche scan is swallowed on error (the CLI still
    renders the global posture).
    """
    threshold = "0"
    require_human = "true"
    if site_config is not None:
        threshold = str(site_config.get("auto_publish_threshold", "0"))
        require_human = str(site_config.get("require_human_approval", "true"))

    armed: list[str] = []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, value FROM app_settings WHERE key LIKE '%auto_publish%'"
            )
        thresholds: dict[str, float] = {}
        dry: dict[str, str] = {}
        for r in rows:
            key = r["key"]
            if key.endswith("_auto_publish_threshold"):
                niche = key[: -len("_auto_publish_threshold")]
                try:
                    thresholds[niche] = float(r["value"])
                except (TypeError, ValueError):
                    thresholds[niche] = 0.0
            elif key.endswith("_auto_publish_dry_run"):
                niche = key[: -len("_auto_publish_dry_run")]
                dry[niche] = str(r["value"]).strip().lower()
        for niche in sorted(thresholds):
            if thresholds[niche] > 0 and dry.get(niche) == "false":
                armed.append(niche)
    except Exception as exc:  # noqa: BLE001 — posture scan is best-effort
        logger.debug("[approval_service] armed-niche scan failed: %s", exc)

    return {
        "auto_publish_threshold": threshold,
        "require_human_approval": require_human,
        "armed_niches": armed,
    }
```

- [ ] **Step 5: Prepend the default row in `list_gates`**

In `list_gates`, add the `awaiting_approval` count to the first `async with pool.acquire() as conn:` block (after the `post_live` query):

```python
        awaiting_approval_count = await conn.fetchval(
            "SELECT COUNT(*) FROM pipeline_tasks WHERE status = 'awaiting_approval'"
        )
```

Then replace the final `return sorted(...)` line with:

```python
    ordered = sorted(gates.values(), key=lambda g: g["gate_name"])

    posture = await _auto_publish_posture(pool=pool, site_config=site_config)
    default_row = {
        "gate_name": "awaiting_approval",
        "enabled": True,
        "mechanism": "default",
        "wired_into": "post_pipeline (every post)",
        "setting_key": None,
        "pending_count": int(awaiting_approval_count or 0),
        **posture,
    }
    return [default_row, *ordered]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/test_approval_service.py -k "list_gates" -q`
Expected: PASS (both the Task 3 merge test — it uses subset/`by_name` lookups so the prepended default row is harmless — and the new default-row test).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/approval_service.py src/cofounder_agent/tests/unit/test_approval_service.py
git commit -m "feat(gates): surface awaiting_approval default gate + auto-publish posture" -m "list_gates now prepends the always-on per-post sign-off gate with its live pending count and the global/niche auto-publish posture." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Honest `gates list` CLI renderer

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/approval.py` (`gates_list_command`)
- Test: `src/cofounder_agent/tests/unit/cli/test_gates_list_cli.py` (create)

**Interfaces:**

- Consumes: `list_gates` rows with `mechanism` / `wired_into` / default-row posture keys (Tasks 3–4).

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/tests/unit/cli/test_gates_list_cli.py`:

```python
"""CLI render test for the honest `poindexter gates list` output."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from poindexter.cli.approval import gates_list_command


def _canned_rows():
    return [
        {
            "gate_name": "awaiting_approval",
            "enabled": True,
            "mechanism": "default",
            "wired_into": "post_pipeline (every post)",
            "setting_key": None,
            "pending_count": 2,
            "auto_publish_threshold": "0",
            "require_human_approval": "true",
            "armed_niches": ["dev_diary"],
        },
        {
            "gate_name": "final_publish_approval",
            "enabled": False,
            "mechanism": "imperative-hold",
            "wired_into": "scheduled_publisher",
            "setting_key": "pipeline_gate_final_publish_approval",
            "pending_count": 0,
        },
    ]


def test_gates_list_renders_default_section_and_table():
    dummy_pool = AsyncMock()
    dummy_pool.close = AsyncMock()
    with (
        patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=dummy_pool),
        ),
        patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "services.approval_service.list_gates",
            new=AsyncMock(return_value=_canned_rows()),
        ),
    ):
        result = CliRunner().invoke(gates_list_command, [])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "DEFAULT PUBLISH GATE" in out
    assert "awaiting_approval" in out
    assert "2 pending" in out
    assert "dev_diary" in out
    assert "CONFIGURABLE GATES" in out
    assert "WIRED INTO" in out
    assert "final_publish_approval" in out
    assert "scheduled_publisher" in out


def test_gates_list_json_is_superset():
    dummy_pool = AsyncMock()
    dummy_pool.close = AsyncMock()
    with (
        patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=dummy_pool),
        ),
        patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "services.approval_service.list_gates",
            new=AsyncMock(return_value=_canned_rows()),
        ),
    ):
        result = CliRunner().invoke(gates_list_command, ["--json"])

    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    # Backcompat: original keys still present on every row.
    for row in payload:
        assert "gate_name" in row
        assert "enabled" in row
        assert "pending_count" in row
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_gates_list_cli.py -q`
Expected: FAIL — output lacks "DEFAULT PUBLISH GATE" / "WIRED INTO" (old flat renderer).

- [ ] **Step 3: Rewrite the renderer**

Replace the body of `gates_list_command` in `src/cofounder_agent/poindexter/cli/approval.py` after the `if json_output:` echo block. Keep everything through the `if json_output: ... return` unchanged; replace the flat-table block (from `if not rows:` to the end of the function) with:

```python
    if not rows:
        click.echo(
            "(no gates configured yet — set one with `poindexter gates set "
            "<gate_name> on`)"
        )
        return

    default_rows = [r for r in rows if r.get("mechanism") == "default"]
    gate_rows = [r for r in rows if r.get("mechanism") != "default"]

    # Default publish gate — the always-on per-post sign-off. This is the gate
    # that actually holds every post; it has no toggle.
    for d in default_rows:
        click.secho(
            "DEFAULT PUBLISH GATE — every post requires per-post sign-off",
            fg="cyan",
            bold=True,
        )
        click.echo(
            f"  {d['gate_name']:<22} always-on    {d.get('pending_count', 0)} pending"
        )
        armed = d.get("armed_niches") or []
        armed_str = ", ".join(armed) if armed else "none"
        click.echo(
            f"  auto-publish: threshold={d.get('auto_publish_threshold')} "
            f"require_human_approval={d.get('require_human_approval')} · "
            f"armed niches: {armed_str}"
        )
        click.echo()

    # Configurable gates — the interrupt-node + imperative-hold toggles.
    click.secho("CONFIGURABLE GATES", fg="cyan", bold=True)
    click.echo(
        f"  {'GATE':<24} {'STATE':<10} {'WIRED INTO':<22} {'PENDING':<8}"
    )
    for row in gate_rows:
        state = "enabled" if row["enabled"] else "disabled"
        color = "green" if row["enabled"] else "yellow"
        click.secho(
            f"  {row['gate_name']:<24} {state:<10} "
            f"{str(row.get('wired_into', '')):<22} {row['pending_count']:<8}",
            fg=color,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_gates_list_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/approval.py src/cofounder_agent/tests/unit/cli/test_gates_list_cli.py
git commit -m "feat(gates): honest gates list renderer — default gate + WIRED INTO" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Docs — CLI reference

**Files:**

- Modify: `docs/operations/cli-reference.md`

**Interfaces:** none (doc-only).

- [ ] **Step 1: Update the `schedule approve/reject` gate note**

In `docs/operations/cli-reference.md`, find the section that begins `### `schedule approve`/`reject`/`pending`/`show-pending``(grep:`poindexter schedule pending`). Replace its descriptive paragraph (currently "Operate the final publish-approval gate on the `posts` table (the gate that fires _after_ scheduling)…") so it states the wiring is live:

```markdown
Operate the `final_publish_approval` gate on the `posts` table — the operator's
last veto before a **scheduled** post is auto-promoted to `published`. When the
gate is enabled, `scheduled_publisher` pauses each due scheduled post here
instead of publishing it (writing `posts.awaiting_gate`), notifies the operator,
and waits; approve clears the gate and the next publisher tick (≤60s) promotes
it. This gate only fires on the scheduled→published timer path — the manual
approve→go-live flow is already a human action and is unaffected.
```

- [ ] **Step 2: Update the `gates list` description**

Find the `poindexter gates list` documentation (grep: `gates list`). Ensure it documents the current output shape; add or update to:

```markdown
`poindexter gates list` shows two groups:

- **DEFAULT PUBLISH GATE** — the always-on `awaiting_approval` per-post sign-off
  (the gate that actually holds every post), with its live pending count and the
  global auto-publish posture (`auto_publish_threshold`, `require_human_approval`)
  plus any niches armed for auto-publish.
- **CONFIGURABLE GATES** — each toggleable gate with its `STATE`
  (`enabled`/`disabled`), `WIRED INTO` (where it fires — e.g. `canonical_blog`,
  `seo_refresh`, `scheduled_publisher`), and `PENDING` count. Toggle with
  `poindexter gates set <gate> on|off`. `--json` emits the full rows (a superset
  of the table, including each gate's `mechanism`: `graph-node` /
  `imperative-hold` / `default`).
```

- [ ] **Step 3: Verify the doc renders and commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_regen_services_doc.py -q` (sanity: doc-adjacent tests still pass; if this test is unrelated, skip).

```bash
git add docs/operations/cli-reference.md
git commit -m "docs(gates): document honest gates list + live final_publish_approval" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run the full affected test set:**

Run:

```bash
cd src/cofounder_agent && poetry run pytest \
  tests/unit/services/test_gate_machinery.py \
  tests/unit/services/test_scheduled_publisher.py \
  tests/unit/test_approval_service.py \
  tests/unit/cli/test_gates_list_cli.py -q
```

Expected: all PASS, 0 failures.

- [ ] **Manual smoke (optional, needs a DB):** `poindexter gates list` shows the DEFAULT PUBLISH GATE section with the live `awaiting_approval` count and a CONFIGURABLE GATES table with a `WIRED INTO` column and `final_publish_approval → scheduled_publisher`.

## Self-Review (completed during authoring)

- **Spec coverage:** Part 1 (publisher wiring, guard, resume, edge case) → Task 2. Part 2 catalog → Task 1; `list_gates` dual-table + catalog → Task 3; default row + posture → Task 4; CLI renderer → Task 5; docs → Task 6. No-migration constraint honored (no migration task). Out-of-scope items (auto-publish niches, manual publish paths, gates table) intentionally have no task.
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `GateSpec` fields (`name`/`mechanism`/`wired_into`/`default_enabled`) are consistent across Tasks 1/3; `list_gates` row keys (`gate_name`/`enabled`/`pending_count`/`mechanism`/`wired_into`/`setting_key`) match between Tasks 3/4 and the CLI/tests in Task 5; `pause_post_at_gate` keyword args in Task 2 match its signature in `posts_approval_service.py`.

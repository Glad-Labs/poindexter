# Social Draft Identification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Subagent-driven execution is disabled in this project (`[[feedback_no_subagent_delegation]]` — subagents bill at metered API rates outside the Max subscription); execute inline, sequentially. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Matt identify which article a pending social draft promotes — by
title and task/post id — directly from the console's SOCIAL DISTRIBUTION
panel, even before that article has published.

**Architecture:** Resolve the promoted article's title and best-available id
server-side, inside `SocialDraftsService.list_drafts()`'s existing query
(extending the join that already computes `resolved_post_status`), thread the
two new fields through the API serializer, and surface them as a new
"Article" column in the console's SOCIAL DISTRIBUTION table — replacing the
"Post ID" column, which is blank for every draft this feature needs to help
with (its post hasn't been backfilled with an id yet).

**Tech Stack:** Python 3.13 / FastAPI / asyncpg (backend), React via the
in-repo vendor build (console, no bundler), pytest (backend tests), Node's
built-in `node:test` (console contract tests).

## Global Constraints

- No schema migration — `pipeline_tasks.topic`, `posts.title`, and `posts.id`
  all already exist; this plan only adds computed columns to an existing
  query.
- No change to `approve_draft`'s post-publish gate behavior (`services/social_drafts.py`) — identification only, not a change to what can be approved.
- No change to CLI (`poindexter social list`) or MCP (`list_social_drafts`)
  output formatting — they receive the two new `SocialDraftRow` fields for
  free via the shared dataclass, but updating their display is out of scope.
- Content-preview / click-through drawer for not-yet-published drafts is
  explicitly out of scope (confirmed with Matt — not needed until the post
  is published, at which point the existing Action Inbox drawer already
  covers it).
- Transport-adapter contract: no SQL or business logic in
  `routes/social_routes.py` — all query logic stays in
  `services/social_drafts.py` (`docs/architecture/2026-06-10-transport-adapter-contract.md`).
- Spec: `docs/superpowers/specs/2026-07-14-social-draft-identification-design.md`.

---

## Running backend tests in this worktree

This worktree has no its own poetry venv. Resolve the main checkout's venv
python once, then reuse it for every `pytest` command in this plan (all run
from the **worktree's** `src/cofounder_agent` as cwd, so first-party imports
resolve to the worktree's code):

```bash
cd "C:/Users/mattm/glad-labs-website/src/cofounder_agent" && poetry env info --path
```

This prints something like
`C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-<hash>---py3.13`
(the `<hash>` changes if the env is ever recreated — always resolve it fresh,
never hardcode a prior value). Call this path `$VENV` below. Every pytest
invocation in this plan is:

```bash
cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/poindexter-epic-762-eae396/src/cofounder_agent" && "$VENV/Scripts/python.exe" -m pytest <test path> -o addopts="" -q -p no:cacheprovider
```

`-o addopts=""` is load-bearing on Windows — it drops the repo's default
`--forked` (pytest-forked needs `os.fork`, absent on Windows).

---

### Task 1: Resolve article title + id in `SocialDraftsService.list_drafts()`

**Files:**

- Modify: `src/cofounder_agent/services/social_drafts.py`
- Test: `src/cofounder_agent/tests/unit/services/test_social_drafts.py`

**Interfaces:**

- Produces: `SocialDraftRow.title: str | None = None` and
  `SocialDraftRow.resolved_post_id: str | None = None` — both default to
  `None` so the existing direct-construction call site in
  `tests/unit/routes/test_social_routes.py` (which doesn't pass them) keeps
  working unchanged. Consumed by Task 2's `_serialize()`.

- [ ] **Step 1: Write the failing tests**

Open `src/cofounder_agent/tests/unit/services/test_social_drafts.py`. Find
the `_list_row` helper (currently ends with
`"approved_at": None, "posted_at": None, "resolved_post_status": None,`) and
add the two new keys so every existing caller of `_list_row()` keeps working:

```python
def _list_row(**overrides) -> dict:
    """A full social_post_drafts row as list_drafts' SELECT d.*, ... returns
    it — includes every column _row_to_dataclass reads, unlike _draft_row
    (which only carries approve_draft's narrower first-fetchrow columns)."""
    row = {
        "id": "d-1", "pipeline_task_id": "task-1", "post_id": None,
        "platform": "twitter", "content": "copy", "platform_config": "{}",
        "status": "pending", "postiz_post_id": None, "error": None,
        "retry_count": 0, "last_retry_at": None, "created_at": None,
        "approved_at": None, "posted_at": None, "resolved_post_status": None,
        "article_title": None, "resolved_post_id": None,
    }
    row.update(overrides)
    return row
```

Then, directly below `test_list_drafts_query_resolves_post_by_id_or_task_metadata`
(and above the `# cancel_orphaned_for_rejected_tasks` section comment), add:

```python
@pytest.mark.asyncio
async def test_list_drafts_title_from_post_when_available():
    """Once a posts row exists, its title (possibly revised post-writing)
    wins over the task's original topic."""
    pool, _conn = _make_pool(
        fetch=[_list_row(article_title="Real Published Title", resolved_post_id="post-99")]
    )
    svc = SocialDraftsService()
    drafts = await svc.list_drafts(None, None, None, pool)
    assert drafts[0].title == "Real Published Title"
    assert drafts[0].resolved_post_id == "post-99"


@pytest.mark.asyncio
async def test_list_drafts_title_falls_back_to_task_topic():
    """Before a posts row exists (task still awaiting_approval), the SQL
    COALESCEs to pipeline_tasks.topic — resolved_post_id stays None."""
    pool, _conn = _make_pool(
        fetch=[_list_row(article_title="Original Task Topic", resolved_post_id=None)]
    )
    svc = SocialDraftsService()
    drafts = await svc.list_drafts(None, None, None, pool)
    assert drafts[0].title == "Original Task Topic"
    assert drafts[0].resolved_post_id is None


@pytest.mark.asyncio
async def test_list_drafts_query_joins_pipeline_tasks_for_title():
    """Locks in the join + COALESCE the title/id resolution depends on —
    same rigor as the existing post-resolution join assertion above."""
    pool, conn = _make_pool(fetch=[])
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool)
    sql = conn.fetch.call_args[0][0].lower()
    assert "join pipeline_tasks" in sql
    assert "pt.task_id = d.pipeline_task_id" in sql
    assert "coalesce(rp.title, pt.topic)" in sql
    assert "coalesce(d.post_id, rp.id)" in sql
```

- [ ] **Step 2: Run tests to verify the new ones fail**

```bash
cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/poindexter-epic-762-eae396/src/cofounder_agent" && "$VENV/Scripts/python.exe" -m pytest tests/unit/services/test_social_drafts.py -o addopts="" -q -p no:cacheprovider
```

Expected: the 3 new tests FAIL —
`test_list_drafts_title_from_post_when_available` and
`test_list_drafts_title_falls_back_to_task_topic` with
`AttributeError: 'SocialDraftRow' object has no attribute 'title'`;
`test_list_drafts_query_joins_pipeline_tasks_for_title` with an `AssertionError`
(the SQL doesn't contain `join pipeline_tasks` yet). All pre-existing tests
in the file still PASS (the two new `_list_row` keys are additive).

- [ ] **Step 3: Implement — dataclass fields**

In `src/cofounder_agent/services/social_drafts.py`, find:

```python
@dataclass
class SocialDraftRow:
    id: str
    pipeline_task_id: str
    post_id: str | None
    platform: str
    content: str
    platform_config: dict[str, Any]
    status: str
    postiz_post_id: str | None
    error: str | None
    retry_count: int
    last_retry_at: datetime | None
    created_at: datetime
    approved_at: datetime | None
    posted_at: datetime | None
    post_status: str | None
```

Replace with:

```python
@dataclass
class SocialDraftRow:
    id: str
    pipeline_task_id: str
    post_id: str | None
    platform: str
    content: str
    platform_config: dict[str, Any]
    status: str
    postiz_post_id: str | None
    error: str | None
    retry_count: int
    last_retry_at: datetime | None
    created_at: datetime
    approved_at: datetime | None
    posted_at: datetime | None
    post_status: str | None
    title: str | None = None
    resolved_post_id: str | None = None
```

- [ ] **Step 4: Implement — the query**

Find the `list_drafts` method:

```python
    async def list_drafts(
        self,
        post_id: str | None,
        pipeline_task_id: str | None,
        status: str | None,
        pool: Any,
    ) -> list[SocialDraftRow]:
        """List drafts, each carrying its resolved post_status.

        post_status lets callers (the console's action inbox) distinguish a
        genuinely-approvable draft from one that would 409 on approve_draft's
        post-link gate (post not 'published' yet, or no posts row at all) —
        the same resolution _resolve_post uses: post_id when linked, else the
        latest posts row matching pipeline_task_id metadata.
        """
        conditions: list[str] = []
        args: list[Any] = []
        if post_id:
            args.append(post_id)
            conditions.append(f"d.post_id = ${len(args)}")
        if pipeline_task_id:
            args.append(pipeline_task_id)
            conditions.append(f"d.pipeline_task_id = ${len(args)}")
        if status:
            args.append(status)
            conditions.append(f"d.status = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT d.*, rp.status AS resolved_post_status
            FROM social_post_drafts d
            LEFT JOIN LATERAL (
                SELECT p.status
                FROM posts p
                WHERE (d.post_id IS NOT NULL AND p.id = d.post_id)
                   OR (d.post_id IS NULL AND p.metadata->>'pipeline_task_id' = d.pipeline_task_id)
                ORDER BY p.created_at DESC
                LIMIT 1
            ) rp ON true
            {where}
            ORDER BY d.created_at DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row_to_dataclass(r) for r in rows]
```

Replace with:

```python
    async def list_drafts(
        self,
        post_id: str | None,
        pipeline_task_id: str | None,
        status: str | None,
        pool: Any,
    ) -> list[SocialDraftRow]:
        """List drafts, each carrying its resolved post_status, title, and id.

        post_status lets callers (the console's action inbox) distinguish a
        genuinely-approvable draft from one that would 409 on approve_draft's
        post-link gate (post not 'published' yet, or no posts row at all) —
        the same resolution _resolve_post uses: post_id when linked, else the
        latest posts row matching pipeline_task_id metadata.

        title/resolved_post_id let callers identify which article a draft
        promotes even before that article publishes: posts.title/posts.id
        win once a posts row exists (can resolve before social_post_drafts.
        post_id itself gets backfilled at publish time), else pipeline_tasks.
        topic is the fallback label for a task with no posts row yet.
        """
        conditions: list[str] = []
        args: list[Any] = []
        if post_id:
            args.append(post_id)
            conditions.append(f"d.post_id = ${len(args)}")
        if pipeline_task_id:
            args.append(pipeline_task_id)
            conditions.append(f"d.pipeline_task_id = ${len(args)}")
        if status:
            args.append(status)
            conditions.append(f"d.status = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT d.*,
                   rp.status AS resolved_post_status,
                   COALESCE(rp.title, pt.topic) AS article_title,
                   COALESCE(d.post_id, rp.id) AS resolved_post_id
            FROM social_post_drafts d
            LEFT JOIN pipeline_tasks pt ON pt.task_id = d.pipeline_task_id
            LEFT JOIN LATERAL (
                SELECT p.id, p.title, p.status
                FROM posts p
                WHERE (d.post_id IS NOT NULL AND p.id = d.post_id)
                   OR (d.post_id IS NULL AND p.metadata->>'pipeline_task_id' = d.pipeline_task_id)
                ORDER BY p.created_at DESC
                LIMIT 1
            ) rp ON true
            {where}
            ORDER BY d.created_at DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row_to_dataclass(r) for r in rows]
```

- [ ] **Step 5: Implement — row mapping**

Find:

```python
def _row_to_dataclass(row: Any) -> SocialDraftRow:
    return SocialDraftRow(
        id=str(row["id"]),
        pipeline_task_id=str(row["pipeline_task_id"]),
        post_id=str(row["post_id"]) if row["post_id"] else None,
        platform=row["platform"],
        content=row["content"],
        platform_config=_parse_jsonb(row["platform_config"]),
        status=row["status"],
        postiz_post_id=row["postiz_post_id"],
        error=row["error"],
        retry_count=row["retry_count"],
        last_retry_at=row["last_retry_at"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        posted_at=row["posted_at"],
        post_status=row["resolved_post_status"],
    )
```

Replace with:

```python
def _row_to_dataclass(row: Any) -> SocialDraftRow:
    return SocialDraftRow(
        id=str(row["id"]),
        pipeline_task_id=str(row["pipeline_task_id"]),
        post_id=str(row["post_id"]) if row["post_id"] else None,
        platform=row["platform"],
        content=row["content"],
        platform_config=_parse_jsonb(row["platform_config"]),
        status=row["status"],
        postiz_post_id=row["postiz_post_id"],
        error=row["error"],
        retry_count=row["retry_count"],
        last_retry_at=row["last_retry_at"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        posted_at=row["posted_at"],
        post_status=row["resolved_post_status"],
        title=row["article_title"],
        resolved_post_id=(
            str(row["resolved_post_id"]) if row["resolved_post_id"] else None
        ),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/poindexter-epic-762-eae396/src/cofounder_agent" && "$VENV/Scripts/python.exe" -m pytest tests/unit/services/test_social_drafts.py -o addopts="" -q -p no:cacheprovider
```

Expected: PASS — every test in the file, including the 3 new ones.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/social_drafts.py src/cofounder_agent/tests/unit/services/test_social_drafts.py
git commit -m "$(cat <<'EOF'
feat(social): resolve article title + id in list_drafts

Lets callers identify which article a pending draft promotes before
that article publishes (posts.title/id once it exists, else the
task's topic) — social_post_drafts.post_id itself stays null until
the linked post is backfilled at publish time.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Serialize `title` + `resolved_post_id` on `/api/social/drafts`

**Files:**

- Modify: `src/cofounder_agent/routes/social_routes.py`
- Test: `src/cofounder_agent/tests/unit/routes/test_social_routes.py`

**Interfaces:**

- Consumes: `SocialDraftRow.title`, `SocialDraftRow.resolved_post_id` (Task 1).
- Produces: `GET /api/social/drafts` response body gains `"title"` and
  `"resolved_post_id"` keys on each draft object — consumed by Task 3's
  console column.

- [ ] **Step 1: Write the failing test**

In `src/cofounder_agent/tests/unit/routes/test_social_routes.py`, inside
`class TestListDraftsSerialization`, add this method directly after
`test_list_drafts_includes_post_status`:

```python
    def test_list_drafts_includes_title_and_resolved_post_id(self, monkeypatch):
        mock_svc = MagicMock()
        mock_svc.list_drafts = AsyncMock(
            return_value=[
                SocialDraftRow(
                    id="draft-1", pipeline_task_id="task-1", post_id=None,
                    platform="bluesky", content="c", platform_config={},
                    status="pending", postiz_post_id=None, error=None,
                    retry_count=0, last_retry_at=None,
                    created_at=datetime.now(timezone.utc),
                    approved_at=None, posted_at=None, post_status=None,
                    title="Why VRAM Bandwidth Matters",
                    resolved_post_id="post-42",
                )
            ]
        )
        monkeypatch.setattr(social_routes_module, "_svc", mock_svc)
        client = TestClient(_build_social_app())

        resp = client.get("/api/social/drafts")

        assert resp.status_code == 200
        draft = resp.json()["drafts"][0]
        assert draft["title"] == "Why VRAM Bandwidth Matters"
        assert draft["resolved_post_id"] == "post-42"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/poindexter-epic-762-eae396/src/cofounder_agent" && "$VENV/Scripts/python.exe" -m pytest tests/unit/routes/test_social_routes.py -o addopts="" -q -p no:cacheprovider
```

Expected: `test_list_drafts_includes_title_and_resolved_post_id` FAILS with
`KeyError: 'title'` (the serializer doesn't emit it yet). All other tests in
the file still PASS.

- [ ] **Step 3: Implement**

In `src/cofounder_agent/routes/social_routes.py`, find:

```python
def _serialize(d: SocialDraftRow) -> dict[str, Any]:
    return {
        "id": d.id,
        "pipeline_task_id": d.pipeline_task_id,
        "post_id": d.post_id,
        "post_status": d.post_status,
        "platform": d.platform,
        "content": d.content,
        "platform_config": d.platform_config,
        "status": d.status,
        "postiz_post_id": d.postiz_post_id,
        "error": d.error,
        "retry_count": d.retry_count,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "approved_at": d.approved_at.isoformat() if d.approved_at else None,
        "posted_at": d.posted_at.isoformat() if d.posted_at else None,
    }
```

Replace with:

```python
def _serialize(d: SocialDraftRow) -> dict[str, Any]:
    return {
        "id": d.id,
        "pipeline_task_id": d.pipeline_task_id,
        "post_id": d.post_id,
        "post_status": d.post_status,
        "platform": d.platform,
        "content": d.content,
        "platform_config": d.platform_config,
        "status": d.status,
        "postiz_post_id": d.postiz_post_id,
        "error": d.error,
        "retry_count": d.retry_count,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "approved_at": d.approved_at.isoformat() if d.approved_at else None,
        "posted_at": d.posted_at.isoformat() if d.posted_at else None,
        "title": d.title,
        "resolved_post_id": d.resolved_post_id,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/poindexter-epic-762-eae396/src/cofounder_agent" && "$VENV/Scripts/python.exe" -m pytest tests/unit/routes/test_social_routes.py -o addopts="" -q -p no:cacheprovider
```

Expected: PASS — every test in the file.

- [ ] **Step 5: Confirm the console contract test still passes (no request-shape change)**

`socialDrafts` in `console/js/__tests__/contracts/contracts.manifest.js` is a
tier-1 request-only entry (`GET /api/social/drafts`, no `shape`/`openapi`
anchor — the route declares no `response_model`), so adding response fields
needs no fixture or snapshot update. Confirm this holds:

```bash
cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/poindexter-epic-762-eae396" && npm run test:console
```

Expected: PASS (offline, no live backend needed).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/routes/social_routes.py src/cofounder_agent/tests/unit/routes/test_social_routes.py
git commit -m "$(cat <<'EOF'
feat(api): serialize title + resolved_post_id on /api/social/drafts

Threads the two new SocialDraftRow fields through the route response
so the console can identify which article a pending draft promotes.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Console — replace "Post ID" with an "Article" column

**Files:**

- Modify: `src/cofounder_agent/console/js/panels2.jsx`
- Modify: `src/cofounder_agent/console/js/api.js` (comment only)

**Interfaces:**

- Consumes: `title`, `resolved_post_id`, `pipeline_task_id` fields on each
  draft object in the `/api/social/drafts` response (Task 2).

This task has no new automated test: this codebase doesn't unit-test
individual console panel components (only the data/api layer and contract
shapes under `console/js/__tests__/` — confirmed by the file listing), and
the display logic being added here (a two-branch label choice + 8-char
truncation) is the same complexity class as the untested code it replaces.
Verification is the existing offline contract suite (sanity check) plus a
manual browser check.

- [ ] **Step 1: Update the panel's header comment**

In `src/cofounder_agent/console/js/panels2.jsx`, find:

```jsx
/* ─── Social Distribution — per-post per-platform draft queue ── */
// GET /api/social/drafts → {drafts:[…]}. Shows every draft's status, error,
// retry count, and Postiz post ID — granularity the Grafana aggregate stats
// don't have. Pending drafts surface here AND in the action inbox (kind='social').
// No mock data: honest-empty in mock mode (no fabricated draft rows).
```

Replace with:

```jsx
/* ─── Social Distribution — per-post per-platform draft queue ── */
// GET /api/social/drafts → {drafts:[…]}. Shows every draft's status, error,
// retry count, Postiz post ID, and the article it promotes (title +
// resolved post/task id — resolved server-side since post_id itself stays
// null until the linked post publishes) — granularity the Grafana aggregate
// stats don't have. Pending drafts surface here AND in the action inbox
// (kind='social'), but only once their post is published (see draftToInbox
// in app.jsx); this table is the only place to identify a draft before that.
// No mock data: honest-empty in mock mode (no fabricated draft rows).
```

- [ ] **Step 2: Rename the column header**

Still in `panels2.jsx`, find:

```jsx
                {[
                  'Platform',
                  'Status',
                  'Post ID',
                  'Error',
                  'Retries',
                  'Postiz ID',
                  'Posted',
                  '',
                ].map((h) => (
```

Replace with:

```jsx
                {[
                  'Platform',
                  'Status',
                  'Article',
                  'Error',
                  'Retries',
                  'Postiz ID',
                  'Posted',
                  '',
                ].map((h) => (
```

- [ ] **Step 3: Replace the cell**

Still in `panels2.jsx`, find:

```jsx
<td
  className="mono c-dim"
  style={{
    padding: '6px 10px',
    maxWidth: 90,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontSize: 10,
  }}
  title={d.post_id || ''}
>
  {d.post_id ? String(d.post_id).slice(0, 8) : '—'}
</td>
```

Replace with:

```jsx
<td style={{ padding: '6px 10px', maxWidth: 200 }}>
  <div
    style={{
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap',
      fontSize: 11,
    }}
    title={d.title || ''}
  >
    {d.title || '—'}
  </div>
  <div
    className="mono c-dim"
    style={{ fontSize: 9, marginTop: 2 }}
    title={d.resolved_post_id || d.pipeline_task_id || ''}
  >
    {d.resolved_post_id
      ? `POST ${String(d.resolved_post_id).slice(0, 8)}`
      : `TASK ${String(d.pipeline_task_id).slice(0, 8)}`}
  </div>
</td>
```

- [ ] **Step 4: Update the `api.js` field-list comment**

In `src/cofounder_agent/console/js/api.js`, find:

```js
// ── social / Postiz draft queue (social_routes.py) ───────
// GET /api/social/drafts → {drafts:[…]} — filterable by post_id/task_id/
// status. Returns id, pipeline_task_id, post_id, platform, content,
// platform_config, status, postiz_post_id, error, retry_count, and three
// timestamps (created_at / approved_at / posted_at). Per-post + per-
// platform granularity the aggregate Prometheus counters can't provide.
// Mock returns honest-empty (no fabricated draft rows).
```

Replace with:

```js
// ── social / Postiz draft queue (social_routes.py) ───────
// GET /api/social/drafts → {drafts:[…]} — filterable by post_id/task_id/
// status. Returns id, pipeline_task_id, post_id, platform, content,
// platform_config, status, postiz_post_id, error, retry_count, title,
// resolved_post_id, and three timestamps (created_at / approved_at /
// posted_at). Per-post + per-platform granularity the aggregate
// Prometheus counters can't provide. Mock returns honest-empty (no
// fabricated draft rows).
```

- [ ] **Step 5: Run the offline console test suite (sanity check)**

```bash
cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/poindexter-epic-762-eae396" && npm run test:console
```

Expected: PASS — this change touches no code any contract/data-layer test
covers, so this is a regression check, not new coverage.

- [ ] **Step 6: Manual verification**

This step needs a running backend and a browser — if that's not available in
your environment, say so explicitly rather than claiming the UI was verified.

Find a real pending draft to check against (no fabricated test data):

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT id, platform, status, pipeline_task_id, post_id FROM social_post_drafts WHERE status = 'pending' LIMIT 5;"
```

Then:

1. Start the backend worker (`npm run dev:cofounder`, or confirm the Docker
   stack is already up) and open the console.
2. Find the "SOCIAL DISTRIBUTION" panel and locate one of the pending rows
   from the query above.
3. Confirm the "Article" column shows a real title (not "—", unless
   `pipeline_tasks.topic` is somehow genuinely empty) and, below it, either
   `POST <8 chars>` or `TASK <8 chars>` matching the `post_id`/`pipeline_task_id`
   from the query.
4. Hover the title to confirm the full (untruncated) title shows in the
   tooltip.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/console/js/panels2.jsx src/cofounder_agent/console/js/api.js
git commit -m "$(cat <<'EOF'
feat(console): show article title/id on pending social drafts

Replaces the SOCIAL DISTRIBUTION panel's "Post ID" column (blank for
every draft whose post hasn't published yet) with an "Article" column
showing the promoted post's title and best-available id, so pending
drafts are identifiable before they reach the Action Inbox.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

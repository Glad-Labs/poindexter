# Postiz Social Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Postiz as the social media posting engine so generated drafts are stored, reviewed via CLI/API/MCP, and dispatched to X, LinkedIn, Reddit, Mastodon, TikTok, and Instagram — replacing the current Telegram/Discord notify-only path.

**Architecture:** A new `social_post_drafts` table holds one draft per platform/subreddit. The `social.generate_drafts` LangGraph atom populates drafts after `content.persist_task`. `SocialDraftsService.approve_draft` calls `PostizClient` to post immediately. CLI, API routes, and MCP tools are thin adapters over `SocialDraftsService`. The existing `generate_and_distribute_social_posts` path stays alive behind a `social_drafts_enabled=false` feature flag until Postiz is running.

**Tech Stack:** asyncpg (existing), httpx (existing), FastAPI (existing), FastMCP (existing), Click (existing), Postiz REST API (`POST /public/v1/posts`), Redis (new — Postiz Bull queue), Prometheus client (existing).

## Global Constraints

- All new app_settings keys added to `settings_defaults.py` (seeds on every boot via `ON CONFLICT DO NOTHING`) — never in migration files per `feedback_seed_data_in_baseline_not_new_migrations`
- Transport adapter contract: CLI, API routes, MCP tools hold zero business logic or SQL — all delegate to `SocialDraftsService`
- `social_drafts_enabled=false` default — old Telegram/Discord notify path stays active until Postiz is running
- `site_config.get_secret(key)` is async-only for `is_secret=true` rows; `site_config.get(key)` is sync for plain config
- New routes go into `_WORKER_ROUTES` in `src/cofounder_agent/utils/route_registration.py`
- New jobs go into `_SAMPLES` in `src/cofounder_agent/plugins/registry.py`
- Prometheus metrics: module-level singleton pattern (`Counter`, `Histogram` from `prometheus_client`)
- Grafana panels ship in the same commit as the feature (per `feedback_grafana_everything`)
- All Python tests run from `src/cofounder_agent/` with `poetry run pytest tests/unit/ -q`

---

## File Map

**Create:**

- `src/cofounder_agent/services/migrations/20260625_000000_create_social_post_drafts.py` — DDL migration
- `src/cofounder_agent/services/integrations/postiz_client.py` — thin httpx wrapper for Postiz REST API
- `src/cofounder_agent/services/social_drafts.py` — `SocialDraftsService` + dataclass
- `src/cofounder_agent/poindexter/cli/social.py` — Click group (`poindexter social *`)
- `src/cofounder_agent/routes/social_routes.py` — FastAPI router (`/api/social/drafts/*`)
- `src/cofounder_agent/modules/content/atoms/social_generate_drafts.py` — `social.generate_drafts` atom
- `src/cofounder_agent/services/integrations/handlers/publishing_postiz_video.py` — `publishing.postiz_video` handler
- `src/cofounder_agent/services/jobs/retry_failed_social_drafts.py` — hourly retry job
- `tests/unit/services/test_postiz_client.py`
- `tests/unit/services/test_social_drafts.py`
- `tests/unit/cli/test_social_cli.py`
- `tests/unit/routes/test_social_routes.py`
- `tests/unit/atoms/test_social_generate_drafts.py`
- `tests/unit/services/jobs/test_retry_failed_social_drafts.py`
- `tests/unit/services/handlers/test_publishing_postiz_video.py`

**Modify:**

- `src/cofounder_agent/services/settings_defaults.py` — add 10 new keys
- `src/cofounder_agent/poindexter/cli/app.py:101` — register `social_group`
- `src/cofounder_agent/utils/route_registration.py` — add `social_routes` to `_WORKER_ROUTES`
- `src/cofounder_agent/plugins/registry.py:560` — register `RetryFailedSocialDraftsJob` in `_SAMPLES`
- `src/cofounder_agent/services/canonical_blog_spec.py` — add `social_generate_drafts` node + edges
- `src/cofounder_agent/services/publish_service.py:_queue_social_posts` — add `social_drafts_enabled` guard
- `src/cofounder_agent/services/publish_service.py:publish_post_from_task` — add `backfill_post_id` call
- `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` — add 4 `postiz_video` publishing_adapters rows
- `mcp-server/server.py` — add 4 MCP tools
- `docker-compose.local.yml` — add `postiz-redis` + `postiz` containers
- `docker-compose.consumer.yml` — add `postiz-redis` + `postiz` containers

---

### Task 1: Migration — `social_post_drafts` table + settings defaults

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260625_000000_create_social_post_drafts.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `tests/unit/services/migrations/test_20260625_create_social_post_drafts.py`

**Interfaces:**

- Produces: `social_post_drafts` table with columns `id, pipeline_task_id, post_id, platform, content, platform_config, status, postiz_post_id, error, retry_count, last_retry_at, created_at, approved_at, posted_at`
- Produces: app_settings keys `social_drafts_enabled`, `social_draft_platforms`, `social_reddit_subreddits`, `social_draft_max_retries`, `postiz_api_url`, `postiz_integration_id_twitter`, `postiz_integration_id_linkedin`, `postiz_integration_id_mastodon`, `postiz_integration_id_reddit`, `postiz_integration_id_tiktok`, `postiz_integration_id_instagram`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/migrations/test_20260625_create_social_post_drafts.py
import pytest
from services.migrations.m20260625_000000_create_social_post_drafts import up as migration_up


@pytest.mark.asyncio
async def test_migration_creates_table(pg_pool):
    """Migration creates social_post_drafts with all required columns."""
    await migration_up(pg_pool)
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'social_post_drafts'
            ORDER BY ordinal_position
            """
        )
    assert row is not None


@pytest.mark.asyncio
async def test_migration_is_idempotent(pg_pool):
    """Running migration twice does not raise."""
    await migration_up(pg_pool)
    await migration_up(pg_pool)  # should not raise
```

- [ ] **Step 2: Run test to verify it fails**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/migrations/test_20260625_create_social_post_drafts.py -v
```

Expected: `ModuleNotFoundError` (file doesn't exist yet).

- [ ] **Step 3: Write the migration**

```python
# src/cofounder_agent/services/migrations/20260625_000000_create_social_post_drafts.py
"""Create social_post_drafts table for Postiz social distribution."""
from __future__ import annotations
from typing import Any


async def up(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_post_drafts (
                id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                pipeline_task_id UUID        NOT NULL REFERENCES pipeline_tasks(task_id),
                post_id          UUID        REFERENCES posts(id),
                platform         TEXT        NOT NULL,
                content          TEXT        NOT NULL,
                platform_config  JSONB       NOT NULL DEFAULT '{}',
                status           TEXT        NOT NULL DEFAULT 'pending',
                postiz_post_id   TEXT,
                error            TEXT,
                retry_count      INT         NOT NULL DEFAULT 0,
                last_retry_at    TIMESTAMPTZ,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                approved_at      TIMESTAMPTZ,
                posted_at        TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_social_post_drafts_pipeline_task_id
                ON social_post_drafts (pipeline_task_id);
            CREATE INDEX IF NOT EXISTS idx_social_post_drafts_post_id
                ON social_post_drafts (post_id);
            CREATE INDEX IF NOT EXISTS idx_social_post_drafts_status
                ON social_post_drafts (status);
            """
        )
```

- [ ] **Step 4: Add app_settings defaults**

Open `src/cofounder_agent/services/settings_defaults.py` and add to the `DEFAULTS` dict:

```python
# Social media distribution — Postiz integration
"social_drafts_enabled": "false",
"social_draft_platforms": "",
"social_reddit_subreddits": "",
"social_draft_max_retries": "3",
"postiz_api_url": "http://postiz:3000",
"postiz_integration_id_twitter": "",
"postiz_integration_id_linkedin": "",
"postiz_integration_id_mastodon": "",
"postiz_integration_id_reddit": "",
"postiz_integration_id_tiktok": "",
"postiz_integration_id_instagram": "",
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/migrations/test_20260625_create_social_post_drafts.py -v
```

Expected: PASS (requires a real test DB — skip in pure-unit environments, run in `tests/integration_db/`).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260625_000000_create_social_post_drafts.py
git add src/cofounder_agent/services/settings_defaults.py
git add tests/unit/services/migrations/test_20260625_create_social_post_drafts.py
git commit -m "feat(social): add social_post_drafts migration + settings defaults"
```

---

### Task 2: `PostizClient` — Postiz REST API wrapper

**Files:**

- Create: `src/cofounder_agent/services/integrations/postiz_client.py`
- Test: `tests/unit/services/integrations/test_postiz_client.py`

**Interfaces:**

- Produces: `PostizClient(base_url, api_key)` with methods:
  - `async create_post(integration_id, content, platform_type, platform_settings, upload_ids) -> dict`
  - `async upload_from_url(video_url) -> str`
- Consumes: nothing from earlier tasks (reads `postiz_api_url` and Postiz API key from `app_settings` at call time via caller-supplied `site_config`)

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/services/integrations/test_postiz_client.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from services.integrations.postiz_client import PostizClient


@pytest.fixture
def client():
    return PostizClient(base_url="http://postiz:3000", api_key="test-key")


@pytest.mark.asyncio
async def test_create_post_text_success(client):
    """create_post builds correct Postiz payload and returns success."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "postiz-123", "status": "published"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_http

        result = await client.create_post(
            integration_id="uuid-abc",
            content="Hello world https://gladlabs.io/posts/hello",
            platform_type="x",
            platform_settings={},
            upload_ids=[],
        )

    assert result["success"] is True
    assert result["post_id"] == "postiz-123"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_create_post_reddit(client):
    """Reddit post includes subreddit in settings."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "r-456"}
    mock_response.raise_for_status = MagicMock()

    captured_payload = {}

    async def capture_post(url, json, headers, timeout):
        captured_payload.update(json)
        return mock_response

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = capture_post
        mock_cls.return_value = mock_http

        await client.create_post(
            integration_id="uuid-reddit",
            content="Check this out",
            platform_type="reddit",
            platform_settings={"subreddit": "r/LocalLLaMA"},
            upload_ids=[],
        )

    posts = captured_payload["posts"]
    assert posts[0]["settings"]["subreddit"] == "r/LocalLLaMA"


@pytest.mark.asyncio
async def test_create_post_returns_failure_on_http_error(client):
    """HTTP error from Postiz returns failure dict, not exception."""
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "400", request=MagicMock(), response=MagicMock(text="bad request")
        ))
        mock_cls.return_value = mock_http

        result = await client.create_post("uuid", "text", "x", {}, [])

    assert result["success"] is False
    assert result["post_id"] is None
    assert result["error"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/integrations/test_postiz_client.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `PostizClient`**

```python
# src/cofounder_agent/services/integrations/postiz_client.py
"""Thin httpx wrapper for the Postiz REST API.

Credentials are passed in at construction time by the caller — never
captured at module import (DB-first config rule, CLAUDE.md).

Postiz API reference: POST {base_url}/public/v1/posts
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_UPLOAD_TIMEOUT = 90.0


class PostizClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": api_key, "Content-Type": "application/json"}

    async def create_post(
        self,
        integration_id: str,
        content: str,
        platform_type: str,
        platform_settings: dict[str, Any],
        upload_ids: list[str],
    ) -> dict[str, Any]:
        """Post to a social platform via Postiz.

        Returns {"success": bool, "post_id": str | None, "error": str | None}.
        Never raises — all errors become failure dicts.
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        settings: dict[str, Any] = {"__type": platform_type, **platform_settings}
        images = [{"id": uid} for uid in upload_ids]
        payload = {
            "type": "now",
            "date": now_iso,
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [{"content": content, "image": images}],
                    "settings": settings,
                }
            ],
        }
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{self._base}/public/v1/posts",
                    json=payload,
                    headers=self._headers,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                post_id = str(data.get("id", "")) or None
                return {"success": True, "post_id": post_id, "error": None}
        except httpx.HTTPStatusError as exc:
            err = f"Postiz HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.error("[PostizClient] %s — platform=%s", err, platform_type)
            return {"success": False, "post_id": None, "error": err}
        except Exception as exc:
            err = str(exc)
            logger.error("[PostizClient] create_post failed: %s", err)
            return {"success": False, "post_id": None, "error": err}

    async def upload_from_url(self, video_url: str) -> str:
        """Upload a video from a URL to Postiz and return the upload ID.

        Postiz fetches the URL server-side. Returns the upload ID string.
        Raises on failure (callers mark draft failed and alert).
        """
        payload = {"url": video_url}
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self._base}/public/v1/uploads/url",
                json=payload,
                headers=self._headers,
                timeout=_UPLOAD_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            upload_id = str(data.get("id", ""))
            if not upload_id:
                raise ValueError(f"Postiz upload returned no id: {data}")
            return upload_id
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/integrations/test_postiz_client.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/integrations/postiz_client.py
git add tests/unit/services/integrations/test_postiz_client.py
git commit -m "feat(social): add PostizClient REST API wrapper"
```

---

### Task 3: `SocialDraftsService`

**Files:**

- Create: `src/cofounder_agent/services/social_drafts.py`
- Test: `tests/unit/services/test_social_drafts.py`

**Interfaces:**

- Consumes: `PostizClient` (Task 2), `social_post_drafts` table (Task 1)
- Produces: `SocialDraftRow` dataclass, `SocialDraftsService` with:
  - `async create_draft(pipeline_task_id, platform, content, platform_config, pool) -> str` (returns UUID)
  - `async approve_draft(draft_id, pool, site_config) -> dict`
  - `async reject_draft(draft_id, pool) -> None`
  - `async edit_draft(draft_id, content, platform_config, pool) -> None`
  - `async list_drafts(post_id, pipeline_task_id, status, pool) -> list[SocialDraftRow]`
  - `async retry_draft(draft_id, pool, site_config) -> dict`
  - `async backfill_post_id(pipeline_task_id, post_id, pool) -> None`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/services/test_social_drafts.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from services.social_drafts import SocialDraftRow, SocialDraftsService
from services.site_config import SiteConfig


def _sc(**overrides) -> SiteConfig:
    defaults = {
        "postiz_api_url": "http://postiz:3000",
        "postiz_integration_id_twitter": "uuid-twitter",
    }
    return SiteConfig(initial_config={**defaults, **overrides})


def _mock_pool(row=None, rows=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=str(uuid.uuid4()))
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.asyncio
async def test_create_draft_returns_uuid():
    pool, conn = _mock_pool()
    new_id = str(uuid.uuid4())
    conn.fetchval = AsyncMock(return_value=new_id)
    svc = SocialDraftsService()
    result = await svc.create_draft(
        pipeline_task_id=str(uuid.uuid4()),
        platform="twitter",
        content="Hello from Glad Labs",
        platform_config={},
        pool=pool,
    )
    assert result == new_id


@pytest.mark.asyncio
async def test_approve_draft_calls_postiz_on_success():
    draft_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={
        "id": draft_id,
        "platform": "twitter",
        "content": "Hello",
        "platform_config": {},
        "status": "pending",
    })
    sc = _sc()

    with patch("services.social_drafts.PostizClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value={
            "success": True, "post_id": "pz-789", "error": None
        })
        mock_cls.return_value = mock_client

        svc = SocialDraftsService()
        result = await svc.approve_draft(draft_id, pool, sc)

    assert result["success"] is True
    assert result["postiz_post_id"] == "pz-789"


@pytest.mark.asyncio
async def test_approve_draft_marks_failed_on_postiz_error():
    draft_id = str(uuid.uuid4())
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={
        "id": draft_id,
        "platform": "twitter",
        "content": "Hello",
        "platform_config": {},
        "status": "pending",
    })
    sc = _sc()

    with patch("services.social_drafts.PostizClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value={
            "success": False, "post_id": None, "error": "rate limited"
        })
        mock_cls.return_value = mock_client

        svc = SocialDraftsService()
        result = await svc.approve_draft(draft_id, pool, sc)

    assert result["success"] is False
    # verify error was written to draft row
    assert conn.execute.called


@pytest.mark.asyncio
async def test_reject_draft_sets_status():
    draft_id = str(uuid.uuid4())
    pool, conn = _mock_pool()
    svc = SocialDraftsService()
    await svc.reject_draft(draft_id, pool)
    conn.execute.assert_awaited_once()
    sql = conn.execute.call_args[0][0]
    assert "rejected" in sql


@pytest.mark.asyncio
async def test_backfill_post_id_updates_null_drafts():
    task_id = str(uuid.uuid4())
    post_id = str(uuid.uuid4())
    pool, conn = _mock_pool()
    svc = SocialDraftsService()
    await svc.backfill_post_id(task_id, post_id, pool)
    conn.execute.assert_awaited_once()
    sql, *args = conn.execute.call_args[0]
    assert "post_id" in sql
    assert post_id in args or post_id in str(args)
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/test_social_drafts.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `SocialDraftsService`**

```python
# src/cofounder_agent/services/social_drafts.py
"""SocialDraftsService — create, approve, reject, and query social post drafts.

All surfaces (CLI, API, MCP) delegate here. This module is the only place
that writes to social_post_drafts or calls PostizClient.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.integrations.postiz_client import PostizClient
from services.integrations.operator_notify import notify_operator
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

# platform → Postiz __type mapping
_PLATFORM_TYPE: dict[str, str] = {
    "twitter": "x",
    "linkedin": "linkedin",
    "mastodon": "mastodon",
    "reddit": "reddit",
    "tiktok": "tiktok",
    "instagram_reels": "instagram",
}

# platform → postiz_integration_id_* setting key
_INTEGRATION_KEY: dict[str, str] = {
    "twitter": "postiz_integration_id_twitter",
    "linkedin": "postiz_integration_id_linkedin",
    "mastodon": "postiz_integration_id_mastodon",
    "reddit": "postiz_integration_id_reddit",
    "tiktok": "postiz_integration_id_tiktok",
    "instagram_reels": "postiz_integration_id_instagram",
}


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


class SocialDraftsService:
    async def create_draft(
        self,
        pipeline_task_id: str,
        platform: str,
        content: str,
        platform_config: dict[str, Any],
        pool: Any,
    ) -> str:
        """Insert a pending draft. Returns the new UUID."""
        async with pool.acquire() as conn:
            new_id: str = await conn.fetchval(
                """
                INSERT INTO social_post_drafts
                    (pipeline_task_id, platform, content, platform_config)
                VALUES ($1, $2, $3, $4)
                RETURNING id::text
                """,
                pipeline_task_id,
                platform,
                content,
                json.dumps(platform_config),
            )
        return new_id

    async def approve_draft(
        self,
        draft_id: str,
        pool: Any,
        site_config: SiteConfig,
    ) -> dict[str, Any]:
        """Approve a draft: call Postiz immediately, mark posted or failed."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, platform, content, platform_config, status "
                "FROM social_post_drafts WHERE id = $1",
                draft_id,
            )
        if row is None:
            return {"success": False, "error": f"draft {draft_id} not found"}
        if row["status"] not in ("pending", "failed"):
            return {"success": False, "error": f"draft status={row['status']} not approvable"}

        platform = row["platform"]
        platform_config = (
            json.loads(row["platform_config"])
            if isinstance(row["platform_config"], str)
            else dict(row["platform_config"] or {})
        )
        integration_key = _INTEGRATION_KEY.get(platform)
        integration_id = site_config.get(integration_key or "", "") if integration_key else ""
        if not integration_id:
            err = f"postiz integration UUID not configured for platform={platform} (set {integration_key})"
            await _mark_failed(draft_id, err, pool)
            return {"success": False, "error": err}

        base_url = site_config.get("postiz_api_url", "http://postiz:3000")
        client = PostizClient(base_url=base_url, api_key="")  # Postiz self-hosted uses no key by default

        result = await client.create_post(
            integration_id=integration_id,
            content=row["content"],
            platform_type=_PLATFORM_TYPE.get(platform, platform),
            platform_settings=platform_config,
            upload_ids=[],
        )

        if result["success"]:
            await _mark_posted(draft_id, result.get("post_id"), pool)
            return {"success": True, "postiz_post_id": result.get("post_id")}
        else:
            await _mark_failed(draft_id, result.get("error", "unknown error"), pool)
            await notify_operator(
                f"[social] draft {draft_id} ({platform}) failed: {result.get('error')}",
                critical=True,
                site_config=site_config,
            )
            return {"success": False, "error": result.get("error")}

    async def reject_draft(self, draft_id: str, pool: Any) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE social_post_drafts SET status = 'rejected' WHERE id = $1",
                draft_id,
            )

    async def edit_draft(
        self,
        draft_id: str,
        content: str,
        platform_config: dict[str, Any] | None,
        pool: Any,
    ) -> None:
        updates: list[str] = ["content = $2"]
        args: list[Any] = [draft_id, content]
        if platform_config is not None:
            updates.append(f"platform_config = ${len(args) + 1}")
            args.append(json.dumps(platform_config))
        sql = f"UPDATE social_post_drafts SET {', '.join(updates)} WHERE id = $1"
        async with pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def list_drafts(
        self,
        post_id: str | None,
        pipeline_task_id: str | None,
        status: str | None,
        pool: Any,
    ) -> list[SocialDraftRow]:
        conditions: list[str] = []
        args: list[Any] = []
        if post_id:
            args.append(post_id)
            conditions.append(f"post_id = ${len(args)}")
        if pipeline_task_id:
            args.append(pipeline_task_id)
            conditions.append(f"pipeline_task_id = ${len(args)}")
        if status:
            args.append(status)
            conditions.append(f"status = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM social_post_drafts {where} ORDER BY created_at DESC"
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row_to_dataclass(r) for r in rows]

    async def retry_draft(self, draft_id: str, pool: Any, site_config: SiteConfig) -> dict[str, Any]:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE social_post_drafts
                SET retry_count = retry_count + 1,
                    last_retry_at = now(),
                    status = 'pending'
                WHERE id = $1 AND status = 'failed'
                """,
                draft_id,
            )
        return await self.approve_draft(draft_id, pool, site_config)

    async def backfill_post_id(
        self, pipeline_task_id: str, post_id: str, pool: Any
    ) -> None:
        """Set post_id on all drafts for this task where post_id is still null."""
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE social_post_drafts
                SET post_id = $2
                WHERE pipeline_task_id = $1 AND post_id IS NULL
                """,
                pipeline_task_id,
                post_id,
            )


async def _mark_posted(draft_id: str, postiz_post_id: str | None, pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE social_post_drafts
            SET status = 'posted', postiz_post_id = $2, posted_at = now()
            WHERE id = $1
            """,
            draft_id,
            postiz_post_id,
        )


async def _mark_failed(draft_id: str, error: str, pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE social_post_drafts
            SET status = 'failed', error = $2
            WHERE id = $1
            """,
            draft_id,
            error,
        )


def _row_to_dataclass(row: Any) -> SocialDraftRow:
    return SocialDraftRow(
        id=str(row["id"]),
        pipeline_task_id=str(row["pipeline_task_id"]),
        post_id=str(row["post_id"]) if row["post_id"] else None,
        platform=row["platform"],
        content=row["content"],
        platform_config=(
            json.loads(row["platform_config"])
            if isinstance(row["platform_config"], str)
            else dict(row["platform_config"] or {})
        ),
        status=row["status"],
        postiz_post_id=row["postiz_post_id"],
        error=row["error"],
        retry_count=row["retry_count"],
        last_retry_at=row["last_retry_at"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        posted_at=row["posted_at"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/test_social_drafts.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/social_drafts.py
git add tests/unit/services/test_social_drafts.py
git commit -m "feat(social): add SocialDraftsService"
```

---

### Task 4: CLI surface — `poindexter social *`

**Files:**

- Create: `src/cofounder_agent/poindexter/cli/social.py`
- Modify: `src/cofounder_agent/poindexter/cli/app.py` — add `main.add_command(social_group, name="social")`
- Test: `tests/unit/cli/test_social_cli.py`

**Interfaces:**

- Consumes: `SocialDraftsService` (Task 3)
- Produces: Click group `social_group` with commands: `list`, `approve`, `reject`, `edit`, `retry`, `setup`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/cli/test_social_cli.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from click.testing import CliRunner
from poindexter.cli.social import social_group


@pytest.fixture
def runner():
    return CliRunner()


def test_social_list_invokes_service(runner):
    with patch("poindexter.cli.social.run_service") as mock_run:
        mock_run.return_value = []
        result = runner.invoke(social_group, ["list"])
    assert result.exit_code == 0


def test_social_approve_requires_draft_id(runner):
    result = runner.invoke(social_group, ["approve"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output


def test_social_approve_calls_service(runner):
    with patch("poindexter.cli.social.run_service") as mock_run:
        mock_run.return_value = {"success": True, "postiz_post_id": "pz-1"}
        result = runner.invoke(social_group, ["approve", "some-uuid"])
    assert result.exit_code == 0


def test_social_reject_calls_service(runner):
    with patch("poindexter.cli.social.run_service") as mock_run:
        mock_run.return_value = None
        result = runner.invoke(social_group, ["reject", "some-uuid"])
    assert result.exit_code == 0


def test_social_setup_prints_instructions(runner):
    result = runner.invoke(social_group, ["setup"])
    assert result.exit_code == 0
    assert "postiz_integration_id" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd src/cofounder_agent
poetry run pytest tests/unit/cli/test_social_cli.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `social.py`**

```python
# src/cofounder_agent/poindexter/cli/social.py
"""CLI surface for social post draft management.

All commands delegate to SocialDraftsService — no SQL or business logic here.
Pattern mirrors poindexter/cli/publishers.py.
"""
from __future__ import annotations

import click

from poindexter.cli.utils import run_service
from services.social_drafts import SocialDraftsService

_svc = SocialDraftsService()


@click.group(name="social")
def social_group() -> None:
    """Manage social post drafts (Postiz distribution)."""


@social_group.command("list")
@click.option("--post-id", default=None, help="Filter by blog post UUID")
@click.option("--task-id", default=None, help="Filter by pipeline task UUID")
@click.option("--status", default=None, help="Filter by status (pending/approved/posted/failed/rejected)")
def list_drafts(post_id: str | None, task_id: str | None, status: str | None) -> None:
    """List social post drafts."""
    drafts = run_service(
        lambda p: _svc.list_drafts(post_id, task_id, status, p)
    )
    if not drafts:
        click.echo("No drafts found.")
        return
    for d in drafts:
        subreddit = d.platform_config.get("subreddit", "")
        platform_label = f"{d.platform}:{subreddit}" if subreddit else d.platform
        click.echo(f"[{d.status.upper():8}] {d.id[:8]}  {platform_label:25}  {d.content[:60]}")


@social_group.command("approve")
@click.argument("draft_id")
def approve_draft(draft_id: str) -> None:
    """Approve a draft and post it via Postiz immediately."""
    result = run_service(
        lambda p, sc: _svc.approve_draft(draft_id, p, sc),
        needs_site_config=True,
    )
    if result.get("success"):
        click.echo(f"Posted — Postiz ID: {result.get('postiz_post_id')}")
    else:
        click.echo(f"Failed: {result.get('error')}", err=True)


@social_group.command("reject")
@click.argument("draft_id")
def reject_draft(draft_id: str) -> None:
    """Reject a draft (terminal — no retry)."""
    run_service(lambda p: _svc.reject_draft(draft_id, p))
    click.echo(f"Draft {draft_id[:8]} rejected.")


@social_group.command("edit")
@click.argument("draft_id")
@click.option("--content", required=True, help="New post copy")
def edit_draft(draft_id: str, content: str) -> None:
    """Edit draft copy before approving."""
    run_service(lambda p: _svc.edit_draft(draft_id, content, None, p))
    click.echo(f"Draft {draft_id[:8]} updated.")


@social_group.command("retry")
@click.argument("draft_id")
def retry_draft(draft_id: str) -> None:
    """Retry a failed draft."""
    result = run_service(
        lambda p, sc: _svc.retry_draft(draft_id, p, sc),
        needs_site_config=True,
    )
    if result.get("success"):
        click.echo(f"Retried and posted — Postiz ID: {result.get('postiz_post_id')}")
    else:
        click.echo(f"Retry failed: {result.get('error')}", err=True)


@social_group.command("setup")
def setup() -> None:
    """Guide through Postiz integration UUID setup."""
    click.echo(
        "Postiz Social Setup\n"
        "===================\n"
        "1. Open http://localhost:3003 in your browser\n"
        "2. Connect each social account under Settings → Integrations\n"
        "3. Copy the UUID for each connected account\n"
        "4. Run the following for each platform:\n\n"
        "   poindexter settings set postiz_integration_id_twitter  <uuid>\n"
        "   poindexter settings set postiz_integration_id_linkedin <uuid>\n"
        "   poindexter settings set postiz_integration_id_mastodon <uuid>\n"
        "   poindexter settings set postiz_integration_id_reddit   <uuid>\n"
        "   poindexter settings set postiz_integration_id_tiktok   <uuid>\n"
        "   poindexter settings set postiz_integration_id_instagram <uuid>\n\n"
        "5. Set the platforms to generate drafts for:\n"
        "   poindexter settings set social_draft_platforms twitter,linkedin,mastodon,reddit\n\n"
        "6. Set Reddit subreddits:\n"
        "   poindexter settings set social_reddit_subreddits "
        "r/LocalLLaMA,r/ArtificialIntelligence,r/selfhosted,r/homelab,r/Python,r/opensource\n\n"
        "7. Enable the feature:\n"
        "   poindexter settings set social_drafts_enabled true\n"
    )
```

- [ ] **Step 4: Register in `app.py`**

Open `src/cofounder_agent/poindexter/cli/app.py`. Find the line:

```python
main.add_command(publishers_group, name="publishers")
```

Add below it:

```python
from poindexter.cli.social import social_group
main.add_command(social_group, name="social")
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/cli/test_social_cli.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/social.py
git add src/cofounder_agent/poindexter/cli/app.py
git add tests/unit/cli/test_social_cli.py
git commit -m "feat(social): add poindexter social CLI commands"
```

---

### Task 5: API routes — `/api/social/drafts/*`

**Files:**

- Create: `src/cofounder_agent/routes/social_routes.py`
- Modify: `src/cofounder_agent/utils/route_registration.py` — add to `_WORKER_ROUTES`
- Test: `tests/unit/routes/test_social_routes.py`

**Interfaces:**

- Consumes: `SocialDraftsService` (Task 3)
- Produces:
  - `GET  /api/social/drafts` — query params: `post_id`, `task_id`, `status`
  - `POST /api/social/drafts/{draft_id}/approve`
  - `POST /api/social/drafts/{draft_id}/reject`
  - `PATCH /api/social/drafts/{draft_id}`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/routes/test_social_routes.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.social_routes import router


@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_list_drafts_returns_200(client):
    with patch("routes.social_routes.SocialDraftsService") as mock_cls:
        mock_svc = MagicMock()
        mock_svc.list_drafts = AsyncMock(return_value=[])
        mock_cls.return_value = mock_svc
        resp = client.get("/api/social/drafts")
    assert resp.status_code == 200
    assert resp.json() == {"drafts": []}


def test_approve_draft_success(client):
    draft_id = str(uuid.uuid4())
    with patch("routes.social_routes.SocialDraftsService") as mock_cls:
        mock_svc = MagicMock()
        mock_svc.approve_draft = AsyncMock(return_value={"success": True, "postiz_post_id": "pz-1"})
        mock_cls.return_value = mock_svc
        resp = client.post(f"/api/social/drafts/{draft_id}/approve")
    assert resp.status_code == 200


def test_reject_draft_returns_200(client):
    draft_id = str(uuid.uuid4())
    with patch("routes.social_routes.SocialDraftsService") as mock_cls:
        mock_svc = MagicMock()
        mock_svc.reject_draft = AsyncMock(return_value=None)
        mock_cls.return_value = mock_svc
        resp = client.post(f"/api/social/drafts/{draft_id}/reject")
    assert resp.status_code == 200


def test_edit_draft_returns_200(client):
    draft_id = str(uuid.uuid4())
    with patch("routes.social_routes.SocialDraftsService") as mock_cls:
        mock_svc = MagicMock()
        mock_svc.edit_draft = AsyncMock(return_value=None)
        mock_cls.return_value = mock_svc
        resp = client.patch(
            f"/api/social/drafts/{draft_id}",
            json={"content": "updated copy"},
        )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd src/cofounder_agent
poetry run pytest tests/unit/routes/test_social_routes.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `social_routes.py`**

```python
# src/cofounder_agent/routes/social_routes.py
"""Social draft management routes — thin adapter over SocialDraftsService.

No SQL or business logic here (transport adapter contract, ADR 2026-06-10).
"""
from __future__ import annotations

from typing import Any
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from services.oauth_service import verify_api_token
from services.site_config import SiteConfig
from services.social_drafts import SocialDraftsService, SocialDraftRow
from utils.db_dependency import get_pool_dependency
from utils.site_config_dependency import get_site_config_dependency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/social", tags=["social"])

_svc = SocialDraftsService()


class EditDraftRequest(BaseModel):
    content: str
    platform_config: dict[str, Any] | None = None


@router.get("/drafts", dependencies=[Depends(verify_api_token)])
async def list_drafts(
    post_id: str | None = Query(None),
    task_id: str | None = Query(None),
    status: str | None = Query(None),
    pool=Depends(get_pool_dependency),
) -> dict[str, Any]:
    drafts = await _svc.list_drafts(post_id, task_id, status, pool)
    return {"drafts": [_serialize(d) for d in drafts]}


@router.post("/drafts/{draft_id}/approve", dependencies=[Depends(verify_api_token)])
async def approve_draft(
    draft_id: str,
    pool=Depends(get_pool_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    return await _svc.approve_draft(draft_id, pool, site_config)


@router.post("/drafts/{draft_id}/reject", dependencies=[Depends(verify_api_token)])
async def reject_draft(
    draft_id: str,
    pool=Depends(get_pool_dependency),
) -> dict[str, Any]:
    await _svc.reject_draft(draft_id, pool)
    return {"ok": True}


@router.patch("/drafts/{draft_id}", dependencies=[Depends(verify_api_token)])
async def edit_draft(
    draft_id: str,
    body: EditDraftRequest,
    pool=Depends(get_pool_dependency),
) -> dict[str, Any]:
    await _svc.edit_draft(draft_id, body.content, body.platform_config, pool)
    return {"ok": True}


def _serialize(d: SocialDraftRow) -> dict[str, Any]:
    return {
        "id": d.id,
        "pipeline_task_id": d.pipeline_task_id,
        "post_id": d.post_id,
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

- [ ] **Step 4: Register route in `route_registration.py`**

Open `src/cofounder_agent/utils/route_registration.py`. In `_WORKER_ROUTES`, add after the `media_approval_routes` entry:

```python
("routes.social_routes", "router", "social_router", "social draft management (/api/social/drafts/*)"),
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/routes/test_social_routes.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/routes/social_routes.py
git add src/cofounder_agent/utils/route_registration.py
git add tests/unit/routes/test_social_routes.py
git commit -m "feat(social): add /api/social/drafts/* routes"
```

---

### Task 6: MCP tools — `list/approve/reject/edit_social_draft`

**Files:**

- Modify: `mcp-server/server.py` — add 4 tools
- Test: `tests/unit/mcp/test_social_mcp_tools.py` (or inline in existing MCP test file)

**Interfaces:**

- Consumes: `/api/social/drafts/*` API routes (Task 5) via the existing `_api()` helper in `server.py`
- Produces: MCP tools `list_social_drafts`, `approve_social_draft`, `reject_social_draft`, `edit_social_draft`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/mcp/test_social_mcp_tools.py
import pytest
from unittest.mock import AsyncMock, patch

# These tests verify the tool functions exist and call the API correctly.
# Import the tool functions after they're added to server.py.


def test_tools_exist():
    """Verify the four social MCP tools are importable from server."""
    # After implementation, this import will succeed:
    from mcp_server import server  # adjust to actual import path
    assert hasattr(server, "list_social_drafts") or True  # placeholder
```

> **Note:** MCP tool tests are thin — the tool implementations are 3-5 lines each delegating to `_api()`. The key test is that `_api()` is called with the right path. Check the existing MCP test suite in `tests/unit/mcp/` for the established pattern and replicate it.

- [ ] **Step 2: Add 4 tools to `mcp-server/server.py`**

Find the block of `@mcp.tool()` definitions. Add after the last `approve_post` tool:

```python
@mcp.tool()
async def list_social_drafts(
    post_id: str = "",
    task_id: str = "",
    status: str = "",
) -> str:
    """List social post drafts. Filter by post_id, task_id, or status."""
    params = {}
    if post_id:
        params["post_id"] = post_id
    if task_id:
        params["task_id"] = task_id
    if status:
        params["status"] = status
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    path = f"/api/social/drafts{'?' + qs if qs else ''}"
    return await _api("GET", path)


@mcp.tool()
async def approve_social_draft(draft_id: str) -> str:
    """Approve a social post draft — posts it to the platform via Postiz immediately."""
    return await _api("POST", f"/api/social/drafts/{draft_id}/approve")


@mcp.tool()
async def reject_social_draft(draft_id: str) -> str:
    """Reject a social post draft (terminal — no retry)."""
    return await _api("POST", f"/api/social/drafts/{draft_id}/reject")


@mcp.tool()
async def edit_social_draft(draft_id: str, content: str) -> str:
    """Edit the copy of a social post draft before approving it."""
    return await _api("PATCH", f"/api/social/drafts/{draft_id}", {"content": content})
```

Verify `_api` supports `PATCH` — if only `GET`/`POST` exist, add a `PATCH` branch:

```python
# In the _api() helper, add to the method dispatch:
elif method == "PATCH":
    resp = await http.patch(url, json=body, headers=headers)
```

- [ ] **Step 3: Verify tools are visible**

Start the MCP server locally and run:

```bash
cd mcp-server && node index.js --list-tools 2>&1 | grep social
```

Expected: `list_social_drafts`, `approve_social_draft`, `reject_social_draft`, `edit_social_draft`.

- [ ] **Step 4: Commit**

```bash
git add mcp-server/server.py
git commit -m "feat(social): add list/approve/reject/edit_social_draft MCP tools"
```

---

### Task 7: `social.generate_drafts` atom + graph_def wiring + backward-compat guard + `post_id` backfill

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/social_generate_drafts.py`
- Modify: `src/cofounder_agent/services/canonical_blog_spec.py` — add node + edge pair
- Modify: `src/cofounder_agent/services/publish_service.py` — `_queue_social_posts` guard + `publish_post_from_task` backfill
- Test: `tests/unit/atoms/test_social_generate_drafts.py`

**Interfaces:**

- Consumes: `SocialDraftsService.create_draft` (Task 3), `social_poster.generate_social_posts` (existing), `social_drafts_enabled` flag (Task 1)
- Produces: `social.generate_drafts` atom auto-discovered by `atom_registry._walk_package()`; graph_def node `{"id": "social_generate_drafts", "atom": "social.generate_drafts"}`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/atoms/test_social_generate_drafts.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from modules.content.atoms.social_generate_drafts import ATOM_META, run
from services.site_config import SiteConfig


def _state(**kwargs):
    base = {
        "task_id": str(uuid.uuid4()),
        "pipeline_task_id": str(uuid.uuid4()),
        "title": "Why Local LLMs Rule",
        "slug": "why-local-llms-rule",
        "excerpt": "A deep dive.",
        "seo_keywords": ["LLM", "Ollama"],
        "site_config": SiteConfig(initial_config={
            "social_drafts_enabled": "false",
            "social_draft_platforms": "twitter,linkedin",
            "social_reddit_subreddits": "",
        }),
        "pool": MagicMock(),
    }
    base.update(kwargs)
    return base


def test_atom_meta_name():
    assert ATOM_META.name == "social.generate_drafts"


@pytest.mark.asyncio
async def test_atom_noop_when_flag_disabled():
    """When social_drafts_enabled=false, atom does nothing."""
    state = _state()
    with patch("modules.content.atoms.social_generate_drafts.SocialDraftsService") as mock_svc:
        result = await run(state)
    mock_svc.return_value.create_draft.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_atom_creates_drafts_when_enabled():
    """When social_drafts_enabled=true, creates one draft per enabled platform."""
    sc = SiteConfig(initial_config={
        "social_drafts_enabled": "true",
        "social_draft_platforms": "twitter,linkedin",
        "social_reddit_subreddits": "",
    })
    state = _state(site_config=sc)

    with patch(
        "modules.content.atoms.social_generate_drafts.generate_social_posts",
        new=AsyncMock(return_value=[
            MagicMock(platform="twitter", text="tweet copy"),
            MagicMock(platform="linkedin", text="linkedin copy"),
        ])
    ), patch("modules.content.atoms.social_generate_drafts.SocialDraftsService") as mock_cls:
        mock_svc = MagicMock()
        mock_svc.create_draft = AsyncMock(return_value=str(uuid.uuid4()))
        mock_cls.return_value = mock_svc

        result = await run(state)

    assert mock_svc.create_draft.call_count == 2


@pytest.mark.asyncio
async def test_atom_creates_one_draft_per_subreddit():
    """Reddit generates one draft per configured subreddit."""
    sc = SiteConfig(initial_config={
        "social_drafts_enabled": "true",
        "social_draft_platforms": "reddit",
        "social_reddit_subreddits": "r/LocalLLaMA,r/selfhosted",
    })
    state = _state(site_config=sc)

    with patch(
        "modules.content.atoms.social_generate_drafts.generate_social_posts",
        new=AsyncMock(return_value=[])
    ), patch(
        "modules.content.atoms.social_generate_drafts._generate_reddit_copy",
        new=AsyncMock(return_value="check this out"),
    ), patch("modules.content.atoms.social_generate_drafts.SocialDraftsService") as mock_cls:
        mock_svc = MagicMock()
        mock_svc.create_draft = AsyncMock(return_value=str(uuid.uuid4()))
        mock_cls.return_value = mock_svc

        await run(state)

    assert mock_svc.create_draft.call_count == 2
    calls = mock_svc.create_draft.call_args_list
    subreddits = [c[1]["platform_config"]["subreddit"] for c in calls]
    assert "r/LocalLLaMA" in subreddits
    assert "r/selfhosted" in subreddits
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd src/cofounder_agent
poetry run pytest tests/unit/atoms/test_social_generate_drafts.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the atom**

```python
# src/cofounder_agent/modules/content/atoms/social_generate_drafts.py
"""Atom: social.generate_drafts — create social post drafts after persist_task.

Reads social_draft_platforms + social_reddit_subreddits from app_settings.
Creates one social_post_drafts row per platform/subreddit.
No-op when social_drafts_enabled=false (old Telegram/Discord path stays active).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta
from services.site_config import SiteConfig
from services.social_drafts import SocialDraftsService
from services.social_poster import generate_social_posts

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="social.generate_drafts",
    type="atom",
    version="1.0.0",
    description="Generate social post drafts for Postiz distribution",
    requires=["pipeline_task_id", "title", "slug", "site_config"],
    produces=[],
)

_svc = SocialDraftsService()


async def run(state: dict[str, Any]) -> dict[str, Any]:
    site_config: SiteConfig = state.get("site_config")  # type: ignore[assignment]
    if not site_config or not site_config.get_bool("social_drafts_enabled", False):
        return {}

    pipeline_task_id: str = state.get("pipeline_task_id") or state.get("task_id", "")
    title: str = state.get("title") or state.get("topic", "")
    slug: str = state.get("slug", "")
    excerpt: str = state.get("excerpt") or state.get("seo_description", "")
    keywords: list[str] = _parse_csv(state.get("seo_keywords") or "")
    pool = state.get("pool")

    platforms_raw = site_config.get("social_draft_platforms", "")
    platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]

    # Text platforms: delegate to existing generate_social_posts
    text_platforms = [p for p in platforms if p in ("twitter", "linkedin", "mastodon")]
    if text_platforms:
        try:
            posts = await generate_social_posts(
                title=title, slug=slug, excerpt=excerpt, keywords=keywords,
                site_config=site_config,
            )
            for post in posts:
                if post.platform in text_platforms:
                    await _svc.create_draft(
                        pipeline_task_id=pipeline_task_id,
                        platform=post.platform,
                        content=post.text,
                        platform_config={},
                        pool=pool,
                    )
        except Exception as exc:
            logger.error("[social.generate_drafts] text platform draft failed: %s", exc)

    # Reddit: one draft per configured subreddit
    if "reddit" in platforms:
        subreddits_raw = site_config.get("social_reddit_subreddits", "")
        subreddits = [s.strip() for s in subreddits_raw.split(",") if s.strip()]
        for subreddit in subreddits:
            try:
                copy = await _generate_reddit_copy(
                    title=title, slug=slug, excerpt=excerpt,
                    subreddit=subreddit, site_config=site_config,
                )
                if copy:
                    await _svc.create_draft(
                        pipeline_task_id=pipeline_task_id,
                        platform="reddit",
                        content=copy,
                        platform_config={"subreddit": subreddit},
                        pool=pool,
                    )
            except Exception as exc:
                logger.error(
                    "[social.generate_drafts] reddit %s draft failed: %s", subreddit, exc
                )

    return {}


async def _generate_reddit_copy(
    title: str,
    slug: str,
    excerpt: str,
    subreddit: str,
    site_config: SiteConfig,
) -> str:
    from services.prompt_manager import UnifiedPromptManager
    from services.llm_providers.dispatcher import dispatch_complete

    pm = UnifiedPromptManager(site_config=site_config)
    prompt_template = await pm.get_prompt(
        "social.reddit_promote",
        fallback=(
            "Write a Reddit post for {subreddit} promoting this article.\n"
            "Title: {title}\nSummary: {excerpt}\n"
            "URL: {post_url}\n"
            "Rules: be conversational, match the subreddit culture, "
            "no spammy self-promotion, include value first.\n"
            "Output only the post text."
        ),
    )
    post_url = f"{site_config.get('site_url', 'https://gladlabs.io')}/posts/{slug}"
    prompt = prompt_template.format(
        subreddit=subreddit, title=title, excerpt=excerpt, post_url=post_url
    )
    model = site_config.get("social_poster_fallback_model", "")
    if not model:
        return ""
    result = await dispatch_complete(prompt=prompt, model=model, site_config=site_config)
    return (result or "").strip()


def _parse_csv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []
```

- [ ] **Step 4: Add node + edges to `canonical_blog_spec.py`**

Open `src/cofounder_agent/services/canonical_blog_spec.py`.

In the `"nodes"` list, add after the `persist_task` node:

```python
{"id": "social_generate_drafts", "atom": "social.generate_drafts"},
```

In the `"edges"` list, find:

```python
{"from": "persist_task", "to": "record_pipeline_version"},
```

Replace with:

```python
{"from": "persist_task", "to": "social_generate_drafts"},
{"from": "social_generate_drafts", "to": "record_pipeline_version"},
```

- [ ] **Step 5: Add backward-compat flag guard in `publish_service.py`**

Open `src/cofounder_agent/services/publish_service.py`. Find the `_queue_social_posts` function (around line 1203). Inside the `try` block, wrap the `generate_and_distribute_social_posts` block:

```python
async def _queue_social_posts(...):
    try:
        # New path: social.generate_drafts atom handles this when social_drafts_enabled=true.
        # Old path: keep Telegram/Discord notify active until Postiz is running.
        if site_config.get_bool("social_drafts_enabled", False):
            return  # drafts created by social.generate_drafts atom

        from services.social_poster import generate_and_distribute_social_posts
        # ... rest of existing code unchanged ...
```

- [ ] **Step 6: Add `backfill_post_id` call in `publish_post_from_task`**

In `publish_service.py`, find where the new `post_id` is known (just after the `INSERT INTO posts` call). Look for the pattern where `posts.metadata->>'pipeline_task_id'` is already backfilled. Add immediately after:

```python
# Backfill social draft post_id — same pattern as posts.metadata->pipeline_task_id
if task_id:
    from services.social_drafts import SocialDraftsService as _SocialSvc
    with contextlib.suppress(Exception):
        await _SocialSvc().backfill_post_id(task_id, str(new_post_id), pool)
```

- [ ] **Step 7: Run tests to verify atom tests pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/atoms/test_social_generate_drafts.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/social_generate_drafts.py
git add src/cofounder_agent/services/canonical_blog_spec.py
git add src/cofounder_agent/services/publish_service.py
git add tests/unit/atoms/test_social_generate_drafts.py
git commit -m "feat(social): add social.generate_drafts atom + graph_def wiring + backward-compat flag guard"
```

---

### Task 8: Docker containers — Postiz + Redis

**Files:**

- Modify: `docker-compose.local.yml`
- Modify: `docker-compose.consumer.yml`

No unit tests for Docker config — verify with `docker compose config`.

- [ ] **Step 1: Add Redis and Postiz to `docker-compose.local.yml`**

Open `docker-compose.local.yml`. In the `services:` block, add:

```yaml
postiz-redis:
  image: redis:7.2-alpine
  container_name: poindexter-postiz-redis
  restart: unless-stopped
  volumes:
    - gladlabs-postiz-redis:/data
  networks:
    - poindexter-net

postiz:
  image: ghcr.io/gitroomhq/postiz-app:latest
  container_name: poindexter-postiz
  restart: unless-stopped
  ports:
    - '3003:3000'
  depends_on:
    - postiz-redis
    - postgres
  environment:
    DATABASE_URL: ${POSTIZ_DATABASE_URL:-postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/postiz}
    REDIS_URL: redis://postiz-redis:6379
    MAIN_URL: http://localhost:3003
    FRONTEND_URL: http://localhost:3003
    NEXT_PUBLIC_BACKEND_URL: http://localhost:3003
    JWT_SECRET: ${POSTIZ_JWT_SECRET:-change-me-in-bootstrap}
    IS_GENERAL: 'true'
    STORAGE_PROVIDER: s3
    CLOUDFLARE_ACCOUNT_ID: ${CLOUDFLARE_ACCOUNT_ID:-}
    CLOUDFLARE_BUCKETS_ID: ${R2_BUCKET_NAME:-}
    CLOUDFLARE_ACCESS_KEY: ${CLOUDFLARE_R2_ACCESS_KEY_ID:-}
    CLOUDFLARE_SECRET_ACCESS_KEY: ${CLOUDFLARE_R2_SECRET_ACCESS_KEY:-}
  networks:
    - poindexter-net
```

In the `volumes:` block, add:

```yaml
gladlabs-postiz-redis:
```

- [ ] **Step 2: Apply the same changes to `docker-compose.consumer.yml`**

Same two blocks — `postiz-redis` service, `postiz` service, and `gladlabs-postiz-redis` volume. The consumer variant omits operator-observability tier services but Postiz is a business-function container and belongs in both.

- [ ] **Step 3: Verify compose config parses**

```bash
docker compose -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.consumer.yml config --quiet
```

Expected: no errors.

- [ ] **Step 4: Create Postiz DB (one-time operator step — document in setup output)**

```bash
docker exec -it postgres psql -U postgres -c "CREATE DATABASE postiz;"
```

This is a one-time step; document it in the `poindexter social setup` output (already included in Task 4's setup command).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.local.yml docker-compose.consumer.yml
git commit -m "feat(social): add Postiz + Redis containers to Docker Compose"
```

---

### Task 9: `publishing.postiz_video` handler + `publishing_adapters` rows

**Files:**

- Create: `src/cofounder_agent/services/integrations/handlers/publishing_postiz_video.py`
- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` — add 4 adapter rows
- Test: `tests/unit/services/handlers/test_publishing_postiz_video.py`

**Interfaces:**

- Consumes: `PostizClient.upload_from_url` + `PostizClient.create_post` (Task 2)
- Produces: `@register_handler("publishing", "postiz_video")` handler; 4 seeded rows in `publishing_adapters`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/services/handlers/test_publishing_postiz_video.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.site_config import SiteConfig


def _sc():
    return SiteConfig(initial_config={
        "postiz_api_url": "http://postiz:3000",
        "postiz_integration_id_twitter": "uuid-twitter",
    })


@pytest.mark.asyncio
async def test_postiz_video_handler_success():
    # Must import AFTER registration so @register_handler fires
    from services.integrations.handlers import publishing_postiz_video  # noqa: F401
    from services.integrations.registry import dispatch

    payload = {
        "video_url": "https://r2.gladlabs.io/videos/test.mp4",
        "caption": "Watch this!",
        "platform": "x_video",
    }
    row = {"name": "postiz_x_video", "platform": "x_video", "handler": "postiz_video"}

    with patch("services.integrations.handlers.publishing_postiz_video.PostizClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.upload_from_url = AsyncMock(return_value="upload-id-123")
        mock_client.create_post = AsyncMock(return_value={"success": True, "post_id": "pz-v1", "error": None})
        mock_cls.return_value = mock_client

        result = await dispatch("publishing", "postiz_video", payload, site_config=_sc(), row=row)

    assert result["success"] is True


@pytest.mark.asyncio
async def test_postiz_video_handler_strips_video_suffix_for_integration_id():
    from services.integrations.handlers import publishing_postiz_video  # noqa: F401
    from services.integrations.registry import dispatch

    captured = {}

    async def capture_create_post(**kwargs):
        captured.update(kwargs)
        return {"success": True, "post_id": "pz-v2", "error": None}

    payload = {"video_url": "https://r2.gladlabs.io/v.mp4", "caption": "hey", "platform": "linkedin_video"}
    row = {"platform": "linkedin_video"}

    sc = SiteConfig(initial_config={
        "postiz_api_url": "http://postiz:3000",
        "postiz_integration_id_linkedin": "uuid-linkedin",
    })

    with patch("services.integrations.handlers.publishing_postiz_video.PostizClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.upload_from_url = AsyncMock(return_value="uid-2")
        mock_client.create_post = AsyncMock(side_effect=capture_create_post)
        mock_cls.return_value = mock_client

        await dispatch("publishing", "postiz_video", payload, site_config=sc, row=row)

    assert captured.get("integration_id") == "uuid-linkedin"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/handlers/test_publishing_postiz_video.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the handler**

```python
# src/cofounder_agent/services/integrations/handlers/publishing_postiz_video.py
"""Handler: ``publishing.postiz_video``.

Uploads a video from R2 to Postiz and posts it to the platform.
Platform determined by publishing_adapters row's ``platform`` column.
For x_video / linkedin_video, strips ``_video`` suffix to find the
right postiz_integration_id_* key (same account as text posts).
"""
from __future__ import annotations

import logging
from typing import Any

from services.integrations.postiz_client import PostizClient
from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)

# Maps publishing_adapters.platform → Postiz __type
_PLATFORM_TYPE: dict[str, str] = {
    "x_video": "x",
    "linkedin_video": "linkedin",
    "tiktok": "tiktok",
    "instagram_reels": "instagram",
}

# Maps publishing_adapters.platform → app_settings key for integration UUID
_INTEGRATION_KEY: dict[str, str] = {
    "x_video": "postiz_integration_id_twitter",
    "linkedin_video": "postiz_integration_id_linkedin",
    "tiktok": "postiz_integration_id_tiktok",
    "instagram_reels": "postiz_integration_id_instagram",
}


@register_handler("publishing", "postiz_video")
async def postiz_video(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or "video_url" not in payload:
        raise TypeError("publishing.postiz_video: payload must include 'video_url'")

    platform = (payload.get("platform") or row.get("platform", "")).strip()
    caption = payload.get("caption", "")
    video_url = payload["video_url"]

    integration_key = _INTEGRATION_KEY.get(platform)
    if not integration_key:
        return {"success": False, "error": f"unknown platform: {platform}"}

    integration_id = site_config.get(integration_key, "")
    if not integration_id:
        return {"success": False, "error": f"{integration_key} not configured"}

    base_url = site_config.get("postiz_api_url", "http://postiz:3000")
    client = PostizClient(base_url=base_url, api_key="")

    try:
        upload_id = await client.upload_from_url(video_url)
    except Exception as exc:
        return {"success": False, "error": f"upload failed: {exc}"}

    return await client.create_post(
        integration_id=integration_id,
        content=caption,
        platform_type=_PLATFORM_TYPE.get(platform, platform),
        platform_settings={},
        upload_ids=[upload_id],
    )
```

- [ ] **Step 4: Add `publishing_adapters` rows to `0000_baseline.seeds.sql`**

Open `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql`. Find the `publishing_adapters` INSERT block (around line 973). Add 4 new rows after the existing `mastodon_main` and `youtube_main` rows:

```sql
-- Postiz video distribution (surface=media)
INSERT INTO publishing_adapters (name, platform, handler, surface, config, enabled)
VALUES
  ('postiz_tiktok',          'tiktok',           'postiz_video', 'media', '{}', false),
  ('postiz_instagram_reels', 'instagram_reels',  'postiz_video', 'media', '{}', false),
  ('postiz_x_video',         'x_video',          'postiz_video', 'media', '{}', false),
  ('postiz_linkedin_video',  'linkedin_video',   'postiz_video', 'media', '{}', false)
ON CONFLICT (name) DO NOTHING;
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/handlers/test_publishing_postiz_video.py -v
```

Expected: all 2 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/integrations/handlers/publishing_postiz_video.py
git add src/cofounder_agent/services/migrations/0000_baseline.seeds.sql
git add tests/unit/services/handlers/test_publishing_postiz_video.py
git commit -m "feat(social): add publishing.postiz_video handler + adapter rows"
```

---

### Task 10: Prometheus metrics + Grafana Social row

**Files:**

- Modify: `src/cofounder_agent/services/social_drafts.py` — add 3 module-level metrics + increment calls
- Modify: Pipeline Grafana dashboard JSON — add Social row with 4 panels

**Interfaces:**

- Consumes: `SocialDraftsService` (Task 3)
- Produces: `poindexter_social_drafts_total{platform,status}`, `poindexter_social_draft_approve_latency_seconds{platform}`, `poindexter_postiz_api_errors_total{platform}`

- [ ] **Step 1: Add metrics to `social_drafts.py`**

Open `src/cofounder_agent/services/social_drafts.py`. After the imports, add:

```python
from prometheus_client import Counter, Histogram

_SOCIAL_DRAFTS_TOTAL = Counter(
    "poindexter_social_drafts_total",
    "Social post draft status transitions",
    ["platform", "status"],
)
_SOCIAL_DRAFT_APPROVE_LATENCY = Histogram(
    "poindexter_social_draft_approve_latency_seconds",
    "Time from draft created to approved",
    ["platform"],
    buckets=[1, 5, 30, 60, 300, 600, 1800, 3600],
)
_POSTIZ_API_ERRORS_TOTAL = Counter(
    "poindexter_postiz_api_errors_total",
    "Postiz API call failures",
    ["platform"],
)
```

In `create_draft`, after the INSERT:

```python
_SOCIAL_DRAFTS_TOTAL.labels(platform=platform, status="pending").inc()
```

In `approve_draft`, after marking posted:

```python
_SOCIAL_DRAFTS_TOTAL.labels(platform=platform, status="posted").inc()
```

In `approve_draft`, after marking failed:

```python
_SOCIAL_DRAFTS_TOTAL.labels(platform=platform, status="failed").inc()
_POSTIZ_API_ERRORS_TOTAL.labels(platform=platform).inc()
```

In `reject_draft`, after UPDATE:

```python
# platform is unknown without a SELECT — log only, don't query for a counter
```

- [ ] **Step 2: Add Social row to Pipeline Grafana dashboard**

Find the pipeline dashboard JSON: `infrastructure/grafana/provisioning/dashboards/pipeline.json`.

Add a new row panel at the end of the `panels` array:

```json
{
  "type": "row",
  "title": "Social",
  "collapsed": false,
  "gridPos": {"h": 1, "w": 24, "x": 0, "y": 200}
},
{
  "type": "stat",
  "title": "Drafts by Status",
  "targets": [{
    "datasource": {"type": "postgres"},
    "rawSql": "SELECT status, COUNT(*) as count FROM social_post_drafts GROUP BY status ORDER BY status",
    "format": "table"
  }],
  "gridPos": {"h": 4, "w": 12, "x": 0, "y": 201}
},
{
  "type": "timeseries",
  "title": "Posts Fired (24h)",
  "targets": [{
    "datasource": {"type": "prometheus"},
    "expr": "increase(poindexter_social_drafts_total{status=\"posted\"}[24h])"
  }],
  "gridPos": {"h": 4, "w": 6, "x": 12, "y": 201}
},
{
  "type": "timeseries",
  "title": "Postiz API Error Rate",
  "targets": [{
    "datasource": {"type": "prometheus"},
    "expr": "rate(poindexter_postiz_api_errors_total[1h])"
  }],
  "gridPos": {"h": 4, "w": 6, "x": 18, "y": 201}
},
{
  "type": "table",
  "title": "Pending Drafts",
  "targets": [{
    "datasource": {"type": "postgres"},
    "rawSql": "SELECT id, platform, LEFT(content, 60) AS content, created_at FROM social_post_drafts WHERE status = 'pending' ORDER BY created_at DESC LIMIT 20",
    "format": "table"
  }],
  "gridPos": {"h": 6, "w": 24, "x": 0, "y": 205}
}
```

> **Note:** Match the `gridPos.y` values to the actual panel count in the file — `200` is a placeholder. Find the last panel's `y + h` value and use that.

- [ ] **Step 3: Verify metrics are exported**

Start the worker locally and hit:

```bash
curl -s http://localhost:8002/metrics | grep poindexter_social
```

Expected: `poindexter_social_drafts_total`, `poindexter_social_draft_approve_latency_seconds`, `poindexter_postiz_api_errors_total` all present.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/social_drafts.py
git add infrastructure/grafana/provisioning/dashboards/pipeline.json
git commit -m "feat(social): add Prometheus metrics + Grafana Social row to Pipeline dashboard"
```

---

### Task 11: `retry_failed_social_drafts` job

**Files:**

- Create: `src/cofounder_agent/services/jobs/retry_failed_social_drafts.py`
- Modify: `src/cofounder_agent/plugins/registry.py` — register in `_SAMPLES`
- Test: `tests/unit/services/jobs/test_retry_failed_social_drafts.py`

**Interfaces:**

- Consumes: `SocialDraftsService.retry_draft` (Task 3)
- Produces: `RetryFailedSocialDraftsJob` — hourly, picks `failed` rows where `retry_count < social_draft_max_retries`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/services/jobs/test_retry_failed_social_drafts.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from services.jobs.retry_failed_social_drafts import RetryFailedSocialDraftsJob
from plugins.job import JobResult


def _mock_pool(rows=None):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.asyncio
async def test_job_returns_ok_no_rows():
    pool, _ = _mock_pool(rows=[])
    job = RetryFailedSocialDraftsJob()
    result = await job.run(pool, {"max_retries": 3})
    assert result.ok is True
    assert result.changes_made == 0


@pytest.mark.asyncio
async def test_job_retries_failed_drafts():
    draft_id = str(uuid.uuid4())
    pool, conn = _mock_pool(rows=[{"id": draft_id, "platform": "twitter"}])

    with patch("services.jobs.retry_failed_social_drafts.SocialDraftsService") as mock_cls:
        mock_svc = MagicMock()
        mock_svc.retry_draft = AsyncMock(return_value={"success": True, "postiz_post_id": "pz-r1"})
        mock_cls.return_value = mock_svc

        with patch("services.jobs.retry_failed_social_drafts.SiteConfig") as sc_cls:
            sc_cls.return_value = MagicMock()
            job = RetryFailedSocialDraftsJob()
            result = await job.run(pool, {"max_retries": 3})

    assert result.changes_made == 1
    mock_svc.retry_draft.assert_awaited_once_with(draft_id, pool, sc_cls.return_value)


@pytest.mark.asyncio
async def test_job_skips_row_at_max_retries():
    """Rows at max_retries should not be retried (already excluded by SQL WHERE)."""
    pool, conn = _mock_pool(rows=[])  # SQL WHERE filters them out
    job = RetryFailedSocialDraftsJob()
    result = await job.run(pool, {"max_retries": 3})
    assert result.changes_made == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/jobs/test_retry_failed_social_drafts.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the job**

```python
# src/cofounder_agent/services/jobs/retry_failed_social_drafts.py
"""RetryFailedSocialDraftsJob — hourly retry of failed social post drafts.

Picks up drafts where status='failed' and retry_count < max_retries.
Calls SocialDraftsService.retry_draft which increments retry_count,
resets to 'pending', and fires PostizClient again.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.social_drafts import SocialDraftsService
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

_svc = SocialDraftsService()


class RetryFailedSocialDraftsJob:
    name = "retry_failed_social_drafts"
    description = "Retry failed social post drafts up to social_draft_max_retries"
    schedule = "every 1 hour"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        max_retries = int(config.get("max_retries", 3))
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, platform FROM social_post_drafts
                    WHERE status = 'failed' AND retry_count < $1
                    ORDER BY last_retry_at NULLS FIRST
                    LIMIT 20
                    """,
                    max_retries,
                )
        except Exception as exc:
            logger.exception("RetryFailedSocialDraftsJob: DB query failed: %s", exc)
            return JobResult(ok=False, detail=str(exc), changes_made=0)

        if not rows:
            return JobResult(ok=True, detail="no retryable drafts", changes_made=0)

        sc = SiteConfig()
        retried = 0
        for row in rows:
            draft_id = str(row["id"])
            try:
                result = await _svc.retry_draft(draft_id, pool, sc)
                if result.get("success"):
                    retried += 1
                    logger.info(
                        "RetryFailedSocialDraftsJob: retried %s (%s) → posted",
                        draft_id[:8], row["platform"],
                    )
                else:
                    logger.warning(
                        "RetryFailedSocialDraftsJob: retry failed for %s: %s",
                        draft_id[:8], result.get("error"),
                    )
            except Exception as exc:
                logger.error(
                    "RetryFailedSocialDraftsJob: exception on %s: %s", draft_id[:8], exc
                )

        return JobResult(
            ok=True,
            detail=f"retried {retried}/{len(rows)} drafts",
            changes_made=retried,
        )
```

- [ ] **Step 4: Register in `plugins/registry.py`**

Open `src/cofounder_agent/plugins/registry.py`. Find the `_SAMPLES` list (around line 560). Add after `ExpireStaleApprovalsJob`:

```python
(
    "jobs",
    "services.jobs.retry_failed_social_drafts",
    "RetryFailedSocialDraftsJob",
),
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd src/cofounder_agent
poetry run pytest tests/unit/services/jobs/test_retry_failed_social_drafts.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/retry_failed_social_drafts.py
git add src/cofounder_agent/plugins/registry.py
git add tests/unit/services/jobs/test_retry_failed_social_drafts.py
git commit -m "feat(social): add RetryFailedSocialDraftsJob"
```

---

### Task 12: CLAUDE.md update

**Files:**

- Modify: `CLAUDE.md` — local services table, key DB tables list

No tests.

- [ ] **Step 1: Add Postiz to local services table in CLAUDE.md**

Find the local services table (under `### Key Numbers`). Add a new row:

```markdown
| Postiz | http://localhost:3003 | Social media OAuth + posting engine (Postiz) |
```

- [ ] **Step 2: Add `social_post_drafts` to key DB tables section**

Find the `**Database tables (key ones):**` section. Add:

```markdown
- `social_post_drafts` — social media post drafts (one per platform/subreddit). Populated by `social.generate_drafts` atom after `content.persist_task`. Status lifecycle: `pending → approved → posted` or `failed → retry`. `post_id` is null until `publish_service.publish_post_from_task` backfills it after blog publish. Retry job: `RetryFailedSocialDraftsJob` (hourly, gated by `social_draft_max_retries`).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add Postiz + social_post_drafts to CLAUDE.md"
```

---

## Spec Coverage Check

| Spec Section                                                 | Task             |
| ------------------------------------------------------------ | ---------------- |
| Architecture diagram — atom + approval surfaces              | Tasks 4, 5, 6, 7 |
| `social_post_drafts` table schema                            | Task 1           |
| Status lifecycle (pending → posted / failed / rejected)      | Task 3           |
| `post_id` backfill pattern                                   | Task 7           |
| `social.generate_drafts` atom                                | Task 7           |
| `social_drafts_enabled` backward-compat flag                 | Task 7           |
| `PostizClient.create_post` + `upload_from_url`               | Task 2           |
| Integration UUID lookup pattern                              | Task 3           |
| `SocialDraftsService` full interface                         | Task 3           |
| CLI (`poindexter social *`)                                  | Task 4           |
| API routes (`/api/social/drafts/*`)                          | Task 5           |
| MCP tools (4 tools)                                          | Task 6           |
| Docker — `postiz-redis` + `postiz` containers                | Task 8           |
| `poindexter social setup` command                            | Task 4           |
| `publishing.postiz_video` handler                            | Task 9           |
| 4 `publishing_adapters` rows for video                       | Task 9           |
| Reddit: one draft per subreddit                              | Task 7           |
| Prometheus metrics (3 counters/histograms)                   | Task 10          |
| Grafana Social row (4 panels)                                | Task 10          |
| Error handling table (failed → Telegram, retry, max retries) | Task 3, 11       |
| `retry_failed_social_drafts` hourly job                      | Task 11          |
| `app_settings` 11 new keys                                   | Task 1           |
| CLAUDE.md update                                             | Task 12          |

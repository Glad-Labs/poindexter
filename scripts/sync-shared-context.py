#!/usr/bin/env python3
"""
Sync Shared Context — auto-generates .shared-context/ from live database state.

Runs on a schedule (cron or brain daemon) to keep OpenClaw and Claude Code
in sync with the current system state. No manual updates needed.

Usage:
    python scripts/sync-shared-context.py
    # Or via cron: */15 * * * * cd /path/to/repo && python scripts/sync-shared-context.py
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:***REMOVED***@hopper.proxy.rlwy.net:32382/railway",
)
LOCAL_DB_URL = os.getenv(
    "LOCAL_DATABASE_URL",
    "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = REPO_ROOT / ".shared-context"
STATE_DIR = SHARED_DIR / "state"


async def main():
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Connect to production DB
    conn = await asyncpg.connect(DB_URL)

    # --- System State ---
    posts = await conn.fetchrow("SELECT COUNT(*) as c FROM posts WHERE status = 'published'")
    drafts = await conn.fetchrow("SELECT COUNT(*) as c FROM posts WHERE status = 'draft'")
    tasks_pending = await conn.fetchrow("SELECT COUNT(*) as c FROM content_tasks WHERE status IN ('pending', 'approved')")
    tasks_failed = await conn.fetchrow("SELECT COUNT(*) as c FROM content_tasks WHERE status = 'failed'")
    settings_count = await conn.fetchrow("SELECT COUNT(*) as c FROM app_settings")
    prompts_count = await conn.fetchrow("SELECT COUNT(*) as c FROM prompt_templates WHERE is_active = true")
    stages_count = await conn.fetchrow("SELECT COUNT(*) as c FROM pipeline_stages WHERE enabled = true")
    subscribers = await conn.fetchrow("SELECT COUNT(*) as c FROM newsletter_subscribers WHERE unsubscribed_at IS NULL")
    affiliates = await conn.fetchrow("SELECT COUNT(*) as c FROM affiliate_links WHERE is_active = true")

    # Categories
    categories = await conn.fetch("SELECT c.name, COUNT(p.id) as posts FROM categories c LEFT JOIN posts p ON c.id = p.category_id AND p.status = 'published' GROUP BY c.name ORDER BY posts DESC")

    # Recent tasks
    recent = await conn.fetch("""
        SELECT LEFT(topic, 60) as topic, status, quality_score, created_at::date as date
        FROM content_tasks WHERE created_at > NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC LIMIT 10
    """)

    # Identity settings
    identity = await conn.fetch("SELECT key, value FROM app_settings WHERE category = 'identity'")
    identity_dict = {r["key"]: r["value"] for r in identity}

    # Pipeline stages
    stages = await conn.fetch("SELECT key, name, enabled, stage_order FROM pipeline_stages ORDER BY stage_order")

    # Active experiments
    experiments = await conn.fetch("SELECT name, stage_key, traffic_split_pct, is_active FROM pipeline_experiments WHERE is_active = true")

    # Cost this week
    cost = await conn.fetchrow("SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_logs WHERE created_at > NOW() - INTERVAL '7 days'")

    await conn.close()

    # Try local DB for embeddings count
    embeddings_count = 0
    try:
        local_conn = await asyncpg.connect(LOCAL_DB_URL)
        row = await local_conn.fetchrow("SELECT COUNT(*) as c FROM embeddings")
        embeddings_count = row["c"]
        await local_conn.close()
    except Exception:
        pass

    # --- Write system-status.md ---
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    status_md = f"""---
name: System Status
last_updated: {ts}
updated_by: sync-shared-context
category: state
---

# System Status (auto-generated)

**Last sync:** {ts}

## Content
- Published posts: {posts['c']}
- Drafts: {drafts['c']}
- Pending/approved tasks: {tasks_pending['c']}
- Failed tasks: {tasks_failed['c']}
- Newsletter subscribers: {subscribers['c']}
- Affiliate links: {affiliates['c']}

## Categories
{chr(10).join(f'- {r["name"]}: {r["posts"]} posts' for r in categories)}

## Configuration
- App settings: {settings_count['c']} keys
- Prompt templates: {prompts_count['c']} active
- Pipeline stages: {stages_count['c']} enabled
- Embeddings: {embeddings_count} vectors
- Weekly API cost: ${float(cost['total']):.4f}

## Identity
{chr(10).join(f'- {k}: {v}' for k, v in sorted(identity_dict.items()))}

## Pipeline Stages
| # | Stage | Enabled |
|---|-------|---------|
{chr(10).join(f'| {r["stage_order"]} | {r["name"]} | {"yes" if r["enabled"] else "NO"} |' for r in stages)}

## Active Experiments
{chr(10).join(f'- {r["name"]} on {r["stage_key"]} ({r["traffic_split_pct"]}% to B)' for r in experiments) if experiments else '- None active'}

## Recent Tasks (24h)
{chr(10).join(f'- [{r["status"]}] {r["topic"]} (score: {r["quality_score"] or "?"})'  for r in recent) if recent else '- No recent tasks'}
"""

    (STATE_DIR / "system-status.md").write_text(status_md, encoding="utf-8")
    print(f"Synced system-status.md ({posts['c']} posts, {settings_count['c']} settings, {embeddings_count} embeddings)")

    # --- Write identity.md for quick reference ---
    identity_md = f"""---
name: System Identity
last_updated: {ts}
updated_by: sync-shared-context
category: identity
---

# System Identity

{chr(10).join(f'**{k}:** {v}' for k, v in sorted(identity_dict.items()))}
"""
    (SHARED_DIR / "identity" / "system-identity.md").mkdir(parents=True, exist_ok=True) if not (SHARED_DIR / "identity").exists() else None
    (SHARED_DIR / "identity" / "system-identity.md").write_text(identity_md, encoding="utf-8")

    print(f"Synced identity ({len(identity_dict)} keys)")


if __name__ == "__main__":
    asyncio.run(main())

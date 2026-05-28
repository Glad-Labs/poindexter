"""Drop dead Gitea/Forgejo schema — Phase A.2 (single-word names).

Phase A (PR #686) dropped 77 unambiguously-named dead tables.
Phase A.2 finishes the Gitea/Forgejo purge with single-word names
(``task`` / ``team`` / ``user`` / ``topic`` / ``release`` / etc.)
that needed stricter SQL-context grep to avoid false positives
against Python identifiers and English words.

Verification per table on this drop list:

- 0 rows on production (live ANALYZE-corrected count)
- 0 SQL-context references in production code — matched against the
  regex ``\\b(FROM|INTO|UPDATE|JOIN|REFERENCES|TABLE)\\s+(public\\.)?<name>\\b``
  plus ``public.<name>\\b`` to catch schema-qualified usage. Excludes
  /migrations/, /tests/, /__pycache__/, /.claude/worktrees/, and
  0000_baseline.

Notable deletions:

- ``alert_actions`` + ``alert_rules`` — Grafana stores its alert
  rules in YAML provisioning (``infrastructure/grafana/provisioning/
  alerting/alert-rules.yml``), not in poindexter's DB. These were
  Gitea CI alert tables.
- ``task`` + ``tasks`` — the live task queue is ``pipeline_tasks``.
- ``user`` — Gitea identity table; poindexter is single-operator.
- ``topic`` — Gitea repo tag; ``topic_*`` poindexter tables stay
  (those are live: topic_batches, topic_candidates, topic_pool).
- ``version`` — Gitea schema-version table; poindexter uses
  ``schema_migrations``.

Uses ``DROP TABLE IF EXISTS ... CASCADE`` — idempotent across
re-runs and handles inter-table FK dependencies.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_DEAD_TABLES = (
    # ── Gitea/Forgejo access + auth ──
    "access",
    "access_token",
    "email_address",
    "oauth2_application",
    "oauth2_grant",
    "protected_branch",
    "public_key",
    # ── Gitea/Forgejo actions (CI) ──
    "action",
    "action_run",
    "action_run_index",
    "action_run_job",
    "action_runner",
    "action_runner_token",
    "action_schedule",
    "action_schedule_spec",
    "action_task",
    "action_task_step",
    "action_tasks_version",
    "agent_permissions",
    "alert_actions",
    "alert_rules",
    "hook_task",
    # ── Gitea/Forgejo repo + git plumbing ──
    "checkpoint_migrations",
    "commit_status",
    "commit_status_index",
    "commit_status_summary",
    "dbfs_data",
    "dbfs_meta",
    "language_stat",
    "milestone",
    "notice",
    "project",
    "project_board",
    "project_issue",
    "push_mirror",
    "release",
    "repo_indexer_status",
    "repo_license",
    "repo_unit",
    "repository",
    "version",
    # ── Gitea/Forgejo issues + reviews ──
    "issue",
    "issue_assignees",
    "issue_content_history",
    "issue_index",
    "issue_label",
    "issue_user",
    "label",
    "pull_request",
    "review",
    # ── Gitea/Forgejo users/teams ──
    "team",
    "topic",  # Gitea repo tags — distinct from topic_batches / topic_candidates / topic_pool
    "user",
    # ── Dead poindexter tables (superseded / never wired) ──
    "sites",  # multi-tenant table — single-operator deployment
    "social_posts",  # superseded by publishing_adapters
    "system_agents",
    "system_credentials",
    "system_devices",
    "system_setting",  # Gitea's, not app_settings (poindexter's live config table)
    "task",  # superseded by pipeline_tasks
    "tasks",  # superseded by pipeline_tasks
)


async def up(pool) -> None:
    """Drop every dead table with CASCADE."""
    async with pool.acquire() as conn:
        dropped: list[str] = []
        for table in _DEAD_TABLES:
            await conn.execute(
                f'DROP TABLE IF EXISTS public."{table}" CASCADE'
            )
            dropped.append(table)
        logger.info(
            "Migration drop_dead_gitea_schema_a2: dropped %d tables: %s",
            len(dropped), ", ".join(dropped),
        )


async def down(pool) -> None:
    """No-op revert.

    Restoring these would mean recreating the Gitea/Forgejo schema +
    superseded poindexter tables (foreign keys, indexes, triggers) —
    one-way cleanup. If a customer needs Gitea later, install it
    fresh against its own DB.
    """
    logger.info(
        "Migration drop_dead_gitea_schema_a2 down: no-op "
        "(dead Gitea/Forgejo + superseded poindexter tables are not recreated)"
    )

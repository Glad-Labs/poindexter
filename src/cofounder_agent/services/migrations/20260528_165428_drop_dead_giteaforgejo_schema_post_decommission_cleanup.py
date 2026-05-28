"""Drop dead Gitea/Forgejo schema + dead poindexter tables (Phase A purge).

Gitea was decommissioned around 2026-04-30 (see ``feedback_railway_deploy``)
but its ~80-table schema lived on inside the poindexter DB because the
0000_baseline squash absorbed it. None of those tables are written or
read by poindexter code — they're pure noise.

Same audit found dead poindexter tables left over from superseded
features (custom_workflows replaced by LangGraph templates, capability_*
unused, content_calendar / fact_overrides / fine_tuning_jobs never wired
to production code, etc.).

Verified each table in this list:
- 0 rows on production (live ANALYZE-corrected count)
- 0 SQL-context references (FROM/INTO/UPDATE/TABLE) in production code,
  excluding /migrations/, /tests/, /__pycache__/, /.claude/worktrees/

Uses ``DROP TABLE IF EXISTS ... CASCADE`` — the CASCADE handles FK
dependencies between Gitea tables (e.g. user → user_setting → access).
Idempotent: re-running on an already-cleaned DB is a no-op.

This is Phase A of the schema cleanup. Phase A.2 (single-word Gitea
table names like ``task`` / ``team`` / ``user`` that require strict
SQL-context grep verification) and Phase B (dead columns on
still-live tables) follow in separate PRs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Tables grouped by origin for readability — DROP order doesn't matter
# because every drop uses CASCADE.
_DEAD_TABLES = (
    # ── Gitea/Forgejo actions/CI ──
    "action_artifact",
    "action_task_output",
    "action_variable",
    # ── Gitea/Forgejo repo concepts ──
    "branch",
    "comment",
    "deploy_key",
    "mirror",
    "protected_tag",
    "pull_auto_merge",
    "renamed_branch",
    "repo_archiver",
    "repo_redirect",
    "repo_topic",
    "repo_transfer",
    # ── Gitea/Forgejo issues + reviews ──
    "issue_dependency",
    "issue_pin",
    "issue_watch",
    "reaction",
    "review_state",
    # ── Gitea/Forgejo packages ──
    "package",
    "package_blob",
    "package_blob_upload",
    "package_cleanup_rule",
    "package_file",
    "package_property",
    "package_version",
    # ── Gitea/Forgejo users/teams/auth ──
    "attachment",
    "badge",
    "collaboration",
    "email_hash",
    "external_login_user",
    "follow",
    "gpg_key",
    "gpg_key_import",
    "jwt_blocklist",
    "lfs_lock",
    "lfs_meta_object",
    "login_source",
    "oauth2_authorization_code",
    "oauth_accounts",
    "org_user",
    "secret",
    "session",
    "star",
    "stopwatch",
    "team_invite",
    "team_repo",
    "team_unit",
    "team_user",
    "tracked_time",
    "two_factor",
    "upload",
    "user_badge",
    "user_blocking",
    "user_open_id",
    "user_redirect",
    "user_setting",
    "watch",
    "webauthn_credential",
    "webhook",
    # ── Dead poindexter tables (superseded features) ──
    "affiliate_links",
    "agent_status",
    "approval_queue",
    "audit_log_summaries",
    "brain_decision_summaries",
    "capability_executions",
    "capability_tasks",
    "content_calendar",
    "custom_workflows",  # replaced by LangGraph templates
    "distribution_channels",
    "fine_tuning_jobs",
    "learning_patterns",
    "pipeline_experiments",  # replaced by Langfuse Datasets/Scores
    "pipeline_run_log",
    "quality_improvement_logs",
    "quality_metrics_daily",
    "voice_messages",
    "workflow_executions",
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
            "Migration drop_dead_gitea_schema: dropped %d tables: %s",
            len(dropped), ", ".join(dropped),
        )


async def down(pool) -> None:
    """No-op revert.

    Restoring these tables would mean recreating the entire Gitea/Forgejo
    schema (foreign keys, indexes, triggers) — a one-way cleanup. If a
    customer needs Gitea later, install it fresh against its own DB
    instead of layering it back onto poindexter's.
    """
    logger.info(
        "Migration drop_dead_gitea_schema down: no-op "
        "(dead Gitea/Forgejo + superseded poindexter tables are not recreated)"
    )

"""Migration 20260507_021328: seed pr_staleness probe app_settings.

ISSUE: brain pr_staleness probe — surface 24h+ green-CI PRs to Discord.

Catches the "agent shipped a PR and the operator forgot" failure mode
that bit Matt today (multiple PRs sat for 12+ hours before he noticed).
The probe lives in ``brain/pr_staleness_probe.py``; the brain daemon
runs it every cycle and an internal cadence gate keeps the real GitHub
round-trip to once an hour.

Seeds the six tunables the probe reads on every cycle:

* ``pr_staleness_probe_enabled`` (bool, default true) — master switch.
* ``pr_staleness_poll_interval_minutes`` (int, default 60) — internal
  cadence gate. Brain dispatches the probe every cycle (~5 min) but
  the GitHub round-trip only fires once per this interval.
* ``pr_staleness_min_hours`` (int, default 24) — minimum PR age before
  it's considered stale.
* ``pr_staleness_dedup_hours`` (int, default 12) — quiet period after
  a stale-alert fires for one PR before it can re-page. Default 12
  re-pings Matt twice a day if he forgot once.
* ``pr_staleness_repo`` (str, default ``Glad-Labs/glad-labs-stack``)
  — owner/name; future-proofs for multi-repo scans.
* ``pr_staleness_max_prs_per_alert`` (int, default 5) — cap on the
  number of PRs surfaced in a single Discord message body so the
  alert fits in one card.

Auth uses the existing ``gh_token`` setting (seeded by
``20260506_005708_seed_gh_token_for_dev_diary.py``) — no new secret.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — operator-set values
preserved on re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "pr_staleness_probe_enabled",
        "true",
        "Master switch for the brain PR staleness probe. When false the "
        "probe short-circuits without hitting GitHub. See "
        "brain/pr_staleness_probe.py.",
    ),
    (
        "pr_staleness_poll_interval_minutes",
        "60",
        "Internal cadence gate for the brain PR staleness probe. The "
        "brain dispatches the probe every cycle (~5 min); the actual "
        "GitHub round-trip only fires once per this interval. Default "
        "60 = once an hour.",
    ),
    (
        "pr_staleness_min_hours",
        "24",
        "Minimum age (in hours) before an open PR is considered stale "
        "by the brain PR staleness probe. PRs younger than this are "
        "skipped before the per-PR check-runs lookup so the probe "
        "doesn't burn rate-limit budget on fresh work.",
    ),
    (
        "pr_staleness_dedup_hours",
        "12",
        "Quiet period (in hours) after a stale-PR alert fires before "
        "the same PR can re-page. Per-PR dedup is anchored on the "
        "fingerprint pr_stale_<repo>_<pr_number> in alert_dedup_state. "
        "Default 12 re-pings the operator twice a day if they forgot "
        "once.",
    ),
    (
        "pr_staleness_repo",
        "Glad-Labs/glad-labs-stack",
        "GitHub repository (``owner/name``) the brain PR staleness "
        "probe scans for open PRs. Future-proofs for multi-repo. Pair "
        "with the ``gh_token`` secret for authenticated rate limits + "
        "private-repo access.",
    ),
    (
        "pr_staleness_max_prs_per_alert",
        "5",
        "Cap on the number of PRs surfaced in a single Discord-ops "
        "message body. Keeps the alert under Discord's per-message "
        "ceiling and stops a runaway backlog from producing an "
        "unreadable card. Surplus PRs are summarized as 'and N more'.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Migration 20260507_021328: app_settings missing -- "
                "skipping (table will be created later)"
            )
            return
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, 'monitoring', $3, FALSE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 20260507_021328: seeded %d/%d pr_staleness_* settings "
            "(remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "Migration 20260507_021328 rolled back: removed %d "
            "pr_staleness_* seeds",
            len(_SEEDS),
        )

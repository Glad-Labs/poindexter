"""Migration 20260522_212507: lower glitchtip_triage_alert_threshold_count
from 100 to 10 so novel recurring issues surface as Telegram/Discord pages
before they reach hundred-occurrence territory.

Background — Matt's 2026-05-22 security-tools audit found 34 open
GlitchTip issues sitting unreviewed because every per-cycle alert path
gated on ``count >= 100``. None of the 34 had crossed the threshold,
so the operator never saw a single notification. Lowering to 10 surfaces
genuinely-recurring novel errors while leaving once-and-done transients
quiet (the same probe still auto-resolves matches against
``glitchtip_triage_auto_resolve_patterns``, so noise rules continue to
absorb known-flapping issues).

10 isn't a tuned value, it's a sensible default. Operators who don't
want any-recurrence pages can flip it back via
``poindexter set-setting glitchtip_triage_alert_threshold_count <N>``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration. Idempotent — re-running just reasserts the
    new value, and the description text is rewritten in place to match.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
               SET value = '10',
                   description = 'Brain triage probe pages via '
                       'notify_operator() when a GlitchTip issue has '
                       'count >= this AND matches no entry in '
                       'glitchtip_triage_auto_resolve_patterns. Default 10 '
                       'surfaces novel recurring errors as a Telegram/'
                       'Discord page before they pile up unseen. Per-issue '
                       'dedupe by id within a single brain process uptime '
                       '— restart the brain to re-page. Bump higher if the '
                       'pager gets too chatty.',
                   updated_at = NOW()
             WHERE key = 'glitchtip_triage_alert_threshold_count'
            """
        )
        # Insert if it was never seeded (fresh installs that missed the
        # baseline INSERT path — unlikely but harmless to cover).
        await conn.execute(
            """
            INSERT INTO app_settings (
                key, value, category, description, is_secret, is_active
            )
            VALUES (
                'glitchtip_triage_alert_threshold_count', '10', 'monitoring',
                'Brain triage probe pages via notify_operator() when a '
                'GlitchTip issue has count >= this AND matches no entry in '
                'glitchtip_triage_auto_resolve_patterns. Default 10 surfaces '
                'novel recurring errors as a Telegram/Discord page before '
                'they pile up unseen. Per-issue dedupe by id within a single '
                'brain process uptime — restart the brain to re-page. Bump '
                'higher if the pager gets too chatty.',
                'f', 't'
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260522_212507: glitchtip_triage_alert_threshold_count "
            "lowered to 10 (was 100) to surface novel recurring issues"
        )


async def down(pool) -> None:
    """Revert to the previous default of 100."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
               SET value = '100',
                   updated_at = NOW()
             WHERE key = 'glitchtip_triage_alert_threshold_count'
            """
        )
        logger.info(
            "Migration 20260522_212507 down: threshold restored to 100"
        )

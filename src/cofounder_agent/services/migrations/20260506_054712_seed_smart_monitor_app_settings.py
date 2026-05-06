"""Migration 20260506_054712: seed smart monitor app_settings

ISSUE: Glad-Labs/poindexter#387 (brain SMART monitoring probe).

Seeds the nine tunables ``brain/smart_monitor.py`` reads on every
cycle. Defaults flag the SMART attributes most predictive of imminent
drive failure (Reallocated_Sector_Ct, Current_Pending_Sector,
Wear_Leveling_Count, Power_On_Hours, SMART self-test). The probe
re-reads each cycle so an operator can re-tune via
``poindexter set <key> <value>`` without restarting the brain.

The dedup window (default 6 h) prevents the same drive+attribute pair
from re-paging on every cycle for the lifetime of the bad sector.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES
                ('smart_monitor_enabled', 'true', 'monitoring',
                 'Master switch for the brain SMART monitor probe (#387). When false, the probe short-circuits without scanning drives.',
                 false, true),
                ('smart_monitor_poll_interval_hours', '6', 'monitoring',
                 'Cadence at which the brain runs `smartctl -a` against each detected drive. Default 6h matches typical SMART attribute update cadence.',
                 false, true),
                ('smart_monitor_drive_filter', '', 'monitoring',
                 'Optional comma-separated list of drive names (e.g. /dev/sda,/dev/nvme0) to restrict scanning to. Empty = scan everything `smartctl --scan-open` finds.',
                 false, true),
                ('smart_monitor_reallocated_sector_threshold', '0', 'monitoring',
                 'Inclusive threshold for Reallocated_Sector_Ct. Anything strictly greater fires a warning alert. 0 = any reallocated sector at all is a warning (most aggressive — recommended).',
                 false, true),
                ('smart_monitor_current_pending_threshold', '0', 'monitoring',
                 'Inclusive threshold for Current_Pending_Sector. Anything strictly greater fires a warning. 0 = any pending sector at all (recommended — pending sectors precede reallocations).',
                 false, true),
                ('smart_monitor_wear_leveling_warn_percent', '90', 'monitoring',
                 'Used-life percentage for SSD Wear_Leveling_Count above which the probe fires a warning. Computed as (100 - normalized_value) for the SMART attribute. Default 90 = warn when only 10% endurance remains.',
                 false, true),
                ('smart_monitor_power_on_hours_info_threshold', '50000', 'monitoring',
                 'Power_On_Hours threshold above which the probe emits an info-severity FYI alert. ~50k h = ~5.7 years; useful for replacement-planning, not an emergency.',
                 false, true),
                ('smart_monitor_smartctl_path', '', 'monitoring',
                 'Absolute path to the smartctl binary. Empty = use shutil.which("smartctl"). Override when smartmontools is installed somewhere unusual (e.g. C:\\Program Files\\smartmontools\\bin\\smartctl.exe).',
                 false, true),
                ('smart_monitor_alert_dedup_minutes', '360', 'monitoring',
                 'Don''t re-fire the same (drive, attribute) alert within this many minutes. Default 360 (6 h) matches the default poll interval — one alert per attribute per cycle, max.',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260506_054712: applied (9 smart_monitor_* settings)"
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'smart_monitor_enabled',
                'smart_monitor_poll_interval_hours',
                'smart_monitor_drive_filter',
                'smart_monitor_reallocated_sector_threshold',
                'smart_monitor_current_pending_threshold',
                'smart_monitor_wear_leveling_warn_percent',
                'smart_monitor_power_on_hours_info_threshold',
                'smart_monitor_smartctl_path',
                'smart_monitor_alert_dedup_minutes'
            )
            """
        )
        logger.info("Migration 20260506_054712: reverted")

"""Regression gate: operator-tenant identifiers must not ship in baseline seeds.

2026-05-27 security audit found 6 rows in ``0000_baseline.seeds.sql`` whose
seeded VALUE contained operator-specific identifiers (Telegram chat ID,
Cloudflare account ID, Cloudinary cloud name, Discord channel ID, Sentry
DSN, R2 storage endpoint). These ship to fresh OSS installs and correlate
back to the Glad Labs operator tenant.

Fix: the values were blanked (replaced with empty string) so fresh installs
start unconfigured and run ``poindexter setup`` to fill them in. Matt's
local DB is unaffected because all inserts use ``ON CONFLICT DO NOTHING``.

This test:
1. Verifies none of the six leaked literals are present in the seed file.
2. Verifies the six rows themselves still exist (so fresh installs seed
   the keys at all, even if the value is blank).
3. Verifies all six inserts use ON CONFLICT DO NOTHING (idempotent).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Leaked values that must no longer appear in the seeds file (Leak 3).
# Using regex matches on patterns rather than exact strings so the gate
# survives minor description edits without needing updates.
# ---------------------------------------------------------------------------

_BANNED_PATTERNS = [
    # Telegram chat ID (numeric, operator-specific)
    # Pattern matches a specific 10-digit number that was seeded; we match
    # the rough shape so it doesn't look like a valid OSS default.
    (
        "telegram_chat_id operator value",
        re.compile(r"'telegram_chat_id',\s*'[0-9]{7,}'"),
    ),
    # Cloudflare account ID (32-char hex)
    (
        "cloudflare_account_id operator value",
        re.compile(r"'cloudflare_account_id',\s*'[0-9a-f]{20,}'"),
    ),
    # Cloudinary cloud name (short alphanum slug, operator-specific)
    (
        "cloudinary_cloud_name operator value",
        re.compile(r"'cloudinary_cloud_name',\s*'[a-z0-9]{5,}'"),
    ),
    # Discord ops channel ID (18-19 digit snowflake)
    (
        "discord_ops_channel_id operator value",
        re.compile(r"'discord_ops_channel_id',\s*'[0-9]{15,}'"),
    ),
    # Sentry / GlitchTip DSN (http[s]://... form containing an install-specific key)
    (
        "sentry_dsn operator value",
        re.compile(r"'sentry_dsn',\s*'https?://[a-f0-9]{20,}@"),
    ),
    # Storage endpoint containing Cloudflare account ID in subdomain
    (
        "storage_endpoint R2 operator value",
        re.compile(r"'storage_endpoint',\s*'https://[0-9a-f]{20,}\.r2\.cloudflarestorage"),
    ),
    # Extra belt-and-suspenders: the telegram description must not reference
    # "Matt DM" — that was the personal identifier in the seed comment.
    (
        "telegram_chat_id description must not say 'Matt DM'",
        re.compile(r"'telegram_chat_id'[^;]*Matt DM"),
    ),
]


@pytest.mark.parametrize("label,pattern", _BANNED_PATTERNS)
def test_operator_identifier_not_seeded(
    label: str, pattern: re.Pattern[str], baseline_seeds_text: str
) -> None:
    """The named operator identifier must NOT appear in the baseline seed file.

    If this fails, an operator-tenant value has re-appeared in the seed
    (e.g. from a re-run of the settings-defaults extractor). Blank the
    value to '' — ON CONFLICT DO NOTHING ensures Matt's local DB is safe.
    """
    match = pattern.search(baseline_seeds_text)
    assert match is None, (
        f"Operator identifier leaked in baseline seeds ({label}).\n"
        f"Matched text: {match.group(0)!r}\n"
        "Blank the seeded value to '' — see 2026-05-27 security audit."
    )


# ---------------------------------------------------------------------------
# Sanity checks: the six rows must still be present (just with blank values)
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = [
    "telegram_chat_id",
    "cloudflare_account_id",
    "cloudinary_cloud_name",
    "discord_ops_channel_id",
    "sentry_dsn",
    "storage_endpoint",
]


@pytest.mark.parametrize("key", _REQUIRED_KEYS)
def test_seed_row_still_present(key: str, baseline_seeds_text: str) -> None:
    """The seed row for each key must still exist (with a blank value).

    Blanking the value is not the same as removing the row — fresh installs
    need the key present in app_settings so code can detect it and prompt
    the operator to configure it.
    """
    assert f"'{key}'" in baseline_seeds_text, (
        f"Seed row for '{key}' is missing from 0000_baseline.seeds.sql. "
        "The row must exist with value='' so fresh installs have the key "
        "in app_settings (operators configure it via poindexter setup)."
    )


@pytest.mark.parametrize("key", _REQUIRED_KEYS)
def test_seed_row_is_idempotent(key: str, baseline_seeds_text: str) -> None:
    """Every seed INSERT for these keys must use ON CONFLICT DO NOTHING.

    Without this clause, replaying the baseline on an existing DB would
    clobber any operator-configured value with the (now-blank) seed.
    """
    # Find the INSERT statement for this key.
    pattern = re.compile(
        r"INSERT INTO app_settings[^;]*?'" + re.escape(key) + r"'[^;]*;",
        re.DOTALL,
    )
    match = pattern.search(baseline_seeds_text)
    assert match is not None, f"No INSERT for '{key}' found in baseline seeds"
    assert "ON CONFLICT (key) DO NOTHING" in match.group(0), (
        f"INSERT for '{key}' is missing ON CONFLICT clause. "
        "Without it, replaying the baseline on an existing DB would "
        "overwrite the operator's runtime-configured value with ''."
    )

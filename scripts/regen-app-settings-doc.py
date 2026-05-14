#!/usr/bin/env python3
"""Regenerate docs/reference/app-settings.md from the live app_settings table.

Run from repo root:  `python scripts/regen-app-settings-doc.py`

**The output ships to the public Poindexter mirror.** The sync filter
strips only specific subpaths under ``docs/`` (audits, brand assets,
superpowers) — ``docs/reference/`` itself ships. The earlier banner that
claimed otherwise was wrong and led to a real bank balance + Tailnet IP
leak before the 2026-05-14 audit caught it.

Three redaction tiers protect against value leaks:

1. ``is_secret=true`` rows: value becomes ``*(encrypted)*``.
2. Secret-shaped strings that *aren't* flagged ``is_secret=true``
   (defense-in-depth — see ``looks_secret``).
3. Operator-specific PII / infra: real bank balances, Tailnet IPs,
   Tailscale Funnel hostnames, etc. Listed in
   ``_PRIVATE_VALUE_KEYS`` + ``_PRIVATE_VALUE_PATTERNS``. Value becomes
   ``*(per-operator)*``; key name + description still ship so OSS users
   know the knob exists.
4. Private-module surface (key NAMES that leak the existence of an
   operator-overlay module): listed in ``_PRIVATE_KEY_PATTERNS``. Row
   is dropped entirely.

Add a new private key by appending to the relevant list and re-running
this script. Don't ship a generated doc that contains the operator's
actual values.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import OrderedDict
from pathlib import Path

# brain/ lives at the repo root (not under src/cofounder_agent). Prepend the
# repo root so `from brain.bootstrap import ...` resolves regardless of the
# caller's CWD — lets `python scripts/regen-app-settings-doc.py` run cleanly
# from anywhere, including a CI workflow that checks for doc drift.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from brain.bootstrap import resolve_database_url  # noqa: E402

_SECRET_PATTERNS = [
    re.compile(r"^[a-f0-9]{20,}$"),
    re.compile(r"^[A-Za-z0-9]{32,}$"),
    re.compile(r"^sk-[A-Za-z0-9]{10,}"),
    re.compile(r"^ghp_|^github_pat_|^gho_"),
    re.compile(r"^xox[baprs]-"),
    re.compile(r"-----BEGIN"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
]
_SECRET_KEY_HINTS = re.compile(r"_(key|token|secret|password|dsn)(_|$)", re.IGNORECASE)

# Keys whose values match a secret-shaped pattern but are public identifiers,
# not credentials. Cloudflare account IDs, for example, appear in dashboard
# URLs and API paths (https://api.cloudflare.com/client/v4/accounts/{id}/...).
# Listing them here suppresses the look-secret redaction so the preview stays
# focused on values that genuinely need rotation.
_NOT_SECRET_KEYS: frozenset[str] = frozenset({
    "cloudflare_account_id",
})

# Keys whose VALUES are operator-specific PII / private infrastructure and
# must not be exported to the public OSS mirror. The key NAMES are fine to
# document (helps OSS users understand what knobs exist); only the value
# gets redacted to `*(per-operator)*`. Add a key here when it stores:
#   - real financial data (bank balances, hardware costs, revenue totals)
#   - operator-specific URLs (Tailnet IPs, Tailscale Funnel hostnames,
#     private LAN endpoints)
#   - any other "this row is the operator's actual value, not a shipped default"
_PRIVATE_VALUE_KEYS: frozenset[str] = frozenset({
    "mercury_balance",
    "hardware_cost_total",
    "preview_base_url",
    "oauth_issuer_url",
    "voice_agent_public_join_url",
})

# Regex over VALUES that captures any operator-specific infrastructure
# pattern, even if the key name doesn't appear in _PRIVATE_VALUE_KEYS
# (e.g. a future setting that happens to default to the operator's tailnet
# address). Belt-and-suspenders for the key-name allowlist above.
_PRIVATE_VALUE_PATTERNS = [
    re.compile(r"\b100\.81\.93\.12\b"),          # operator Tailnet IP
    re.compile(r"\b\w+\.taild4f626\.ts\.net\b"),  # operator Tailscale Funnel
]


def is_private_value(key: str, value: str) -> bool:
    """Return True if this row's VALUE must be redacted from the public doc.

    Distinct from ``looks_secret`` (credential-shaped) — these are
    plaintext values that just happen to encode the operator's identity
    or financial reality. Key names stay; values get replaced.
    """
    if key in _PRIVATE_VALUE_KEYS:
        return True
    if value:
        return any(p.search(value) for p in _PRIVATE_VALUE_PATTERNS)
    return False


# Key-name patterns that point at a `visibility="private"` Module's
# surface. Matching rows get dropped from the doc entirely — the key
# name itself leaks the existence of the private overlay.
_PRIVATE_KEY_PATTERNS = [
    re.compile(r"^mercury_"),                            # operator-overlay banking integration
    re.compile(r"^plugin_job_(last_run|last_status)_poll_mercury$"),
]


def is_private_key(key: str) -> bool:
    """Return True if the key NAME exposes a private module's surface.

    Rows matching this filter are skipped entirely so the public doc
    doesn't even hint at the private module's existence.
    """
    return any(p.search(key) for p in _PRIVATE_KEY_PATTERNS)


def looks_secret(key: str, value: str) -> bool:
    if not value:
        return False
    if key in _NOT_SECRET_KEYS:
        return False
    if (
        _SECRET_KEY_HINTS.search(key)
        and len(value) >= 10
        and "." not in value
        and "/" not in value
    ):
        return True
    return any(p.search(value) for p in _SECRET_PATTERNS)


async def main() -> None:
    import asyncpg

    conn = await asyncpg.connect(resolve_database_url())
    try:
        rows = await conn.fetch(
            """
            SELECT category, key, value, description, is_secret
            FROM app_settings
            WHERE is_active = true
            ORDER BY category NULLS LAST, key
            """,
        )
        # Drop rows whose keys belong to private modules entirely —
        # the key NAME leaks the module's existence, not just the
        # value. Counted in the banner so the operator can confirm
        # the filter caught everything they expected.
        private_dropped = sum(1 for r in rows if is_private_key(r["key"]))
        rows = [r for r in rows if not is_private_key(r["key"])]
        groups: OrderedDict[str, list] = OrderedDict()
        for r in rows:
            groups.setdefault(r["category"] or "uncategorized", []).append(r)
        encrypted = sum(1 for r in rows if r["is_secret"])
        redacted = sum(
            1
            for r in rows
            if not r["is_secret"] and looks_secret(r["key"], r["value"] or "")
        )
        per_operator = sum(
            1
            for r in rows
            if not r["is_secret"]
            and is_private_value(r["key"], r["value"] or "")
        )
    finally:
        await conn.close()

    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out: list[str] = [
        "# App settings reference",
        "",
        f"> **Auto-generated from live `app_settings` table on {stamp}.**  ",
        f"> Every runtime-configurable knob in the Poindexter pipeline.",
        (
            f"> {len(rows)} active rows across {len(groups)} categories. "
            f"{encrypted} stored encrypted via pgcrypto (`is_secret=true`); "
            f"{redacted} additional values redacted as secret-shaped "
            f"(defense-in-depth); {per_operator} values redacted as "
            "operator-specific (Tailnet IPs, financial reality, etc.) so "
            "this file is safe to ship to the public OSS mirror."
        ),
        "",
        "> Generated values are example/per-operator. Set yours via "
        "`poindexter set <key> <value>` or `poindexter settings set "
        "<key> <value> --secret` for `is_secret=true` rows.",
        "",
        "> **To regenerate:** `python scripts/regen-app-settings-doc.py`",
        "",
        "To change any value:",
        "",
        "```sql",
        "-- Read",
        "SELECT key, value, updated_at FROM app_settings WHERE key = 'content_quality_minimum';",
        "",
        "-- Write (non-secret)",
        "UPDATE app_settings SET value = '78', updated_at = NOW() WHERE key = 'content_quality_minimum';",
        "",
        "-- Write (secret — use the helper so pgcrypto encrypts on write)",
        "-- See services/plugins/secrets.py::set_secret() for the Python API.",
        "```",
        "",
        "The worker re-reads on every poll; no restart needed.",
        "",
        "---",
        "",
        "## Table of contents",
        "",
    ]
    for cat, rs in groups.items():
        anchor = cat.replace("_", "-").replace(" ", "-").lower()
        s = "s" if len(rs) != 1 else ""
        out.append(f"- [{cat}](#{anchor}) ({len(rs)} key{s})")
    out.append("")
    for cat, rs in groups.items():
        out.append(f"## {cat}")
        out.append("")
        out.append("| Key | Default | Classification | Description |")
        out.append("| --- | --- | --- | --- |")
        for r in rs:
            key = r["key"]
            val = r["value"] or ""
            cls = ""
            if r["is_secret"]:
                val = "*(encrypted)*"
                cls = "encrypted"
            elif looks_secret(key, val):
                val = (
                    "*(redacted — looks secret-shaped but not classified "
                    "`is_secret=true` in DB)*"
                )
                cls = "look-secret"
            elif is_private_value(key, val):
                # Operator-specific value (bank balance, Tailnet IP,
                # Tailscale Funnel hostname, etc.). Key name + description
                # still ship so OSS users know what the knob does;
                # value gets the placeholder.
                val = "*(per-operator)*"
                cls = "per-operator"
            elif len(val) > 40:
                val = val[:37] + "..."
            key_esc = key.replace("|", r"\|")
            val_esc = val.replace("|", r"\|").replace("\n", " ")
            desc = (r["description"] or "").replace("|", r"\|").replace("\n", " ")
            if len(desc) > 120:
                desc = desc[:117] + "..."
            out.append(f"| `{key_esc}` | `{val_esc}` | {cls} | {desc} |")
        out.append("")

    target = _REPO / "docs" / "reference" / "app-settings.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(
        f"Wrote {target}: {len(rows)} rows shown "
        f"({private_dropped} private-module keys dropped, "
        f"{encrypted} encrypted, "
        f"{redacted} look-secret redacted, "
        f"{per_operator} per-operator redacted)",
    )


if __name__ == "__main__":
    asyncio.run(main())

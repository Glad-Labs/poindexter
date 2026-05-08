#!/usr/bin/env python3
"""Regenerate docs/reference/app-settings.md from the live app_settings table.

Run from repo root:  `python scripts/regen-app-settings-doc.py`
Output is excluded from the public sync (`scripts/sync-to-github.sh` strips
`docs/`) so it's safe to include real category/key context. Secret-classified
values and secret-shaped-but-unclassified values are both redacted per the
two-tier defense in this script — see gitea#278 for the classification gap.
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
        groups: OrderedDict[str, list] = OrderedDict()
        for r in rows:
            groups.setdefault(r["category"] or "uncategorized", []).append(r)
        encrypted = sum(1 for r in rows if r["is_secret"])
        redacted = sum(
            1
            for r in rows
            if not r["is_secret"] and looks_secret(r["key"], r["value"] or "")
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
            f"an additional {redacted} values are redacted in the preview "
            "below as defense-in-depth against secret-shaped strings that "
            "weren't classified as secrets in the DB."
        ),
        "",
        "> This file is checked into `docs/` which is **excluded from the "
        "public Poindexter sync** (`scripts/sync-to-github.sh` strips "
        "`docs/`). Safe to regenerate from operator state. Not safe to "
        "publish outside the private mirror.",
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
        f"Wrote {target}: {len(rows)} rows, {encrypted} encrypted, "
        f"{redacted} look-secret redacted",
    )


if __name__ == "__main__":
    asyncio.run(main())

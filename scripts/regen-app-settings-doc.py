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
import os
import re
import sys
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

# brain/ lives at the repo root (not under src/cofounder_agent). Prepend the
# repo root so `from brain.bootstrap import ...` resolves regardless of the
# caller's CWD — lets `python scripts/regen-app-settings-doc.py` run cleanly
# from anywhere, including a CI workflow that checks for doc drift.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from brain.bootstrap import resolve_database_url  # noqa: E402

# This file is generated for the PUBLIC OSS mirror, so the ONLY correct source
# is a throwaway DB seeded by the baseline migration — that is what CI does
# (.github/workflows/regen-app-settings-doc.yml spins up pgvector/pgvector:pg16,
# runs migrations_smoke.py, then this script). A fresh baseline has exactly two
# is_secret rows and both are empty placeholders.
#
# Two guards, because the default resolution order actively works against that.
_ALLOW_OPERATOR_DB_ENV = "REGEN_ALLOW_OPERATOR_DB"


def _doc_database_url() -> str | None:
    """Resolve the DSN with ``DATABASE_URL`` ABOVE ``bootstrap.toml``.

    ``brain.bootstrap.resolve_database_url`` deliberately ranks bootstrap.toml
    first: for a runtime entry point (worker, brain, CLI) the operator's own
    install IS the right target, and an env var should not silently redirect
    production. This script is the opposite case — a doc generator whose output
    ships publicly — so the precedence is inverted here rather than changed
    globally, which would move production's footing for every other caller.

    Without this, an operator running the script locally gets prod: bootstrap.toml
    exists on every real install and wins over an explicitly exported
    ``DATABASE_URL``, so the command silently reads live settings while appearing
    to honour the env var you just set. (Observed 2026-08-09: a local run against
    an explicit throwaway DSN still rendered all 1420 prod rows.)
    """
    return resolve_database_url(explicit=(os.getenv("DATABASE_URL") or "").strip() or None)


async def _assert_fresh_baseline_db(conn) -> None:
    """Refuse to render the public doc from a populated operator database.

    The signal is exact, not heuristic: the baseline seeds two ``is_secret``
    placeholders and both are empty (``0000_baseline.seeds.sql``). A single
    is_secret row with a non-empty value therefore means real operator
    credentials are present, so this is somebody's live install.

    Failing loud beats trusting the redaction tiers. Those tiers are
    defense-in-depth against *known* key-name and value shapes; they cannot
    know that an operator-only key added last week holds a private hostname.
    Never generating from prod in the first place is the actual control.
    """
    populated_secrets = await conn.fetchval(
        "SELECT count(*) FROM app_settings "
        "WHERE is_secret = true AND coalesce(value, '') <> ''"
    )
    if not populated_secrets:
        return
    if os.getenv(_ALLOW_OPERATOR_DB_ENV, "").strip().lower() in {"1", "true", "yes"}:
        print(
            f"WARNING: {populated_secrets} populated secret(s) — this is an "
            f"operator database. Proceeding only because {_ALLOW_OPERATOR_DB_ENV} "
            "is set. Do NOT commit the result.",
            file=sys.stderr,
        )
        return
    raise SystemExit(
        f"refusing to regenerate the public app-settings doc: the target database "
        f"holds {populated_secrets} populated secret(s), so it is a live operator "
        f"install, not a fresh baseline.\n\n"
        f"This doc ships to the public OSS mirror and must be rendered from a "
        f"throwaway DB seeded by the baseline migration:\n"
        f"  docker run -d --rm --name pdx-doc -e POSTGRES_USER=postgres \\\n"
        f"    -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=poindexter_test \\\n"
        f"    -p 55433:5432 pgvector/pgvector:pg16\n"
        f"  export DATABASE_URL=postgres://postgres:postgres@localhost:55433/poindexter_test\n"
        f"  python scripts/ci/migrations_smoke.py\n"
        f"  python scripts/regen-app-settings-doc.py\n\n"
        f"Note that exporting DATABASE_URL is only honoured because this script "
        f"inverts the usual bootstrap.toml-first precedence; every other tool "
        f"would still read your operator DB.\n\n"
        f"To inspect your own settings without writing the doc, query app_settings "
        f"directly or use `poindexter settings list`. Set "
        f"{_ALLOW_OPERATOR_DB_ENV}=1 only for a deliberate local render you will "
        f"not commit."
    )


_SECRET_PATTERNS = [
    re.compile(r"^[a-f0-9]{20,}$"),
    re.compile(r"^[A-Za-z0-9]{32,}$"),
    re.compile(r"^sk-[A-Za-z0-9]{10,}"),
    re.compile(r"^ghp_|^github_pat_|^gho_|^ghs_"),
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
    # === Operator identity (feedback_no_operator_info_to_public_repo, 2026-05-23) ===
    "owner_name",                # carries the operator's real name
    "writing_style_reference",   # may carry "<name>'s writing style traits ..."
    "company_founder_name",      # operator's name
    "social_linkedin_url",       # operator's LinkedIn URL
    "social_x_url",              # operator's X/Twitter URL
    "social_x_handle",           # operator's X/Twitter handle
    # === Operator infrastructure identifiers (2026-06-30) — DB values were
    # empty at the last regen and have since been populated; not caught by the
    # value patterns below, so key-listed explicitly ===
    "cloudflare_account_id",     # real Cloudflare account UUID
    "sentry_dsn",                # GlitchTip/Sentry ingestion DSN (credential-shaped)
    "podcast_spotify_url",       # operator's Spotify show URL
    "offsite_backup_repository", # Backblaze B2 endpoint + bucket name
})

# Regex over VALUES that captures any operator-specific infrastructure
# pattern, even if the key name doesn't appear in _PRIVATE_VALUE_KEYS
# (e.g. a future setting that happens to default to the operator's tailnet
# address). Belt-and-suspenders for the key-name allowlist above.
_PRIVATE_VALUE_PATTERNS = [
    re.compile(r"\b100\.81\.93\.12\b"),          # operator Tailnet IP
    re.compile(r"\b\w+\.taild4f626\.ts\.net\b"),  # operator Tailscale Funnel
    # Operator brand / identity in values (site URLs, @gladlabs.io emails,
    # storage bucket, company_products, caption-bias prompt). "Glad Labs"
    # (with a space — the human-readable brand) is intentionally NOT matched
    # so company_name still ships as the example value.
    re.compile(r"gladlabs", re.IGNORECASE),
    re.compile(r"\bmattg\b", re.IGNORECASE),      # operator handle (CF Workers subdomain, GitHub)
    re.compile(r"\bmattm\b", re.IGNORECASE),      # operator Windows username (file-path values)
    re.compile(r"\.r2\.dev\b", re.IGNORECASE),    # operator R2 public bucket (pub-<hash>.r2.dev)
    re.compile(r"\.r2\.cloudflarestorage\.com\b", re.IGNORECASE),  # operator R2 S3 endpoint (account-id host)
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
    # FinanceModule (visibility="private") surface — the key NAMES (and their
    # "Mercury" descriptions) leak the private banking overlay. Catches
    # finance_*, plugin.job.poll_mercury, and prometheus.{rule,threshold}.*
    # for the Mercury poll. "finance"/"mercury" appear in no generic key
    # (verified 2026-06-30), so this drops the whole overlay surface.
    re.compile(r"[Ff]inance|[Mm]ercury"),
]


def is_private_key(key: str) -> bool:
    """Return True if the key NAME exposes a private module's surface.

    Rows matching this filter are skipped entirely so the public doc
    doesn't even hint at the private module's existence.
    """
    return any(p.search(key) for p in _PRIVATE_KEY_PATTERNS)


_STAMP_OVERRIDE_ENV = "REGEN_DATE_OVERRIDE"


def resolved_stamp(environ: dict[str, str] | None = None) -> str:
    """Resolve the ``YYYY-MM-DD`` stamp embedded in the doc's banner.

    CI pins this via ``REGEN_DATE_OVERRIDE`` so the regenerated file is
    byte-stable across runs of the same source state — otherwise the
    "Auto-generated on {today}" line moves every nightly run and the
    drift-check workflow opens a new PR for what is essentially the
    same content. The override is a plain string (no parsing) so callers
    can pass anything human-readable; ``main()`` writes it through.

    Falls back to today's UTC date when the env var is unset, matching
    the original behavior for interactive `python scripts/regen-app-settings-doc.py`
    runs at Matt's terminal.
    """
    env = environ if environ is not None else os.environ
    override = env.get(_STAMP_OVERRIDE_ENV)
    if override:
        return override
    return datetime.now(UTC).strftime("%Y-%m-%d")


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

    conn = await asyncpg.connect(_doc_database_url())
    try:
        await _assert_fresh_baseline_db(conn)
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

    stamp = resolved_stamp()
    out: list[str] = [
        "# App settings reference",
        "",
        f"> **Auto-generated from live `app_settings` table on {stamp}.**  ",
        "> Every runtime-configurable knob in the Poindexter pipeline.",
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
        "`poindexter settings set <key> <value>` (add `--secret` to store "
        "the value encrypted with `is_secret=true`).",
        "",
        (
            "> **To regenerate:** CI does this nightly "
            "(`.github/workflows/regen-app-settings-doc.yml`) against a "
            "throwaway DB seeded by the baseline migration — that is the only "
            "correct source, since this file ships to the public mirror. "
            "Running the script against your own install is refused; see the "
            "script's error message for the local throwaway-DB procedure."
        ),
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
        "-- Write (secret) — raw SQL can't encrypt; use the CLI instead:",
        "--   poindexter settings set <key> <value> --secret",
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

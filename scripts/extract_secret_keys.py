#!/usr/bin/env python3
"""Heuristically identify is_secret=true keys from migration source files.

Walks every migration file under services/migrations/ that does an
INSERT INTO app_settings, looks for nearby is_secret=TRUE / "is_secret": True
markers, and emits the union of keys that appear secret-flagged.

Used during issue #379 to make sure seed_all_defaults() never accidentally
puts a placeholder string in a slot that is meant to be encrypted-at-rest.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIG_DIR = ROOT / "src" / "cofounder_agent" / "services" / "migrations"

# Conservative heuristic key-name patterns that we treat as secrets even
# if a migration didn't explicitly mark them is_secret=true. These are
# names we'd rather miss-include than miss-exclude.
# Tight key-name patterns. A key matching ANY of these is excluded from
# the seed registry. We err toward over-excluding — the operator can
# always set them later via the secrets API or `poindexter setup`.
SECRET_NAME_PATTERNS = [
    re.compile(r".*_api_key$"),
    re.compile(r".*_api_token$"),
    re.compile(r".*_password$"),
    re.compile(r".*_secret$"),
    re.compile(r".*_secret_key$"),
    re.compile(r".*_dsn$"),
    re.compile(r".*_bot_token$"),
    re.compile(r"^api_token$"),
    re.compile(r"^cli_oauth_client_secret$"),
    re.compile(r"^cli_oauth_client_id$"),
    re.compile(r"^jwt_secret(_key)?$"),
    re.compile(r"^session_secret$"),
    re.compile(r"^revalidate_secret$"),
    re.compile(r"^encryption_master_key$"),
    re.compile(r"^langfuse_(public|secret)_key$"),
    re.compile(r"^database_url$"),  # contains credentials
    re.compile(r"^litellm_master_key$"),
    re.compile(r"^operator_id$"),  # PII
    re.compile(r"^owner_email$"),  # PII (operator's email)
    re.compile(r"^smtp_user$"),  # SMTP creds
    re.compile(r"^newsletter_from_email$"),  # PII identifier
    re.compile(r"^telegram_chat_id$"),  # PII identifier
    re.compile(r".*_webhook_url$"),  # webhook URLs leak server identity
]


def _is_secret_name(name: str) -> bool:
    return any(p.match(name) for p in SECRET_NAME_PATTERNS)


def main() -> int:
    secret_keys: set[str] = set()

    if MIG_DIR.is_dir():
        for path in MIG_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            # Pattern A: dict literal "is_secret": True near "key": "name"
            for m in re.finditer(
                r'"key"\s*:\s*"([a-zA-Z0-9_.\-]+)"[^}]{0,400}?"is_secret"\s*:\s*True',
                text, re.DOTALL,
            ):
                secret_keys.add(m.group(1))
            # Pattern B: tuple literal ("key", value, "category", "desc", True)
            # Reads from the `_SETTINGS = [(...), ...]` block in 0058 et al.
            for m in re.finditer(
                r'\(\s*"([a-zA-Z0-9_.\-]+)"\s*,'
                r'\s*"[^"]*"\s*,'
                r'\s*"[^"]*"\s*,'
                r'\s*"[^"]*"\s*,'
                r'\s*True\s*\)',
                text,
            ):
                key = m.group(1)
                # Skip false positives — common control words
                if key.lower() in {"true", "false", "none"}:
                    continue
                secret_keys.add(key)

    # Union with the heuristic name patterns applied across the
    # extract.json universe.
    extract_path = ROOT / "scripts" / "settings_defaults_extract.json"
    if extract_path.is_file():
        data = json.loads(extract_path.read_text(encoding="utf-8"))
        for k in data["by_key"]:
            if _is_secret_name(k):
                secret_keys.add(k)

    out = sorted(secret_keys)
    out_path = ROOT / "scripts" / "settings_secret_keys.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} — {len(out)} secret key(s)")
    for k in out:
        print(f"  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

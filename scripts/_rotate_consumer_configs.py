"""One-off consumer-config rotation — feat/rotate-api-token.

Reads new token from a 0600 file and rewrites POINDEXTER_API_TOKEN /
GLADLABS_API_TOKEN env entries in:

  - ~/.claude.json (mcpServers.poindexter.env, mcpServers.gladlabs.env)
  - ~/.openclaw/openclaw.json (skills entries)
  - ~/.poindexter/bootstrap.toml (api_token = "...") — set to placeholder
    pointing at app_settings (the encrypted DB row is now source of truth)

Each file is backed up alongside as <name>.bak.rotate before any change.
Never echoes the token. Reports a count of replacements per file.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path


def _update_claude_json(new_token: str) -> dict:
    p = Path.home() / ".claude.json"
    if not p.exists():
        return {"file": str(p), "skipped": "missing"}
    shutil.copy2(p, p.with_suffix(p.suffix + ".bak.rotate"))
    data = json.loads(p.read_text(encoding="utf-8"))
    servers = data.get("mcpServers", {})
    changed: list[str] = []
    for name in ("poindexter", "gladlabs"):
        s = servers.get(name)
        if not s:
            continue
        env = s.get("env", {})
        for key in ("POINDEXTER_API_TOKEN", "GLADLABS_API_TOKEN"):
            if key in env:
                env[key] = new_token
                changed.append(f"{name}.{key}")
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"file": str(p), "updated": changed}


def _update_openclaw_json(new_token: str) -> dict:
    p = Path.home() / ".openclaw" / "openclaw.json"
    if not p.exists():
        return {"file": str(p), "skipped": "missing"}
    shutil.copy2(p, p.with_suffix(p.suffix + ".bak.rotate"))
    data = json.loads(p.read_text(encoding="utf-8"))
    changed = 0
    # OpenClaw config has skills with env dicts that may contain
    # POINDEXTER_API_TOKEN / GLADLABS_API_TOKEN. Walk the whole
    # tree and replace any matching key value.

    def walk(obj):
        nonlocal changed
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k in ("POINDEXTER_API_TOKEN", "GLADLABS_API_TOKEN") and isinstance(v, str):
                    obj[k] = new_token
                    changed += 1
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"file": str(p), "updated_count": changed}


def _update_bootstrap_toml(new_token: str) -> dict:
    p = Path.home() / ".poindexter" / "bootstrap.toml"
    if not p.exists():
        return {"file": str(p), "skipped": "missing"}
    shutil.copy2(p, p.with_suffix(p.suffix + ".bak.rotate"))
    text = p.read_text(encoding="utf-8")
    # The bootstrap.toml api_token line is a legacy plaintext fallback
    # that the worker no longer uses (app_settings.api_token is
    # source-of-truth). Updating for consistency so any tooling that
    # still reads bootstrap.toml gets the rotated value.
    new_text, n = re.subn(
        r'^api_token\s*=\s*".*?"',
        f'api_token = "{new_token}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    p.write_text(new_text, encoding="utf-8")
    return {"file": str(p), "replaced": n}


def main(token_path: str) -> None:
    new_token = Path(token_path).read_text(encoding="utf-8").strip()
    if not new_token or len(new_token) < 32:
        print("ERROR: token file empty or too short", file=sys.stderr)
        sys.exit(2)

    results = [
        _update_claude_json(new_token),
        _update_openclaw_json(new_token),
        _update_bootstrap_toml(new_token),
    ]
    for r in results:
        print(r)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: _rotate_consumer_configs.py <token-file>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])

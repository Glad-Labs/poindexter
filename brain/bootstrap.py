"""bootstrap — resolve the minimum config needed to reach the database (#198).

Poindexter keeps every runtime setting in the `app_settings` DB table.
But you can't read the DB table without knowing where the DB is. This
module solves the chicken-and-egg: it finds the one value you need to
bootstrap — the database URL — from the first place it can:

    1. explicit argument passed by the caller (CLI --db-url, tests)
    2. ~/.poindexter/bootstrap.toml  (Jellyfin/Plex-style user config)
    3. DATABASE_URL environment variable
    4. LOCAL_DATABASE_URL environment variable
    5. POINDEXTER_MEMORY_DSN environment variable (legacy)
    6. notify the operator + return None

Only stdlib is used, so this module loads before any asyncpg/httpx/fastapi
imports. That lets every entry point — brain daemon, worker startup, MCP
server, poindexter CLI — share one source of truth.

bootstrap.toml format:

    # ~/.poindexter/bootstrap.toml
    database_url = "postgresql://..."
    # Optional — used for graceful degradation when the DB is down and
    # the system still needs to email/page you.
    telegram_bot_token = "..."
    telegram_chat_id = "..."
    discord_ops_webhook_url = "..."

The file is written by `poindexter setup` on first run. No tool should
read or write it except through this module.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Python 3.11+ ships tomllib in stdlib; Python 3.10 needs tomli. We target
# 3.12 but guard for older runtimes so the import doesn't break them.
try:
    import tomllib as _tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — runtimes we don't ship for
    _tomllib = None  # type: ignore[assignment]


BOOTSTRAP_DIR = Path.home() / ".poindexter"
BOOTSTRAP_FILE = BOOTSTRAP_DIR / "bootstrap.toml"

# Env var names checked in order. The first one that's set wins.
_DB_URL_ENV_VARS = (
    "DATABASE_URL",
    "LOCAL_DATABASE_URL",
    "POINDEXTER_MEMORY_DSN",
)


def _read_bootstrap_toml() -> dict[str, Any]:
    """Return the parsed bootstrap.toml contents, or an empty dict if missing."""
    if not BOOTSTRAP_FILE.is_file():
        return {}
    if _tomllib is None:
        # No TOML parser available. Callers will fall through to env vars.
        return {}
    try:
        with BOOTSTRAP_FILE.open("rb") as f:
            return _tomllib.load(f)
    except Exception as exc:
        sys.stderr.write(
            f"WARNING: failed to parse {BOOTSTRAP_FILE}: {exc!r}. "
            "Falling back to environment variables.\n"
        )
        sys.stderr.flush()
        return {}


def resolve_database_url(*, explicit: str | None = None) -> str | None:
    """Find the database URL. Returns None if nothing is configured.

    Args:
        explicit: A value passed in by the caller (CLI --db-url, tests).
                  Takes priority over every other source.

    Returns:
        The first non-empty database URL found, or None if no source
        yielded one. Callers should notify the operator and exit on None.
    """
    if explicit:
        return explicit

    config = _read_bootstrap_toml()
    toml_url = (config.get("database_url") or "").strip()
    if toml_url:
        return toml_url

    for name in _DB_URL_ENV_VARS:
        v = (os.getenv(name) or "").strip()
        if v:
            return v

    return None


def require_database_url(
    *,
    explicit: str | None = None,
    source: str = "unknown",
) -> str:
    """Like resolve_database_url() but notifies + exits on None.

    Use this in entry points (brain daemon, worker startup, MCP server,
    long-running CLI) where there's no meaningful way to continue without
    a DB URL.
    """
    url = resolve_database_url(explicit=explicit)
    if url:
        return url

    # Import locally so a broken notifier doesn't mask the real error.
    _sys = sys
    try:
        from brain.operator_notifier import notify_operator
    except Exception:
        _sys.stderr.write(
            "FATAL: no database URL configured and operator_notifier could not be imported.\n"
            "Set DATABASE_URL in the environment or run `poindexter setup`.\n"
        )
        _sys.stderr.flush()
        _sys.exit(2)

    notify_operator(
        title="Poindexter cannot start — no database URL configured",
        detail=(
            f"Checked: explicit arg, {BOOTSTRAP_FILE}, and env vars "
            f"{', '.join(_DB_URL_ENV_VARS)}. None of them yielded a value.\n\n"
            "Fix: run `poindexter setup` to create "
            f"{BOOTSTRAP_FILE} interactively, or set DATABASE_URL in the "
            "environment.\n\n"
            "Example DATABASE_URL:\n"
            "  postgresql://poindexter:<password>@localhost:15432/poindexter_brain"
        ),
        source=source,
        severity="critical",
    )
    _sys.exit(2)


def get_bootstrap_value(key: str, default: str = "") -> str:
    """Read a single value from bootstrap.toml, env var fallback, then default.

    Currently used for the operator-notification channels (Telegram /
    Discord) that might be needed BEFORE the DB is reachable, so they
    live in bootstrap.toml alongside `database_url`.
    """
    config = _read_bootstrap_toml()
    toml_val = str(config.get(key) or "").strip()
    if toml_val:
        return toml_val
    env_val = os.getenv(key.upper(), "").strip()
    if env_val:
        return env_val
    return default


def bootstrap_file_exists() -> bool:
    """True iff ~/.poindexter/bootstrap.toml exists (used by the setup wizard)."""
    return BOOTSTRAP_FILE.is_file()


def write_bootstrap_toml(values: dict[str, str]) -> Path:
    """Write bootstrap.toml atomically. Returns the path.

    Only the `poindexter setup` wizard should call this.
    """
    BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BOOTSTRAP_FILE.with_suffix(".toml.tmp")

    # Hand-write TOML so we don't need `tomli-w` in stdlib-only contexts.
    lines = [
        "# Poindexter bootstrap config — machine-generated by `poindexter setup`.",
        "# The ONLY config that has to live on disk. Everything else lives in",
        "# the app_settings DB table. Keep this file readable only by you.",
        "",
    ]
    for key, value in values.items():
        if value is None:
            continue
        safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key} = "{safe}"')
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Restrict permissions where the platform supports it (no-op on Windows).
    try:
        import stat

        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

    tmp.replace(BOOTSTRAP_FILE)
    return BOOTSTRAP_FILE

"""Shared bootstrap.toml + DSN resolver for ``poindexter <cmd>`` CLIs.

Every ``poindexter`` subcommand needs to read the same DB DSN. Originally
each module had its own copy-pasted ``_dsn()`` that read env vars only —
``brain.bootstrap`` is the canonical resolver but it's not on sys.path
for installed CLI invocations (poindexter-backend ships only
``cofounder_agent``), so importing it silently fails and we fall through
to env vars.

That bug class burned ``poindexter auth migrate-cli`` on Matt's host: a
stale ``LOCAL_DATABASE_URL`` pointing at an unreachable cloud DSN took
priority over the working ``~/.poindexter/bootstrap.toml::database_url``,
and asyncpg timed out at connect with WinError 121.

This module vendors a minimal bootstrap.toml reader so every CLI gets
the same resolution order — matching CLAUDE.md §Configuration:

  ``~/.poindexter/bootstrap.toml::<key>`` → env vars (in order).
"""

from __future__ import annotations

import os
import sys


def read_bootstrap_value(key: str) -> str:
    """Read a single key from ``~/.poindexter/bootstrap.toml``.

    Returns "" if the file is missing, the key is missing, or anything
    fails. Callers fall through to env vars + their own error message.
    Uses stdlib ``tomllib`` on Python 3.11+, falls back to ``tomli``.
    """
    try:
        if sys.version_info >= (3, 11):
            import tomllib as _tomllib
        else:  # pragma: no cover — tomli only on 3.10
            import tomli as _tomllib  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return ""
    path = os.path.expanduser("~/.poindexter/bootstrap.toml")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            data = _tomllib.load(f)
    except Exception:  # noqa: BLE001
        return ""
    return str(data.get(key) or "").strip()


def ensure_secret_key() -> bool:
    """Make sure ``POINDEXTER_SECRET_KEY`` is in ``os.environ``.

    ``plugins.secrets`` reads the encryption key from the env. The
    bootstrap.toml stores it under ``poindexter_secret_key``, but only
    the worker startup path reads it into the env automatically — bare
    ``poindexter <cmd>`` invocations would silently fall through to
    static-bearer auth (and a stale ``POINDEXTER_KEY`` env var) when
    the OAuth secrets couldn't decrypt.

    Returns True if the key is now present (already set or just loaded),
    False if no source could supply it. Callers can keep going either
    way — this is best-effort.
    """
    if os.getenv("POINDEXTER_SECRET_KEY"):
        return True
    key = read_bootstrap_value("poindexter_secret_key")
    if key:
        os.environ["POINDEXTER_SECRET_KEY"] = key
        return True
    return False


def resolve_dsn() -> str:
    """Resolve the DB DSN, preferring bootstrap.toml over env vars.

    Order:

    1. ``~/.poindexter/bootstrap.toml::database_url``
    2. ``POINDEXTER_MEMORY_DSN``
    3. ``LOCAL_DATABASE_URL``
    4. ``DATABASE_URL``

    Raises ``RuntimeError`` if nothing is configured. Callers should
    catch + present a friendly error to the operator (Click's default
    is fine — the message text is clear about what to set).
    """
    dsn = read_bootstrap_value("database_url") or (
        os.getenv("POINDEXTER_MEMORY_DSN")
        or os.getenv("LOCAL_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    )
    if not dsn:
        raise RuntimeError(
            "No DSN — set ~/.poindexter/bootstrap.toml::database_url "
            "(preferred) or POINDEXTER_MEMORY_DSN / LOCAL_DATABASE_URL / "
            "DATABASE_URL env var.",
        )
    return dsn

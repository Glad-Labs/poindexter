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
        # silent-ok: `""` is this function's DOCUMENTED return for "no value
        # recoverable" (see docstring) — callers fall through to env vars and
        # raise their own, better error. No TOML parser available is one such
        # case, and it runs before any logging is configured.
        return ""
    path = os.path.expanduser("~/.poindexter/bootstrap.toml")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            data = _tomllib.load(f)
    except Exception:  # noqa: BLE001
        # silent-ok: same documented `""` contract — an unreadable or
        # malformed bootstrap.toml is indistinguishable from an absent one to
        # every caller, and each falls through to env vars with its own error.
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


async def open_cli_pool(
    dsn: str | None = None,
    *,
    min_size: int = 1,
    max_size: int = 2,
    **kwargs: object,
):
    """Open the CLI's asyncpg pool AND attach the global audit sink.

    Every ``poindexter <cmd>`` that opens a DB pool goes through here
    (paired with :func:`close_cli_pool`) instead of a bare
    ``asyncpg.create_pool`` / ``pool.close()``.

    Why: ``utils/findings.py::emit_finding`` → ``audit_log_bg`` writes
    through a global ``AuditLogger`` that only ``DatabaseService`` used to
    initialise — i.e. worker / Prefect contexts. A CLI-invoked service
    that emitted a finding (``poindexter pro sync`` during the first live
    Pro purchase, 2026-08-26) had no global logger, so the finding was
    DROPPED and never reached the alert pipeline. This seam initialises a
    minimal ``AuditLogger`` on the command's own pool so CLI-context
    findings persist like any other.

    Fail-soft by design: a broken audit attach must never block the
    command itself (which may be the very command needed to repair the
    system) — the pool is returned either way.
    """
    import asyncpg

    pool = await asyncpg.create_pool(
        dsn or resolve_dsn(), min_size=min_size, max_size=max_size, **kwargs
    )
    try:
        from services.audit_log import init_global_audit_logger

        # quiet=True: the info-level init line would print to stderr on
        # every CLI invocation (logger_config attaches a stderr handler at
        # INFO on import).
        init_global_audit_logger(pool, quiet=True)
    except Exception:  # noqa: BLE001
        # silent-ok: findings are best-effort in CLI context — the command
        # must run even when the audit seam can't attach. A finding emitted
        # later still hits audit_log_bg's loud-drop path (#303), so the
        # loss is visible, just not fixable from here.
        pass
    return pool


async def close_cli_pool(pool) -> None:
    """Detach the audit sink, flush in-flight finding writes, close the pool.

    The drain-before-close ordering is the point: ``audit_log_bg`` writes
    are fire-and-forget tasks, and a finding emitted moments before command
    teardown is only *scheduled* — closing the pool first kills the write
    with ``InterfaceError('pool is closing')`` and loses the finding (the
    GlitchTip #863 race, same one ``DatabaseService.close`` drains for).

    Reset happens before drain so no new write can target the pool once
    teardown has begun; the conditional reset can't clobber a logger a
    different context (e.g. ``cli/pipeline.py``'s full ``DatabaseService``)
    re-initialised with its own pool. Fail-soft: the ``finally`` guarantees
    the pool closes no matter what the audit teardown does.
    """
    try:
        from services.audit_log import drain_pending_writes, reset_global_audit_logger

        reset_global_audit_logger(pool)
        await drain_pending_writes()
    except Exception:  # noqa: BLE001
        # silent-ok: teardown is best-effort — closing the pool below is
        # the part that must always run, and a failed drain already logs
        # loudly per-write via audit_log's #303 handlers.
        pass
    finally:
        await pool.close()

"""DB-driven gate for fine-grained content validators (Validators CRUD V1).

Background
----------

PR #160 introduced ``app_settings.qa_allow_first_person_niches`` as a
single-rule, niche-level escape hatch baked directly into
``score_accuracy``. Migration 0135 generalises that pattern across
every fine-grained validator: each rule lives in
``content_validator_rules`` (id, name, enabled, severity, threshold,
applies_to_niches, description) so operators can flip individual
checks on/off — and scope them to specific niches — without a code
change.

This module is the call-site helper that the validator modules consult
to decide whether to run a given rule. It owns:

* A short-TTL in-memory cache of the rules table so per-post checks
  don't issue a SQL query each time.
* The legacy CSV bridge for ``qa_allow_first_person_niches`` so the
  PR #160 path keeps working alongside the new ``applies_to_niches``
  column. We honor BOTH: a niche listed in the CSV is treated as a
  bypass even when the new table doesn't list a niche-scope.

Usage
-----

::

    from services.validator_config import is_validator_enabled, get_validator_threshold

    if not is_validator_enabled("first_person_claims", niche="dev_diary"):
        return  # rule disabled in DB, or niche-scoped out

    threshold = get_validator_threshold(
        "code_block_density",
        default={"min_blocks_per_700w": 1, "min_line_ratio_pct": 20},
    )

Both lookups fail open — if the DB table is missing (cold-boot, fresh
clone, migration not yet run), every rule is reported as enabled with
the caller-provided default threshold. That preserves current behavior
and keeps the validator running until the operator's DB catches up.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
#
# In-memory cache lives for ``_CACHE_TTL_SECONDS``. The validators run
# once per post (synchronously, off the asyncio loop), so the cache is
# guarded by a regular threading.Lock — callers are sync.

_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class _ValidatorRule:
    name: str
    enabled: bool
    severity: str
    threshold: dict[str, Any]
    applies_to_niches: tuple[str, ...] | None  # None = all niches
    description: str


_cache_lock = threading.Lock()
_cache: dict[str, _ValidatorRule] = {}
_cache_loaded_at: float = 0.0
_cache_load_failed: bool = False


def _resolve_dsn() -> str:
    """Resolve a DSN for the synchronous cache loader.

    Order: ``brain.bootstrap.resolve_database_url()`` (project-wide
    canonical resolver) → ``DATABASE_URL`` → ``LOCAL_DATABASE_URL`` →
    ``POINDEXTER_MEMORY_DSN``. Returns ``""`` if nothing resolves —
    callers treat that as a fail-open signal.
    """
    try:
        import sys
        from pathlib import Path

        proj = Path(__file__).resolve()
        for parent in proj.parents:
            if (parent / "brain" / "bootstrap.py").is_file():
                if str(parent) not in sys.path:
                    sys.path.insert(0, str(parent))
                break
        from brain.bootstrap import resolve_database_url
        url = resolve_database_url()
        if url:
            return url
    except Exception:
        pass
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("LOCAL_DATABASE_URL")
        or os.getenv("POINDEXTER_MEMORY_DSN")
        or ""
    )


def _load_rules_sync() -> dict[str, _ValidatorRule]:
    """Pull every row from ``content_validator_rules`` (sync, blocking).

    Returns an empty dict on any failure; callers fail open.
    """
    dsn = _resolve_dsn()
    if not dsn:
        return {}

    import asyncio
    import asyncpg  # type: ignore[import-not-found]

    async def _fetch() -> list[dict]:
        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            rows = await conn.fetch(
                """
                SELECT name, enabled, severity, threshold, applies_to_niches,
                       description
                  FROM content_validator_rules
                """
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    try:
        if loop and loop.is_running():
            # Caller is on an event loop (worker pipeline). Run the
            # asyncpg query on a side thread so we don't deadlock.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _fetch())
                rows = future.result(timeout=10)
        else:
            rows = asyncio.run(_fetch())
    except Exception as exc:
        logger.warning(
            "[VALIDATOR_CONFIG] DB load failed (rules will be treated as "
            "enabled): %s", exc,
        )
        return {}

    out: dict[str, _ValidatorRule] = {}
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        threshold = row.get("threshold") or {}
        if isinstance(threshold, str):
            try:
                threshold = json.loads(threshold)
            except (TypeError, ValueError):
                threshold = {}
        niches_raw = row.get("applies_to_niches")
        if niches_raw is None:
            niches: tuple[str, ...] | None = None
        else:
            # asyncpg returns Postgres TEXT[] as a Python list.
            niches = tuple(str(n).strip().lower() for n in niches_raw if str(n).strip())
            if not niches:
                niches = None
        out[name] = _ValidatorRule(
            name=name,
            enabled=bool(row.get("enabled", True)),
            severity=str(row.get("severity") or "warning"),
            threshold=threshold if isinstance(threshold, dict) else {},
            applies_to_niches=niches,
            description=str(row.get("description") or ""),
        )
    return out


def _get_cached_rules() -> dict[str, _ValidatorRule]:
    """Return the cached rules dict, refreshing if the TTL has expired."""
    global _cache, _cache_loaded_at, _cache_load_failed
    now = time.time()
    with _cache_lock:
        if _cache and (now - _cache_loaded_at) < _CACHE_TTL_SECONDS:
            return _cache
        rules = _load_rules_sync()
        if rules:
            _cache = rules
            _cache_loaded_at = now
            _cache_load_failed = False
        else:
            # Don't overwrite a previously-good cache with an empty load —
            # fail open by reusing what we had.
            _cache_load_failed = True
            _cache_loaded_at = now
        return _cache


def reset_cache() -> None:
    """Drop the in-memory cache. For tests + the periodic reloader."""
    global _cache, _cache_loaded_at, _cache_load_failed
    with _cache_lock:
        _cache = {}
        _cache_loaded_at = 0.0
        _cache_load_failed = False


def seed_cache_for_tests(rules: dict[str, dict[str, Any]]) -> None:
    """Replace the cache with caller-provided rules. Tests only.

    Each rule dict supports the same keys as the DB row: ``enabled``,
    ``severity``, ``threshold``, ``applies_to_niches``, ``description``.
    Missing keys default to the standard "enabled, warning, no scope"
    set so callers only need to specify the bits they're testing.
    """
    global _cache, _cache_loaded_at, _cache_load_failed
    new_cache: dict[str, _ValidatorRule] = {}
    for name, spec in rules.items():
        niches = spec.get("applies_to_niches")
        if niches is not None:
            niches = tuple(str(n).strip().lower() for n in niches if str(n).strip())
            if not niches:
                niches = None
        new_cache[name] = _ValidatorRule(
            name=name,
            enabled=bool(spec.get("enabled", True)),
            severity=str(spec.get("severity", "warning")),
            threshold=dict(spec.get("threshold") or {}),
            applies_to_niches=niches,
            description=str(spec.get("description", "")),
        )
    with _cache_lock:
        _cache = new_cache
        _cache_loaded_at = time.time()
        _cache_load_failed = False


# ---------------------------------------------------------------------------
# Public lookups
# ---------------------------------------------------------------------------


def _legacy_first_person_bypass(niche: str | None) -> bool:
    """Return True iff the legacy CSV bypass applies for ``niche``.

    Honors the PR #160 ``qa_allow_first_person_niches`` app_setting so
    operators that pinned the bypass via the old path keep working. Only
    consulted when the rule name is ``first_person_claims`` — every
    other rule reads niche scope exclusively from ``applies_to_niches``.
    """
    if not niche:
        return False
    try:
        from services.site_config import site_config
    except Exception:
        return False
    csv = site_config.get("qa_allow_first_person_niches", "")
    if not csv:
        return False
    allow = {s.strip().lower() for s in str(csv).split(",") if s.strip()}
    return str(niche).strip().lower() in allow


def is_validator_enabled(name: str, niche: str | None = None) -> bool:
    """Should validator ``name`` run for a post in ``niche``?

    Lookup order:

    1. If the DB row exists and ``enabled = false`` → False.
    2. If the DB row has ``applies_to_niches`` set AND ``niche`` is
       not in that list → False.
    3. If the DB row is missing entirely → True (fail open — fresh DB
       or cold boot shouldn't silently disable the whole gate).

    Special case: for ``first_person_claims``, an additional bypass
    applies if ``niche`` is in the legacy
    ``qa_allow_first_person_niches`` CSV setting (PR #160 path).
    """
    if name == "first_person_claims" and _legacy_first_person_bypass(niche):
        return False

    rules = _get_cached_rules()
    rule = rules.get(name)
    if rule is None:
        return True  # fail open — unknown rule means "no DB row; run it"
    if not rule.enabled:
        return False
    if rule.applies_to_niches is not None:
        if not niche:
            # Rule is niche-scoped but the post has no niche →
            # treat as out-of-scope (would otherwise silently apply
            # the rule to posts that aren't part of any listed niche).
            return False
        return str(niche).strip().lower() in rule.applies_to_niches
    return True


def get_validator_threshold(
    name: str,
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the operator-tuned threshold dict for validator ``name``.

    Falls back to ``default or {}`` when the DB row is absent or its
    threshold column is empty. The dict keys are validator-specific —
    each validator owns its own threshold schema (see seed defaults in
    migration 0135).
    """
    rules = _get_cached_rules()
    rule = rules.get(name)
    if rule is None or not rule.threshold:
        return dict(default or {})
    # Merge so the caller's defaults are preserved for any keys the
    # operator didn't pin in the DB row.
    merged: dict[str, Any] = dict(default or {})
    merged.update(rule.threshold)
    return merged


def get_validator_severity(name: str, default: str = "warning") -> str:
    """Return the severity tier for validator ``name`` ('info'/'warning'/'error')."""
    rules = _get_cached_rules()
    rule = rules.get(name)
    if rule is None:
        return default
    return rule.severity


def list_validator_rules() -> list[_ValidatorRule]:
    """Snapshot of every cached rule. For CLI ``poindexter validators list``."""
    rules = _get_cached_rules()
    return sorted(rules.values(), key=lambda r: r.name)


__all__ = [
    "is_validator_enabled",
    "get_validator_threshold",
    "get_validator_severity",
    "list_validator_rules",
    "reset_cache",
    "seed_cache_for_tests",
]

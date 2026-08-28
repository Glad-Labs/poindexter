"""Deterministic rule matching + circuit breaker + rate cap for the firefighter.

All reads are non-secret app_settings / plain tables, so we use pool.fetchval
directly (no secret_reader needed). Every helper is best-effort: a DB error
returns the safe answer (no match / breaker-open-but-caller-still-gated).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("brain.remediation.rules")


def _coerce_json(value: Any) -> dict[str, Any]:
    """JSONB may arrive as a dict (codec set) or a str (default). Normalise."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _normalize_rule(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "alertname": row.get("alertname"),
        "match_regex": row.get("match_regex"),
        "action_name": row.get("action_name"),
        "params": _coerce_json(row.get("params")),
        "max_attempts_per_window": row.get("max_attempts_per_window"),
        "window_minutes": row.get("window_minutes"),
        "verify_after_seconds": row.get("verify_after_seconds"),
    }


async def _read_str(pool: Any, key: str, default: str) -> str:
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[firefighter] read %s failed: %s — default", key, e)
        return default
    return default if value is None else str(value)


async def _read_int(pool: Any, key: str, default: int) -> int:
    raw = await _read_str(pool, key, str(default))
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return default


async def _read_float(pool: Any, key: str, default: float) -> float:
    raw = await _read_str(pool, key, str(default))
    try:
        return float(str(raw).strip())
    except (ValueError, TypeError):
        return default


_TRUTHY = ("true", "1", "yes", "on")

# Circular-dependency guard default (spec's "never ask the LLM to fix the thing
# it runs on"): the LLM long-tail path is SKIPPED for alerts whose name matches
# this. MUST stay in sync with the ops_firefighter_llm_exclude_regex seed in
# services/settings_defaults.py so a fresh/partial DB behaves identically.
_DEFAULT_LLM_EXCLUDE_REGEX = r"(?i)(ollama|gpu|vram|cuda|inference)"


async def load_firefighter_config(pool: Any) -> dict[str, Any]:
    """Snapshot the firefighter knobs once per cycle."""
    enabled_raw = await _read_str(pool, "ops_firefighter_enabled", "true")
    allowlist_raw = await _read_str(pool, "ops_firefighter_action_allowlist", "")
    allowlist = [p.strip() for p in allowlist_raw.split(",") if p.strip()]
    longtail_raw = await _read_str(pool, "ops_firefighter_llm_longtail_enabled", "true")
    return {
        "enabled": enabled_raw.strip().lower() in _TRUTHY,
        "max_attempts_per_window": await _read_int(pool, "ops_firefighter_max_attempts_per_window", 3),
        "window_minutes": await _read_int(pool, "ops_firefighter_window_minutes", 60),
        "verify_after_seconds": await _read_int(pool, "ops_firefighter_verify_after_seconds", 120),
        "max_actions_per_hour": await _read_int(pool, "ops_firefighter_max_actions_per_hour", 10),
        "action_allowlist": allowlist,
        # --- Plan B: LLM long-tail selector gates ---
        "llm_longtail_enabled": longtail_raw.strip().lower() in _TRUTHY,
        "min_repeats": await _read_int(pool, "ops_firefighter_min_repeats", 2),
        "min_age_minutes": await _read_int(pool, "ops_firefighter_min_age_minutes", 10),
        "min_confidence": await _read_float(pool, "ops_firefighter_min_confidence", 0.6),
        # Fallback MUST match settings_defaults.DEFAULTS["ops_firefighter_model"]
        # (same discipline as the exclude-regex default above): this is a FOURTH
        # home for a hardcoded default, alongside the three seed sources the
        # settings_seed_value_drift_lint covers, and the brain reads it on a
        # fresh/partial DB. The 2026-08-27 licence sweep retired ollama/llama3.2:3b
        # from every other default and missed this one — its repo-wide grep was
        # truncated, so brain/ was never checked. Sweep brain/ too.
        # Pinned by tests/unit/brain/test_remediation_rules.py.
        "model": await _read_str(pool, "ops_firefighter_model", "ollama/granite4.2:3b"),
        "llm_exclude_regex": await _read_str(
            pool, "ops_firefighter_llm_exclude_regex", _DEFAULT_LLM_EXCLUDE_REGEX
        ),
    }


async def match_rule(pool: Any, *, alertname: str, fingerprint: str) -> dict[str, Any] | None:
    """First enabled rule whose alertname matches exactly, else whose regex
    matches the alertname or fingerprint. None if nothing matches."""
    try:
        rows = await pool.fetch(
            """
            SELECT id, alertname, match_regex, action_name, params,
                   max_attempts_per_window, window_minutes, verify_after_seconds
            FROM remediation_rules
            WHERE enabled = TRUE
            ORDER BY id ASC
            """
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] remediation_rules fetch failed: %s", e)
        return None
    for r in rows:
        rd = dict(r)
        if rd.get("alertname") and rd["alertname"] == alertname:
            return _normalize_rule(rd)
    for r in rows:
        rd = dict(r)
        rx = rd.get("match_regex")
        if not rx:
            continue
        try:
            if re.search(rx, alertname or "") or (fingerprint and re.search(rx, fingerprint)):
                return _normalize_rule(rd)
        except re.error as e:
            logger.warning("[firefighter] bad match_regex on rule %s: %s", rd.get("id"), e)
            continue
    return None


async def circuit_breaker_tripped(
    pool: Any, *, fingerprint: str, action_name: str,
    max_attempts: int, window_minutes: int,
) -> bool:
    """True when >= max_attempts of this (fingerprint, action) ran in-window."""
    if max_attempts <= 0 or window_minutes <= 0:
        return False
    try:
        cnt = await pool.fetchval(
            """
            SELECT count(*) FROM audit_log
            WHERE event_type = 'remediation_action'
              AND details->>'fingerprint' = $1
              AND details->>'action_name' = $2
              AND timestamp >= now() - make_interval(mins => $3)
            """,
            fingerprint, action_name, window_minutes,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] circuit-breaker count failed: %s — allowing", e)
        return False
    return int(cnt or 0) >= max_attempts


async def global_rate_exceeded(pool: Any, *, max_actions_per_hour: int) -> bool:
    """True when >= max_actions_per_hour remediation_action rows in the last hour."""
    if max_actions_per_hour <= 0:
        return False
    try:
        cnt = await pool.fetchval(
            """
            SELECT count(*) FROM audit_log
            WHERE event_type = 'remediation_action'
              AND timestamp >= now() - interval '1 hour'
            """
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] global-rate count failed: %s — allowing", e)
        return False
    return int(cnt or 0) >= max_actions_per_hour

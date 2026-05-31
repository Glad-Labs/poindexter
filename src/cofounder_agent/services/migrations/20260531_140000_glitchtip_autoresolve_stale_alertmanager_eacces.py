r"""Migration 20260531_140000: fix glitchtip auto-resolve patterns + add alertmanager rule

Two fixes to ``glitchtip_triage_auto_resolve_patterns``:

1. **Invalid-JSON repair (the real bug).** The 0000_baseline seed stored the
   regex patterns with bare backslash escapes (e.g. ``\\[Errno 111\\]`` written
   as ``\[Errno 111\]``), which is NOT valid JSON — ``\[`` is not a legal JSON
   escape. So ``glitchtip_triage_probe`` failed to parse the value every cycle
   and **disabled ALL auto-resolution**. That's why known-noise issues (incl.
   the stale alertmanager.yml EACCES) kept getting surfaced: the auto-resolver
   was silently off. Re-seeding the canonical rule set as properly-escaped JSON
   re-enables it.

2. **Add the alertmanager.yml EACCES rule.** Before #524's atomic-write fix,
   RenderAlertmanagerConfigJob (non-root worker) hit
   ``PermissionError ... /etc/alertmanager/config/alertmanager.yml`` on every
   config change. The fix (temp-file + os.replace into the 0777 dir) means the
   worker now atomically replaces the file — verified live 2026-05-31
   (``ok=True 'alertmanager config unchanged'``). A genuine recurrence would
   also surface as ``ok=False`` in the worker render-job log + ``poindexter
   doctor``, so auto-resolving the GlitchTip echo doesn't blind us.

Robust: if the existing value parses as a JSON list, the alertmanager rule is
appended if missing (preserving any operator-added rules). If it does NOT parse
(the invalid-baseline case, on a fresh DB or an un-repaired prod), it is reset
to the canonical valid set below. Idempotent either way.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_KEY = "glitchtip_triage_auto_resolve_patterns"

_ALERTMANAGER_RULE = {
    "title_pattern": r"PermissionError.*alertmanager\.yml",
    "action": "resolve",
    "reason": (
        "Stale pre-#524 EACCES on alertmanager.yml; render job now "
        "atomic-writes the worker-owned config (verified ok=True 2026-05-31). "
        "A real recurrence also surfaces in the worker render-job log + doctor."
    ),
}

# Canonical valid set — the 6 baseline rules (correctly escaped) + the new one.
# Used only when the stored value is unparseable (repairs the invalid-JSON seed).
_CANONICAL: list[dict] = [
    {"title_pattern": r"Error while fetching prompt .* \[Errno 111\] Connection refused",
     "action": "resolve", "reason": "Langfuse transient unreachable"},
    {"title_pattern": r"(ConnectError|ConnectTimeout|gaierror).*name resolution",
     "action": "resolve", "max_count": 20, "reason": "Docker/upstream DNS flap"},
    {"title_pattern": r"^ReadTimeout$",
     "action": "resolve", "max_count": 10, "reason": "Single-shot ollama/HN timeout"},
    {"title_pattern": r"^CancelledError$",
     "action": "resolve", "reason": "Scheduler shutdown propagation"},
    {"title_pattern": r"discord_post: HTTP 429",
     "action": "resolve", "reason": "Rate-limited, retry-after handles it"},
    {"title_pattern": r"\[GH-90\] finalize_task ABORTED:",
     "action": "resolve", "reason": "Expected race-abort, should be INFO not ERROR"},
    _ALERTMANAGER_RULE,
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", _KEY)

        parsed: list | None = None
        if raw:
            try:
                candidate = json.loads(raw)
                if isinstance(candidate, list):
                    parsed = candidate
            except (ValueError, TypeError):
                parsed = None  # invalid JSON (the baseline-escaping bug)

        if parsed is None:
            # Unparseable or absent → reset to the canonical valid set. This is
            # the invalid-JSON repair: the brain was failing to parse and had
            # auto-resolution disabled entirely.
            new_rules = list(_CANONICAL)
            logger.info(
                "Migration glitchtip_autoresolve: value unparseable/absent — "
                "reset to %d canonical rules (re-enables auto-triage)", len(new_rules),
            )
        else:
            # Valid list → preserve operator rules, append ours if missing.
            new_rules = parsed
            if not any(
                isinstance(r, dict) and r.get("title_pattern") == _ALERTMANAGER_RULE["title_pattern"]
                for r in new_rules
            ):
                new_rules.append(_ALERTMANAGER_RULE)
                logger.info("Migration glitchtip_autoresolve: appended alertmanager rule")
            else:
                logger.info("Migration glitchtip_autoresolve: alertmanager rule already present")

        new_value = json.dumps(new_rules)
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            _KEY, new_value,
        )


async def down(pool) -> None:
    """Remove only the alertmanager rule (leave the repaired/other rules)."""
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", _KEY)
        if not raw:
            return
        try:
            rules = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(rules, list):
            return
        filtered = [
            r for r in rules
            if not (isinstance(r, dict) and r.get("title_pattern") == _ALERTMANAGER_RULE["title_pattern"])
        ]
        if len(filtered) != len(rules):
            await conn.execute(
                "UPDATE app_settings SET value = $2 WHERE key = $1", _KEY, json.dumps(filtered)
            )

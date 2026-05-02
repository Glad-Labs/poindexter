"""GlitchTip triage probe — auto-resolve known noise, alert on novel issues.

Operationalizes Matt's `feedback_alert_auto_triage.md`: every alert must
have a resolution path, and the system tries to fix before bothering
humans. GlitchTip is the worker's error-tracking sink. Without triage
it accumulates hundreds of "issues" that are really one of three things:

1. **Known noise** — transient infra failures we already plan to fix
   elsewhere (Langfuse exporter when langfuse-web is restarting, ANSI-
   coloured structlog lines that GlitchTip splits into "unique" issues
   per task ID, AllModelsFailedError waves from a single Ollama outage).
2. **Real bugs** — unique exception classes / culprits that recur and
   need a code fix.
3. **One-off transients** — happened once, never again, can be closed.

This probe pulls every unresolved issue every cycle and applies an
operator-controlled ruleset (DB-driven via app_settings) to:

  * **Auto-resolve** issues whose title matches a known-noise regex
    AND have low recurrence — the resolution is "we already know,
    closing as expected noise".
  * **Alert via notify_operator()** on issues with count above
    ``glitchtip_triage_alert_threshold_count`` that we've not alerted
    on this cycle (per-issue dedupe by id).
  * **Summarize** the cycle for the daily roll-up consumer.

Everything is best-effort: GlitchTip down => probe returns
``status=unknown`` and the brain cycle continues. No exceptions
escape this module.

Standalone — depends only on stdlib + httpx (already a brain dep).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified path
    from brain.operator_notifier import notify_operator

try:
    from docker_utils import localize_url
except ImportError:  # pragma: no cover — package-qualified path
    from brain.docker_utils import localize_url

logger = logging.getLogger("brain.glitchtip_triage_probe")

# ---------------------------------------------------------------------------
# Tunables — ALL read from app_settings at probe time so the operator can
# adjust without redeploying the brain. See migration 0133 for defaults.
# ---------------------------------------------------------------------------

# Master enable/disable. Defaults to "true" — once seeded, the probe runs
# every cycle. Operator flips to "false" to silence it (e.g. if
# GlitchTip itself is being upgraded and would 500 every call).
ENABLED_SETTING_KEY = "glitchtip_triage_enabled"

# Sentry-compatible API token (is_secret=true). Without this the probe
# can't authenticate. Probe degrades to status=unknown rather than
# crashing the brain cycle.
TOKEN_SETTING_KEY = "glitchtip_triage_api_token"

# Where GlitchTip is reachable from the brain. Defaults to the local
# compose hostname. localize_url() rewrites localhost → host.docker.internal
# only when the brain runs inside the container.
BASE_URL_SETTING_KEY = "glitchtip_base_url"
BASE_URL_DEFAULT = "http://glitchtip-web:8000"

# Org slug to query. GlitchTip allows multi-org but the poindexter setup
# only ever provisions one. Configurable so a tenant who renames the
# org doesn't need a brain redeploy.
ORG_SLUG_SETTING_KEY = "glitchtip_triage_org_slug"
ORG_SLUG_DEFAULT = "glad-labs"

# Issues with `count` above this get a notify_operator() alert when they
# match no known-noise rule. Default 100 = "this happened a hundred
# times — humans should look".
ALERT_THRESHOLD_SETTING_KEY = "glitchtip_triage_alert_threshold_count"
ALERT_THRESHOLD_DEFAULT = 100

# JSONB array of triage rules. Each entry shape:
#   {
#     "title_pattern": "<regex>",       # python re, evaluated against title
#     "action": "resolve" | "ignore",   # resolve = PUT status=resolved;
#                                       # ignore = leave as-is but suppress
#                                       # alerts (so you stop being paged
#                                       # without hiding the data)
#     "reason": "<freeform>",           # logged into audit_log
#     "max_count": <int> | null,        # optional ceiling — only auto-act
#                                       # on issues with count <= this.
#                                       # Stops auto-resolving runaway
#                                       # outages we should still notice.
#     "min_age_days": <int> | null,     # optional age floor — only auto-act
#                                       # on issues whose firstSeen is at
#                                       # least this many days ago. Lets
#                                       # operators write "GC anything older
#                                       # than 7 days with <5 occurrences"
#                                       # rules without auto-closing fresh
#                                       # noise that might still be live.
#     "level_in": [<level>, ...] | null # optional level filter
#   }
RULES_SETTING_KEY = "glitchtip_triage_auto_resolve_patterns"

# Hard cap on issues processed per cycle — each issue is one HTTP call,
# big batches stall the cycle. We page through paginated results until
# this cap is hit, then bail (the next cycle continues from page 0
# again — newer issues outrank older ones, so the cap effectively
# prioritizes the freshest).
MAX_ISSUES_PER_CYCLE = 500

# Per-request HTTP timeouts.
HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_READ_TIMEOUT_S = 15.0

# Probe interval — runs on every brain cycle (5-min). Internal dedupe
# keeps notifications quiet between cycles.
PROBE_INTERVAL_SECONDS = 300

# Module-level dedupe state — issue IDs we've alerted on this process
# uptime. Resets on restart so a brain restart re-pages on still-active
# noisy issues, which is the right behavior.
_alerted_ids: set[str] = set()


# ---------------------------------------------------------------------------
# Settings I/O
# ---------------------------------------------------------------------------


async def _read_setting(pool, key: str, default: str = "") -> str:
    """Read a string app_settings value. Defaults gracefully on failure."""
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:
        logger.warning(
            "[GLITCHTIP_TRIAGE] Could not read %s from app_settings: %s",
            key, exc,
        )
        return default
    if val is None:
        return default
    return str(val)


async def _read_secret(pool, key: str) -> str:
    """Read a (possibly encrypted) secret from app_settings.

    Mirrors the pattern in brain_daemon._BrainSecretReader: plaintext rows
    come back verbatim; ``enc:v1:`` rows get pgp_sym_decrypt'd with
    POINDEXTER_SECRET_KEY. The probe degrades to "" on any failure
    (decryption error, missing key) and the caller treats "" as "not
    configured yet, skip cycle".
    """
    import os
    try:
        row = await pool.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:
        logger.warning(
            "[GLITCHTIP_TRIAGE] Could not read secret %s: %s", key, exc
        )
        return ""
    if not row or not row["value"]:
        return ""
    val = row["value"]
    if not row["is_secret"] or not val.startswith("enc:v1:"):
        return val
    pkey = os.getenv("POINDEXTER_SECRET_KEY")
    if not pkey:
        logger.warning(
            "[GLITCHTIP_TRIAGE] POINDEXTER_SECRET_KEY unset — can't decrypt %s",
            key,
        )
        return ""
    try:
        return await pool.fetchval(
            "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
            val[len("enc:v1:"):],
            pkey,
        ) or ""
    except Exception as exc:
        logger.warning(
            "[GLITCHTIP_TRIAGE] decrypt %s failed: %s", key, exc
        )
        return ""


async def _read_rules(pool) -> list[dict[str, Any]]:
    """Parse the JSONB rules array. Returns [] on any parse failure.

    A single bad rule shouldn't disable the entire ruleset, so we
    validate per-entry and skip malformed ones with a warning.
    """
    raw = await _read_setting(pool, RULES_SETTING_KEY, "[]")
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[GLITCHTIP_TRIAGE] %s is not valid JSON: %s — disabling rules",
            RULES_SETTING_KEY, exc,
        )
        return []
    if not isinstance(data, list):
        logger.warning(
            "[GLITCHTIP_TRIAGE] %s must be a JSON array, got %s",
            RULES_SETTING_KEY, type(data).__name__,
        )
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        pat = entry.get("title_pattern")
        action = entry.get("action")
        if not isinstance(pat, str) or action not in ("resolve", "ignore"):
            logger.debug(
                "[GLITCHTIP_TRIAGE] Skipping malformed rule: %r", entry
            )
            continue
        try:
            compiled = re.compile(pat, re.DOTALL)
        except re.error as exc:
            logger.warning(
                "[GLITCHTIP_TRIAGE] Bad regex in rule %r: %s — skipping",
                pat, exc,
            )
            continue
        cleaned.append({
            "title_pattern": pat,
            "_compiled": compiled,
            "action": action,
            "reason": entry.get("reason") or "",
            "max_count": entry.get("max_count"),
            "min_age_days": entry.get("min_age_days"),
            "level_in": entry.get("level_in"),
        })
    return cleaned


async def _read_threshold(pool) -> int:
    raw = await _read_setting(
        pool, ALERT_THRESHOLD_SETTING_KEY, str(ALERT_THRESHOLD_DEFAULT)
    )
    try:
        v = int(raw)
        return v if v > 0 else ALERT_THRESHOLD_DEFAULT
    except ValueError:
        return ALERT_THRESHOLD_DEFAULT


# ---------------------------------------------------------------------------
# GlitchTip API client (thin)
# ---------------------------------------------------------------------------


_NEXT_CURSOR_RE = re.compile(r'cursor="([^"]+)"')


def _parse_next_cursor(link_header: str) -> str | None:
    """Pull the next-page cursor out of a Sentry-compatible Link header.

    Format: ``<...?cursor=XYZ&limit=100>; rel="next"; results="true"; cursor="XYZ"``
    Returns None when ``rel="next"`` segment has ``results="false"`` or
    is missing entirely (terminal page).
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' not in part:
            continue
        if 'results="false"' in part:
            return None
        m = _NEXT_CURSOR_RE.search(part)
        if m:
            return m.group(1)
    return None


async def _fetch_open_issues(
    client: "httpx.AsyncClient",
    base_url: str,
    org_slug: str,
    *,
    limit_per_page: int = 100,
    max_issues: int = MAX_ISSUES_PER_CYCLE,
) -> list[dict[str, Any]]:
    """Page through ``/api/0/organizations/<slug>/issues/?query=is:unresolved``."""
    issues: list[dict[str, Any]] = []
    cursor: str | None = None
    safety_pages = 0
    while len(issues) < max_issues and safety_pages < 50:
        params: dict[str, Any] = {
            "query": "is:unresolved",
            "limit": limit_per_page,
        }
        if cursor:
            params["cursor"] = cursor
        try:
            r = await client.get(
                f"{base_url}/api/0/organizations/{org_slug}/issues/",
                params=params,
            )
        except Exception as exc:
            logger.warning("[GLITCHTIP_TRIAGE] fetch failed: %s", exc)
            break
        if r.status_code != 200:
            logger.warning(
                "[GLITCHTIP_TRIAGE] issues endpoint returned %d: %s",
                r.status_code, (r.text or "")[:200],
            )
            break
        try:
            batch = r.json()
        except json.JSONDecodeError as exc:
            logger.warning(
                "[GLITCHTIP_TRIAGE] bad JSON from issues endpoint: %s", exc
            )
            break
        if not isinstance(batch, list) or not batch:
            break
        issues.extend(batch)
        next_cursor = _parse_next_cursor(r.headers.get("Link", ""))
        if not next_cursor:
            break
        cursor = next_cursor
        safety_pages += 1
    if len(issues) > max_issues:
        issues = issues[:max_issues]
    return issues


async def _resolve_issue(
    client: "httpx.AsyncClient",
    base_url: str,
    issue_id: str,
) -> bool:
    """PUT ``status=resolved`` on a single issue. True on success."""
    try:
        r = await client.put(
            f"{base_url}/api/0/issues/{issue_id}/",
            json={"status": "resolved"},
        )
    except Exception as exc:
        logger.warning(
            "[GLITCHTIP_TRIAGE] resolve %s failed: %s", issue_id, exc
        )
        return False
    if r.status_code in (200, 202, 204):
        return True
    logger.warning(
        "[GLITCHTIP_TRIAGE] resolve %s returned %d: %s",
        issue_id, r.status_code, (r.text or "")[:200],
    )
    return False


# ---------------------------------------------------------------------------
# Rule application
# ---------------------------------------------------------------------------


def _match_rule(
    issue: dict[str, Any], rules: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the first rule that matches this issue, or None.

    Rules evaluated in declaration order — operator authors them most-
    specific-first. Rules may carry these optional gates (any present
    must be satisfied for the rule to match):

    * ``max_count``    — issue.count must be <= this value.
    * ``min_age_days`` — ``now - issue.firstSeen`` must be >= this
                         many days. Lets operators write rules like
                         "GC anything older than 7 days with <5
                         occurrences" without auto-closing fresh
                         noise that might still be flapping.
    * ``level_in``     — issue.level must be in this list.
    """
    title = issue.get("title") or ""
    count = int(issue.get("count") or 0)
    level = issue.get("level") or "error"
    for rule in rules:
        if rule.get("max_count") is not None:
            try:
                if count > int(rule["max_count"]):
                    continue
            except (TypeError, ValueError):
                pass
        if rule.get("min_age_days") is not None and not _issue_meets_min_age(
            issue, rule["min_age_days"],
        ):
            continue
        levels = rule.get("level_in")
        if levels and level not in levels:
            continue
        if rule["_compiled"].search(title):
            return rule
    return None


def _issue_meets_min_age(issue: dict[str, Any], min_age_days: Any) -> bool:
    """Return True iff issue.firstSeen is at least ``min_age_days`` days old.

    Returns False (rule can't match) when:
    * ``min_age_days`` doesn't parse as a positive number,
    * the issue has no ``firstSeen`` field, or
    * ``firstSeen`` doesn't parse as an ISO-8601 timestamp.

    A failed parse is the safer default than silently ignoring the gate
    — if the operator pinned an age floor and we can't measure age, we
    decline to act on the issue. The cycle continues; no other rules
    are short-circuited.
    """
    try:
        floor = float(min_age_days)
    except (TypeError, ValueError):
        return False
    if floor <= 0:
        # Treat <=0 as "no gate" — act regardless. Matches operator
        # intuition that 0 days = no waiting required.
        return True

    raw = issue.get("firstSeen")
    if not raw:
        return False
    try:
        from datetime import datetime, timezone
        # GlitchTip / Sentry returns ISO 8601 with trailing 'Z'. Python's
        # fromisoformat accepts the offset form natively in 3.11+; we
        # normalise the Z just to keep it deterministic on older runtimes.
        ts_str = str(raw).rstrip("Z") + "+00:00" if str(raw).endswith("Z") else str(raw)
        first_seen = datetime.fromisoformat(ts_str)
    except (TypeError, ValueError):
        return False
    if first_seen.tzinfo is None:
        from datetime import timezone
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    from datetime import datetime, timezone
    age_days = (datetime.now(timezone.utc) - first_seen).total_seconds() / 86400.0
    return age_days >= floor


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


async def _emit_audit_event(
    pool,
    event: str,
    detail: str,
    *,
    extra: dict[str, Any] | None = None,
    severity: str = "info",
) -> None:
    """Best-effort audit_log write. Mirrors compose_drift_probe pattern."""
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            event,
            "brain.glitchtip_triage_probe",
            json.dumps(payload),
            severity,
        )
    except Exception as exc:
        logger.debug(
            "[GLITCHTIP_TRIAGE] Could not write audit event %s: %s",
            event, exc,
        )


# ---------------------------------------------------------------------------
# Top-level probe entry point
# ---------------------------------------------------------------------------


async def run_glitchtip_triage_probe(
    pool,
    *,
    notify_fn=None,
    http_client_factory=None,
) -> dict[str, Any]:
    """Single execution of the triage probe. Returns a structured summary.

    Args:
        pool: asyncpg pool for app_settings + audit_log.
        notify_fn: operator notifier callable (defaults to
            :func:`brain.operator_notifier.notify_operator`).
        http_client_factory: zero-arg callable that returns an
            ``httpx.AsyncClient`` context manager — supplied by tests
            so they can inject a mock client without monkeypatching httpx.
    """
    notify_fn = notify_fn or notify_operator

    # ---- 0) Master enable check ------------------------------------------
    enabled = (await _read_setting(pool, ENABLED_SETTING_KEY, "true")).strip().lower()
    if enabled in ("false", "0", "no", "off"):
        return {
            "ok": True,
            "status": "disabled",
            "detail": f"Triage probe disabled via app_settings.{ENABLED_SETTING_KEY}",
        }

    # ---- 1) Pull config --------------------------------------------------
    token = await _read_secret(pool, TOKEN_SETTING_KEY)
    if not token:
        detail = (
            f"GlitchTip API token not configured (app_settings."
            f"{TOKEN_SETTING_KEY}). Probe cannot authenticate. Run the audit "
            f"script (scripts/glitchtip_audit.py) to mint a token, then "
            f"`poindexter set {TOKEN_SETTING_KEY} <token> --secret`."
        )
        await _emit_audit_event(pool, "probe.glitchtip_triage_unconfigured", detail)
        return {"ok": True, "status": "unconfigured", "detail": detail}

    base_url = (await _read_setting(pool, BASE_URL_SETTING_KEY, BASE_URL_DEFAULT)).strip() or BASE_URL_DEFAULT
    base_url = localize_url(base_url).rstrip("/")
    org_slug = (await _read_setting(pool, ORG_SLUG_SETTING_KEY, ORG_SLUG_DEFAULT)).strip() or ORG_SLUG_DEFAULT
    threshold = await _read_threshold(pool)
    rules = await _read_rules(pool)

    if httpx is None:  # pragma: no cover — only when dep is uninstalled
        return {
            "ok": False,
            "status": "no_httpx",
            "detail": "httpx not installed in brain image",
        }

    # ---- 2) Build HTTP client + pull all open issues ---------------------
    timeout = httpx.Timeout(HTTP_READ_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Poindexter-GlitchTipTriage/1.0",
    }

    if http_client_factory is None:
        client_cm = httpx.AsyncClient(
            timeout=timeout, headers=headers, follow_redirects=True
        )
    else:
        client_cm = http_client_factory()

    started = time.time()
    auto_resolved: list[dict[str, Any]] = []
    auto_resolve_failed: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    alerted: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    try:
        async with client_cm as client:
            issues = await _fetch_open_issues(client, base_url, org_slug)

            for issue in issues:
                issue_id = str(issue.get("id") or "")
                if not issue_id:
                    continue
                title = issue.get("title") or ""
                count = int(issue.get("count") or 0)
                level = issue.get("level") or "error"

                rule = _match_rule(issue, rules)
                if rule and rule["action"] == "resolve":
                    ok = await _resolve_issue(client, base_url, issue_id)
                    record = {
                        "id": issue_id,
                        "title": title[:160],
                        "count": count,
                        "rule_pattern": rule["title_pattern"],
                        "reason": rule["reason"],
                    }
                    if ok:
                        auto_resolved.append(record)
                        # Drop from alert dedupe so a future re-occurrence
                        # past the rule's max_count still pages.
                        _alerted_ids.discard(issue_id)
                    else:
                        auto_resolve_failed.append(record)
                    continue
                if rule and rule["action"] == "ignore":
                    ignored.append({
                        "id": issue_id,
                        "title": title[:160],
                        "count": count,
                        "rule_pattern": rule["title_pattern"],
                        "reason": rule["reason"],
                    })
                    continue

                # No matching rule. Page if above threshold and not yet
                # alerted in this process lifetime.
                if count >= threshold and issue_id not in _alerted_ids:
                    permalink = issue.get("permalink") or ""
                    detail_msg = (
                        f"Title: {title[:300]}\n"
                        f"Count: {count}\n"
                        f"Level: {level}\n"
                        f"Last seen: {issue.get('lastSeen')}\n"
                        f"Permalink: {permalink}\n"
                        f"\n"
                        f"To silence: add a rule to "
                        f"app_settings.{RULES_SETTING_KEY}, OR resolve "
                        f"manually in the GlitchTip UI."
                    )
                    try:
                        notify_fn(
                            title=f"GlitchTip novel high-count issue: {title[:80]}",
                            detail=detail_msg,
                            source="brain.glitchtip_triage_probe",
                            severity="warning" if level != "fatal" else "critical",
                        )
                        _alerted_ids.add(issue_id)
                        alerted.append({
                            "id": issue_id,
                            "title": title[:160],
                            "count": count,
                            "level": level,
                        })
                    except Exception as exc:
                        logger.warning(
                            "[GLITCHTIP_TRIAGE] notify_fn failed for %s: %s",
                            issue_id, exc,
                        )
    except Exception as exc:
        # Any unanticipated failure — degrade to status=unknown so the
        # brain cycle keeps running.
        logger.warning(
            "[GLITCHTIP_TRIAGE] Top-level exception: %s", exc, exc_info=True
        )
        await _emit_audit_event(
            pool,
            "probe.glitchtip_triage_error",
            f"{type(exc).__name__}: {str(exc)[:300]}",
            severity="warning",
        )
        return {
            "ok": False,
            "status": "error",
            "detail": f"{type(exc).__name__}: {str(exc)[:300]}",
            "issues_seen": len(issues),
        }

    elapsed_ms = int((time.time() - started) * 1000)

    summary = {
        "ok": True,
        "status": "completed",
        "detail": (
            f"Saw {len(issues)} open issue(s); "
            f"auto-resolved {len(auto_resolved)}, "
            f"ignored {len(ignored)}, "
            f"alerted {len(alerted)}, "
            f"failed-resolve {len(auto_resolve_failed)}"
        ),
        "issues_seen": len(issues),
        "auto_resolved_count": len(auto_resolved),
        "auto_resolve_failed_count": len(auto_resolve_failed),
        "ignored_count": len(ignored),
        "alerted_count": len(alerted),
        "elapsed_ms": elapsed_ms,
        "auto_resolved": auto_resolved[:50],   # cap for storage
        "alerted": alerted[:50],
    }

    # Persist a summary audit row — the daily roll-up consumer queries this.
    await _emit_audit_event(
        pool,
        "probe.glitchtip_triage_cycle",
        summary["detail"],
        extra={
            "issues_seen": summary["issues_seen"],
            "auto_resolved_count": summary["auto_resolved_count"],
            "ignored_count": summary["ignored_count"],
            "alerted_count": summary["alerted_count"],
            "auto_resolve_failed_count": summary["auto_resolve_failed_count"],
            "elapsed_ms": elapsed_ms,
        },
    )
    logger.info("[GLITCHTIP_TRIAGE] %s in %dms", summary["detail"], elapsed_ms)
    return summary


# ---------------------------------------------------------------------------
# Probe Protocol adapter — for the registry-driven path.
# ---------------------------------------------------------------------------


class GlitchTipTriageProbe:
    """Probe-Protocol-compatible wrapper around :func:`run_glitchtip_triage_probe`."""

    name: str = "glitchtip_triage"
    description: str = (
        "Pulls unresolved GlitchTip issues each cycle, auto-resolves "
        "matches against the operator-controlled known-noise ruleset, "
        "and pages on novel high-count issues."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_glitchtip_triage_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                k: summary[k]
                for k in (
                    "issues_seen",
                    "auto_resolved_count",
                    "ignored_count",
                    "alerted_count",
                    "auto_resolve_failed_count",
                    "elapsed_ms",
                    "status",
                )
                if k in summary
            },
            severity="info" if summary.get("ok") else "warning",
        )

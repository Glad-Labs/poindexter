"""PR staleness probe — surface 24h+ green-CI PRs to Discord ops.

Closes the operator-forgot-the-PR failure mode: agents ship a PR and
the operator (Matt) forgets to merge it for 12+ hours. Today's wakeup
showed multiple PRs in that exact state. The Discord ops channel is
where Matt actually looks at his code-review backlog, so this probe
nudges him there once per PR per dedup window.

Lifecycle per cycle (mirrors brain/glitchtip_triage_probe.py):

1. Read every tunable from ``app_settings`` (DB-configurable, no
   redeploy needed). Master switch is ``pr_staleness_probe_enabled``.
2. Internal cadence gate: only do the actual GitHub round-trip when
   the last "real pass" was at least
   ``pr_staleness_poll_interval_minutes`` ago. Default 60 min — the
   issue's "fires once an hour" clause.
3. ``GET /repos/{repo}/pulls?state=open&per_page=50`` with the
   ``gh_token`` Bearer (or unauth, which fails loud for private repos).
4. For each open PR:
   * Compute age = now - created_at in hours. Skip if age <
     ``pr_staleness_min_hours`` (default 24).
   * Fetch ``GET /repos/{repo}/commits/{sha}/check-runs`` to confirm
     CI is all-green. Skip if any check-run is non-success or still
     in progress.
   * If CI green AND age >= threshold AND not deduped (per-PR
     fingerprint inside the dedup window) → collect.
5. If any collected → write ONE coalesced ``alert_events`` row at
   severity=warning so the dispatcher routes Discord-only per
   feedback_telegram_vs_discord. Cap rendered PR list at
   ``pr_staleness_max_prs_per_alert`` so the message body fits in
   one Discord card.
6. Each surfaced PR gets a fingerprint row inserted into
   ``alert_dedup_state`` (per-PR) so we don't re-page on the next
   cycle until ``pr_staleness_dedup_hours`` has elapsed.

Design parity with the rest of the brain:

- DB-configurable through ``app_settings`` — every tunable is a row.
- Standalone module: only stdlib + asyncpg + httpx (already a brain
  dep). No SiteConfig import.
- Mirrors brain/glitchtip_triage_probe.py + brain/backup_watcher.py
  lifecycle: a ``run_pr_staleness_probe`` entry point with injectable
  ``now_fn`` / ``http_client_factory`` / ``notify_fn`` seams for
  unit tests; module-level dedup state with a ``_reset_state()``
  test hook.
- Fails LOUD per feedback_no_silent_defaults: GitHub API errors emit
  a ``probe.pr_staleness_failed`` audit row at severity=warning and
  return ``ok=False`` so the brain cycle's probe-failures count
  reflects reality.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified path
    from brain.operator_notifier import notify_operator

try:
    from secret_reader import read_app_setting as _shared_read_app_setting
except ImportError:  # pragma: no cover — package-qualified path
    from brain.secret_reader import read_app_setting as _shared_read_app_setting

logger = logging.getLogger("brain.pr_staleness_probe")


# ---------------------------------------------------------------------------
# App_settings keys — every tunable lives in the DB so an operator can
# adjust without redeploying the brain. Defaults below match the
# 20260506_*_seed_pr_staleness_probe_app_settings migration.
# ---------------------------------------------------------------------------

ENABLED_KEY = "pr_staleness_probe_enabled"
POLL_INTERVAL_MINUTES_KEY = "pr_staleness_poll_interval_minutes"
MIN_HOURS_KEY = "pr_staleness_min_hours"
DEDUP_HOURS_KEY = "pr_staleness_dedup_hours"
REPO_KEY = "pr_staleness_repo"
MAX_PRS_PER_ALERT_KEY = "pr_staleness_max_prs_per_alert"

# Token reuse — same secret the dev_diary topic source already populates.
TOKEN_SETTING_KEY = "gh_token"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 60
DEFAULT_MIN_HOURS = 24
DEFAULT_DEDUP_HOURS = 12
DEFAULT_REPO = "Glad-Labs/poindexter"
DEFAULT_MAX_PRS_PER_ALERT = 5

# Brain default cycle is ~5 min; the registry-driven probe path runs
# every cycle and the inner cadence gate decides whether to do real work.
PROBE_INTERVAL_SECONDS = 5 * 60

# Per-request HTTP timeouts. Conservative — github.com is on the public
# internet, and we don't want a slow GitHub day to block the brain cycle.
HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_READ_TIMEOUT_S = 15.0

# Hard cap on PRs scanned per cycle. The ?per_page=50 call covers any
# realistic open-PR backlog; without a cap a misconfigured repo with
# thousands of stale PRs would fan out into thousands of check-runs
# requests.
MAX_PRS_PER_CYCLE = 50

# Discord-message body cap. Matt's spec mentions ≤1800 chars to fit in
# one Discord card; we cap rendered entries at MAX_PRS_PER_ALERT then
# truncate the assembled body to this hard ceiling as a last line of
# defense against unusually long PR titles.
MAX_DETAIL_BODY_CHARS = 1800


# ---------------------------------------------------------------------------
# Module-level state — persists for the lifetime of the brain process so
# the cadence gate survives across cycles. Per-PR dedup is persisted to
# alert_dedup_state for restart-safety; this module-level state is only
# the cadence gate, which is fine to reset on restart.
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "last_real_pass_at": None,  # datetime — last "do work" cycle
}


def _reset_state() -> None:
    """Test hook — clear the cadence-gate memory."""
    _state["last_real_pass_at"] = None


# ---------------------------------------------------------------------------
# app_settings reads — direct asyncpg, mirrors brain/gate_pending_summary_probe.
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    """Return ``app_settings[key]`` or the default on missing/error."""
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PR_STALENESS] Could not read %s from app_settings: %s — using default %r",
            key, exc, default,
        )
        return default
    if val is None:
        return default
    return val


def _coerce_bool(val: Any, default: bool) -> bool:
    """Parse common truthy strings; fall back to default on anything else."""
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    """Parse an int with a safe fallback."""
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


async def _read_config(pool: Any) -> dict[str, Any]:
    """Pull every probe tunable in one helper."""
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"),
        DEFAULT_ENABLED,
    )
    poll_interval_minutes = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES),
        DEFAULT_POLL_INTERVAL_MINUTES,
    )
    min_hours = _coerce_int(
        await _read_setting(pool, MIN_HOURS_KEY, DEFAULT_MIN_HOURS),
        DEFAULT_MIN_HOURS,
    )
    dedup_hours = _coerce_int(
        await _read_setting(pool, DEDUP_HOURS_KEY, DEFAULT_DEDUP_HOURS),
        DEFAULT_DEDUP_HOURS,
    )
    repo = str(await _read_setting(pool, REPO_KEY, DEFAULT_REPO)).strip() or DEFAULT_REPO
    max_prs = _coerce_int(
        await _read_setting(pool, MAX_PRS_PER_ALERT_KEY, DEFAULT_MAX_PRS_PER_ALERT),
        DEFAULT_MAX_PRS_PER_ALERT,
    )
    if max_prs <= 0:
        max_prs = DEFAULT_MAX_PRS_PER_ALERT

    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "min_hours": min_hours,
        "dedup_hours": dedup_hours,
        "repo": repo,
        "max_prs": max_prs,
    }


async def _read_token(pool: Any) -> str:
    """Read ``gh_token`` from app_settings, falling back to ``GITHUB_TOKEN`` env."""
    val = await _shared_read_app_setting(pool, TOKEN_SETTING_KEY, default="")
    if val:
        return val
    return os.getenv("GITHUB_TOKEN", "").strip()


# ---------------------------------------------------------------------------
# alert_dedup_state — per-PR fingerprint persistence so a brain restart
# inside the dedup window doesn't re-page.
# ---------------------------------------------------------------------------


def _fingerprint_for(repo: str, pr_number: int) -> str:
    """Stable per-PR dedup key the alert_dedup_state row hangs on."""
    return f"pr_stale_{repo}_{pr_number}"


async def _is_pr_deduped(
    pool: Any,
    *,
    fingerprint: str,
    now_utc: datetime,
    dedup_hours: int,
) -> bool:
    """Return True iff a dedup row for this fingerprint exists and is fresh."""
    if dedup_hours <= 0:
        return False
    try:
        row = await pool.fetchrow(
            "SELECT last_seen_at FROM alert_dedup_state WHERE fingerprint = $1",
            fingerprint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[PR_STALENESS] alert_dedup_state lookup failed for %s: %s — treating as not deduped",
            fingerprint, exc,
        )
        return False
    if not row:
        return False
    last_seen = row["last_seen_at"]
    if not isinstance(last_seen, datetime):
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now_utc - last_seen) < timedelta(hours=dedup_hours)


async def _record_pr_dedup(
    pool: Any,
    *,
    fingerprint: str,
    now_utc: datetime,
    pr_title: str,
) -> None:
    """Upsert the dedup row so future cycles inside the window suppress."""
    try:
        await pool.execute(
            """
            INSERT INTO alert_dedup_state (
                fingerprint, first_seen_at, last_seen_at, repeat_count,
                severity, source, sample_message
            ) VALUES ($1, $2, $2, 1, 'warning', 'brain.pr_staleness_probe', $3)
            ON CONFLICT (fingerprint) DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                repeat_count = alert_dedup_state.repeat_count + 1
            """,
            fingerprint, now_utc, pr_title[:300],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PR_STALENESS] alert_dedup_state upsert failed for %s: %s",
            fingerprint, exc,
        )


# ---------------------------------------------------------------------------
# audit_log — best-effort timeline write
# ---------------------------------------------------------------------------


async def _emit_audit_event(
    pool: Any,
    event: str,
    detail: str,
    *,
    extra: Optional[dict[str, Any]] = None,
    severity: str = "info",
) -> None:
    """Write a single audit_log row; never raises."""
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
            "brain.pr_staleness_probe",
            json.dumps(payload),
            severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PR_STALENESS] audit_log insert failed: %s", exc)


# ---------------------------------------------------------------------------
# alert_events — single coalesced row routed Discord-only by severity.
# ---------------------------------------------------------------------------


async def _emit_stale_alert(
    pool: Any,
    *,
    repo: str,
    pr_count: int,
    body: str,
    pr_numbers: list[int],
) -> bool:
    """Insert one ``status='firing'`` row; the dispatcher routes Discord-only."""
    alertname = f"pr_stale_{repo.replace('/', '_')}"
    labels = {
        "source": "brain.pr_staleness_probe",
        "category": "pr_staleness",
        "repo": repo,
        "pr_count": str(pr_count),
    }
    annotations = {
        "summary": (
            f"{pr_count} PR(s) older than 24h with green CI need a merge "
            f"decision in {repo}"
        ),
        "description": body,
        "pr_numbers": ",".join(str(n) for n in pr_numbers),
    }
    # Per-cycle fingerprint so the row itself is always fresh — per-PR
    # dedup is handled separately via alert_dedup_state lookups before
    # we get here.
    fingerprint = (
        f"pr-staleness-{alertname}-{int(datetime.now(timezone.utc).timestamp())}"
    )
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES (
                $1, 'warning', 'firing', $2::jsonb, $3::jsonb, NOW(), $4
            )
            """,
            alertname,
            json.dumps(labels),
            json.dumps(annotations),
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PR_STALENESS] Failed to write firing alert %s: %s",
            alertname, exc,
        )
        return False


# ---------------------------------------------------------------------------
# GitHub REST API client (thin)
# ---------------------------------------------------------------------------


async def _fetch_open_prs(
    client: "httpx.AsyncClient",
    repo: str,
) -> list[dict[str, Any]]:
    """Return the open-PR list for ``owner/name`` (one page, capped at 50)."""
    r = await client.get(
        f"https://api.github.com/repos/{repo}/pulls",
        params={"state": "open", "per_page": MAX_PRS_PER_CYCLE},
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"GitHub /pulls returned {r.status_code}: "
            f"{(r.text or '')[:200]}"
        )
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(
            f"GitHub /pulls returned non-list payload: {type(data).__name__}"
        )
    return data


async def _fetch_check_runs(
    client: "httpx.AsyncClient",
    repo: str,
    sha: str,
) -> list[dict[str, Any]]:
    """Return the check-run list for one commit SHA."""
    r = await client.get(
        f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs",
        params={"per_page": 100},
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"GitHub /check-runs returned {r.status_code}: "
            f"{(r.text or '')[:200]}"
        )
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(
            f"GitHub /check-runs returned non-dict payload: {type(data).__name__}"
        )
    runs = data.get("check_runs")
    if not isinstance(runs, list):
        return []
    return runs


def _ci_all_green(check_runs: list[dict[str, Any]]) -> bool:
    """Return True iff every check-run is completed AND conclusion=success.

    Empty list also counts as "not green" — a PR with zero check-runs
    isn't a confirmed pass and we'd rather under-alert than nag the
    operator about an unreviewable PR.
    """
    if not check_runs:
        return False
    for run in check_runs:
        status = (run.get("status") or "").strip().lower()
        if status != "completed":
            return False
        conclusion = (run.get("conclusion") or "").strip().lower()
        if conclusion != "success":
            return False
    return True


def _parse_iso8601_utc(raw: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into a UTC-aware datetime; None on failure."""
    if not raw:
        return None
    try:
        s = str(raw)
        # Python 3.10's fromisoformat doesn't accept the trailing "Z".
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        ts = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


# ---------------------------------------------------------------------------
# Discord-message body builder
# ---------------------------------------------------------------------------


def _build_discord_body(
    repo: str,
    stale_prs: list[dict[str, Any]],
    *,
    min_hours: int,
    max_prs: int,
) -> str:
    """Render the per-PR bulleted list with a header line."""
    header = (
        f"\U0001F7E1 [pr-staleness] {len(stale_prs)} PR(s) older than "
        f"{min_hours}h with green CI need a merge decision"
    )
    lines = [header, ""]
    for entry in stale_prs[:max_prs]:
        age_h = entry["age_hours"]
        author = entry["author"] or "unknown"
        additions = entry["additions"]
        deletions = entry["deletions"]
        lines.append(
            f"• #{entry['number']} {entry['title']} "
            f"({age_h}h, +{additions}/-{deletions}, by {author})"
        )
    if len(stale_prs) > max_prs:
        lines.append(
            f"… and {len(stale_prs) - max_prs} more "
            f"(see {repo} pulls)"
        )
    body = "\n".join(lines)
    if len(body) > MAX_DETAIL_BODY_CHARS:
        body = body[: MAX_DETAIL_BODY_CHARS - 1] + "…"
    return body


# ---------------------------------------------------------------------------
# Top-level probe entry point
# ---------------------------------------------------------------------------


NotifyFn = Callable[..., Any]
HttpClientFactory = Callable[..., Any]


async def run_pr_staleness_probe(
    pool: Any,
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    notify_fn: Optional[NotifyFn] = None,
    http_client_factory: Optional[HttpClientFactory] = None,
) -> dict[str, Any]:
    """Single execution of the PR-staleness probe; returns a structured summary.

    Args:
        pool: asyncpg pool for app_settings + alert_events + alert_dedup_state.
        now_fn: ``() -> aware datetime`` — defaults to ``datetime.now(UTC)``.
            Tests inject a fixed clock so dedup math is deterministic.
        notify_fn: operator notifier callable. Defaults to
            ``brain.operator_notifier.notify_operator``. Used ONLY for
            loud-failure paths (GitHub auth misconfig). The success
            path writes a Discord-only ``alert_events`` row.
        http_client_factory: zero-arg callable returning an
            ``httpx.AsyncClient`` context manager — supplied by tests so
            they can inject a mock client without monkeypatching httpx.
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    notify_fn = notify_fn or notify_operator

    config = await _read_config(pool)
    if not config["enabled"]:
        return {
            "ok": True,
            "status": "disabled",
            "stale_prs": 0,
            "alert_emitted": False,
            "detail": (
                f"PR staleness probe disabled "
                f"(app_settings.{ENABLED_KEY}=false)"
            ),
        }

    now_utc = now_fn()
    poll_interval_minutes = int(config["poll_interval_minutes"])
    min_hours = int(config["min_hours"])
    dedup_hours = int(config["dedup_hours"])
    repo = config["repo"]
    max_prs = int(config["max_prs"])

    # Internal cadence gate. Brain cycle is ~5 min but the issue spec
    # says "fires once an hour".
    last_pass = _state.get("last_real_pass_at")
    if isinstance(last_pass, datetime):
        elapsed = (now_utc - last_pass).total_seconds()
        if elapsed < poll_interval_minutes * 60:
            return {
                "ok": True,
                "status": "skipped_interval",
                "stale_prs": 0,
                "alert_emitted": False,
                "detail": (
                    f"Within poll interval ({poll_interval_minutes} min) — skipped."
                ),
            }

    if httpx is None:  # pragma: no cover — only when dep is uninstalled
        await _emit_audit_event(
            pool,
            "probe.pr_staleness_failed",
            "httpx not installed in brain image",
            severity="warning",
        )
        return {
            "ok": False,
            "status": "no_httpx",
            "stale_prs": 0,
            "alert_emitted": False,
            "detail": "httpx not installed in brain image",
        }

    # ---- GitHub round-trip ----------------------------------------------
    token = await _read_token(pool)
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Poindexter-PRStalenessProbe/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = httpx.Timeout(HTTP_READ_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S)
    if http_client_factory is None:
        client_cm = httpx.AsyncClient(
            timeout=timeout, headers=headers, follow_redirects=True
        )
    else:
        client_cm = http_client_factory()

    stale_prs: list[dict[str, Any]] = []
    skipped_too_young = 0
    skipped_ci_not_green = 0
    skipped_deduped = 0
    pr_count_seen = 0

    try:
        async with client_cm as client:
            prs = await _fetch_open_prs(client, repo)
            pr_count_seen = len(prs)

            for pr in prs:
                number = pr.get("number")
                if not isinstance(number, int):
                    continue
                created_at = _parse_iso8601_utc(pr.get("created_at"))
                if created_at is None:
                    continue

                age_seconds = (now_utc - created_at).total_seconds()
                age_hours = age_seconds / 3600.0
                if age_hours < min_hours:
                    skipped_too_young += 1
                    continue

                head = pr.get("head") or {}
                sha = head.get("sha") if isinstance(head, dict) else None
                if not sha:
                    skipped_ci_not_green += 1
                    continue

                runs = await _fetch_check_runs(client, repo, str(sha))
                if not _ci_all_green(runs):
                    skipped_ci_not_green += 1
                    continue

                fingerprint = _fingerprint_for(repo, number)
                if await _is_pr_deduped(
                    pool,
                    fingerprint=fingerprint,
                    now_utc=now_utc,
                    dedup_hours=dedup_hours,
                ):
                    skipped_deduped += 1
                    continue

                user = pr.get("user") or {}
                author = (
                    user.get("login")
                    if isinstance(user, dict) else None
                ) or ""

                stale_prs.append({
                    "number": number,
                    "title": (pr.get("title") or "")[:160],
                    "age_hours": int(age_hours),
                    "author": author,
                    "additions": int(pr.get("additions") or 0),
                    "deletions": int(pr.get("deletions") or 0),
                    "fingerprint": fingerprint,
                })
    except Exception as exc:  # noqa: BLE001 — fail loud per feedback_no_silent_defaults
        detail = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger.warning("[PR_STALENESS] GitHub round-trip failed: %s", detail, exc_info=True)
        await _emit_audit_event(
            pool,
            "probe.pr_staleness_failed",
            detail,
            extra={"repo": repo},
            severity="warning",
        )
        # Loud-failure operator nudge — surfaces the misconfig once even
        # if the dispatcher's per-fingerprint dedup later collapses repeats.
        try:
            notify_fn(
                title=f"PR staleness probe failed against {repo}",
                detail=(
                    f"{detail}\n\n"
                    f"Fix: confirm app_settings.gh_token is set and has "
                    f"`repo` read scope, or unset "
                    f"app_settings.{ENABLED_KEY} until the API is reachable."
                ),
                source="brain.pr_staleness_probe",
                severity="warning",
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": False,
            "status": "github_error",
            "stale_prs": 0,
            "alert_emitted": False,
            "pr_count_seen": pr_count_seen,
            "detail": detail,
        }

    # Mark the cadence gate now that the round-trip completed cleanly.
    _state["last_real_pass_at"] = now_utc

    if not stale_prs:
        summary = {
            "ok": True,
            "status": "no_stale_prs",
            "stale_prs": 0,
            "alert_emitted": False,
            "pr_count_seen": pr_count_seen,
            "skipped_too_young": skipped_too_young,
            "skipped_ci_not_green": skipped_ci_not_green,
            "skipped_deduped": skipped_deduped,
            "detail": (
                f"Saw {pr_count_seen} open PR(s); 0 stale "
                f"(too_young={skipped_too_young}, "
                f"ci_not_green={skipped_ci_not_green}, "
                f"deduped={skipped_deduped})"
            ),
        }
        await _emit_audit_event(
            pool,
            "probe.pr_staleness_cycle",
            summary["detail"],
            extra={
                "repo": repo,
                "pr_count_seen": pr_count_seen,
                "skipped_too_young": skipped_too_young,
                "skipped_ci_not_green": skipped_ci_not_green,
                "skipped_deduped": skipped_deduped,
            },
        )
        return summary

    body = _build_discord_body(
        repo, stale_prs, min_hours=min_hours, max_prs=max_prs,
    )
    pr_numbers = [int(entry["number"]) for entry in stale_prs]
    alert_emitted = await _emit_stale_alert(
        pool,
        repo=repo,
        pr_count=len(stale_prs),
        body=body,
        pr_numbers=pr_numbers,
    )

    # Stamp the dedup table per-PR — even on alert_events failure we
    # still record so we don't fan out duplicate failures next cycle.
    for entry in stale_prs:
        await _record_pr_dedup(
            pool,
            fingerprint=entry["fingerprint"],
            now_utc=now_utc,
            pr_title=entry["title"],
        )

    summary = {
        "ok": alert_emitted,
        "status": "alert_emitted" if alert_emitted else "alert_emit_failed",
        "stale_prs": len(stale_prs),
        "alert_emitted": alert_emitted,
        "pr_count_seen": pr_count_seen,
        "skipped_too_young": skipped_too_young,
        "skipped_ci_not_green": skipped_ci_not_green,
        "skipped_deduped": skipped_deduped,
        "pr_numbers": pr_numbers,
        "detail": (
            f"Saw {pr_count_seen} open PR(s); {len(stale_prs)} stale "
            f"(emitted={alert_emitted})"
        ),
    }
    await _emit_audit_event(
        pool,
        "probe.pr_staleness_cycle",
        summary["detail"],
        extra={
            "repo": repo,
            "pr_count_seen": pr_count_seen,
            "stale_prs": len(stale_prs),
            "pr_numbers": pr_numbers,
            "alert_emitted": alert_emitted,
        },
    )
    logger.info("[PR_STALENESS] %s", summary["detail"])
    return summary


# ---------------------------------------------------------------------------
# Probe Protocol adapter — for the registry-driven path.
# ---------------------------------------------------------------------------


class PRStalenessProbe:
    """Probe-Protocol-compatible wrapper around ``run_pr_staleness_probe``."""

    name: str = "pr_staleness"
    description: str = (
        "Pulls open PRs from GitHub each cycle and surfaces any that are "
        "older than the staleness threshold AND have all-green CI but no "
        "merge decision. Routes a single coalesced Discord-ops alert."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_pr_staleness_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                k: summary[k]
                for k in (
                    "status",
                    "stale_prs",
                    "alert_emitted",
                    "pr_count_seen",
                    "skipped_too_young",
                    "skipped_ci_not_green",
                    "skipped_deduped",
                )
                if k in summary
            },
            severity="info" if summary.get("ok") else "warning",
        )

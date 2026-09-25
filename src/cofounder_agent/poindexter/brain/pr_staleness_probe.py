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
- Pages a broken probe ONCE per failure episode, not once per pass.
  The episode lives in ``brain_knowledge`` so a brain restart does
  not re-page; the operator hears again only when the failure changes,
  when a replaced ``gh_token`` fails too, as a reminder every
  ``pr_staleness_failure_repage_hours``, and once more on recovery.
  See "Failure episodes" below for the incident that earned this.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from poindexter.brain.operator_notifier import notify_operator
from poindexter.brain.secret_reader import read_app_setting as _shared_read_app_setting

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
FAILURE_REPAGE_HOURS_KEY = "pr_staleness_failure_repage_hours"

# Token reuse — same secret the dev_diary topic source already populates.
TOKEN_SETTING_KEY = "gh_token"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 60
DEFAULT_MIN_HOURS = 24
DEFAULT_DEDUP_HOURS = 12
DEFAULT_REPO = "Glad-Labs/poindexter"
DEFAULT_MAX_PRS_PER_ALERT = 5
DEFAULT_FAILURE_REPAGE_HOURS = 24

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
    "last_real_pass_at": None,  # datetime — last "do work" cycle, failed or not
    # Operator-facing text of the last failed pass, None once a pass succeeds.
    # Lets the cycles skipped by the cadence gate keep reporting ok=False
    # while the probe is broken, instead of reading as healthy 11 cycles in 12.
    "failing_detail": None,
}


def _reset_state() -> None:
    """Test hook — clear the cadence-gate memory."""
    _state["last_real_pass_at"] = None
    _state["failing_detail"] = None


# ---------------------------------------------------------------------------
# app_settings reads — direct asyncpg (the brain has no SiteConfig), the same
# shape every sibling brain probe uses.
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
    # 0 is meaningful here: page once per episode and never remind.
    failure_repage_hours = max(0, _coerce_int(
        await _read_setting(pool, FAILURE_REPAGE_HOURS_KEY, DEFAULT_FAILURE_REPAGE_HOURS),
        DEFAULT_FAILURE_REPAGE_HOURS,
    ))

    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "min_hours": min_hours,
        "dedup_hours": dedup_hours,
        "repo": repo,
        "max_prs": max_prs,
        "failure_repage_hours": failure_repage_hours,
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
        logger.warning(
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
        last_seen = last_seen.replace(tzinfo=UTC)
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
# Failure episodes — page a broken probe once, not every pass.
#
# 2026-09-23 23:13 UTC the gh_token was replaced with one that cannot see the
# private repo, and from 23:37 every pass failed with the same 404. The
# failure path neither advanced the hourly cadence gate nor remembered that it
# had paged, so the probe retried every ~5-min brain cycle and called
# notify_operator each time: 286 pages on 2026-09-24, 52 of them delivered to
# Discord. The only brake was the notifier's in-memory 30-min
# ``operator_page_cooldown_minutes``, and every brain restart reset even that.
# ``pr_staleness_dedup_hours`` never applied: it dedups stale-PR alerts, not
# the probe's own failure.
#
# The brain cannot fix a bad token — only the operator can — so the failure
# opens an EPISODE, persisted in brain_knowledge (restart-safe; the
# clock_skew_probe / data_freshness_probe shape), and pages when:
#   * the episode opens;
#   * the failure changes (different endpoint, status class or exception) —
#     that is new information, e.g. 404 -> 401;
#   * the gh_token row was replaced mid-episode and the new token fails too —
#     the operator's fix did not take, and a day of silence would read as
#     success;
#   * the previous page reached no channel (a failed send must not swallow
#     the page — the notifier's own rule);
#   * ``pr_staleness_failure_repage_hours`` have passed since the last page
#     (a reminder; 0 = never remind).
# Every other failing pass is still recorded — audit_log row, WARNING log,
# ok=False — it just does not page. The first successful pass after a paged
# episode sends one recovery note and closes the episode.
# ---------------------------------------------------------------------------

FAILURE_STATE_ENTITY = "pr_staleness_probe"

# page_reason values, as they appear in the summary and the audit row.
PAGE_NEW = "new"
PAGE_CHANGED = "changed"
PAGE_TOKEN_REPLACED = "token_replaced"
PAGE_UNDELIVERED = "undelivered"
PAGE_REMINDER = "reminder"


def _failure_attribute(repo: str) -> str:
    """brain_knowledge attribute holding the open episode for ``repo``."""
    return f"failure_episode:{repo}"


async def _read_failure_episode(pool: Any, repo: str) -> dict[str, Any] | None:
    """Return the open failure episode for ``repo``, or None."""
    try:
        raw = await pool.fetchval(
            "SELECT value FROM brain_knowledge WHERE entity = $1 AND attribute = $2",
            FAILURE_STATE_ENTITY, _failure_attribute(repo),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PR_STALENESS] failure-episode read failed for %s: %s — treating "
            "the failure as new, so the operator may be paged again",
            repo, exc,
        )
        return None
    if not raw:
        return None
    try:
        episode = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[PR_STALENESS] failure-episode row for %s is not JSON (%.80r) — "
            "starting a new episode", repo, raw,
        )
        return None
    return episode if isinstance(episode, dict) else None


async def _write_failure_episode(pool: Any, repo: str, episode: dict[str, Any]) -> None:
    """Upsert the episode row; never raises."""
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, $2, $3, 1.0, 'pr_staleness_probe')
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            FAILURE_STATE_ENTITY, _failure_attribute(repo), json.dumps(episode),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PR_STALENESS] failure-episode write failed for %s: %s — the next "
            "failing pass cannot tell it already paged and may page again",
            repo, exc,
        )


async def _clear_failure_episode(pool: Any, repo: str) -> None:
    """Delete the episode row; never raises."""
    try:
        await pool.execute(
            "DELETE FROM brain_knowledge WHERE entity = $1 AND attribute = $2",
            FAILURE_STATE_ENTITY, _failure_attribute(repo),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PR_STALENESS] failure-episode clear failed for %s: %s — the "
            "recovery note may be sent again on the next clean pass",
            repo, exc,
        )


async def _read_token_changed_at(pool: Any) -> str | None:
    """When the ``gh_token`` row's value last changed (ISO), or None.

    Reads the row's timestamp, never the secret. ``updated_at`` moves only
    when the value is written (``app_settings_set_updated_at_trigger`` is
    ``BEFORE UPDATE OF value``), so read-telemetry stamps don't move it.
    """
    try:
        val = await pool.fetchval(
            "SELECT updated_at FROM app_settings WHERE key = $1",
            TOKEN_SETTING_KEY,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PR_STALENESS] could not read %s.updated_at: %s — a replaced "
            "token that still fails will wait for the next reminder",
            TOKEN_SETTING_KEY, exc,
        )
        return None
    if not isinstance(val, datetime):
        return None
    if val.tzinfo is None:
        val = val.replace(tzinfo=UTC)
    return val.isoformat()


def _decide_failure_page(
    prev: dict[str, Any] | None,
    *,
    signature: str,
    token_changed_at: str | None,
    now_utc: datetime,
    repage_hours: int,
) -> tuple[dict[str, Any], str | None]:
    """Fold one failing pass into the episode.

    Returns ``(episode, page_reason)``; ``page_reason`` is None when this
    pass should stay quiet. Pure — the caller persists the episode and, once
    a page is delivered, stamps ``paged_at``.
    """
    now_iso = now_utc.isoformat()
    if not prev:
        return {
            "signature": signature,
            "token_changed_at": token_changed_at,
            "since": now_iso,
            "attempts": 1,
            "paged_at": None,
            "pages": 0,
        }, PAGE_NEW

    episode = dict(prev)
    episode["attempts"] = _coerce_int(prev.get("attempts"), 0) + 1
    if not episode.get("since"):
        episode["since"] = now_iso

    if prev.get("signature") != signature:
        episode["signature"] = signature
        episode["previous_signature"] = prev.get("signature")
        episode["token_changed_at"] = token_changed_at
        return episode, PAGE_CHANGED

    prev_token = prev.get("token_changed_at")
    if token_changed_at and token_changed_at != prev_token:
        episode["token_changed_at"] = token_changed_at
        # Only a KNOWN earlier token is evidence of a replacement; a first
        # successful read of the timestamp is just bookkeeping.
        if prev_token:
            return episode, PAGE_TOKEN_REPLACED

    paged_at = _parse_iso8601_utc(prev.get("paged_at"))
    if paged_at is None:
        return episode, PAGE_UNDELIVERED
    if repage_hours > 0 and now_utc - paged_at >= timedelta(hours=repage_hours):
        return episode, PAGE_REMINDER
    return episode, None


def _page_delivered(results: Any) -> bool:
    """Did ``notify_operator`` get the page to an external channel?

    False only when a configured channel FAILED to send — retrying next pass
    can fix that. No channel configured at all is not retryable (alerts.log
    already has it), and a return value this function can't read (a test or
    custom notifier) counts as delivered rather than re-paging every pass.
    """
    if not isinstance(results, dict):
        return True
    statuses = [str(results.get(channel) or "") for channel in ("discord", "telegram")]
    if any(s in ("discord", "telegram") or s.startswith("suppressed") for s in statuses):
        return True
    return not any("send failed" in s for s in statuses)


def _fmt_utc(iso: Any) -> str:
    """Render a stored ISO timestamp as ``YYYY-MM-DD HH:MM UTC``."""
    ts = _parse_iso8601_utc(iso)
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if ts else "unknown"


# ---------------------------------------------------------------------------
# audit_log — best-effort timeline write
# ---------------------------------------------------------------------------


async def _emit_audit_event(
    pool: Any,
    event: str,
    detail: str,
    *,
    extra: dict[str, Any] | None = None,
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
        # silent-ok: mirror only. Stale PRs are alerted by _emit_stale_alert
        # (its own coalesced alert_events row -> alert_dispatcher), and the
        # probe-failure event is paired with a notify_fn call — which now
        # also warns if that delivery fails (see run_pr_staleness_probe).
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
        f"pr-staleness-{alertname}-{int(datetime.now(UTC).timestamp())}"
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


class GitHubAPIError(RuntimeError):
    """GitHub answered with a non-200; keeps what the operator page needs."""

    def __init__(
        self,
        endpoint: str,
        status_code: int,
        body: str,
        *,
        rate_limited: bool = False,
        ref: str = "",
    ) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        self.body = body
        self.rate_limited = rate_limited
        self.ref = ref
        super().__init__(f"GitHub /{endpoint} returned {status_code}: {body[:200]}")


def _is_rate_limited(response: Any) -> bool:
    """True for GitHub's primary or secondary rate-limit answer (403/429)."""
    if response.status_code not in (403, 429):
        return False
    headers = getattr(response, "headers", None) or {}
    try:
        remaining = headers.get("x-ratelimit-remaining")
    except Exception:  # noqa: BLE001 — a header object we can't read is "no header"
        remaining = None
    if remaining is not None and str(remaining).strip() == "0":
        return True
    return "rate limit" in (getattr(response, "text", "") or "").lower()


def _github_message(body: str) -> str:
    """The ``message`` field of a GitHub error body, else a compact excerpt."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])[:200]
    return " ".join((body or "").split())[:200] or "no body"


_TOKEN_FIX = "`poindexter settings set gh_token <token> --secret`"


def _describe_failure(exc: BaseException, *, repo: str, has_token: bool) -> tuple[str, str]:
    """Map a failed round-trip to ``(signature, operator-facing detail)``.

    ``signature`` is the failure's identity for episode dedup: the same
    signature on the next pass is the same failure, not news. It groups by
    what the operator would do about it — every 5xx is one "GitHub is
    having a bad day" failure — so flapping between 502 and 503 stays quiet.
    """
    if not isinstance(exc, GitHubAPIError):
        name = type(exc).__name__
        # str() of an httpx timeout is the EMPTY string; name the class alone.
        msg = str(exc).strip()
        what = f"{name}: {msg[:200]}" if msg else name
        return name, (
            f"{what} — the GitHub round-trip for {repo} did not complete. "
            f"Usually a network or GitHub blip; the probe retries on its "
            f"next pass."
        )

    status = exc.status_code
    said = _github_message(exc.body)
    if exc.rate_limited:
        return f"{exc.endpoint}:rate-limited", (
            f"GitHub rate-limited the probe on /{exc.endpoint} (HTTP {status}: "
            f"{said}). It retries on its next pass; if this persists, another "
            f"gh_token consumer is spending the budget."
        )
    if status >= 500:
        return f"{exc.endpoint}:5xx", (
            f"GitHub /{exc.endpoint} for {repo} returned {status} — a GitHub-side "
            f"error. The probe retries on its next pass."
        )
    if status == 401:
        return f"{exc.endpoint}:401", (
            f"GitHub rejected the gh_token (HTTP 401: {said}) — it is invalid, "
            f"expired or revoked. Replace it with {_TOKEN_FIX}."
        )
    if exc.endpoint == "pulls" and status == 404:
        if not has_token:
            return "pulls:404-no-token", (
                f"gh_token is not set, and GitHub answers 404 to an anonymous "
                f"request for {repo} — the repo is private, or the name in "
                f"app_settings.{REPO_KEY} is wrong. Set a token that can read "
                f"its pull requests and checks with {_TOKEN_FIX}."
            )
        return "pulls:404", (
            f"The gh_token cannot see {repo}, check its scopes. GitHub answers "
            f"404, not 403, for a private repo the token has no access to, so "
            f"unless app_settings.{REPO_KEY} is misspelled, the token is the "
            f"problem. It needs {repo} in its repository access with Pull "
            f"requests (read) and Checks (read), or the classic `repo` scope. "
            f"Rotate it with {_TOKEN_FIX}."
        )
    if exc.endpoint == "pulls" and status == 403:
        return "pulls:403", (
            f"The gh_token may not list pull requests on {repo} (HTTP 403: "
            f"{said}), check its scopes: it needs Pull requests (read) and "
            f"Checks (read). Rotate it with {_TOKEN_FIX}."
        )
    if exc.endpoint == "check-runs" and status == 403:
        return "check-runs:403", (
            f"The gh_token can list pull requests on {repo} but cannot read "
            f"their check runs (HTTP 403: {said}) — grant it Checks (read), or "
            f"rotate it with {_TOKEN_FIX}."
        )
    if exc.endpoint == "check-runs" and status == 404:
        return "check-runs:404", (
            f"GitHub /check-runs returned 404 for commit {exc.ref[:9]} on "
            f"{repo} — the commit is gone (force-pushed or deleted head) or "
            f"the gh_token cannot read checks there. Nothing is surfaced for "
            f"any PR this pass."
        )
    return f"{exc.endpoint}:{status}", (
        f"GitHub /{exc.endpoint} for {repo} returned HTTP {status}: {said}"
    )


async def _fetch_open_prs(
    client: httpx.AsyncClient,
    repo: str,
) -> list[dict[str, Any]]:
    """Return the open-PR list for ``owner/name`` (one page, capped at 50)."""
    r = await client.get(
        f"https://api.github.com/repos/{repo}/pulls",
        params={"state": "open", "per_page": MAX_PRS_PER_CYCLE},
    )
    if r.status_code != 200:
        raise GitHubAPIError(
            "pulls", r.status_code, r.text or "",
            rate_limited=_is_rate_limited(r),
        )
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(
            f"GitHub /pulls returned non-list payload: {type(data).__name__}"
        )
    return data


async def _fetch_check_runs(
    client: httpx.AsyncClient,
    repo: str,
    sha: str,
) -> list[dict[str, Any]]:
    """Return the check-run list for one commit SHA."""
    r = await client.get(
        f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs",
        params={"per_page": 100},
    )
    if r.status_code != 200:
        raise GitHubAPIError(
            "check-runs", r.status_code, r.text or "",
            rate_limited=_is_rate_limited(r), ref=sha,
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


def _parse_iso8601_utc(raw: Any) -> datetime | None:
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
        ts = ts.replace(tzinfo=UTC)
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


def _build_failure_page(
    *,
    repo: str,
    reason: str,
    detail: str,
    episode: dict[str, Any],
    poll_interval_minutes: int,
    repage_hours: int,
) -> tuple[str, str]:
    """Render ``(title, body)`` for a failure page."""
    if reason == PAGE_REMINDER:
        title = f"PR staleness probe still failing against {repo}"
    else:
        title = f"PR staleness probe failed against {repo}"

    lines = [detail, ""]
    if reason == PAGE_CHANGED:
        lines.append(
            f"The failure changed (was {episode.get('previous_signature')}, "
            f"now {episode.get('signature')})."
        )
    elif reason == PAGE_TOKEN_REPLACED:
        lines.append(
            f"The gh_token was replaced at "
            f"{_fmt_utc(episode.get('token_changed_at'))}, and the new token "
            f"fails the same way."
        )
    elif reason == PAGE_UNDELIVERED:
        lines.append("The previous page about this failure reached no channel.")
    attempts = _coerce_int(episode.get("attempts"), 1)
    lines.append(
        f"Failing since {_fmt_utc(episode.get('since'))} "
        f"({attempts} attempt{'s' if attempts != 1 else ''}); the probe retries "
        f"every {poll_interval_minutes} min and stays quiet while the failure "
        f"is unchanged."
    )
    if repage_hours > 0:
        lines.append(
            f"Next reminder in {repage_hours}h if it persists; a recovery note "
            f"follows when it clears."
        )
    else:
        lines.append(
            f"Reminders are off (app_settings.{FAILURE_REPAGE_HOURS_KEY}=0); a "
            f"recovery note follows when it clears."
        )
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level probe entry point
# ---------------------------------------------------------------------------


NotifyFn = Callable[..., Any]
HttpClientFactory = Callable[..., Any]


async def _handle_round_trip_failure(
    pool: Any,
    exc: BaseException,
    *,
    repo: str,
    has_token: bool,
    now_utc: datetime,
    config: dict[str, Any],
    notify_fn: NotifyFn,
    pr_count_seen: int,
) -> dict[str, Any]:
    """Record a failed pass; page only when the episode says it is news."""
    signature, detail = _describe_failure(exc, repo=repo, has_token=has_token)
    # A traceback adds nothing to a status code GitHub sent us; keep it for
    # the failures nobody anticipated.
    logger.warning(
        "[PR_STALENESS] GitHub round-trip failed (%s): %s", signature, detail,
        exc_info=not isinstance(exc, GitHubAPIError),
    )
    _state["failing_detail"] = detail

    repage_hours = int(config["failure_repage_hours"])
    prev = await _read_failure_episode(pool, repo)
    episode, reason = _decide_failure_page(
        prev,
        signature=signature,
        token_changed_at=await _read_token_changed_at(pool),
        now_utc=now_utc,
        repage_hours=repage_hours,
    )
    episode["last_detail"] = detail[:500]

    paged = False
    if reason is None:
        logger.info(
            "[PR_STALENESS] failure unchanged (%s, attempt %s since %s) — "
            "already paged at %s; not paging again",
            signature, episode.get("attempts"), episode.get("since"),
            episode.get("paged_at"),
        )
    else:
        title, body = _build_failure_page(
            repo=repo,
            reason=reason,
            detail=detail,
            episode=episode,
            poll_interval_minutes=int(config["poll_interval_minutes"]),
            repage_hours=repage_hours,
        )
        try:
            results = notify_fn(
                title=title,
                detail=body,
                source="brain.pr_staleness_probe",
                severity="warning",
                # Per-failure key so the notifier's own cooldown can never
                # swallow a CHANGED failure behind the previous page.
                dedup_key=f"pr_staleness_failed:{repo}:{signature}",
            )
        except Exception as notify_exc:  # noqa: BLE001
            # The probe failure itself is already logged above; what would be
            # lost here is that the operator's PAGE never went out. This
            # system is run from a phone via Telegram/Discord, so a dead
            # notifier reads as "no stale PRs" — silence looks like health.
            logger.warning(
                "[PR_STALENESS] probe-failure notification could not be "
                "delivered (%s: %s) — the operator was NOT paged about the "
                "GitHub error above; retrying on the next pass",
                type(notify_exc).__name__, notify_exc,
            )
        else:
            paged = _page_delivered(results)
            if not paged:
                logger.warning(
                    "[PR_STALENESS] probe-failure page reached no channel (%s) — "
                    "retrying on the next pass", results,
                )
        if paged:
            episode["paged_at"] = now_utc.isoformat()
            episode["pages"] = _coerce_int(episode.get("pages"), 0) + 1

    await _write_failure_episode(pool, repo, episode)
    await _emit_audit_event(
        pool,
        "probe.pr_staleness_failed",
        detail,
        extra={
            "repo": repo,
            "signature": signature,
            "attempts": episode.get("attempts"),
            "failing_since": episode.get("since"),
            "page_reason": reason,
            "paged": paged,
        },
        severity="warning",
    )
    return {
        "ok": False,
        "status": "github_error",
        "stale_prs": 0,
        "alert_emitted": False,
        "pr_count_seen": pr_count_seen,
        "detail": detail,
        "failure_signature": signature,
        "failed_attempts": episode.get("attempts"),
        "failing_since": episode.get("since"),
        "page_reason": reason,
        "paged": paged,
    }


async def _close_failure_episode(
    pool: Any,
    *,
    repo: str,
    notify_fn: NotifyFn,
) -> bool:
    """End an open failure episode after a clean pass; True if one was open.

    Sends one recovery note — only when the episode reached the operator;
    an episode nobody was told about has nothing to take back.
    """
    episode = await _read_failure_episode(pool, repo)
    if not episode:
        return False
    await _clear_failure_episode(pool, repo)

    attempts = _coerce_int(episode.get("attempts"), 0)
    since = _fmt_utc(episode.get("since"))
    signature = episode.get("signature") or "unknown"
    note = (
        f"GitHub answered for {repo} again after {attempts} failed "
        f"attempt{'s' if attempts != 1 else ''} since {since} (last failure: "
        f"{signature}). Stale-PR checks resume on the normal cadence."
    )
    logger.info("[PR_STALENESS] recovered — %s", note)
    await _emit_audit_event(
        pool,
        "probe.pr_staleness_recovered",
        note,
        extra={
            "repo": repo,
            "signature": signature,
            "attempts": attempts,
            "failing_since": episode.get("since"),
            "was_paged": bool(episode.get("paged_at")),
        },
    )
    if episode.get("paged_at"):
        try:
            notify_fn(
                title=f"PR staleness probe recovered against {repo}",
                detail=note,
                source="brain.pr_staleness_probe",
                severity="info",
                dedup_key=f"pr_staleness_recovered:{repo}",
            )
        except Exception as notify_exc:  # noqa: BLE001
            logger.warning(
                "[PR_STALENESS] recovery note could not be delivered (%s: %s) — "
                "the operator still believes the probe is failing",
                type(notify_exc).__name__, notify_exc,
            )
    return True


async def run_pr_staleness_probe(
    pool: Any,
    *,
    now_fn: Callable[[], datetime] | None = None,
    notify_fn: NotifyFn | None = None,
    http_client_factory: HttpClientFactory | None = None,
) -> dict[str, Any]:
    """Single execution of the PR-staleness probe; returns a structured summary.

    Args:
        pool: asyncpg pool for app_settings + alert_events + alert_dedup_state.
        now_fn: ``() -> aware datetime`` — defaults to ``datetime.now(UTC)``.
            Tests inject a fixed clock so dedup math is deterministic.
        notify_fn: operator notifier callable. Defaults to
            ``brain.operator_notifier.notify_operator``. Used ONLY for
            the probe's own health — a failure page per episode (see
            "Failure episodes") and the recovery note that ends one.
            Stale PRs go out as a Discord-only ``alert_events`` row.
        http_client_factory: zero-arg callable returning an
            ``httpx.AsyncClient`` context manager — supplied by tests so
            they can inject a mock client without monkeypatching httpx.
    """
    now_fn = now_fn or (lambda: datetime.now(UTC))
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
            # A skipped cycle inherits the last real pass's verdict, so the
            # brain heartbeat keeps showing a broken probe as broken.
            failing = _state.get("failing_detail")
            detail = f"Within poll interval ({poll_interval_minutes} min) — skipped."
            if failing:
                detail += f" Last attempt failed: {failing}"
            return {
                "ok": failing is None,
                "status": "skipped_interval",
                "stale_prs": 0,
                "alert_emitted": False,
                "detail": detail,
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

    # Every real attempt advances the cadence gate, failed ones included, so
    # a persistent failure is retried once per poll interval rather than
    # every ~5-min brain cycle (branch_drift_probe does the same). Until
    # 2026-09-25 only a clean pass advanced it, which is how one bad token
    # became 286 attempts — and 286 pages — in a day.
    _state["last_real_pass_at"] = now_utc

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
        return await _handle_round_trip_failure(
            pool,
            exc,
            repo=repo,
            has_token=bool(token),
            now_utc=now_utc,
            config=config,
            notify_fn=notify_fn,
            pr_count_seen=pr_count_seen,
        )

    _state["failing_detail"] = None
    await _close_failure_episode(pool, repo=repo, notify_fn=notify_fn)

    if not stale_prs:
        summary: dict[str, Any] = {
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
        from poindexter.brain.probe_interface import ProbeResult

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
                    "failure_signature",
                    "failed_attempts",
                    "page_reason",
                    "paged",
                )
                if k in summary
            },
            severity="info" if summary.get("ok") else "warning",
        )

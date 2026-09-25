"""Branch-drift deploy canary (glad-labs-stack#942).

The worker/brain/pipeline-bot/prefect-worker containers bind-mount the
host checkout's source live, so prod runs whatever branch that checkout
sits on. When it's parked on a stale branch, merged work on origin/main
never reaches production — the drift that hid the #355 atom-cutover.

The existing migration-drift signals (services/metrics_exporter.py +
the worker /api/health migrations block) are structurally BLIND to this:
they compare on-disk migration files (the bind-mounted checkout) to
schema_migrations rows, so a migration that exists on origin/main but
not on the stale branch isn't even on disk to be counted.

This probe reads ground truth from OUTSIDE the bind-mount:

1. The running checkout's HEAD SHA from a read-only ``.git`` mount
   (``branch_drift_git_dir``, default ``/host-git``) via ``git rev-parse``.
   This is the one fact nothing else in a container can supply.
2. origin/main's SHA + the behind-count from the GitHub REST API
   (``branch_drift_repo``, authed with the ``gh_token`` secret — the repo
   is private). The mount is read-only on purpose: the probe never
   fetches inside the container.

Alert-only — it NEVER runs git pull/deploy itself (a checkout move can
clobber WIP or pull breaking changes mid-pipeline). It writes an
``alert_events`` row pointing at ``pwsh ./scripts/deploy-worker.ps1``.

When the canary itself cannot run, it says so once per failure episode
(``brain/failure_episode.py``, shared with the PR staleness probe):

* LOUD failures page when the episode opens. These are the ones only the
  operator can fix: a ``gh_token`` GitHub rejects (401), may not use (a 403
  that is not a rate limit) or that cannot see the private repo (a 404 on
  ``/commits/main``: GitHub answers 404, not 403, for a repo the token has no
  access to), plus a missing token, an unreadable ``.git`` mount or a
  missing httpx. They page again when the failure changes, when a replaced
  ``gh_token`` fails too, when the last page reached no channel, and every
  ``branch_drift_failure_repage_hours`` as a reminder. One recovery note
  follows on the first clean pass.
* QUIET failures (5xx, timeouts, DNS, rate limits) stay audit-only unless
  they last ``branch_drift_transient_failure_page_hours`` without a break.
  A canary blind for that long is news whatever the cause.

Until 2026-09-25 every GitHub error was audit-only. From 2026-09-23 23:37 UTC
the replaced ``gh_token`` could not see the repo, and the canary failed on
every pass (99 ``probe.branch_drift_failed`` rows on 09-24) with nobody told.
On 2026-08-17 it had been blind for about three hours on the same 404.

Design parity with brain/pr_staleness_probe.py: DB-configurable through
app_settings, standalone (stdlib + asyncpg + httpx + the git binary),
fail-loud per feedback_no_silent_defaults, and injectable seams
(``now_fn`` / ``http_client_factory`` / ``git_runner`` / ``notify_fn``)
plus a ``_reset_state()`` test hook.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from poindexter.brain import failure_episode
from poindexter.brain.github_errors import TOKEN_FIX, GitHubAPIError, github_message
from poindexter.brain.operator_notifier import notify_operator
from poindexter.brain.secret_reader import read_app_setting as _shared_read_app_setting

logger = logging.getLogger("brain.branch_drift_probe")


# ---------------------------------------------------------------------------
# app_settings keys — every tunable lives in the DB (operator-adjustable).
# Defaults match the seed migration.
# ---------------------------------------------------------------------------

ENABLED_KEY = "branch_drift_probe_enabled"
POLL_INTERVAL_MINUTES_KEY = "branch_drift_poll_interval_minutes"
REPO_KEY = "branch_drift_repo"
DEDUP_HOURS_KEY = "branch_drift_dedup_hours"
GIT_DIR_KEY = "branch_drift_git_dir"
MIN_COMMITS_BEHIND_KEY = "branch_drift_min_commits_behind"
FAILURE_REPAGE_HOURS_KEY = "branch_drift_failure_repage_hours"
TRANSIENT_FAILURE_PAGE_HOURS_KEY = "branch_drift_transient_failure_page_hours"

TOKEN_SETTING_KEY = "gh_token"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 15
DEFAULT_REPO = "Glad-Labs/glad-labs-stack"
DEFAULT_DEDUP_HOURS = 6
# Minimum commits-behind before a drift PAGES. A continuously-deploying prod is
# perpetually 1-2 commits behind origin/main (auto-deploy trails merges by
# minutes) — that transient lag is healthy steady state, not drift, and each
# deploy moved local_head so the per-head fingerprint churned and never
# deduped (#2295: 69 alerts/7d, 57 of them just "1 behind"). Only a meaningful
# backlog (>= this) signals a stuck / forgotten deploy; a genuinely stuck prod
# freezes local_head, so its fingerprint IS stable and dedup works as intended.
DEFAULT_MIN_COMMITS_BEHIND = 3
DEFAULT_GIT_DIR = "/host-git"
# Hours between reminders while the canary keeps failing (0 = never remind).
DEFAULT_FAILURE_REPAGE_HOURS = 24
# Hours a transient failure must last, unbroken, before the canary pages that
# it is blind (0 = never). Matches DEFAULT_DEDUP_HOURS: the canary may be
# blind for as long as it would stay quiet about an unchanged drift anyway.
DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS = 6

PROBE_INTERVAL_SECONDS = 5 * 60

HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_READ_TIMEOUT_S = 15.0
GIT_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Module-level state — cadence gate across cycles (reset on restart is fine;
# per-(head,main) dedup is persisted in alert_dedup_state and the failure
# episode in brain_knowledge, both restart-safe).
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "last_real_pass_at": None,
    # Operator-facing text of the last failed pass, None once a pass succeeds.
    # Cycles skipped by the cadence gate report it, so a broken canary does not
    # read as healthy on two brain cycles in three.
    "failing_detail": None,
}


def _reset_state() -> None:
    """Test hook — clear the cadence-gate memory."""
    _state["last_real_pass_at"] = None
    _state["failing_detail"] = None


# ---------------------------------------------------------------------------
# app_settings reads (direct asyncpg, mirrors brain/pr_staleness_probe).
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BRANCH_DRIFT] Could not read %s: %s — using default %r",
            key, exc, default,
        )
        return default
    return default if val is None else val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


async def _read_config(pool: Any) -> dict[str, Any]:
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED
    )
    poll_interval_minutes = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES),
        DEFAULT_POLL_INTERVAL_MINUTES,
    )
    dedup_hours = _coerce_int(
        await _read_setting(pool, DEDUP_HOURS_KEY, DEFAULT_DEDUP_HOURS),
        DEFAULT_DEDUP_HOURS,
    )
    min_commits_behind = _coerce_int(
        await _read_setting(pool, MIN_COMMITS_BEHIND_KEY, DEFAULT_MIN_COMMITS_BEHIND),
        DEFAULT_MIN_COMMITS_BEHIND,
    )
    repo = str(await _read_setting(pool, REPO_KEY, DEFAULT_REPO)).strip() or DEFAULT_REPO
    git_dir = str(await _read_setting(pool, GIT_DIR_KEY, DEFAULT_GIT_DIR)).strip() or DEFAULT_GIT_DIR
    # 0 is meaningful for both: never remind / never page a transient failure.
    failure_repage_hours = max(0, _coerce_int(
        await _read_setting(pool, FAILURE_REPAGE_HOURS_KEY, DEFAULT_FAILURE_REPAGE_HOURS),
        DEFAULT_FAILURE_REPAGE_HOURS,
    ))
    transient_failure_page_hours = max(0, _coerce_int(
        await _read_setting(
            pool, TRANSIENT_FAILURE_PAGE_HOURS_KEY, DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS,
        ),
        DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS,
    ))
    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "dedup_hours": dedup_hours,
        "min_commits_behind": max(1, min_commits_behind),
        "repo": repo,
        "git_dir": git_dir,
        "failure_repage_hours": failure_repage_hours,
        "transient_failure_page_hours": transient_failure_page_hours,
    }


async def _read_token(pool: Any) -> str:
    val = await _shared_read_app_setting(pool, TOKEN_SETTING_KEY, default="")
    if val:
        return str(val).strip()
    return os.getenv("GITHUB_TOKEN", "").strip()


# ---------------------------------------------------------------------------
# Local HEAD via the read-only .git mount (pure read, no network).
# ---------------------------------------------------------------------------


def _read_local_head(git_dir: str) -> tuple[str, str]:
    """Return (head_sha, branch_name) from the mounted git dir.

    Raises RuntimeError on any git failure (missing mount, not a repo,
    git binary absent) so the caller fails loud.
    """
    def _git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", "--git-dir", git_dir, *args],
                capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("git binary not on PATH in brain image") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"git {' '.join(args)} timed out") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} exit {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:200]}"
            )
        return (proc.stdout or "").strip()

    head = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not head:
        raise RuntimeError("git rev-parse HEAD returned empty")
    return head, branch


# ---------------------------------------------------------------------------
# GitHub REST client (thin). Default factory builds an authed httpx client.
# ---------------------------------------------------------------------------


def _default_client_factory(token: str):
    def _make():
        return httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Poindexter-BranchDriftProbe/1.0",
            },
            timeout=httpx.Timeout(HTTP_READ_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S),
        )
    return _make


async def _fetch_main_sha(client: Any, repo: str) -> str:
    r = await client.get(f"https://api.github.com/repos/{repo}/commits/main")
    if r.status_code != 200:
        raise GitHubAPIError.from_response("commits/main", r)
    data = r.json()
    sha = data.get("sha") if isinstance(data, dict) else None
    if not sha:
        raise RuntimeError("GitHub /commits/main returned no sha")
    return str(sha)


async def _compare_commits(client: Any, repo: str, base: str, head: str) -> dict[str, Any] | None:
    """Return the compare payload, or None when GitHub can't resolve the
    pair (404 — typically an unpushed local HEAD). Raise on other errors."""
    r = await client.get(f"https://api.github.com/repos/{repo}/compare/{base}...{head}")
    if r.status_code == 404:
        # Not a credential failure: /commits/main just answered with the same
        # token, so the token can see the repo. GitHub cannot resolve the pair.
        return None
    if r.status_code != 200:
        raise GitHubAPIError.from_response("compare", r)
    data = r.json()
    return data if isinstance(data, dict) else None


def _classify_drift(
    local_head: str,
    main_sha: str,
    compare: dict[str, Any] | None,
    *,
    min_behind: int = 1,
) -> dict[str, Any]:
    """Decide whether prod is behind origin/main.

    compare is GET /compare/{local_head}...{main_sha}: its ``ahead_by`` is
    the number of commits main has that local_head lacks = the behind count.
    None means GitHub couldn't resolve the pair (unpushed HEAD) -> drifted
    with an uncomputable count (real drift — pages regardless of ``min_behind``).

    ``min_behind`` (default 1 = page on any lag; prod default 3) is the smallest
    computable backlog that counts as drift. A smaller lag is reported as
    ``branch_status="within_deploy_lag"`` (``drifted=False``) so the probe logs
    it but never pages — see DEFAULT_MIN_COMMITS_BEHIND / #2295.
    """
    if local_head == main_sha:
        return {"drifted": False, "behind": 0, "branch_status": "on_main"}
    if compare is None:
        return {"drifted": True, "behind": None, "branch_status": "unknown_head"}
    behind = compare.get("ahead_by")
    behind = int(behind) if isinstance(behind, int) else 0
    if behind >= max(1, min_behind):
        return {"drifted": True, "behind": behind, "branch_status": compare.get("status", "diverged")}
    if behind > 0:
        # A small lag: prod is catching up (auto-deploy trails merges). Log, don't page.
        return {"drifted": False, "behind": behind, "branch_status": "within_deploy_lag"}
    # Differing SHAs but main is not ahead -> prod is ahead (unmerged local
    # work) or identical. Not "behind" — don't page.
    return {"drifted": False, "behind": 0, "branch_status": compare.get("status", "ahead")}


# ---------------------------------------------------------------------------
# Dedup (alert_dedup_state) — per (repo, local_head) fingerprint.
#
# main_sha is deliberately NOT in the key: the local checkout's HEAD is the
# stable identity of the "still behind" condition. origin/main advancing means
# prod is *further* behind, but it's the same drift event and must not reset
# the dedup window — else every new commit to main re-pages (Glad-Labs/
# glad-labs-stack#1105: 33 alerts/24h). The window resets naturally when the
# operator deploys (local_head moves).
# ---------------------------------------------------------------------------


def _fingerprint_for(repo: str, local_head: str) -> str:
    return f"branch_drift_{repo}_{local_head[:12]}"


async def _is_deduped(pool: Any, *, fingerprint: str, now_utc: datetime, dedup_hours: int) -> bool:
    if dedup_hours <= 0:
        return False
    try:
        row = await pool.fetchrow(
            "SELECT last_seen_at FROM alert_dedup_state WHERE fingerprint = $1",
            fingerprint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BRANCH_DRIFT] dedup lookup failed for %s: %s — treating as not "
            "deduped (branch-drift alert may re-fire)",
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


async def _record_dedup(pool: Any, *, fingerprint: str, now_utc: datetime, message: str) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO alert_dedup_state (
                fingerprint, first_seen_at, last_seen_at, repeat_count,
                severity, source, sample_message
            ) VALUES ($1, $2, $2, 1, 'warning', 'brain.branch_drift_probe', $3)
            ON CONFLICT (fingerprint) DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                repeat_count = alert_dedup_state.repeat_count + 1
            """,
            fingerprint, now_utc, message[:300],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BRANCH_DRIFT] dedup upsert failed for %s: %s", fingerprint, exc)


# ---------------------------------------------------------------------------
# audit_log + alert_events writes.
# ---------------------------------------------------------------------------


async def _emit_audit_event(pool: Any, event: str, detail: str, *, extra: dict[str, Any] | None = None, severity: str = "info") -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ($1, $2, $3::jsonb, $4)",
            event, "brain.branch_drift_probe", json.dumps(payload), severity,
        )
    except Exception as exc:  # noqa: BLE001
        # silent-ok: mirror only. Drift itself is alerted by
        # _emit_drift_alert (its own alert_events row -> alert_dispatcher),
        # and a probe.branch_drift_failed event rides on the failure episode
        # (brain_knowledge), which pages the operator whenever the failure is
        # news — so every load-bearing event here has an independent path to
        # the operator.
        logger.debug("[BRANCH_DRIFT] audit_log insert failed: %s", exc)


async def _emit_drift_alert(pool: Any, *, repo: str, branch: str, local_head: str, main_sha: str, behind: int | None) -> bool:
    behind_txt = f"{behind} commit(s) behind" if behind is not None else "behind (count unknown — HEAD not on origin)"
    alertname = f"branch_drift_{repo.replace('/', '_')}"
    labels = {
        "source": "brain.branch_drift_probe",
        "category": "branch_drift",
        "repo": repo,
        "branch": branch,
    }
    annotations = {
        "summary": f"prod checkout is {behind_txt} origin/main in {repo}",
        "description": (
            f"\U0001F7E1 [branch-drift] The running checkout is on '{branch}' "
            f"@ {local_head[:9]}, {behind_txt} origin/main @ {main_sha[:9]}.\n"
            f"Merged work is NOT deployed. Bring prod to main with:\n"
            f"    pwsh ./scripts/deploy-worker.ps1"
        ),
        "local_head": local_head,
        "main_sha": main_sha,
    }
    # main_sha intentionally omitted — see _fingerprint_for: the stale local
    # HEAD is the drift identity; main advancing must not mint a new key.
    fingerprint = f"branch-drift-{alertname}-{local_head[:12]}"
    try:
        await pool.execute(
            "INSERT INTO alert_events (alertname, severity, status, labels, "
            "annotations, starts_at, fingerprint) VALUES "
            "($1, 'warning', 'firing', $2::jsonb, $3::jsonb, NOW(), $4)",
            alertname, json.dumps(labels), json.dumps(annotations), fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BRANCH_DRIFT] failed to write firing alert %s: %s", alertname, exc)
        return False


# ---------------------------------------------------------------------------
# When the canary itself fails: page once per episode, not once per pass.
#
# The episode mechanism is brain/failure_episode.py, shared with the PR
# staleness probe. What is specific to this canary is which failures are LOUD
# (page on their own) and which are QUIET (audit-only unless they last
# branch_drift_transient_failure_page_hours) and what the page says.
# ---------------------------------------------------------------------------

FAILURE_STATE_ENTITY = "branch_drift_probe"
_SOURCE = "brain.branch_drift_probe"
# What the canary needs the gh_token to grant, quoted by every credential page.
_NEEDS = "Contents (read)"


def _failure_key(repo: str) -> failure_episode.EpisodeKey:
    """Where the open failure episode for ``repo`` lives in brain_knowledge."""
    return failure_episode.EpisodeKey(
        entity=FAILURE_STATE_ENTITY,
        attribute=f"failure_episode:{repo}",
        label="BRANCH_DRIFT",
    )


def _quiet_page_after(config: dict[str, Any]) -> timedelta | None:
    hours = int(config["transient_failure_page_hours"])
    return timedelta(hours=hours) if hours > 0 else None


def _describe_github_failure(
    exc: BaseException, *, repo: str, poll_interval_minutes: int,
) -> tuple[str, str, bool]:
    """Map a failed GitHub round-trip to ``(signature, detail, loud)``.

    LOUD means a credential or configuration problem that only the operator
    can fix: a 401, a 403 that is not a rate limit, a 404 on ``/commits/main``
    (the token cannot see the repo), and any other 3xx/4xx, which a retry will
    not change. QUIET means a retry can fix it: 5xx, rate limits, and anything
    that is not an HTTP answer at all (timeout, DNS, a reset connection).

    ``signature`` is the failure's identity for the episode. It groups by
    what the operator would do about it, so flapping between 502 and 503 is
    one failure.
    """
    retry = f"The canary retries every {poll_interval_minutes} min."
    if not isinstance(exc, GitHubAPIError):
        name = type(exc).__name__
        # str() of an httpx timeout is the EMPTY string; name the class alone.
        msg = str(exc).strip()
        what = f"{name}: {msg[:200]}" if msg else name
        return name, (
            f"{what}. The canary's GitHub round-trip for {repo} did not "
            f"complete, usually a network or GitHub blip. {retry}"
        ), False

    endpoint, status = exc.endpoint, exc.status_code
    said = github_message(exc.body)
    if exc.rate_limited:
        return f"{endpoint}:rate-limited", (
            f"GitHub rate-limited the canary on /{endpoint} (HTTP {status}: "
            f"{said}). {retry} If this persists, another gh_token consumer is "
            f"spending the budget."
        ), False
    if status >= 500:
        return f"{endpoint}:5xx", (
            f"GitHub /{endpoint} for {repo} returned {status}, a GitHub-side "
            f"error. {retry}"
        ), False
    if status == 401:
        return f"{endpoint}:401", (
            f"GitHub rejected the gh_token (HTTP 401: {said}). It is invalid, "
            f"expired or revoked. The branch-drift canary needs a token with "
            f"{_NEEDS} on {repo}. Replace it with {TOKEN_FIX}."
        ), True
    if status == 404 and endpoint == "commits/main":
        return "commits/main:404", (
            f"The gh_token cannot see {repo}, check its scopes. GitHub answers "
            f"404, not 403, for a private repo the token has no access to, so "
            f"unless app_settings.{REPO_KEY} is misspelled, the token is the "
            f"problem. The branch-drift canary needs {_NEEDS} on {repo}: a "
            f"fine-grained token with {repo} in its repository access, or the "
            f"classic `repo` scope. Rotate it with {TOKEN_FIX}."
        ), True
    if status == 403:
        return f"{endpoint}:403", (
            f"The gh_token may not read {repo} (HTTP 403: {said}), check its "
            f"scopes. The branch-drift canary needs {_NEEDS} on {repo}. If the "
            f"organization enforces SAML single sign-on, the token must also be "
            f"authorized for it. Rotate it with {TOKEN_FIX}."
        ), True
    if 300 <= status < 400:
        return f"{endpoint}:3xx", (
            f"GitHub redirected /{endpoint} for {repo} (HTTP {status}), so the "
            f"repo was renamed or transferred. Set app_settings.{REPO_KEY} to "
            f"its new owner/name."
        ), True
    return f"{endpoint}:{status}", (
        f"GitHub /{endpoint} for {repo} returned HTTP {status}: {said}. A retry "
        f"will not change that answer. Check app_settings.{REPO_KEY} and the "
        f"gh_token."
    ), True


def _anticipated(exc: BaseException | None) -> bool:
    """True for failures whose message says it all (no traceback needed)."""
    if exc is None or isinstance(exc, RuntimeError):  # incl. GitHubAPIError
        return True
    return httpx is not None and isinstance(exc, httpx.HTTPError)


def _build_failure_page(
    *,
    repo: str,
    reason: str,
    detail: str,
    episode: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, str]:
    """Render ``(title, body)`` for a failure page."""
    if reason == failure_episode.PAGE_REMINDER:
        title = f"Branch-drift canary still cannot run against {repo}"
    else:
        title = f"Branch-drift canary cannot run against {repo}"
    lines = [
        detail,
        "",
        "Until it runs, prod falling behind origin/main goes unnoticed.",
    ]
    lines += failure_episode.episode_lines(
        episode,
        reason=reason,
        retry_minutes=int(config["poll_interval_minutes"]),
        repage_hours=int(config["failure_repage_hours"]),
        repage_setting_key=FAILURE_REPAGE_HOURS_KEY,
        credential_key=TOKEN_SETTING_KEY,
        quiet_page_after=_quiet_page_after(config),
    )
    return title, "\n".join(lines)


async def _handle_failure(
    pool: Any,
    *,
    signature: str,
    detail: str,
    loud: bool,
    repo: str,
    now_utc: datetime,
    config: dict[str, Any],
    notify_fn: Callable[..., Any],
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """Record a failed pass; page only when the episode says it is news."""
    logger.warning(
        "[BRANCH_DRIFT] canary failed (%s, %s): %s",
        signature, "loud" if loud else "transient", detail,
        exc_info=not _anticipated(exc),
    )
    _state["failing_detail"] = detail
    outcome = await failure_episode.record_failure(
        pool,
        _failure_key(repo),
        signature=signature,
        detail=detail,
        now_utc=now_utc,
        notify_fn=notify_fn,
        render=lambda episode, reason: _build_failure_page(
            repo=repo, reason=reason, detail=detail, episode=episode, config=config,
        ),
        source=_SOURCE,
        dedup_prefix=f"branch_drift_failed:{repo}",
        loud=loud,
        quiet_page_after=_quiet_page_after(config),
        repage_hours=int(config["failure_repage_hours"]),
        credential_key=TOKEN_SETTING_KEY,
    )
    episode = outcome.episode
    await _emit_audit_event(
        pool,
        "probe.branch_drift_failed",
        detail,
        extra={
            "repo": repo,
            "signature": signature,
            "transient": not loud,
            "attempts": episode.get("attempts"),
            "failing_since": episode.get("since"),
            "page_reason": outcome.reason,
            "paged": outcome.paged,
        },
        severity="warning",
    )
    return {
        "ok": False,
        "status": "failed",
        "behind": None,
        "alert_emitted": False,
        "detail": detail,
        "failure_signature": signature,
        "transient": not loud,
        "failed_attempts": episode.get("attempts"),
        "failing_since": episode.get("since"),
        "page_reason": outcome.reason,
        "paged": outcome.paged,
    }


async def _close_failure_episode(
    pool: Any,
    *,
    repo: str,
    config: dict[str, Any],
    notify_fn: Callable[..., Any],
) -> None:
    """End an open failure episode after a clean round-trip.

    Sends one recovery note, and only when the episode reached the operator.
    An episode nobody was told about has nothing to take back.
    """
    episode = await failure_episode.close_episode(pool, _failure_key(repo))
    if not episode:
        return
    note = (
        f"The branch-drift canary is checking {repo} again "
        f"{failure_episode.recovery_summary(episode)}. Drift checks resume "
        f"every {config['poll_interval_minutes']} min."
    )
    logger.info("[BRANCH_DRIFT] recovered: %s", note)
    await _emit_audit_event(
        pool,
        "probe.branch_drift_recovered",
        note,
        extra={
            "repo": repo,
            "signature": episode.get("signature") or "unknown",
            "attempts": episode.get("attempts"),
            "failing_since": episode.get("since"),
            "was_paged": bool(episode.get("paged_at")),
        },
    )
    if episode.get("paged_at"):
        failure_episode.send_page(
            notify_fn,
            label="BRANCH_DRIFT",
            title=f"Branch-drift canary running again against {repo}",
            detail=note,
            source=_SOURCE,
            severity="info",
            dedup_key=f"branch_drift_recovered:{repo}",
            if_undelivered="the operator still believes the canary is blind",
        )


# ---------------------------------------------------------------------------
# Top-level probe entry point.
# ---------------------------------------------------------------------------


async def run_branch_drift_probe(
    pool: Any,
    *,
    now_fn: Callable[[], datetime] | None = None,
    notify_fn: Callable[..., Any] | None = None,
    http_client_factory: Callable[..., Any] | None = None,
    git_runner: Callable[[str], tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """One execution of the branch-drift canary; returns a structured summary."""
    now_fn = now_fn or (lambda: datetime.now(UTC))
    notify_fn = notify_fn or notify_operator
    git_runner = git_runner or _read_local_head

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "behind": 0, "alert_emitted": False,
                "detail": f"branch-drift probe disabled (app_settings.{ENABLED_KEY}=false)"}

    now_utc = now_fn()
    repo = config["repo"]

    # Cadence gate — only do the real round-trip every poll_interval_minutes.
    last = _state["last_real_pass_at"]
    if isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if (now_utc - last) < timedelta(minutes=config["poll_interval_minutes"]):
            # A skipped cycle inherits the last real pass's failure, so the
            # brain heartbeat keeps showing a broken canary as broken.
            failing = _state["failing_detail"]
            detail = "within poll interval"
            if failing:
                detail += f"; last attempt failed: {failing}"
            return {"ok": failing is None, "status": "skipped", "behind": 0,
                    "alert_emitted": False, "detail": detail}

    # Every real attempt advances the cadence gate, so a persistent failure
    # is retried once per poll interval rather than every brain cycle (~5 min).
    _state["last_real_pass_at"] = now_utc

    async def _fail(
        signature: str, detail: str, *, loud: bool, exc: BaseException | None = None,
    ) -> dict[str, Any]:
        # A canary that can't run must not fail silently
        # (feedback_no_silent_defaults), and must not page every pass either:
        # the episode decides when the failure is news.
        return await _handle_failure(
            pool, signature=signature, detail=detail, loud=loud, repo=repo,
            now_utc=now_utc, config=config, notify_fn=notify_fn, exc=exc,
        )

    token = await _read_token(pool)
    if not token:
        return await _fail(
            "no-token",
            f"gh_token is not set, so the branch-drift canary cannot query the "
            f"private repo {repo}. It needs a token with {_NEEDS} on {repo}. Set "
            f"it with {TOKEN_FIX}.",
            loud=True,
        )

    # Local HEAD (mounted .git, no network).
    try:
        local_head, branch = await asyncio.to_thread(git_runner, config["git_dir"])
    except Exception as exc:  # noqa: BLE001
        return await _fail(
            "git-head",
            f"could not read the running checkout's HEAD from "
            f"{config['git_dir']}: {exc}. Check the read-only .git mount on the "
            f"brain-daemon container "
            f"(`${{POINDEXTER_DEPLOY_ROOT:-.}}/.git:/host-git:ro`) and "
            f"app_settings.{GIT_DIR_KEY}.",
            loud=True,
            exc=exc,
        )

    # origin/main truth + compare (GitHub API).
    if http_client_factory is None and httpx is None:
        return await _fail(
            "no-httpx",
            "httpx is not installed in the brain image, so the canary cannot "
            "query GitHub. Rebuild the brain image.",
            loud=True,
        )
    factory = http_client_factory or _default_client_factory(token)
    try:
        async with factory() as client:
            main_sha = await _fetch_main_sha(client, repo)
            compare = None
            if local_head != main_sha:
                compare = await _compare_commits(client, repo, local_head, main_sha)
    except Exception as exc:  # noqa: BLE001
        signature, detail, loud = _describe_github_failure(
            exc, repo=repo, poll_interval_minutes=int(config["poll_interval_minutes"]),
        )
        return await _fail(signature, detail, loud=loud, exc=exc)

    # The canary ran: whatever the verdict, it is not blind any more.
    _state["failing_detail"] = None
    await _close_failure_episode(pool, repo=repo, config=config, notify_fn=notify_fn)

    verdict = _classify_drift(
        local_head, main_sha, compare, min_behind=config["min_commits_behind"]
    )
    if not verdict["drifted"]:
        if verdict["branch_status"] == "within_deploy_lag":
            # Below the paging threshold — a transient deploy lag, not drift.
            # Log it (so the lag is visible in audit_log / dashboards) but never
            # page and never write an alert_events row (#2295).
            await _emit_audit_event(
                pool, "probe.branch_drift_ok",
                f"within deploy lag: HEAD {local_head[:9]} is {verdict['behind']} "
                f"behind origin/main {main_sha[:9]} "
                f"(< {config['min_commits_behind']} min_commits_behind — not paging)",
            )
            return {"ok": True, "status": "within_deploy_lag", "behind": verdict["behind"],
                    "alert_emitted": False, "branch": branch, "local_head": local_head,
                    "main_sha": main_sha, "detail": "within deploy lag — not paging"}
        await _emit_audit_event(
            pool, "probe.branch_drift_ok",
            f"on main: HEAD {local_head[:9]} == origin/main {main_sha[:9]}",
        )
        return {"ok": True, "status": "no_drift", "behind": 0, "alert_emitted": False,
                "branch": branch, "local_head": local_head, "main_sha": main_sha,
                "detail": "checkout matches origin/main"}

    behind = verdict["behind"]
    behind_txt = f"{behind} behind" if behind is not None else "behind (uncomputable)"
    detail = f"branch '{branch}' @ {local_head[:9]} is {behind_txt} origin/main @ {main_sha[:9]}"

    fingerprint = _fingerprint_for(config["repo"], local_head)
    if await _is_deduped(pool, fingerprint=fingerprint, now_utc=now_utc, dedup_hours=config["dedup_hours"]):
        logger.info("[BRANCH_DRIFT] drift unchanged (%s) — dedup-suppressed", fingerprint)
        return {"ok": False, "status": "drift_detected", "behind": behind, "alert_emitted": False,
                "branch": branch, "local_head": local_head, "main_sha": main_sha, "detail": detail}

    await _emit_audit_event(pool, "probe.branch_drift_detected", detail,
                            extra={"branch": branch, "behind": behind}, severity="warning")
    emitted = await _emit_drift_alert(
        pool, repo=config["repo"], branch=branch, local_head=local_head,
        main_sha=main_sha, behind=behind,
    )
    if emitted:
        await _record_dedup(pool, fingerprint=fingerprint, now_utc=now_utc, message=detail)

    return {"ok": False, "status": "drift_detected", "behind": behind, "alert_emitted": emitted,
            "branch": branch, "local_head": local_head, "main_sha": main_sha, "detail": detail}

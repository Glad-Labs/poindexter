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

Design parity with brain/pr_staleness_probe.py: DB-configurable through
app_settings, standalone (stdlib + asyncpg + httpx + the git binary),
fail-loud per feedback_no_silent_defaults, and injectable seams
(``now_fn`` / ``http_client_factory`` / ``git_runner`` / ``notify_fn``)
plus a ``_reset_state()`` test hook.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

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

TOKEN_SETTING_KEY = "gh_token"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 15
DEFAULT_REPO = "Glad-Labs/poindexter"
DEFAULT_DEDUP_HOURS = 6
DEFAULT_GIT_DIR = "/host-git"

PROBE_INTERVAL_SECONDS = 5 * 60

HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_READ_TIMEOUT_S = 15.0
GIT_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Module-level state — cadence gate across cycles (reset on restart is fine;
# per-(head,main) dedup is persisted in alert_dedup_state for restart-safety).
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {"last_real_pass_at": None}


def _reset_state() -> None:
    """Test hook — clear the cadence-gate memory."""
    _state["last_real_pass_at"] = None


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
    repo = str(await _read_setting(pool, REPO_KEY, DEFAULT_REPO)).strip() or DEFAULT_REPO
    git_dir = str(await _read_setting(pool, GIT_DIR_KEY, DEFAULT_GIT_DIR)).strip() or DEFAULT_GIT_DIR
    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "dedup_hours": dedup_hours,
        "repo": repo,
        "git_dir": git_dir,
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
        raise RuntimeError(
            f"GitHub /commits/main returned {r.status_code}: {(r.text or '')[:200]}"
        )
    data = r.json()
    sha = data.get("sha") if isinstance(data, dict) else None
    if not sha:
        raise RuntimeError("GitHub /commits/main returned no sha")
    return str(sha)


async def _compare_commits(client: Any, repo: str, base: str, head: str) -> Optional[dict[str, Any]]:
    """Return the compare payload, or None when GitHub can't resolve the
    pair (404 — typically an unpushed local HEAD). Raise on other errors."""
    r = await client.get(f"https://api.github.com/repos/{repo}/compare/{base}...{head}")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(
            f"GitHub /compare returned {r.status_code}: {(r.text or '')[:200]}"
        )
    data = r.json()
    return data if isinstance(data, dict) else None


def _classify_drift(local_head: str, main_sha: str, compare: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Decide whether prod is behind origin/main.

    compare is GET /compare/{local_head}...{main_sha}: its ``ahead_by`` is
    the number of commits main has that local_head lacks = the behind count.
    None means GitHub couldn't resolve the pair (unpushed HEAD) -> drifted
    with an uncomputable count.
    """
    if local_head == main_sha:
        return {"drifted": False, "behind": 0, "branch_status": "on_main"}
    if compare is None:
        return {"drifted": True, "behind": None, "branch_status": "unknown_head"}
    behind = compare.get("ahead_by")
    behind = int(behind) if isinstance(behind, int) else 0
    if behind > 0:
        return {"drifted": True, "behind": behind, "branch_status": compare.get("status", "diverged")}
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
        logger.debug("[BRANCH_DRIFT] dedup lookup failed for %s: %s", fingerprint, exc)
        return False
    if not row:
        return False
    last_seen = row["last_seen_at"]
    if not isinstance(last_seen, datetime):
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
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


async def _emit_audit_event(pool: Any, event: str, detail: str, *, extra: Optional[dict[str, Any]] = None, severity: str = "info") -> None:
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
        logger.debug("[BRANCH_DRIFT] audit_log insert failed: %s", exc)


async def _emit_drift_alert(pool: Any, *, repo: str, branch: str, local_head: str, main_sha: str, behind: Optional[int]) -> bool:
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
# Top-level probe entry point.
# ---------------------------------------------------------------------------


async def run_branch_drift_probe(
    pool: Any,
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    notify_fn: Optional[Callable[..., Any]] = None,
    http_client_factory: Optional[Callable[..., Any]] = None,
    git_runner: Optional[Callable[[str], tuple[str, str]]] = None,
) -> dict[str, Any]:
    """One execution of the branch-drift canary; returns a structured summary."""
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    notify_fn = notify_fn or notify_operator
    git_runner = git_runner or _read_local_head

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "behind": 0, "alert_emitted": False,
                "detail": f"branch-drift probe disabled (app_settings.{ENABLED_KEY}=false)"}

    now_utc = now_fn()

    # Cadence gate — only do the real round-trip every poll_interval_minutes.
    last = _state["last_real_pass_at"]
    if isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now_utc - last) < timedelta(minutes=config["poll_interval_minutes"]):
            return {"ok": True, "status": "skipped", "behind": 0, "alert_emitted": False,
                    "detail": "within poll interval"}

    # Every real attempt advances the cadence gate — so a persistent failure
    # (bad token, broken .git mount) pages at most once per poll interval
    # rather than every brain cycle (~5 min).
    _state["last_real_pass_at"] = now_utc

    async def _fail(detail: str, *, page: bool = False) -> dict[str, Any]:
        logger.warning("[BRANCH_DRIFT] %s", detail)
        await _emit_audit_event(pool, "probe.branch_drift_failed", detail, severity="warning")
        # Page the operator on CONFIGURATION failures (missing token, broken
        # .git mount) — a canary that can't run must not fail silently
        # (feedback_no_silent_defaults). Transient GitHub errors stay
        # audit-only (page=False) to avoid blip noise.
        if page:
            try:
                notify_fn(
                    title="Branch-drift canary cannot run",
                    detail=detail,
                    source="brain.branch_drift_probe",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BRANCH_DRIFT] notify_fn failed: %s", exc)
        return {"ok": False, "status": "failed", "behind": None, "alert_emitted": False, "detail": detail}

    token = await _read_token(pool)
    if not token:
        return await _fail(
            f"gh_token missing — cannot query private repo {config['repo']}. "
            f"Set it with `poindexter set gh_token <token>`.",
            page=True,
        )

    # Local HEAD (mounted .git, no network).
    try:
        local_head, branch = git_runner(config["git_dir"])
    except Exception as exc:  # noqa: BLE001
        return await _fail(
            f"could not read local HEAD from {config['git_dir']}: {exc}", page=True
        )

    # origin/main truth + compare (GitHub API).
    if http_client_factory is None and httpx is None:
        return await _fail("httpx not installed in brain image — cannot query GitHub", page=True)
    factory = http_client_factory or _default_client_factory(token)
    try:
        async with factory() as client:
            main_sha = await _fetch_main_sha(client, config["repo"])
            compare = None
            if local_head != main_sha:
                compare = await _compare_commits(client, config["repo"], local_head, main_sha)
    except Exception as exc:  # noqa: BLE001
        return await _fail(f"GitHub API error for {config['repo']}: {exc}")

    verdict = _classify_drift(local_head, main_sha, compare)
    if not verdict["drifted"]:
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

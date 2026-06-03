"""FindingsAlertRouterJob — bridge ``audit_log`` findings to ``alert_events``.

Closes the long-standing silent-route gap described in
``utils/findings.py``'s docstring:

    "no automatic delivery. The audit_log row IS the finding. Triage
    manually via SQL until the dispatcher lands."

Captured 2026-05-15: ``emit_finding(severity='critical')`` calls have
been writing to ``audit_log`` for months — 108 critical findings in the
last 7 days alone — but the brain's ``alert_dispatcher`` polls
``alert_events``, not ``audit_log``, so none of those criticals ever
reached an operator. The intended ``critical -> Telegram`` /
``warn -> Discord`` routing matrix (per
``feedback_telegram_vs_discord``) was a no-op.

This job is the missing bridge. Every minute it:

1. Reads the persisted watermark
   (``app_settings.findings_alert_route_watermark`` — the highest
   ``audit_log.id`` we've already routed).
2. Selects up to 200 new ``severity in ('warn','warning','critical')``
   findings with ``id > watermark``.
3. Inserts one ``alert_events`` row per finding using a stable
   fingerprint derived from ``details->>'dedup_key'`` (falling back to
   ``source:kind``).
4. Advances the watermark.

The brain's existing ``alert_dispatcher`` then handles delivery AND
dedup — its ``alert_dedup_state`` table will collapse repeated fires
of the same fingerprint into one operator page + suppressed counter,
so a chronic finding (e.g. ``media_drift`` every 15 min) doesn't spam
the operator. See ``brain/alert_dispatcher.py``.

Severity normalization: ``warn`` -> ``warning`` so the dispatcher's
severity matrix matches its existing routing tables (the codebase has
both shapes — Prometheus convention is ``warning``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

from plugins.job import JobResult
from services.jobs.fix_broken_external_links import FixBrokenExternalLinksJob
from services.jobs.fix_broken_internal_links import FixBrokenInternalLinksJob
from services.jobs.fix_uncategorized_posts import FixUncategorizedPostsJob
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# How many findings to forward per cycle. With the bridge running every
# 60s a backlog of thousands clears in minutes, so cap each cycle to
# bound the transaction time. The watermark guarantees no row is
# processed twice.
_BATCH_LIMIT = 200

# Watermark key. Stored in app_settings as a stringified bigint so the
# operator can inspect / reset it from psql without touching code.
_WATERMARK_KEY = "findings_alert_route_watermark"

# audit_log.severity values that should be routed to operators. ``info``
# stays out of alert_events — it's queryable via SQL but doesn't page.
_ROUTABLE_SEVERITIES = ("warn", "warning", "critical")

# Severity ordering for per-kind min_severity gating. emit_finding uses
# 'warn'; alert_events/Prometheus use 'warning' — _normalize_severity
# collapses them, but rank both so a raw value never KeyErrors.
_SEV_RANK = {"info": 0, "warn": 1, "warning": 1, "critical": 2}


async def _load_policies(pool: Any) -> dict[str, dict[str, str]]:
    """Load every findings.<kind>.<field> app_setting into {kind: {field: value}}.

    The router consults per-kind policies ONLY — it deliberately does NOT
    fold in findings.default.* (an unlisted kind must stay loud, never be
    silently suppressed by a default of log_only; that is exactly the
    silent-drop the alert audit eliminated)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key LIKE 'findings.%.%'"
        )
    policies: dict[str, dict[str, str]] = {}
    for r in rows:
        parts = r["key"].split(".")
        # findings.<kind>.<field>
        if len(parts) != 3:
            continue
        _, kind, field = parts
        if not kind or kind == "default":
            continue  # empty/default keys are not per-kind policies
        policies.setdefault(kind, {})[field] = r["value"]
    return policies


def _issue_labels_for(kind: str, policies: dict[str, dict[str, str]]) -> list[str]:
    """Per-kind issue labels from findings.<kind>.labels, split + stripped.

    Returns [] when unset — the caller still stamps the `finding` marker.
    Content-derived (the kind), never a default priority/milestone."""
    raw = (policies.get(kind) or {}).get("labels", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _delivery_for(kind: str, severity: str, policies: dict[str, dict[str, str]]) -> str:
    """Resolve the delivery action for one finding.

    Returns one of: 'route' (insert alert_events -> dispatcher picks
    channel by severity), 'log_only' (suppress paging), 'telegram',
    'discord', 'auto_fix', 'github_issue'. Named channels ('telegram',
    'discord') pass through so callers can distinguish per-kind routing
    from the dispatcher's default severity matrix. Unrecognised delivery
    values also pass through (fail loud at the consumer, not silently here).

    Invariants preserved from the prior _should_route:
    - A kind with NO policy => 'route' (stay loud).
    - A 'critical' finding NEVER resolves to log_only (misconfig => route).
    - Otherwise gate on per-kind min_severity (default 'warning')."""
    pol = policies.get(kind)
    if pol is None:
        return "route"

    sev = _normalize_severity(severity)
    delivery = pol.get("delivery", "route")

    if sev == "critical":
        return "route" if delivery == "log_only" else delivery

    min_sev = _normalize_severity(pol.get("min_severity", "warning"))
    if min_sev not in _SEV_RANK:
        logger.warning(
            "[findings_alert_router] kind=%s has unrecognised min_severity "
            "%r; treating as 'warning'", kind, min_sev,
        )
        min_sev = "warning"
    if _SEV_RANK.get(sev, 0) < _SEV_RANK[min_sev]:
        return "log_only"
    return delivery


# kind -> auto-fix Job class. Only kinds with a REAL fix job belong here.
# A kind with delivery='auto_fix' but no entry falls back to its
# `fallback` channel (route) — never silently dropped. missing_seo is
# intentionally absent (flag-only today; falls back to github_issue).
_AUTOFIX_JOBS: dict[str, Any] = {
    "broken_external_link": FixBrokenExternalLinksJob,
    "broken_internal_link": FixBrokenInternalLinksJob,
    "uncategorized_post": FixUncategorizedPostsJob,
}


async def _dispatch_auto_fix(
    pool: Any, config: dict[str, Any], finding: dict[str, Any], kind: str
) -> bool:
    """Run the mapped auto-fix job for an auto_fix finding.

    Returns True if the fix job ran ok (finding considered handled).
    Returns False if no job is mapped or the job raised / returned
    not-ok, so the caller applies the fallback channel. Emits an
    info-level ``<kind>_autofixed`` follow-up finding recording the
    outcome (seeded policy maps it to log_only)."""
    job_cls = _AUTOFIX_JOBS.get(kind)
    if job_cls is None:
        return False
    try:
        result = await job_cls().run(pool, config)
    except Exception as exc:  # job protocol says don't raise, but be safe
        logger.warning(
            "[findings_alert_router] auto_fix job %s raised for "
            "audit_log.id=%s: %s", kind, finding.get("id"), exc,
        )
        return False
    emit_finding(
        source="findings_alert_router",
        kind=f"{kind}_autofixed",
        title=f"auto-fix ran for {kind}",
        body=(
            f"Triggered {job_cls.__name__} from finding "
            f"audit_log.id={finding.get('id')}: ok={result.ok} "
            f"changes_made={result.changes_made} — {result.detail}"
        ),
        severity="info",
        dedup_key=f"autofixed:{kind}:{finding.get('id')}",
    )
    return result.ok


_FINDINGS_ISSUE_REPO = "Glad-Labs/poindexter"  # private — operator findings


async def _dispatch_github_issue(
    finding: dict[str, Any], kind: str, labels: list[str] | None = None
) -> bool:
    """File a GitHub issue for a finding via the `gh` CLI.

    Returns True if the issue was created OR a same-title open issue
    already exists (dedup). Returns False if `gh` is unavailable/unauth
    or the create failed, so the caller applies the fallback channel.

    Operational dependency: the worker needs `gh` on PATH and
    authenticated. Absent that, findings fall back to a page — loud, not
    silent."""
    if shutil.which("gh") is None:
        logger.warning("[findings_alert_router] `gh` not on PATH; cannot file %s", kind)
        return False

    details = finding.get("details") or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = {}
    title = details.get("title") or f"{kind} finding"
    body = (details.get("body") or "")[:60000]

    # Dedup: is there already an open issue with this exact title?
    list_proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "list", "--repo", _FINDINGS_ISSUE_REPO,
        "--state", "open", "--search", title, "--json", "title",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(list_proc.communicate(), timeout=30.0)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("[findings_alert_router] `gh issue list` timed out for %s", kind)
        try:
            list_proc.kill()
        except ProcessLookupError:
            # `gh` already exited between the timeout and kill() — benign race.
            pass
        return False
    if list_proc.returncode == 0:
        try:
            existing = json.loads(out or b"[]")
            if any(i.get("title") == title for i in existing):
                logger.info("[findings_alert_router] dup issue exists: %r", title)
                return True
        except json.JSONDecodeError:
            pass  # fall through to create

    # Always include the `finding` marker; add any kind-derived labels.
    # gh fails the whole create on an unknown label, so the labels MUST exist
    # in _FINDINGS_ISSUE_REPO (see the findings.<kind>.labels seeds + the
    # `finding` label provisioned in glad-labs-stack).
    label_args: list[str] = []
    for lbl in ["finding", *(labels or [])]:
        if lbl and lbl not in (label_args[1::2] if label_args else []):
            label_args += ["--label", lbl]

    create_proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "create", "--repo", _FINDINGS_ISSUE_REPO,
        "--title", title, "--body", body, *label_args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(create_proc.communicate(), timeout=30.0)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("[findings_alert_router] `gh issue create` timed out for %s", kind)
        try:
            create_proc.kill()
        except ProcessLookupError:
            # `gh` already exited between the timeout and kill() — benign race.
            pass
        return False
    if create_proc.returncode != 0:
        logger.warning(
            "[findings_alert_router] `gh issue create` failed for %s: %s",
            kind, (err or b"").decode(errors="replace")[:500],
        )
        return False
    return True


async def _deliver_fallback(
    pool: Any, finding: dict[str, Any], kind: str, fallback: str
) -> bool:
    """Apply a policy's fallback when the primary channel could not act.

    Returns True if the finding was actually routed to alert_events,
    False if the fallback was a no-op (fallback='log_only' — the finding
    stays in audit_log only). Any fallback other than log_only routes."""
    if fallback == "log_only":
        logger.info(
            "[findings_alert_router] %s primary failed; fallback=log_only "
            "for audit_log.id=%s", kind, finding.get("id"),
        )
        return False
    force_channel = fallback if fallback in ("telegram", "discord") else None
    await _insert_alert_event(pool, finding, force_channel=force_channel)
    return True


def _normalize_severity(raw: str) -> str:
    """Map emit_finding severities to the Prometheus convention used by
    alert_events. ``warn`` -> ``warning`` so the dispatcher's severity
    matrix matches; ``critical`` stays as-is. Anything else passes through
    so we don't lose information (the dispatcher logs unknown severities)."""
    s = (raw or "").strip().lower()
    if s == "warn":
        return "warning"
    return s


def _build_fingerprint(source: str, details: dict[str, Any]) -> str:
    """Stable identifier for the alert_dispatcher's dedup engine.

    Prefer the caller-provided ``dedup_key`` (per ``emit_finding``'s
    contract — already designed for cross-fire stability). Fall back to
    ``source:kind`` which is stable per kind of finding but coarser.
    """
    dk = details.get("dedup_key")
    if dk:
        return f"finding:{source}:{dk}"
    kind = details.get("kind") or "unknown"
    return f"finding:{source}:{kind}"


def _build_alertname(source: str, details: dict[str, Any]) -> str:
    """Human-readable alertname. The dispatcher uses this in Discord /
    Telegram embeds. Keeping ``source:kind`` keeps the operator's mental
    model 1-to-1 with the audit_log row."""
    kind = details.get("kind") or "finding"
    return f"{source}:{kind}"


async def _read_watermark(pool: Any) -> int:
    """Fetch the last-routed audit_log.id. Returns 0 if missing or
    unparseable (fresh install / corrupted row — the bridge will replay
    everything from the start, which is what we want)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1",
            _WATERMARK_KEY,
        )
    if row is None or not row["value"]:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        logger.warning(
            "[findings_alert_router] watermark %r unparseable; resetting to 0",
            row["value"],
        )
        return 0


async def _write_watermark(pool: Any, new_id: int) -> None:
    """Advance the watermark via UPSERT. Idempotent on the same id."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, updated_at)
            VALUES ($1, $2, 'plugin_telemetry', $3, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
            """,
            _WATERMARK_KEY,
            str(new_id),
            (
                "Highest audit_log.id already forwarded to alert_events by "
                "FindingsAlertRouterJob. Operators can reset to 0 to replay."
            ),
        )


async def _fetch_unrouted_findings(pool: Any, watermark: int) -> list[dict[str, Any]]:
    """Pull up to ``_BATCH_LIMIT`` un-routed findings above the watermark."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, source, severity, details
            FROM audit_log
            WHERE event_type = 'finding'
              AND severity = ANY($1::text[])
              AND id > $2
            ORDER BY id ASC
            LIMIT $3
            """,
            list(_ROUTABLE_SEVERITIES),
            watermark,
            _BATCH_LIMIT,
        )
    return [dict(r) for r in rows]


def _finding_kind(finding: dict[str, Any]) -> str:
    """Extract the finding ``kind`` from a row's details JSON (or 'unknown')."""
    details_raw = finding.get("details") or {}
    if isinstance(details_raw, str):
        try:
            details_raw = json.loads(details_raw)
        except json.JSONDecodeError:
            return "unknown"
    if not isinstance(details_raw, dict):
        return "unknown"
    return details_raw.get("kind") or "unknown"


async def _insert_alert_event(
    pool: Any, finding: dict[str, Any], force_channel: str | None = None
) -> None:
    """Insert one ``alert_events`` row mirroring the existing probe
    patterns in ``brain/mcp_http_probe.py`` and friends. Dispatcher takes
    over from here — picks channel by severity, dedup by fingerprint.

    ``force_channel`` (the per-kind ``findings.<kind>.delivery`` value when
    it names a channel — ``telegram`` / ``discord``) is stamped into the
    ``labels`` JSON so ``brain/alert_dispatcher._channels_for`` can honor
    the per-kind delivery policy instead of routing purely by severity.
    ``None`` (the 'route' / auto_fix-fallback default) leaves the label
    out, so the dispatcher's severity matrix decides — unchanged behavior
    for every kind that doesn't pin a channel."""
    details_raw = finding.get("details") or {}
    if isinstance(details_raw, str):
        try:
            details_raw = json.loads(details_raw)
        except json.JSONDecodeError:
            details_raw = {}
    if not isinstance(details_raw, dict):
        details_raw = {}

    source = finding["source"] or "unknown"
    alertname = _build_alertname(source, details_raw)
    severity = _normalize_severity(finding.get("severity") or "info")
    fingerprint = _build_fingerprint(source, details_raw)

    labels_dict: dict[str, Any] = {
        "source": source,
        "kind": details_raw.get("kind") or "finding",
        "audit_log_id": finding["id"],
    }
    if force_channel:
        labels_dict["force_channel"] = force_channel
    labels = json.dumps(labels_dict)
    annotations = json.dumps({
        "summary": details_raw.get("title") or alertname,
        "description": (details_raw.get("body") or "")[:4000],
    })

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alert_events (
                alertname, status, severity, category,
                labels, annotations, fingerprint
            ) VALUES (
                $1, 'firing', $2, 'finding',
                $3::jsonb, $4::jsonb, $5
            )
            """,
            alertname,
            severity,
            labels,
            annotations,
            fingerprint,
        )


class FindingsAlertRouterJob:
    """Periodic bridge: ``audit_log`` findings -> ``alert_events``."""

    name = "findings_alert_router"
    schedule = "every 60 seconds"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        watermark = await _read_watermark(pool)
        rows = await _fetch_unrouted_findings(pool, watermark)
        if not rows:
            return JobResult(
                ok=True,
                detail=f"no new findings above watermark {watermark}",
                changes_made=0,
            )

        policies = await _load_policies(pool)
        routed = autofixed = filed = suppressed = errors = 0
        max_id = watermark
        first_failed_id: int | None = None

        for r in rows:
            kind = _finding_kind(r)
            severity = r.get("severity") or "info"
            delivery = _delivery_for(kind, severity, policies)
            fallback = (policies.get(kind) or {}).get("fallback", "route")

            try:
                if delivery == "log_only":
                    suppressed += 1
                elif delivery == "auto_fix":
                    if await _dispatch_auto_fix(pool, config, r, kind):
                        autofixed += 1
                    elif await _deliver_fallback(pool, r, kind, fallback):
                        routed += 1
                    else:
                        suppressed += 1
                elif delivery == "github_issue":
                    labels = _issue_labels_for(kind, policies)
                    if await _dispatch_github_issue(r, kind, labels):
                        filed += 1
                    elif await _deliver_fallback(pool, r, kind, fallback):
                        routed += 1
                    else:
                        suppressed += 1
                else:  # 'route' / 'telegram' / 'discord'
                    # telegram/discord pin the channel via a force_channel
                    # label honored by brain/alert_dispatcher; 'route' leaves
                    # it None so the dispatcher's severity matrix decides.
                    force_channel = (
                        delivery if delivery in ("telegram", "discord") else None
                    )
                    await _insert_alert_event(pool, r, force_channel=force_channel)
                    routed += 1
            except Exception as exc:
                errors += 1
                if first_failed_id is None:
                    first_failed_id = int(r["id"])
                logger.warning(
                    "[findings_alert_router] delivery=%s failed for "
                    "audit_log.id=%s: %s", delivery, r.get("id"), exc,
                )
                continue  # do NOT advance the watermark past this row
            max_id = max(max_id, int(r["id"]))

        # Never advance the watermark past the FIRST failed row, even if
        # later rows succeeded — otherwise that finding (possibly critical)
        # is skipped forever. The cost is re-delivering the already-succeeded
        # rows after it next cycle, which is acceptable (alerts/issues dedupe)
        # versus silently dropping a finding.
        if first_failed_id is not None:
            max_id = min(max_id, first_failed_id - 1)

        if max_id > watermark:
            await _write_watermark(pool, max_id)

        return JobResult(
            ok=errors == 0,
            detail=(
                f"routed {routed}, auto-fixed {autofixed}, filed {filed}, "
                f"suppressed {suppressed}, {errors} error(s); "
                f"watermark {watermark} -> {max_id}"
            ),
            changes_made=routed + autofixed + filed,
        )

"""Alert dispatcher — brain-side outbound for ``alert_events``.

Implements the brain's half of the unified alerting flow described in
Glad-Labs/poindexter#340 (Phase 0 of brain-as-DECIDER):

    Grafana ─► Poindexter Webhook ─► alert_events (dispatched_at IS NULL)
              │                       │
              │   (worker, persist    │   (brain, this module,
              │    only — does not    │    poll → notify_operator → mark)
              │    dispatch)          ▼
              │                      Telegram (critical)
              │                      Discord  (warning/info)
              ▼
        webhook handler

Why split persistence from dispatch?

The worker's webhook handler used to do both inline. That coupled the
operator's pager to the worker's uptime — when the worker crashed
mid-alert the page silently disappeared. Moving dispatch into the
brain gives us:

1. **Durability.** ``alert_events`` rows are the source of truth; the
   brain re-attempts on restart simply by polling ``WHERE dispatched_at
   IS NULL``. No alert is lost to a worker restart between persist and
   dispatch.
2. **Single source of routing.** Every notification (Grafana alerts +
   future brain-internal alerts) goes through one queue with one
   formatter. Operators have one place to look when "did the alert
   actually fire?" comes up.
3. **Decider readiness.** Phase A in #340 swaps this module's "always
   page" body for an LLM triage step that classifies + remediates.
   The poll/mark machinery here is reusable as-is — only the
   "what to do with each row" function changes.

Cadence: 30 seconds. Faster than the 5-min health cycle because
alerts are time-sensitive (an operator paged 4 minutes after Postgres
fell over might as well not have been paged), but slower than the
webhook itself (we don't want to hammer the DB while idle). The brain
daemon already runs the 5-min cycle; this module gets its own loop
in ``brain_daemon.start_alert_dispatcher_loop`` so neither blocks the
other.

Failure posture:

- ``notify_operator`` raising → the row is marked
  ``dispatched_at = NOW(), dispatch_result = 'error: <message>'``.
  We never retry inside this module — by the time we surface a
  failure to the row, the alert has been seen by the dispatcher and
  recorded. Re-delivery is the operator's call (clear ``dispatched_at``
  and the next poll picks it up).
- DB error during the poll → logged + swallowed; the loop continues.
  The brain's existing watchdog will detect a stuck cycle if poll
  errors persist.
- The brain-side ``notify()`` returns ``True`` only when at least
  one channel (Telegram or Discord) actually accepted the message.
  This module's ``_adapter`` wraps that bool into a ``NotifyFailed``
  exception so a downed Telegram + missing Discord webhook surfaces
  as ``dispatch_result = 'error: notify returned False'`` rather
  than a phantom ``'sent'`` (the bug Glad-Labs/poindexter#342
  diagnosed: dispatcher claimed sent=N, operator got nothing).
- The worker-side ``notify_operator`` (when reachable) is best-effort
  and swallows transport errors internally — its success/failure
  contract isn't a bool. Per-channel failures from that path show up
  in the worker's own logs; the dispatcher records ``'sent'`` because
  the call returned without raising.

Imports kept cheap on purpose:

The brain's pyproject.toml is intentionally minimal (asyncpg, httpx,
pyyaml — see ``brain/pyproject.toml``). The worker-side
``services.integrations.operator_notify`` module pulls in a chunk of
the FastAPI/cofounder closure, so importing it directly from the
brain image (where ``services/`` isn't on the path) would fail.
We import lazily inside the dispatch function and fall back to the
brain's own ``notify`` helper (Telegram + Discord ops webhook) when
the framework path isn't reachable. v1 acceptable per the issue
discussion; cleanup tracked in #340.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import urllib.error
import urllib.request
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("brain.alert_dispatcher")


class NotifyFailed(RuntimeError):
    """Raised when ``notify`` reported zero channels accepted the message.

    Caught by ``poll_and_dispatch`` — the row gets marked
    ``dispatch_result = 'error: <reason>'`` and the cycle continues.
    Surfaced as its own type so tests can assert on it without
    string-matching exception messages.
    """


# ---------------------------------------------------------------------------
# Triage retry / network constants. Defaults match the seeded
# app_settings rows from migration 20260506_052451 (#347 step 1) but the
# dispatcher reads the live values per call so an operator can tune
# without restarting the brain.
# ---------------------------------------------------------------------------

_DEFAULT_TRIAGE_RETRY_MAX = 3
_DEFAULT_TRIAGE_BACKOFF_SECONDS: tuple[float, ...] = (10.0, 30.0, 90.0)
# Brain → worker HTTP timeout. Conservative — model_router calls can take
# several seconds on a cold model load; we don't want the brain to give
# up while the worker is mid-Ollama-stream.
_TRIAGE_HTTP_TIMEOUT_SECONDS = 30.0
# Status codes that should NOT trigger a retry. 503 means the tier has
# no provider (config issue); 402 means the cost guard denied the call
# (also config). 401 is auth misconfiguration. Retrying these would
# burn budget chasing the same failure on every cycle.
_TRIAGE_NO_RETRY_STATUSES: frozenset[int] = frozenset({401, 402, 503})


# ---------------------------------------------------------------------------
# Message formatting — copied verbatim from
# routes/alertmanager_webhook_routes.py::_format_alert_message at the
# commit that moved dispatch out of the worker. Keeping a literal copy
# here (instead of importing) preserves brain-side image isolation —
# the brain image doesn't ship the worker's services/ tree.
# ---------------------------------------------------------------------------


def _format_alert_message(alert: dict[str, Any]) -> str:
    """Render a compact, human-readable Telegram/Discord message.

    Copied from routes/alertmanager_webhook_routes.py so the brain
    image stays decoupled from the worker codebase. If you change one,
    change both — there's an integration test in the worker side that
    exercises the worker's helper, and a unit test in
    tests/unit/brain/test_alert_dispatcher.py that exercises this one.
    """
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    alertname = labels.get("alertname", "UnknownAlert")
    severity = labels.get("severity") or "info"
    status = (alert.get("status") or "firing").upper()
    summary = annotations.get("summary", "")
    description = (annotations.get("description") or "").strip()

    header = f"[{status} · {severity}] {alertname}"
    if summary:
        header = f"{header} — {summary}"
    if description:
        return f"{header}\n\n{description}"
    return header


def _row_to_alert_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Reshape an ``alert_events`` row into the dict shape the formatter
    expects (the one Alertmanager sends in its webhook payload).

    Columns: alertname, status, severity, category, labels (jsonb),
             annotations (jsonb).
    Reshapes to: {status, labels: {alertname, severity, category, ...},
                  annotations: {...}}.
    """
    import json as _json

    labels: dict[str, Any] = {}
    annotations: dict[str, Any] = {}
    raw_labels = row.get("labels")
    raw_annotations = row.get("annotations")
    if isinstance(raw_labels, str):
        try:
            labels = _json.loads(raw_labels) or {}
        except (ValueError, TypeError):
            labels = {}
    elif isinstance(raw_labels, dict):
        labels = dict(raw_labels)
    if isinstance(raw_annotations, str):
        try:
            annotations = _json.loads(raw_annotations) or {}
        except (ValueError, TypeError):
            annotations = {}
    elif isinstance(raw_annotations, dict):
        annotations = dict(raw_annotations)

    # Ensure the canonical fields the formatter reads from labels are
    # present even if the legacy persist code didn't copy them into the
    # JSONB blob.
    labels.setdefault("alertname", row.get("alertname") or "UnknownAlert")
    if row.get("severity") and "severity" not in labels:
        labels["severity"] = row["severity"]
    if row.get("category") and "category" not in labels:
        labels["category"] = row["category"]

    return {
        "status": row.get("status") or "firing",
        "labels": labels,
        "annotations": annotations,
    }


# ---------------------------------------------------------------------------
# Notify resolution — try worker-side framework first, fall back to the
# brain's own Telegram/Discord helpers.
# ---------------------------------------------------------------------------


# Type for the notify callable: takes (message, *, critical) and returns
# an awaitable that resolves to the brain.notify dict (with
# ``telegram_message_id`` / ``discord_message_id`` keys) or ``None`` for
# legacy (worker-side) notifiers that don't surface message ids. Defined
# as a Callable so tests can inject their own without monkeypatching the
# import path.
NotifyFn = Callable[..., Awaitable[Optional[dict[str, Any]]]]


async def _resolve_notify_fn(pool: Any = None) -> Optional[NotifyFn]:
    """Return a coroutine notify function, or None if nothing is reachable.

    Order:
    1. ``services.integrations.operator_notify.notify_operator`` — the
       canonical worker-side dispatcher. Available when the brain runs
       in-process with the worker (rare) or when the worker's source
       tree is on the brain image's PYTHONPATH (acknowledged code
       smell, see module docstring).
    2. Brain's own ``brain.brain_daemon.notify`` — direct Telegram +
       Discord ops webhook. Best-effort, has been the brain's
       always-on path since day one.
    3. None — neither path is reachable. Caller logs and marks the
       row with the resolution failure so the operator sees it in
       ``alert_events.dispatch_result``.

    ``pool`` is forwarded to the brain.notify branch so the secrets it
    lazily fetches (per Glad-Labs/poindexter#344) hit the same DB the
    dispatcher polled. Tests can pass ``None`` and the adapter falls
    back to the cross-instance pool registry inside ``brain.notify``.
    """
    try:
        from services.integrations.operator_notify import notify_operator  # type: ignore
        return notify_operator
    except Exception as e:  # noqa: BLE001 — narrow imports later
        logger.debug(
            "[alert_dispatcher] worker notify_operator unavailable: %s "
            "— falling back to brain.notify", e,
        )

    # The brain daemon imports this module and has its own notify()
    # function. Both flat (`import brain_daemon`) and package-qualified
    # (`from brain import brain_daemon`) imports are supported because
    # the Dockerfile mirrors brain/ files into both /app and /app/brain/.
    brain_daemon_mod = sys.modules.get("brain_daemon") or sys.modules.get("brain.brain_daemon")
    if brain_daemon_mod is None:
        try:
            import brain_daemon as brain_daemon_mod  # type: ignore  # noqa: F811
        except ImportError:
            try:
                from brain import brain_daemon as brain_daemon_mod  # type: ignore  # noqa: F811
            except ImportError:
                brain_daemon_mod = None

    if brain_daemon_mod is not None and hasattr(brain_daemon_mod, "notify"):
        notify_callable = brain_daemon_mod.notify

        async def _adapter(message: str, *, critical: bool = False) -> Optional[dict[str, Any]]:
            # critical is a no-op for the brain helper — it always sends
            # to both Telegram and Discord ops. Severity is encoded in
            # the message header itself, which is enough for Matt to
            # triage on his phone.
            del critical
            # Glad-Labs/poindexter#344: brain.notify is now async and
            # accepts ``pool=`` so it can lazy-fetch the Telegram +
            # Discord secrets via ``read_app_setting``. We thread the
            # dispatcher's pool in so the secret read hits the right DB
            # and dodges the module-instance landmine the old global
            # cache fell into.
            #
            # #347 step 5: brain.notify now returns a dict carrying the
            # per-channel message ids so the firefighter follow-up can
            # quote-reply the same Telegram thread. Legacy/worker-side
            # notifiers and old test stubs may still return ``None`` or
            # a ``bool`` — we normalise here.
            result = notify_callable(message, pool=pool) if pool is not None else notify_callable(message)
            if hasattr(result, "__await__"):
                value = await result
            else:
                value = result
            # The brain notify returns ``{ok, telegram_message_id,
            # discord_message_id}``. ``ok=False`` means the operator did
            # NOT receive the alert (no token, malformed URL, all
            # transports down). Raise so poll_and_dispatch's try/except
            # marks the row with an honest ``dispatch_result = 'error: ...'``
            # instead of silently recording ``'sent'`` while the page
            # vanished into a black hole — Glad-Labs/poindexter#342.
            if value is False:  # legacy bool path
                raise NotifyFailed(
                    "brain.notify reported no channel accepted the message "
                    "(check telegram_bot_token, telegram_chat_id, "
                    "discord_ops_webhook_url in app_settings)"
                )
            if isinstance(value, dict) and value.get("ok") is False:
                raise NotifyFailed(
                    "brain.notify reported no channel accepted the message "
                    "(check telegram_bot_token, telegram_chat_id, "
                    "discord_ops_webhook_url in app_settings)"
                )
            # Surface the dict so _dispatch_one can hand it to _triage_one
            # for follow-up threading. Legacy stubs returning None / True
            # bypass triage threading — the diagnosis still posts but as a
            # standalone message.
            if isinstance(value, dict):
                return value
            return None

        return _adapter

    return None


# ---------------------------------------------------------------------------
# DB poll + dispatch
# ---------------------------------------------------------------------------


_POLL_SQL = """
SELECT id, alertname, status, severity, category, labels, annotations
FROM alert_events
WHERE dispatched_at IS NULL
ORDER BY id ASC
LIMIT $1
"""

_MARK_SENT_SQL = """
UPDATE alert_events
SET dispatched_at = NOW(), dispatch_result = 'sent'
WHERE id = $1
"""

_MARK_ERROR_SQL = """
UPDATE alert_events
SET dispatched_at = NOW(), dispatch_result = $2
WHERE id = $1
"""


async def poll_and_dispatch(
    pool: Any,
    *,
    batch_size: int = 50,
    notify_fn: Optional[NotifyFn] = None,
) -> dict[str, int]:
    """Poll undispatched ``alert_events`` rows and notify the operator.

    Args:
        pool: asyncpg-style pool with ``.fetch()`` / ``.execute()``.
        batch_size: Maximum rows per cycle. 50 is generous — Grafana
            typically batches alert pushes, so a single cycle handles
            a whole evaluation interval's worth even on a busy alerting
            day.
        notify_fn: Override for the notify resolution step. Tests pass
            an AsyncMock here; production leaves it None and the
            module resolves a real callable on its own.

    Returns:
        Dict with counts: ``{'polled': N, 'sent': N, 'errors': N}``.
        Useful for the brain cycle log + future Prometheus metric.

    Best-effort: any exception in the poll itself is logged and an
    empty result is returned. Per-row failures get marked on the row
    with ``dispatch_result = 'error: ...'`` and counted toward
    ``errors`` — the loop continues with the next row.
    """
    summary = {"polled": 0, "sent": 0, "errors": 0}

    try:
        rows = await pool.fetch(_POLL_SQL, batch_size)
    except Exception as e:  # noqa: BLE001
        # Most likely cause: migration 0137 hasn't run yet, so the
        # dispatched_at column doesn't exist. Log once at warning so
        # the operator knows; subsequent cycles fail the same way
        # silently (debug only) until the column appears.
        msg = str(e)
        if "dispatched_at" in msg or "column" in msg.lower():
            logger.warning(
                "[alert_dispatcher] poll failed (likely migration 0137 "
                "pending): %s", msg,
            )
        else:
            logger.warning("[alert_dispatcher] poll failed: %s", msg)
        return summary

    if not rows:
        return summary

    summary["polled"] = len(rows)

    if notify_fn is None:
        notify_fn = await _resolve_notify_fn(pool=pool)

    if notify_fn is None:
        # Mark every polled row with a clear error so operators see it
        # in ``alert_events.dispatch_result`` instead of a phantom
        # silent failure. Without this, rows would stay
        # ``dispatched_at IS NULL`` forever and the cycle would re-pick
        # them every 30s — an infinite log spam loop.
        err = "error: no notify channel reachable (no worker, no brain.notify)"
        for row in rows:
            try:
                await pool.execute(_MARK_ERROR_SQL, row["id"], err)
                summary["errors"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[alert_dispatcher] failed to mark row %s as errored: %s",
                    row.get("id"), e,
                )
        logger.warning(
            "[alert_dispatcher] no notify channel reachable — marked "
            "%d rows as errored", summary["errors"],
        )
        return summary

    # _triage_tasks accumulates parallel triage tasks scheduled this
    # cycle so the dispatcher can short-poll them in tests / one-shot
    # mode. In production the loop runs forever; orphaned tasks finish
    # in the background and log their own outcome. We attach them to a
    # name so the asyncio debug tooling shows what's running.
    _triage_tasks: list[asyncio.Task[Any]] = []
    triage_enabled = await _read_triage_enabled(pool)

    for row in rows:
        notify_result = await _dispatch_one(pool, row, notify_fn, summary)
        # Schedule the parallel triage task — never awaited inline so the
        # operator's page never waits on the LLM (the spec's hard NO).
        # Triage is scheduled regardless of notify outcome so a flaky
        # Telegram doesn't block the LLM analysis the operator wants
        # for diagnosing the OTHER half of the failure. When notify
        # fails the parent message ids are absent and the follow-up
        # falls back to a standalone diagnosis send (still useful).
        if triage_enabled:
            try:
                task = asyncio.create_task(
                    _triage_one(pool, row, notify_result or {}),
                    name=f"triage_one_{row.get('id')}",
                )
                _triage_tasks.append(task)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[alert_dispatcher] failed to schedule triage for row %s: %s",
                    row.get("id"), e,
                )

    if summary["sent"] or summary["errors"]:
        logger.info(
            "[alert_dispatcher] cycle: polled=%d sent=%d errors=%d "
            "triage_scheduled=%d",
            summary["polled"], summary["sent"], summary["errors"],
            len(_triage_tasks),
        )
    return summary


async def _dispatch_one(
    pool: Any,
    row: Any,
    notify_fn: NotifyFn,
    summary: dict[str, int],
) -> Optional[dict[str, Any]]:
    """Notify the operator about ONE alert row + mark it dispatched.

    Returns the notify_fn result dict (carrying ``telegram_message_id`` /
    ``discord_message_id``) on success so the caller can hand it to the
    parallel triage task. Returns ``None`` on failure — the row is
    already marked errored before we return.

    Failure isolation: any exception from the notify path is caught,
    logged, and recorded on the row. Callers MUST NOT raise from this
    helper — the loop in ``poll_and_dispatch`` is best-effort and a
    single bad row can't take the dispatcher down.
    """
    row_id = row["id"]
    try:
        alert = _row_to_alert_dict(dict(row))
        severity = (alert.get("labels") or {}).get("severity") or ""
        critical = severity.lower() == "critical"
        message = _format_alert_message(alert)
        notify_result = await notify_fn(message, critical=critical)
        await pool.execute(_MARK_SENT_SQL, row_id)
        summary["sent"] += 1
        # The adapter normalises legacy ``None`` / ``True`` returns to
        # ``None`` here; only the new dict shape (#347 step 5) flows
        # through to triage. Legacy notifiers still get the raw alert,
        # they just lose the threading on the follow-up.
        return notify_result if isinstance(notify_result, dict) else None
    except Exception as e:  # noqa: BLE001
        err_msg = f"error: {str(e)[:400]}"
        try:
            await pool.execute(_MARK_ERROR_SQL, row_id, err_msg)
        except Exception as mark_err:  # noqa: BLE001
            # If we can't even mark the row, log and move on — the
            # next cycle will pick it up again. This is the only
            # path that can cause re-delivery, and it's gated on a
            # second DB failure so the surface is small.
            logger.warning(
                "[alert_dispatcher] failed to mark row %s after "
                "dispatch error %r: %s", row_id, e, mark_err,
            )
        summary["errors"] += 1
        logger.warning(
            "[alert_dispatcher] dispatch failed for row %s: %s",
            row_id, e,
        )
        return None


# ---------------------------------------------------------------------------
# Triage path — Glad-Labs/poindexter#347 step 4. Parallel asyncio task
# spawned per dispatched row; POSTs to the worker's /api/triage
# endpoint, retries transient errors per app_settings, sends the
# diagnosis as a follow-up reply when the LLM produces one.
# ---------------------------------------------------------------------------


async def _read_triage_enabled(pool: Any) -> bool:
    """Read the master kill-switch from app_settings.

    Defaults to ``True`` so a fresh install with the migration applied
    gets enrichment automatically. Returns ``False`` on any read error
    (don't enrich if we can't even read the setting — fail-closed).
    """
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "ops_triage_enabled",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[alert_dispatcher] could not read ops_triage_enabled "
            "(treating as disabled): %s", e,
        )
        return False
    if value is None:
        return True  # missing row = default ON (matches seed migration)
    return str(value).strip().lower() in ("true", "1", "yes", "on")


async def _read_triage_retry_config(pool: Any) -> tuple[int, list[float]]:
    """Read retry max + backoff list from app_settings.

    Returns ``(max_attempts, [backoff_seconds, ...])``. Both fields
    fall back to module-level defaults when the rows are missing or
    unparseable. ``len(backoff)`` is padded / trimmed to match
    ``max_attempts`` so a misconfigured pair doesn't crash the loop.
    """
    max_attempts = _DEFAULT_TRIAGE_RETRY_MAX
    backoff: list[float] = list(_DEFAULT_TRIAGE_BACKOFF_SECONDS)
    try:
        max_value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "ops_triage_retry_max",
        )
        if max_value is not None:
            max_attempts = max(1, int(str(max_value).strip()))
    except Exception as e:  # noqa: BLE001
        logger.debug("[alert_dispatcher] ops_triage_retry_max parse failed: %s", e)
    try:
        backoff_value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "ops_triage_retry_backoff_seconds",
        )
        if backoff_value:
            parsed = json.loads(backoff_value)
            if isinstance(parsed, list):
                backoff = [float(x) for x in parsed if x is not None]
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[alert_dispatcher] ops_triage_retry_backoff_seconds parse failed: %s", e
        )
    # Pad / trim to match max_attempts so the loop never index-errors.
    if len(backoff) < max_attempts:
        last = backoff[-1] if backoff else _DEFAULT_TRIAGE_BACKOFF_SECONDS[-1]
        backoff.extend([last] * (max_attempts - len(backoff)))
    elif len(backoff) > max_attempts:
        backoff = backoff[:max_attempts]
    return max_attempts, backoff


async def _read_api_base_url(pool: Any) -> str:
    """Resolve the worker base URL the brain should POST /api/triage to.

    Matches the existing ``api_base_url`` setting (seeded by
    ``brain/seed_app_settings.json`` to ``http://worker:8002`` — the
    canonical worker URL inside the brain container).
    """
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "api_base_url",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[alert_dispatcher] api_base_url read failed: %s", e)
        return ""
    return (value or "").strip().rstrip("/")


async def _mint_oauth_token(pool: Any, base_url: str) -> Optional[str]:
    """Mint a brain OAuth JWT via the existing brain.oauth_client helper.

    The helper does its own caching (~30 s skew) so calling per-triage
    is cheap. Returns ``None`` when OAuth credentials aren't configured;
    the caller logs and skips triage in that case rather than firing
    unauthenticated calls at the worker.
    """
    try:
        try:
            from oauth_client import oauth_client_from_pool  # flat import
        except ImportError:  # pragma: no cover — package-qualified path
            from brain.oauth_client import oauth_client_from_pool
        client = await oauth_client_from_pool(pool, base_url=base_url)
        if not client.using_oauth:
            await client.aclose()
            return None
        token = await client.get_token()
        await client.aclose()
        return token
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[alert_dispatcher] OAuth token mint for triage failed: %s", e
        )
        return None


def _post_triage_sync(
    url: str, payload: bytes, token: str, timeout: float
) -> tuple[int, bytes]:
    """Synchronous urllib POST helper. Caller wraps in ``to_thread``.

    Brain stays on the stdlib + asyncpg + urllib triad (see
    ``brain/pyproject.toml``). Returns ``(status_code, body_bytes)``.
    Network / DNS failures raise; HTTP error responses (4xx/5xx) come
    back through the ``HTTPError`` branch and are mapped to a tuple so
    the caller can branch on ``_TRIAGE_NO_RETRY_STATUSES`` cleanly.
    """
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "PoinDexterBrain-Triage/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        # Read the body so the caller can surface the worker's reason
        # in the audit log / follow-up message.
        body = b""
        try:
            body = e.read()
        except Exception:  # noqa: BLE001
            pass
        return e.code, body


async def _send_triage_followup(
    diagnosis: str,
    notify_result: dict[str, Any],
    pool: Any,
) -> None:
    """Post the diagnosis as a follow-up to the original notify message.

    Resolves :func:`brain.brain_daemon.send_followup` lazily so the
    dispatcher module stays decoupled from the brain daemon's import
    side effects. Threads on Telegram's ``reply_to_message_id`` and
    Discord's ``message_reference`` when the parent ids are present.
    Skipped silently when the diagnosis is empty (per the spec's
    "LLM returned empty -> brain skips the follow-up entirely" row).
    """
    if not diagnosis:
        return
    brain_daemon_mod = sys.modules.get("brain_daemon") or sys.modules.get("brain.brain_daemon")
    if brain_daemon_mod is None:
        try:
            import brain_daemon as brain_daemon_mod  # type: ignore  # noqa: F811
        except ImportError:
            try:
                from brain import brain_daemon as brain_daemon_mod  # type: ignore  # noqa: F811
            except ImportError:
                logger.warning(
                    "[alert_dispatcher] brain_daemon unavailable — "
                    "cannot send triage follow-up"
                )
                return
    if not hasattr(brain_daemon_mod, "send_followup"):
        logger.warning(
            "[alert_dispatcher] brain_daemon.send_followup missing — "
            "the brain image needs the #347 step 5 patch applied"
        )
        return
    try:
        await brain_daemon_mod.send_followup(
            diagnosis,
            parent_telegram_message_id=notify_result.get("telegram_message_id"),
            parent_discord_message_id=notify_result.get("discord_message_id"),
            pool=pool,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[alert_dispatcher] send_followup raised: %s — diagnosis "
            "produced but not delivered", e,
        )


async def _triage_one(
    pool: Any,
    row: Any,
    notify_result: dict[str, Any],
    *,
    sleep_fn: Optional[Callable[[float], Awaitable[None]]] = None,
) -> Optional[dict[str, Any]]:
    """Run firefighter triage for ONE alert row in the background.

    Posts to the worker's ``/api/triage`` endpoint, retries transient
    errors per ``ops_triage_retry_max`` / ``ops_triage_retry_backoff_seconds``,
    then sends a follow-up reply with the diagnosis text via
    :func:`brain.brain_daemon.send_followup`.

    Args:
        pool: asyncpg pool — used for app_settings + OAuth mint.
        row: ``alert_events`` row dict (the dispatcher just polled it).
        notify_result: The dict returned by ``notify_fn`` on the parent
            message — carries ``telegram_message_id`` and
            ``discord_message_id`` for follow-up threading.
        sleep_fn: Override for ``asyncio.sleep`` used between retries.
            Tests pass a no-op so the suite doesn't actually wait minutes
            between attempts.

    Returns:
        The triage response dict on success (``{diagnosis, model,
        tokens, ms}``), or ``None`` when the call was abandoned (no
        provider, retries exhausted, etc.). Always logged either way.

    Per the spec (#347 failure-handling table):

    - 200 / empty diagnosis → no follow-up sent.
    - 402 ``cost_guarded`` → operator gets a one-line ``[triage skipped:
      cost_guard]`` follow-up so they know why no diagnosis arrived.
    - 503 ``no_provider`` → no follow-up; logged ``triage_no_provider``.
      No retry (config issue, not transient).
    - 5xx / network timeout → retry per backoff. After exhaustion the
      original alert is left alone (operator already has the page).

    Exception isolation: never raises out of this coroutine. Any blow-up
    here would otherwise become an "unhandled task exception" log line
    that obscures the real signal — we catch + log and the alert stays
    enriched-or-not based on what actually happened.
    """
    sleep_fn = sleep_fn or asyncio.sleep
    row_id = row["id"]
    try:
        base_url = await _read_api_base_url(pool)
        if not base_url:
            logger.warning(
                "[alert_dispatcher] triage skipped row=%s: api_base_url "
                "unset — set app_settings.api_base_url=http://worker:8002",
                row_id,
            )
            return None

        token = await _mint_oauth_token(pool, base_url)
        if not token:
            logger.warning(
                "[alert_dispatcher] triage skipped row=%s: brain OAuth "
                "client unconfigured — run `poindexter auth migrate-brain`",
                row_id,
            )
            return None

        # Reshape the row labels/annotations the way the worker expects.
        alert = _row_to_alert_dict(dict(row))
        triage_payload = {
            "alert_event_id": row_id,
            "alertname": row.get("alertname") or "",
            "severity": row.get("severity") or "",
            "labels": alert.get("labels") or {},
            "annotations": alert.get("annotations") or {},
        }
        payload_bytes = json.dumps(triage_payload, default=str).encode("utf-8")
        url = f"{base_url}/api/triage"

        max_attempts, backoff = await _read_triage_retry_config(pool)

        for attempt in range(1, max_attempts + 1):
            try:
                status_code, body = await asyncio.to_thread(
                    _post_triage_sync, url, payload_bytes, token,
                    _TRIAGE_HTTP_TIMEOUT_SECONDS,
                )
            except Exception as e:  # noqa: BLE001 — network / dns / timeout
                logger.warning(
                    "[alert_dispatcher] triage row=%s attempt %d/%d network "
                    "failure: %s", row_id, attempt, max_attempts, e,
                )
                if attempt >= max_attempts:
                    logger.warning(
                        "[alert_dispatcher] triage_dropped row=%s: retries "
                        "exhausted on network failure", row_id,
                    )
                    return None
                await sleep_fn(backoff[attempt - 1])
                continue

            if 200 <= status_code < 300:
                try:
                    response_obj = json.loads(body.decode("utf-8")) if body else {}
                except (ValueError, UnicodeDecodeError) as e:
                    logger.warning(
                        "[alert_dispatcher] triage row=%s: 200 with "
                        "unparseable body (%s); skipping follow-up",
                        row_id, e,
                    )
                    return None
                diagnosis = (response_obj.get("diagnosis") or "").strip() if isinstance(response_obj, dict) else ""
                if diagnosis:
                    await _send_triage_followup(diagnosis, notify_result, pool)
                    logger.info(
                        "[alert_dispatcher] triage_ok row=%s model=%s tokens=%d "
                        "ms=%d", row_id,
                        response_obj.get("model", "?"),
                        response_obj.get("tokens", 0),
                        response_obj.get("ms", 0),
                    )
                else:
                    logger.info(
                        "[alert_dispatcher] triage_ok_empty row=%s — LLM "
                        "produced nothing, skipping follow-up", row_id,
                    )
                return response_obj if isinstance(response_obj, dict) else None

            # Non-2xx. Branch on the no-retry set.
            if status_code in _TRIAGE_NO_RETRY_STATUSES:
                if status_code == 402:
                    # Cost-guard denial — operator gets a one-line
                    # follow-up so they know enrichment was suppressed
                    # by the budget cap, not by an LLM error.
                    await _send_triage_followup(
                        "[triage skipped: cost_guard]",
                        notify_result, pool,
                    )
                    logger.info(
                        "[alert_dispatcher] triage_cost_guarded row=%s "
                        "(no retry)", row_id,
                    )
                elif status_code == 503:
                    logger.info(
                        "[alert_dispatcher] triage_no_provider row=%s "
                        "(no retry, config issue)", row_id,
                    )
                else:
                    logger.warning(
                        "[alert_dispatcher] triage_auth_failed row=%s "
                        "status=%d (no retry, config issue)",
                        row_id, status_code,
                    )
                return None

            # Transient (5xx) — retry with backoff if budget remains.
            logger.warning(
                "[alert_dispatcher] triage row=%s attempt %d/%d "
                "HTTP %d — %s",
                row_id, attempt, max_attempts, status_code,
                body[:200].decode("utf-8", "replace") if body else "",
            )
            if attempt >= max_attempts:
                logger.warning(
                    "[alert_dispatcher] triage_dropped row=%s: retries "
                    "exhausted on HTTP %d", row_id, status_code,
                )
                return None
            await sleep_fn(backoff[attempt - 1])

        return None
    except Exception as e:  # noqa: BLE001 — wholesale safety net
        logger.warning(
            "[alert_dispatcher] _triage_one row=%s raised: %s",
            row_id, e, exc_info=True,
        )
        return None

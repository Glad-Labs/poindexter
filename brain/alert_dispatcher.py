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

import logging
import sys
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
# an awaitable. Defined as a Callable so tests can inject their own
# without monkeypatching the import path.
NotifyFn = Callable[..., Awaitable[None]]


async def _resolve_notify_fn() -> Optional[NotifyFn]:
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
        sync_notify = brain_daemon_mod.notify

        async def _adapter(message: str, *, critical: bool = False) -> None:
            # Brain's notify is sync (uses urllib). Run it inline — the
            # urllib calls are short (10s timeout) and we're already
            # off the main brain cycle on a 30s loop, so a blocking
            # send is fine. If we ever move to long-poll telegram or
            # something fancier, swap to asyncio.to_thread here.
            #
            # critical is a no-op for the brain helper — it always sends
            # to both Telegram and Discord ops. Severity is encoded in
            # the message header itself, which is enough for Matt to
            # triage on his phone.
            del critical
            ok = sync_notify(message)
            # The brain notify returns True iff at least one channel
            # accepted the message. False = operator did NOT receive
            # the alert (no token, malformed URL, all transports down).
            # Raise so poll_and_dispatch's try/except marks the row
            # with an honest ``dispatch_result = 'error: ...'`` instead
            # of silently recording ``'sent'`` while the page vanished
            # into a black hole — the exact failure
            # Glad-Labs/poindexter#342 traced.
            if ok is False:  # explicit identity — None from legacy stubs is treated as success
                raise NotifyFailed(
                    "brain.notify reported no channel accepted the message "
                    "(check telegram_bot_token, telegram_chat_id, "
                    "discord_ops_webhook_url in app_settings)"
                )

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
        notify_fn = await _resolve_notify_fn()

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

    for row in rows:
        row_id = row["id"]
        try:
            alert = _row_to_alert_dict(dict(row))
            severity = (alert.get("labels") or {}).get("severity") or ""
            critical = severity.lower() == "critical"
            message = _format_alert_message(alert)
            await notify_fn(message, critical=critical)
            await pool.execute(_MARK_SENT_SQL, row_id)
            summary["sent"] += 1
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

    if summary["sent"] or summary["errors"]:
        logger.info(
            "[alert_dispatcher] cycle: polled=%d sent=%d errors=%d",
            summary["polled"], summary["sent"], summary["errors"],
        )
    return summary

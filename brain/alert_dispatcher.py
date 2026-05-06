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
import hashlib
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
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
# Coalesce + severity-routing knobs (Glad-Labs/poindexter#420). Defaults
# match the seeded app_settings rows from migration
# 20260506_221522_alert_dedup_state_and_routing_settings.py. The dispatcher
# reads the live values per cycle so an operator can flip a knob without
# restarting the brain.
# ---------------------------------------------------------------------------

_DEFAULT_SUPPRESS_WINDOW_MINUTES = 30
_DEFAULT_SUMMARIZE_THRESHOLD_MINUTES = 30
_DEFAULT_DEDUP_RETENTION_HOURS = 168

# Severities that page Telegram. Anything else is Discord-only per
# feedback_telegram_vs_discord. We treat unknown severities as warning
# (Discord-only) to fail-safe — a typo in a label shouldn't suddenly
# light up Matt's phone.
_TELEGRAM_SEVERITIES: frozenset[str] = frozenset({"critical", "error"})

# Number / timestamp patterns stripped during message normalization so
# that "Service X is down (attempt 5)" and "Service X is down (attempt
# 6)" collapse to the same fingerprint. Timestamps stripped first so a
# numeric pattern doesn't eat the digits inside a timestamp.
_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_HMS_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
# Match digit runs and decimals greedily without requiring a word boundary
# at the end -- "89.5C" should fingerprint as "<N>C" rather than "<N>.5C"
# (the trailing C is alphabetic, so a closing \b fails between "5" and "C"
# and the decimal portion would otherwise survive normalization). The
# leading \b keeps us from chewing the digits inside a slug like "v2x"
# becoming "v<N>x" -- only standalone numeric runs are normalized.
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?")


def _default_now() -> datetime:
    """Return the current UTC-aware timestamp.

    Module-level indirection so tests can monkeypatch the clock without
    replacing ``datetime`` itself (which would break ``isinstance(...,
    datetime)`` checks elsewhere in the module). Production code calls
    this exactly once per dispatch decision.
    """
    return datetime.now(timezone.utc)


def _normalize_message(message: str) -> str:
    """Collapse a message into its fingerprint-stable form.

    Strips trailing whitespace, replaces ISO timestamps + clock-style
    HH:MM(:SS) blobs with ``<TS>``, then replaces every other run of
    digits with ``<N>``. The remaining text carries the alert's
    structural identity — alertname, severity tag, the actual prose —
    while shedding the per-fire numeric noise that would otherwise
    fingerprint as a fresh alert on every cycle.

    Examples:
        "Service openclaw down (3 retries since 2026-05-06T22:15:00Z)"
            -> "Service openclaw down (<N> retries since <TS>)"
        "GPU temp 89.5C threshold 85"
            -> "GPU temp <N>C threshold <N>"
    """
    if not message:
        return ""
    text = message.strip()
    text = _TS_RE.sub("<TS>", text)
    text = _HMS_RE.sub("<TS>", text)
    text = _NUM_RE.sub("<N>", text)
    return text


def _compute_fingerprint(
    *, source: str, severity: str, message: str
) -> str:
    """Build the per-alert dedup key.

    ``source|severity|normalized_message`` joined with literal pipes,
    SHA-256 hashed for a fixed-length primary key. Severity is part of
    the key so a "warning openclaw down" and "critical openclaw down"
    do NOT collide — escalating severity should re-page even inside the
    suppression window.
    """
    raw = f"{source or ''}|{severity or ''}|{_normalize_message(message)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _read_app_setting_str(pool: Any, key: str, default: str) -> str:
    """Pool-agnostic helper — returns the string value or the default.

    Mirrors brain/alert_sync.py::_get_setting so callers don't need to
    import each module's helper. Treats both missing rows and missing
    tables as "use the default" — the dispatcher gracefully degrades to
    pre-#420 behaviour if the migration hasn't run yet.
    """
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[alert_dispatcher] app_settings read failed for %s (%s) -- "
            "using default",
            key, e,
        )
        return default
    if value is None:
        return default
    return str(value)


async def _read_app_setting_int(pool: Any, key: str, default: int) -> int:
    """Read an int-valued app_setting with safe fallback."""
    raw = await _read_app_setting_str(pool, key, str(default))
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        logger.debug(
            "[alert_dispatcher] app_setting %s = %r unparseable -- "
            "using default %d",
            key, raw, default,
        )
        return default


async def _read_force_telegram_event_types(pool: Any) -> frozenset[str]:
    """Parse the alert_force_telegram_event_types CSV into a set.

    Empty / missing -> empty set (no overrides). Whitespace around
    each entry is stripped; case-insensitive matching upstream.
    """
    raw = await _read_app_setting_str(
        pool, "alert_force_telegram_event_types", ""
    )
    if not raw:
        return frozenset()
    parts = [p.strip() for p in raw.split(",") if p and p.strip()]
    return frozenset(p.lower() for p in parts)


def _channels_for(
    severity: str,
    *,
    alertname: str,
    category: str,
    force_telegram_set: frozenset[str],
) -> tuple[bool, bool]:
    """Resolve (telegram, discord) booleans for a given severity + event.

    Severity matrix (per feedback_telegram_vs_discord + #420):

    * ``critical`` / ``error`` -> BOTH Telegram + Discord
    * ``warning`` / ``info`` (and anything else) -> Discord ONLY
    * ``alert_force_telegram_event_types`` membership -> ALWAYS adds
      Telegram regardless of severity. Matched case-insensitively
      against ``alertname``, ``category``, AND any ``event_type`` label
      the route layer may have copied through. Discord still fires
      because warnings still belong on Discord.
    """
    sev = (severity or "").strip().lower()
    telegram = sev in _TELEGRAM_SEVERITIES
    discord = True  # Discord is the always-on log channel.

    # Force-Telegram override — match alertname OR category. Both are
    # lowercased + stripped before comparison so an operator can put
    # either column's value in the CSV without surprise.
    if force_telegram_set:
        candidates = {
            (alertname or "").strip().lower(),
            (category or "").strip().lower(),
        }
        candidates.discard("")
        if candidates & force_telegram_set:
            telegram = True

    return telegram, discord


# ---------------------------------------------------------------------------
# Dedup-state helpers. Persistent so a brain restart mid-burst doesn't
# reset the suppression window and re-page the operator on every restart.
# ---------------------------------------------------------------------------


async def _fetch_dedup_state(
    pool: Any, fingerprint: str
) -> Optional[dict[str, Any]]:
    """Return the current dedup-state row, or None if no prior fire.

    Treated as "no prior fire" on any DB error (table missing,
    transient connection blip) -- the dispatcher then dispatches the
    alert normally. Fail-open is the right posture here: a broken
    dedup-state read should never silently swallow an alert.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT fingerprint, first_seen_at, last_seen_at, repeat_count,
                   summary_dispatched_at, severity, source, sample_message
            FROM alert_dedup_state
            WHERE fingerprint = $1
            """,
            fingerprint,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[alert_dispatcher] alert_dedup_state lookup failed for %s "
            "(%s) -- treating as first fire",
            fingerprint[:12], e,
        )
        return None
    if row is None:
        return None
    return dict(row)


async def _insert_dedup_state(
    pool: Any,
    *,
    fingerprint: str,
    severity: str,
    source: str,
    sample_message: str,
    now: datetime,
) -> None:
    """Record a first-fire. Idempotent — DO NOTHING on duplicate primary key
    so two cycles racing on the same fingerprint don't crash."""
    try:
        await pool.execute(
            """
            INSERT INTO alert_dedup_state
                (fingerprint, first_seen_at, last_seen_at, repeat_count,
                 severity, source, sample_message)
            VALUES ($1, $2, $2, 1, $3, $4, $5)
            ON CONFLICT (fingerprint) DO NOTHING
            """,
            fingerprint, now, severity, source, sample_message,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[alert_dispatcher] alert_dedup_state insert failed for %s "
            "(%s) -- continuing without dedup state",
            fingerprint[:12], e,
        )


async def _bump_dedup_state(
    pool: Any, *, fingerprint: str, now: datetime
) -> None:
    """Increment repeat_count + push last_seen_at on a suppressed repeat."""
    try:
        await pool.execute(
            """
            UPDATE alert_dedup_state
            SET repeat_count = repeat_count + 1,
                last_seen_at = $2
            WHERE fingerprint = $1
            """,
            fingerprint, now,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[alert_dispatcher] alert_dedup_state bump failed for %s "
            "(%s)",
            fingerprint[:12], e,
        )


async def _mark_summary_dispatched(
    pool: Any, *, fingerprint: str, now: datetime
) -> None:
    """Latch summary_dispatched_at so the threshold doesn't re-fire."""
    try:
        await pool.execute(
            """
            UPDATE alert_dedup_state
            SET summary_dispatched_at = $2,
                last_seen_at = $2
            WHERE fingerprint = $1
            """,
            fingerprint, now,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[alert_dispatcher] failed to mark summary_dispatched_at "
            "for %s (%s) -- LLM summary may re-fire next cycle",
            fingerprint[:12], e,
        )


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

    notify_fn_injected = notify_fn is not None
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

    # Glad-Labs/poindexter#420: read coalesce + severity-routing config
    # once per cycle. The dedup config is passed into _dispatch_one so
    # every row sees a consistent snapshot even if an operator flips a
    # knob mid-cycle.
    #
    # ``notify_fn_injected`` tracks whether the caller supplied their own
    # notify (test path) vs the dispatcher resolving one (production
    # path). Tests rely on every dispatch flowing through the injected
    # AsyncMock so call counts match -- when injected we keep the legacy
    # "notify_fn does it all" contract and only flip the ``critical``
    # kwarg to reflect the severity matrix. When NOT injected we have a
    # real adapter and can route warnings directly to brain.send_discord
    # so they never reach Telegram.
    dedup_config = await _read_dedup_config(pool)
    dedup_config["notify_fn_injected"] = notify_fn_injected

    for row in rows:
        notify_result = await _dispatch_one(
            pool, row, notify_fn, summary,
            dedup_config=dedup_config,
        )
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

    suppressed = summary.get("suppressed", 0)
    summaries = summary.get("summaries", 0)
    if summary["sent"] or summary["errors"] or suppressed or summaries:
        logger.info(
            "[alert_dispatcher] cycle: polled=%d sent=%d errors=%d "
            "suppressed=%d summaries=%d triage_scheduled=%d",
            summary["polled"], summary["sent"], summary["errors"],
            suppressed, summaries, len(_triage_tasks),
        )
    return summary


async def _dispatch_one(
    pool: Any,
    row: Any,
    notify_fn: NotifyFn,
    summary: dict[str, int],
    *,
    dedup_config: dict[str, Any] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any] | None:
    """Notify the operator about ONE alert row + mark it dispatched.

    Returns the notify_fn result dict (carrying ``telegram_message_id`` /
    ``discord_message_id``) on success so the caller can hand it to the
    parallel triage task. Returns ``None`` on failure — the row is
    already marked errored before we return.

    When ``dedup_config`` is provided (Glad-Labs/poindexter#420), the
    dispatcher consults ``alert_dedup_state`` and may:

    * Suppress the dispatch entirely when the fingerprint last fired
      inside the suppression window. The row is still marked (with
      ``dispatch_result = 'suppressed: repeat ...'``) so the operator
      can see the dedup decision in ``alert_events``.
    * Escalate to an AI summary when the fingerprint has been firing
      continuously for >= the configured threshold and no summary has
      been dispatched yet for this run. The summary is dispatched in
      place of the raw alert — the operator sees ONE coalesced alert,
      not the Nth dumb repeat.

    When ``dedup_config`` is None the legacy v1 behaviour applies:
    every row dispatches via the resolved notify_fn with no dedup,
    no severity routing override, no AI summary.

    Failure isolation: any exception from the notify path is caught,
    logged, and recorded on the row. Callers MUST NOT raise from this
    helper — the loop in ``poll_and_dispatch`` is best-effort and a
    single bad row can't take the dispatcher down.
    """
    row_id = row["id"]
    try:
        alert = _row_to_alert_dict(dict(row))
        labels = alert.get("labels") or {}
        severity = (labels.get("severity") or "").strip()
        alertname = (labels.get("alertname") or row.get("alertname") or "").strip()
        category = (labels.get("category") or row.get("category") or "").strip()
        message = _format_alert_message(alert)

        if dedup_config is not None:
            decision = await _evaluate_dedup_decision(
                pool, message=message,
                severity=severity, alertname=alertname, category=category,
                config=dedup_config, now_fn=now_fn,
            )
            if decision["action"] == "suppress":
                await pool.execute(
                    _MARK_ERROR_SQL, row_id,
                    decision["dispatch_result"],
                )
                summary["sent"] += 0
                summary.setdefault("suppressed", 0)
                summary["suppressed"] += 1
                logger.info(
                    "[alert_dispatcher] suppressed row=%s "
                    "fingerprint=%s repeat_count=%s",
                    row_id, decision["fingerprint"][:12],
                    decision.get("repeat_count"),
                )
                return None
            if decision["action"] == "summary":
                summary_text = await _build_summary_payload(
                    pool, row=row, base_message=message,
                    state=decision["state"],
                    config=dedup_config, now=decision["now"],
                )
                routed = await _routed_notify(
                    pool=pool,
                    notify_fn=notify_fn,
                    message=summary_text,
                    severity=decision["state"]["severity"] or severity,
                    alertname=alertname,
                    category=category,
                    force_telegram_set=dedup_config["force_telegram_set"],
                    notify_fn_injected=dedup_config.get(
                        "notify_fn_injected", False
                    ),
                )
                await pool.execute(_MARK_SENT_SQL, row_id)
                await _mark_summary_dispatched(
                    pool,
                    fingerprint=decision["fingerprint"],
                    now=decision["now"],
                )
                summary["sent"] += 1
                summary.setdefault("summaries", 0)
                summary["summaries"] += 1
                logger.info(
                    "[alert_dispatcher] dispatched AI summary row=%s "
                    "fingerprint=%s repeat_count=%s",
                    row_id, decision["fingerprint"][:12],
                    decision["state"].get("repeat_count"),
                )
                return routed if isinstance(routed, dict) else None
            # action == "dispatch" -- fall through to severity-routed send.

        # Severity-routed dispatch path (also the legacy fall-through).
        if dedup_config is not None:
            notify_result = await _routed_notify(
                pool=pool,
                notify_fn=notify_fn,
                message=message,
                severity=severity,
                alertname=alertname,
                category=category,
                force_telegram_set=dedup_config["force_telegram_set"],
                notify_fn_injected=dedup_config.get(
                    "notify_fn_injected", False
                ),
            )
        else:
            critical = severity.lower() == "critical"
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
# Coalesce + summary helpers (Glad-Labs/poindexter#420). Kept below the
# dispatch primitives so the legacy code paths above stay easy to read.
# ---------------------------------------------------------------------------


async def _evaluate_dedup_decision(
    pool: Any,
    *,
    message: str,
    severity: str,
    alertname: str,
    category: str,
    config: dict[str, Any],
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Decide whether to dispatch, suppress, or escalate to AI summary.

    Returns a dict with one of three ``action`` values:

    * ``"dispatch"`` — first fire (or first fire after the suppression
      window expired). Caller dispatches the raw alert and the helper
      has already INSERTed the dedup-state row.
    * ``"suppress"`` — repeat inside the suppression window AND the
      threshold for AI summary has not yet been crossed (or the
      summary has already been dispatched once for this run). Caller
      records the suppression on the alert_events row.
    * ``"summary"`` — the threshold was just crossed and we have not
      yet dispatched a summary. Caller calls ``_build_summary_payload``,
      sends through the routed notify, and latches
      ``summary_dispatched_at``.

    The dict also returns ``fingerprint``, ``now``, ``state`` (the
    dedup-state row as it stood after this call), ``repeat_count``,
    and ``dispatch_result`` (a human-readable string for the caller
    to write to ``alert_events.dispatch_result`` on suppress).
    """
    now_fn = now_fn or _default_now
    now = now_fn()

    # Source priority for the fingerprint: alertname (most stable) ->
    # category -> a fixed sentinel. The legacy formatter already builds
    # a header that includes alertname, so the same string isn't
    # collapsing across two unrelated alerts.
    source = alertname or category or "alert"
    fingerprint = _compute_fingerprint(
        source=source, severity=severity, message=message
    )

    state = await _fetch_dedup_state(pool, fingerprint)
    suppress_window_min = config["suppress_window_minutes"]
    threshold_min = config["summarize_threshold_minutes"]

    # Window 0 disables dedup entirely -- every fire dispatches and we
    # don't bother updating the state table. This matches the spec's
    # "set window to 0 to disable dedup" knob.
    if suppress_window_min <= 0:
        return {
            "action": "dispatch",
            "fingerprint": fingerprint,
            "now": now,
            "state": None,
            "repeat_count": 1,
        }

    if state is None:
        # First fire (or first since retention janitor pruned). Record
        # baseline and dispatch.
        await _insert_dedup_state(
            pool,
            fingerprint=fingerprint,
            severity=severity,
            source=source,
            sample_message=message,
            now=now,
        )
        return {
            "action": "dispatch",
            "fingerprint": fingerprint,
            "now": now,
            "state": {
                "first_seen_at": now,
                "last_seen_at": now,
                "repeat_count": 1,
                "summary_dispatched_at": None,
                "severity": severity,
                "source": source,
                "sample_message": message,
            },
            "repeat_count": 1,
        }

    last_seen_at = _coerce_datetime(state.get("last_seen_at")) or now
    first_seen_at = _coerce_datetime(state.get("first_seen_at")) or now
    summary_dispatched_at = _coerce_datetime(state.get("summary_dispatched_at"))
    age_since_last_seen = (now - last_seen_at).total_seconds() / 60.0
    age_since_first_seen = (now - first_seen_at).total_seconds() / 60.0

    # Outside the suppression window? Treat as a fresh first fire --
    # reset the baseline and dispatch.
    if age_since_last_seen >= suppress_window_min:
        try:
            await pool.execute(
                """
                UPDATE alert_dedup_state
                SET first_seen_at = $2,
                    last_seen_at  = $2,
                    repeat_count  = 1,
                    summary_dispatched_at = NULL,
                    severity      = $3,
                    source        = $4,
                    sample_message = $5
                WHERE fingerprint = $1
                """,
                fingerprint, now, severity, source, message,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[alert_dispatcher] dedup-state reset failed for %s (%s)",
                fingerprint[:12], e,
            )
        return {
            "action": "dispatch",
            "fingerprint": fingerprint,
            "now": now,
            "state": {
                "first_seen_at": now,
                "last_seen_at": now,
                "repeat_count": 1,
                "summary_dispatched_at": None,
                "severity": severity,
                "source": source,
                "sample_message": message,
            },
            "repeat_count": 1,
        }

    # Inside the suppression window. Increment the counter unconditionally
    # so the summary's "fired N times" line is correct even when the
    # summary itself fires later.
    await _bump_dedup_state(pool, fingerprint=fingerprint, now=now)
    new_repeat_count = int(state.get("repeat_count") or 1) + 1
    state["repeat_count"] = new_repeat_count
    state["last_seen_at"] = now

    # Threshold-escalation? The duration of the burst (now - first_seen)
    # is what gates the AI summary, NOT the per-repeat interval. Once
    # the threshold is crossed AND no summary has been dispatched yet
    # for this run, we synthesize the summary in place of the raw fire.
    if (
        threshold_min > 0
        and age_since_first_seen >= threshold_min
        and summary_dispatched_at is None
    ):
        return {
            "action": "summary",
            "fingerprint": fingerprint,
            "now": now,
            "state": state,
            "repeat_count": new_repeat_count,
        }

    # Otherwise: pure suppression. Return a dispatch_result line the
    # caller can persist to alert_events.dispatch_result so the dedup
    # decision is observable in the audit trail.
    return {
        "action": "suppress",
        "fingerprint": fingerprint,
        "now": now,
        "state": state,
        "repeat_count": new_repeat_count,
        "dispatch_result": (
            f"suppressed: repeat {new_repeat_count}, "
            f"first_seen={first_seen_at.isoformat()}"
        )[:400],
    }


def _coerce_datetime(value: Any) -> datetime | None:
    """Best-effort -> aware UTC datetime for arithmetic.

    asyncpg returns timezone-aware datetimes for TIMESTAMPTZ columns;
    test mocks may pass a string or a naive datetime. We normalise to
    UTC-aware so the (now - first_seen_at) subtraction never raises.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


async def _routed_notify(
    *,
    pool: Any,
    notify_fn: NotifyFn,
    message: str,
    severity: str,
    alertname: str,
    category: str,
    force_telegram_set: frozenset[str],
    notify_fn_injected: bool = False,
) -> dict[str, Any] | None:
    """Dispatch ``message`` honouring the severity routing matrix.

    Tries the brain's per-channel send helpers first so warning alerts
    stay off Telegram. Falls back to ``notify_fn`` (the legacy
    "send to whichever channel is reachable" adapter) when:

    * The severity matrix says BOTH channels should fire (critical /
      error / force-Telegram override) — legacy notify_fn already
      sends to both, so we reuse it instead of duplicating sends.
    * The brain daemon isn't importable (unit tests stub the module
      via monkeypatch; the legacy notify_fn is whatever the test
      injected and we honour it for back-compat).

    Returns the same shape ``notify_fn`` would: a dict carrying
    ``telegram_message_id`` / ``discord_message_id`` / ``ok``, or
    ``None`` if the legacy notifier didn't surface ids.

    Raises ``NotifyFailed`` when neither channel accepts the message --
    the caller's existing try/except marks the alert row with the
    error so the operator sees it in alert_events.dispatch_result
    instead of a phantom 'sent'.
    """
    telegram, discord = _channels_for(
        severity,
        alertname=alertname,
        category=category,
        force_telegram_set=force_telegram_set,
    )

    # ``critical=`` reflects the routing decision, NOT the raw severity.
    # That matters for the force-telegram override: a warning whose
    # alertname is in the override CSV must dispatch with
    # ``critical=True`` so a downstream notifier that uses the kwarg to
    # decide channel reach (the legacy notify_fn does) lights up
    # Telegram. Pure severity is preserved in the message header for
    # the operator's reading.
    routing_critical = telegram and discord

    # Test-injected notify path: keep the legacy "notify_fn does it all"
    # contract so unit tests that stub a single AsyncMock still see the
    # expected call count. The severity matrix flows through via the
    # ``critical`` kwarg -- which is what existing tests assert on. The
    # actual channel split is exercised by the production-path branch
    # below + the new dedicated tests.
    if notify_fn_injected:
        return await notify_fn(message, critical=routing_critical)

    # When BOTH channels are needed AND we're on the production path,
    # the auto-resolved notify adapter already sends to both. Reuse it
    # so we don't duplicate Telegram + Discord sends.
    if telegram and discord:
        return await notify_fn(message, critical=routing_critical)

    # Discord-only path on production. Try brain.send_discord directly
    # so warnings don't reach Telegram. If brain.send_discord is
    # unreachable we fall back to the legacy notify_fn with
    # critical=False -- the legacy fallback may still page Telegram,
    # but at least the operator isn't left blind.
    brain_daemon_mod = _resolve_brain_daemon_module()
    if brain_daemon_mod is None or not hasattr(brain_daemon_mod, "send_discord"):
        return await notify_fn(message, critical=False)

    # ``brain.send_discord`` defaults to ``discord_lab_logs_webhook_url``
    # (the public lab-logs channel) when no webhook is passed -- but ops
    # alerts must go to the ops channel. Mirror ``brain.notify``'s
    # resolution order: explicit ``discord_ops_webhook_url`` from
    # app_settings, then the env-var fallback. Without this the
    # discord-only path fired ``no discord_ops_webhook_url`` even when
    # the ops URL was correctly seeded -- the lookup was just being
    # done against the wrong key downstream.
    ops_url = ""
    if pool is not None:
        ops_url = await _read_app_setting_str(
            pool, "discord_ops_webhook_url", ""
        )
    if not ops_url:
        ops_url = os.getenv("DISCORD_OPS_WEBHOOK_URL", "")

    try:
        dc_id = await brain_daemon_mod.send_discord(
            message, webhook_url=ops_url or None, pool=pool
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[alert_dispatcher] brain.send_discord raised on routed "
            "warning -- falling back to legacy notify: %s", e,
        )
        return await notify_fn(message, critical=False)
    if not dc_id:
        raise NotifyFailed(
            "discord-only routing rejected: no discord_ops_webhook_url "
            "(check app_settings)"
        )
    return {
        "telegram_message_id": None,
        "discord_message_id": dc_id if isinstance(dc_id, str) else str(dc_id),
        "ok": True,
    }


def _resolve_brain_daemon_module() -> Any | None:
    """Find the brain.brain_daemon module across the flat / package paths.

    Identical resolution to ``_resolve_notify_fn`` -- pulled out so the
    routing helper doesn't need to repeat the import dance. Returns
    ``None`` when neither path resolves; callers fall back to the
    legacy notify_fn in that case.
    """
    mod = sys.modules.get("brain_daemon") or sys.modules.get("brain.brain_daemon")
    if mod is not None:
        return mod
    try:
        import brain_daemon as mod  # type: ignore
        return mod
    except ImportError:
        try:
            from brain import brain_daemon as mod  # type: ignore
            return mod
        except ImportError:
            return None


async def _build_summary_payload(
    pool: Any,
    *,
    row: Any,
    base_message: str,
    state: dict[str, Any],
    config: dict[str, Any],
    now: datetime,
) -> str:
    """Generate the AI-summary message body.

    Tries the worker /api/triage endpoint with augmented annotations
    (passes the burst's repeat_count + duration + correlated audit_log
    rows in the alert payload so the existing firefighter prompt has
    the context it needs). Falls back to a deterministic degraded
    summary when the LLM call fails for any reason -- ``no silent
    fallbacks`` per CLAUDE.md, the operator still gets a coalesced
    message so the burst isn't simply lost.

    The fallback line ("This alert has fired N times in M minutes;
    LLM summary unavailable") matches the spec's degraded-path
    requirement verbatim. The successful-path output is the LLM's
    diagnosis paragraph prefixed with a deterministic header so the
    operator can tell at a glance that this is a summary of a
    coalesced burst, not a fresh single fire.
    """
    first_seen = _coerce_datetime(state.get("first_seen_at")) or now
    repeat_count = int(state.get("repeat_count") or 1)
    duration_min = max(1, int((now - first_seen).total_seconds() // 60))
    severity = (state.get("severity") or "").strip() or "info"
    source = state.get("source") or "alert"

    correlated = await _fetch_correlated_audit_rows(
        pool, since=first_seen, until=now,
    )

    header = (
        f"[SUMMARY · {severity}] {source}\n"
        f"Repeating alert -- fired {repeat_count} times "
        f"over the last {duration_min} min."
    )

    diagnosis = await _request_summary_diagnosis(
        pool, row=row, base_message=base_message,
        repeat_count=repeat_count, duration_min=duration_min,
        correlated=correlated, config=config,
    )

    if diagnosis:
        return f"{header}\n\n{diagnosis}"
    return (
        f"{header}\n\n"
        f"This alert has fired {repeat_count} times in {duration_min} "
        f"minutes; LLM summary unavailable."
    )


async def _fetch_correlated_audit_rows(
    pool: Any,
    *,
    since: datetime,
    until: datetime,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Pull a handful of audit_log rows from the burst window.

    Best-effort: returns an empty list on any DB error so the LLM
    summary still fires with just the core repeat context. The query
    is bounded by ``limit`` so a chatty burst window can't OOM the
    summary payload.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT id, event_type, source, severity, details, timestamp
            FROM audit_log
            WHERE timestamp >= $1 AND timestamp <= $2
            ORDER BY timestamp DESC
            LIMIT $3
            """,
            since, until, limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[alert_dispatcher] correlated audit_log fetch failed (%s)", e,
        )
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(dict(r))
    return out


async def _request_summary_diagnosis(
    pool: Any,
    *,
    row: Any,
    base_message: str,
    repeat_count: int,
    duration_min: int,
    correlated: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    """POST a synthesized triage request to the worker; return the diagnosis.

    Reuses the existing ``_post_triage_sync`` + OAuth mint primitives
    so we don't duplicate the auth / retry plumbing. Pads the alert
    annotations with the burst metadata so the firefighter's existing
    prompt sees:

    * ``annotations.summary`` -- the original alert summary line
    * ``annotations.description`` -- the original description PLUS
      "Repeated N times over M minutes." appended so the LLM has it
      whether it picks up summary or description.
    * ``annotations.repeat_count`` / ``annotations.duration_minutes``
      -- machine-readable copies for future prompt iterations.
    * ``annotations.correlated_events`` -- a small JSON list of
      audit_log rows from the burst window so the LLM can name other
      services that fired alongside.

    Returns the LLM's diagnosis text, or ``""`` on any failure (the
    caller falls back to the deterministic degraded summary).

    Failure modes (all mapped to ``""`` so the caller can decide):

    * No ``api_base_url`` configured -- log + skip.
    * No OAuth token mintable -- log + skip.
    * Worker returns 402/503 -- log + skip (no retry, config issues).
    * Worker returns 5xx / network failure on every retry -- log + skip.
    * Worker returns 200 with empty diagnosis -- ``""`` flows through.
    """
    base_url = await _read_api_base_url(pool)
    if not base_url:
        logger.debug(
            "[alert_dispatcher] summary skipped: api_base_url unset"
        )
        return ""

    token = await _mint_oauth_token(pool, base_url)
    if not token:
        logger.debug(
            "[alert_dispatcher] summary skipped: brain OAuth client "
            "unconfigured"
        )
        return ""

    alert = _row_to_alert_dict(dict(row))
    base_annotations = dict(alert.get("annotations") or {})
    augmented_description = base_annotations.get("description") or ""
    burst_line = (
        f"Repeated {repeat_count} times over {duration_min} minutes."
    )
    if augmented_description:
        augmented_description = f"{augmented_description}\n\n{burst_line}"
    else:
        augmented_description = burst_line
    base_annotations["description"] = augmented_description
    base_annotations["repeat_count"] = repeat_count
    base_annotations["duration_minutes"] = duration_min
    base_annotations["base_message"] = base_message
    if correlated:
        base_annotations["correlated_events"] = correlated[:10]

    triage_payload = {
        "alert_event_id": row.get("id"),
        "alertname": row.get("alertname") or "",
        "severity": row.get("severity") or "",
        "labels": alert.get("labels") or {},
        "annotations": base_annotations,
        # Brain marker so the worker can branch a future summary-prompt
        # without breaking the existing single-fire triage path.
        "summary_request": True,
    }
    payload_bytes = json.dumps(triage_payload, default=str).encode("utf-8")
    url = f"{base_url}/api/triage"

    max_attempts = config.get("triage_retry_max", _DEFAULT_TRIAGE_RETRY_MAX)
    backoff = list(
        config.get("triage_backoff", _DEFAULT_TRIAGE_BACKOFF_SECONDS)
    )
    if len(backoff) < max_attempts:
        last = backoff[-1] if backoff else _DEFAULT_TRIAGE_BACKOFF_SECONDS[-1]
        backoff.extend([last] * (max_attempts - len(backoff)))

    sleep_fn = config.get("sleep_fn") or asyncio.sleep

    for attempt in range(1, max_attempts + 1):
        try:
            status_code, body = await asyncio.to_thread(
                _post_triage_sync, url, payload_bytes, token,
                _TRIAGE_HTTP_TIMEOUT_SECONDS,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[alert_dispatcher] summary attempt %d/%d network "
                "failure: %s", attempt, max_attempts, e,
            )
            if attempt >= max_attempts:
                return ""
            await sleep_fn(backoff[attempt - 1])
            continue

        if 200 <= status_code < 300:
            try:
                response_obj = json.loads(body.decode("utf-8")) if body else {}
            except (ValueError, UnicodeDecodeError):
                return ""
            if not isinstance(response_obj, dict):
                return ""
            return (response_obj.get("diagnosis") or "").strip()

        if status_code in _TRIAGE_NO_RETRY_STATUSES:
            logger.info(
                "[alert_dispatcher] summary not generated -- worker "
                "returned %d (no retry)", status_code,
            )
            return ""

        logger.warning(
            "[alert_dispatcher] summary attempt %d/%d HTTP %d",
            attempt, max_attempts, status_code,
        )
        if attempt >= max_attempts:
            return ""
        await sleep_fn(backoff[attempt - 1])

    return ""


async def _read_dedup_config(pool: Any) -> dict[str, Any]:
    """Snapshot the #420 knobs once per cycle so we don't hit app_settings
    inside the per-row hot loop.

    Returned shape mirrors the dispatch helpers' kwargs:

    * ``suppress_window_minutes`` (int >= 0)
    * ``summarize_threshold_minutes`` (int >= 0)
    * ``force_telegram_set`` (frozenset[str], lowercased)
    * ``triage_retry_max`` / ``triage_backoff`` -- pulled from the
      existing ops_triage_* keys so the AI summary path inherits the
      same retry posture as the firefighter triage call.
    """
    suppress_window = await _read_app_setting_int(
        pool,
        "alert_repeat_suppress_window_minutes",
        _DEFAULT_SUPPRESS_WINDOW_MINUTES,
    )
    summarize_threshold = await _read_app_setting_int(
        pool,
        "alert_repeat_summarize_threshold_minutes",
        _DEFAULT_SUMMARIZE_THRESHOLD_MINUTES,
    )
    force_set = await _read_force_telegram_event_types(pool)
    triage_retry_max, triage_backoff = await _read_triage_retry_config(pool)
    return {
        "suppress_window_minutes": max(0, suppress_window),
        "summarize_threshold_minutes": max(0, summarize_threshold),
        "force_telegram_set": force_set,
        "triage_retry_max": triage_retry_max,
        "triage_backoff": triage_backoff,
    }


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

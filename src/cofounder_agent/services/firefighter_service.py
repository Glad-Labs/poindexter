"""Firefighter ops LLM service — diagnosis-only enrichment for alerts.

Implements Glad-Labs/poindexter#347 step 2 (the service module + unit
tests; route + brain wiring land in subsequent PRs as steps 3-6).

The brain daemon's ``alert_dispatcher`` already pages the operator on
every ``alert_events`` row. This module gives the brain a parallel
"diagnosis" path: fetch a curated bundle of DB context, hand it to the
local LLM via ``model_router``, and produce ONE short paragraph that
the brain can post as a follow-up to the same Telegram thread.

Public surface (called by ``routes/triage_routes.py`` once step 3 lands):

- :func:`build_triage_context` — runs four SQL queries against the
  shared pool to assemble (alert + 5 same-name history + 10 audit_log
  rows + the affected ``pipeline_tasks`` row when the alert carries a
  ``task_id`` label + a snapshot of relevant ``app_settings`` keys).
  Honours ``ops_triage_max_context_tokens`` by dropping the oldest
  audit_log rows first when the assembled bundle would exceed budget.
- :func:`run_triage` — invokes ``model_router.invoke(model_class=...)``
  with the operator-persona system prompt and the assembled context.
  Truncates the output to ``ops_triage_max_diagnosis_tokens`` and
  returns ``{"diagnosis", "model", "tokens", "ms"}``. Returns
  ``diagnosis=""`` (empty string, not None, never raises) when the
  LLM produces nothing — the caller's spec'd behaviour is to skip
  the follow-up rather than post empty.
- :func:`_default_system_prompt` — operator-persona prompt; reads the
  override from ``site_config`` and falls back to the migration-seeded
  default verbatim.

DI rules (poindexter#95 / Phase H):

Every public function takes the ``site_config`` instance as a
parameter. There is NO module-level singleton import. Tests build
their own ``SiteConfig(initial_config=...)`` per the conftest pattern.

Why no persistence on the alert row? v1 is transient — diagnoses ride
the Telegram thread and aren't persisted to ``alert_events``. v3 (when
actions enter the picture) may add a ``diagnosis`` column.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)


# Roughly 4 chars per token — used both for context budgeting and for
# truncating the LLM diagnosis to the configured token cap. Cheap +
# good enough for Telegram-friendly cap enforcement; we deliberately
# avoid pulling tiktoken into this hot path.
_CHARS_PER_TOKEN = 4


# Verbatim default from the seed migration
# (20260506_052451_seed_firefighter_ops_triage_app_settings.py). Used
# as the fallback when the ``ops_triage_system_prompt`` setting is
# missing or empty (e.g. fresh install before the seed migration ran,
# or operator deliberately wiped the row).
_FALLBACK_SYSTEM_PROMPT = (
    "You are the Poindexter operator. The system you are diagnosing is "
    "the Poindexter content pipeline -- a self-hosted FastAPI worker, "
    "brain daemon, Postgres + pgvector, Ollama for LLM inference. You "
    "will be shown an alert + curated database state. Your job is to "
    "write ONE SHORT PARAGRAPH (<=400 tokens) explaining: what likely "
    "happened, why you think so (cite the rows you saw), and one "
    "suggested next step the operator could take. Do NOT propose code "
    "changes -- those go to a different escalation path. Do NOT suggest "
    "ALL POSSIBLE causes -- commit to your most likely diagnosis. If "
    "the context is genuinely ambiguous, say so plainly and stop."
)


class _ModelRouterLike(Protocol):
    """Duck-typed interface for the model_router dependency.

    The triage path calls ``await router.invoke(model_class=..., system=...,
    user=...)`` and expects ``{"text": str, "model": str, "tokens": int}``
    (or any object exposing those attributes). Tests pass an
    ``unittest.mock.AsyncMock`` configured to return the right shape.
    """

    async def invoke(
        self,
        *,
        model_class: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_labels(raw: Any) -> dict[str, Any]:
    """``alert_events.labels`` is JSONB — asyncpg returns either a dict
    (with the JSONB codec registered) or a JSON string. Accept both so
    callers don't have to care which path is wired up.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _row_to_dict(row: Any) -> dict[str, Any]:
    """asyncpg ``Record`` rows expose ``dict()``; plain dicts pass through."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {}


def _approx_tokens(payload: Any) -> int:
    """Cheap token estimate — ``len(serialized) / 4``. Good enough for
    budgeting against ``ops_triage_max_context_tokens``; we don't need
    tiktoken-grade accuracy to decide which audit rows to drop.
    """
    try:
        text = json.dumps(payload, default=str)
    except Exception:
        text = str(payload)
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _alert_stem(alertname: str) -> str:
    """Extract a short stem from an alertname for the app_settings filter.

    ``probe_public_site_failed`` -> ``probe`` so we surface
    ``probe_*`` settings. Falls back to the first underscore-separated
    word; empty string when alertname is missing.
    """
    if not alertname:
        return ""
    return alertname.split("_", 1)[0].lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_triage_context(
    pool: Any,
    alert_event_id: int,
    site_config: SiteConfig,
) -> dict[str, Any]:
    """Assemble the curated context bundle the LLM sees.

    Runs (in order, all against the shared pool):

    1. The triggering ``alert_events`` row.
    2. Up to 5 most-recent same-name alerts (excluding the trigger).
    3. 10 most-recent ``audit_log`` rows (newest first).
    4. The ``pipeline_tasks`` row IF the alert's labels contain a
       ``task_id`` (omitted otherwise — no KeyError, just absent).
    5. A snapshot of ``app_settings`` keys whose name starts with the
       alertname's first underscore-separated stem (the simpler of the
       two interpretations in the spec — keeps the surface narrow and
       relevant to the alert without an explicit allowlist).

    Honours ``ops_triage_max_context_tokens`` by dropping the oldest
    audit_log rows one at a time until the assembled context fits.
    Returns the bundle as a plain dict — not pre-serialised, so callers
    can shape the prompt text however they want.
    """
    async with pool.acquire() as conn:
        alert_row = await conn.fetchrow(
            "SELECT id, alertname, status, severity, category, labels, "
            "annotations, starts_at, ends_at, fingerprint, received_at "
            "FROM alert_events WHERE id = $1",
            alert_event_id,
        )
        alert = _row_to_dict(alert_row)
        alertname = (alert.get("alertname") or "") if alert else ""
        labels = _coerce_labels(alert.get("labels")) if alert else {}

        history_rows = await conn.fetch(
            "SELECT id, alertname, status, severity, labels, "
            "annotations, received_at "
            "FROM alert_events "
            "WHERE alertname = $1 AND id <> $2 "
            "ORDER BY received_at DESC LIMIT 5",
            alertname, alert_event_id,
        )
        history = [_row_to_dict(r) for r in history_rows]

        audit_rows = await conn.fetch(
            "SELECT id, timestamp, event_type, source, task_id, "
            "details, severity "
            "FROM audit_log "
            "ORDER BY timestamp DESC LIMIT 10",
        )
        audit = [_row_to_dict(r) for r in audit_rows]

        bundle: dict[str, Any] = {
            "alert": alert,
            "history": history,
            "audit_log": audit,
        }

        task_id = labels.get("task_id") if isinstance(labels, dict) else None
        if task_id:
            task_row = await conn.fetchrow(
                "SELECT id, task_id, task_type, topic, status, stage, "
                "percentage, message, model_used, error_message, "
                "created_at, updated_at, started_at, completed_at "
                "FROM pipeline_tasks WHERE task_id = $1",
                task_id,
            )
            if task_row:
                bundle["pipeline_task"] = _row_to_dict(task_row)

        stem = _alert_stem(alertname)
        if stem:
            settings_rows = await conn.fetch(
                "SELECT key, value FROM app_settings "
                "WHERE is_secret = FALSE AND key LIKE $1 "
                "ORDER BY key LIMIT 25",
                f"{stem}%",
            )
            bundle["app_settings"] = {
                r["key"]: r["value"] for r in settings_rows
            }
        else:
            bundle["app_settings"] = {}

    # Token budget — drop oldest audit_log rows first until we fit.
    max_tokens = site_config.get_int("ops_triage_max_context_tokens", 4000)
    while bundle["audit_log"] and _approx_tokens(bundle) > max_tokens:
        bundle["audit_log"].pop()  # newest-first list; pop tail = drop oldest

    if _approx_tokens(bundle) > max_tokens:
        logger.warning(
            "[firefighter] context still over budget after dropping all "
            "audit_log rows (alert_event_id=%s, max_tokens=%d) -- "
            "downstream prompt will be the truncated bundle as-is",
            alert_event_id, max_tokens,
        )

    return bundle


async def run_triage(
    context: dict[str, Any],
    site_config: SiteConfig,
    model_router: _ModelRouterLike,
) -> dict[str, Any]:
    """Invoke the LLM and return the diagnosis bundle.

    Always returns a dict with ``diagnosis``, ``model``, ``tokens``,
    ``ms`` keys. ``diagnosis`` is an empty string (never None, never
    raises) when the LLM produces nothing — the caller's contract is
    to skip the Telegram follow-up in that case rather than post empty.
    """
    model_class = site_config.get("ops_triage_model_class", "ops_triage")
    max_diag_tokens = site_config.get_int("ops_triage_max_diagnosis_tokens", 400)
    system_prompt = _default_system_prompt(site_config)

    user_payload = json.dumps(context, default=str, ensure_ascii=False)

    started = time.perf_counter()
    try:
        result = await model_router.invoke(
            model_class=model_class,
            system=system_prompt,
            user=user_payload,
            max_tokens=max_diag_tokens,
        )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "[firefighter] model_router.invoke raised: %s (model_class=%s)",
            e, model_class,
        )
        return {"diagnosis": "", "model": model_class, "tokens": 0, "ms": elapsed_ms}

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    text = ""
    model_name = model_class
    tokens = 0
    if isinstance(result, dict):
        text = (result.get("text") or "").strip()
        model_name = result.get("model") or model_class
        tokens = int(result.get("tokens") or 0)
    elif result is not None:
        # Tolerate object-style returns from a future model_router.
        text = (getattr(result, "text", "") or "").strip()
        model_name = getattr(result, "model", model_class) or model_class
        tokens = int(getattr(result, "tokens", 0) or 0)

    if not text:
        return {"diagnosis": "", "model": model_name, "tokens": tokens, "ms": elapsed_ms}

    char_budget = max_diag_tokens * _CHARS_PER_TOKEN
    _SUFFIX = " [...]"
    if len(text) > char_budget:
        keep = max(0, char_budget - len(_SUFFIX))
        text = text[:keep].rstrip() + _SUFFIX

    return {"diagnosis": text, "model": model_name, "tokens": tokens, "ms": elapsed_ms}


def _default_system_prompt(site_config: SiteConfig) -> str:
    """Return the operator-persona system prompt.

    Reads ``ops_triage_system_prompt`` from ``site_config`` (seeded by
    the 20260506_052451 migration) and falls back to the verbatim
    default when the key is missing or empty.
    """
    value = site_config.get("ops_triage_system_prompt", "")
    if value and value.strip():
        return value
    return _FALLBACK_SYSTEM_PROMPT

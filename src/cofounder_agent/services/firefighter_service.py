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
- :func:`_resolve_system_prompt` — operator-persona prompt; resolves via
  :class:`UnifiedPromptManager` (Langfuse → ``skills/ops/triage/SKILL.md``)
  and falls back to the inline ``_FALLBACK_SYSTEM_PROMPT`` — logged at
  ERROR — only when the prompt registry is unreachable.

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


# Prompt key in UnifiedPromptManager. The authoritative body lives at
# skills/ops/triage/SKILL.md::ops.triage.system_prompt (the prompt_templates
# table is retired, poindexter#47 Phase 2); a Langfuse ``production`` label
# can override only behind ``langfuse_prompt_overrides_enabled``.
_PROMPT_KEY = "ops.triage.system_prompt"


# Last-resort fallback for :func:`_resolve_system_prompt`, used only when the
# prompt registry is unreachable (early bootstrap, monkey-patched manager in
# tests). This MUST stay byte-identical to the canonical SKILL.md body —
# INCLUDING the trailing newline the SKILL.md loader emits — or the
# parametrized drift-guard in tests/unit/services/test_prompt_fallback_drift.py
# trips. Edit the SKILL.md body and this constant together.
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
    "the context is genuinely ambiguous, say so plainly and stop.\n"
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
    system_prompt = _resolve_system_prompt()

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


# ---------------------------------------------------------------------------
# Plan B — LLM long-tail action selector.
#
# For an alert with NO deterministic remediation_rule, the brain asks this
# selector (running on the worker, where the LLM libs live) to pick ONE action
# from the registry catalog the brain passes in, or abstain. The safety-critical
# invariant: the returned action_name is ALWAYS one of the catalog names or "".
# Validation lives here — the model's output is untrusted. The confidence
# THRESHOLD is applied by the brain engine (alongside allowlist / breaker /
# rate-cap); this layer only validates the shape and surfaces the score.
# ---------------------------------------------------------------------------

_SELECT_SYSTEM_PROMPT = (
    "You are an SRE remediation action selector for an autonomous ops system. "
    "You are given an ALERT that has no pre-written rule, plus a fixed CATALOG "
    "of remediation actions. Choose the SINGLE best action from the catalog to "
    "try first, or abstain if none is clearly safe and appropriate.\n\n"
    "Respond with ONLY a JSON object — no prose, no code fence:\n"
    '{"action_name": "<exactly one catalog name, or empty string to abstain>", '
    '"params": {<only the params that action documents>}, '
    '"confidence": <float 0.0-1.0>, "reason": "<one short sentence>"}\n\n'
    "Rules: action_name MUST be exactly one of the catalog names, or empty to "
    "abstain. Prefer to abstain (empty action_name, confidence 0) when the alert "
    "is ambiguous or no catalog action addresses it — a wrong action is worse "
    "than paging a human."
)


def _parse_selection_json(text: str) -> dict[str, Any] | None:
    """Best-effort parse of the model's JSON selection.

    Tolerates ```json fences and surrounding prose (small local models emit both).
    Returns the decoded object, or None when no JSON object can be extracted —
    the caller treats None as abstain.
    """
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # ```json\n{...}\n``` or ```\n{...}\n``` — take the fenced body.
        parts = s.split("```")
        if len(parts) >= 3:
            s = parts[1]
        s = s.strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
    # Try the whole (de-fenced) string first, then the first {...} span as a
    # last resort. A narrow parse failure just moves on to the next candidate
    # (``continue``, not a silent swallow); exhausting them returns None, which
    # the caller treats as abstain.
    candidates = [s]
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        candidates.append(s[start : end + 1])
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


async def select_remediation_action(
    *,
    alert: dict[str, Any],
    action_catalog: list[dict[str, Any]],
    site_config: SiteConfig,
    model_router: _ModelRouterLike,
) -> dict[str, Any]:
    """Pick one allowlisted action for an un-ruled alert, or abstain.

    Returns ``{action_name, params, confidence, reason, model, ms}``. On any
    problem — empty catalog, LLM error, unparseable output, or an action_name
    not in ``action_catalog`` — returns ``action_name=""`` (abstain). Never
    raises: the brain reads abstain as "page as usual".
    """
    abstain: dict[str, Any] = {
        "action_name": "", "params": {}, "confidence": 0.0,
        "reason": "", "model": "", "ms": 0,
    }
    catalog_names = {
        str(a["name"]) for a in action_catalog
        if isinstance(a, dict) and a.get("name")
    }
    if not catalog_names:
        return abstain

    model_class = site_config.get("ops_firefighter_model", "ollama/llama3.2:3b")
    user_payload = json.dumps(
        {"alert": alert, "catalog": action_catalog}, default=str, ensure_ascii=False
    )

    started = time.perf_counter()
    try:
        result = await model_router.invoke(
            model_class=model_class, system=_SELECT_SYSTEM_PROMPT,
            user=user_payload, max_tokens=512,
        )
    except Exception as e:  # noqa: BLE001 — selection must not raise into the caller
        logger.warning("[firefighter] selector invoke raised: %s — abstaining", e)
        return abstain
    ms = int((time.perf_counter() - started) * 1000)

    text, model_name = "", model_class
    if isinstance(result, dict):
        text = (result.get("text") or "").strip()
        model_name = result.get("model") or model_class
    elif result is not None:
        text = (getattr(result, "text", "") or "").strip()
        model_name = getattr(result, "model", model_class) or model_class

    parsed = _parse_selection_json(text)
    if not isinstance(parsed, dict):
        logger.info("[firefighter] selector output not parseable JSON — abstaining")
        return {**abstain, "model": model_name, "ms": ms}

    action_name = str(parsed.get("action_name") or "").strip()
    if action_name not in catalog_names:
        # Off-list or empty -> abstain. THE untrusted-output guard: the model can
        # never make the engine run an action the brain didn't offer.
        return {**abstain, "model": model_name, "ms": ms}

    params = parsed.get("params")
    if not isinstance(params, dict):
        params = {}
    raw_conf = parsed.get("confidence")
    try:
        confidence = float(raw_conf) if raw_conf is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(parsed.get("reason") or "")[:500]

    return {
        "action_name": action_name, "params": params, "confidence": confidence,
        "reason": reason, "model": model_name, "ms": ms,
    }


def _resolve_system_prompt() -> str:
    """Return the operator-persona triage system prompt.

    Resolves through :class:`services.prompt_manager.UnifiedPromptManager`,
    which chains the Langfuse ``production`` label → the
    ``skills/ops/triage/SKILL.md`` default. The pre-#485
    ``app_settings.ops_triage_system_prompt`` override key was retired
    (poindexter#485 follow-up) in favour of that stack — operators now tune
    the prompt via the SKILL.md pack (authoritative; PR-managed) with
    Langfuse overrides only behind ``langfuse_prompt_overrides_enabled``,
    per ``docs/architecture/prompt-management.md``.

    ``_FALLBACK_SYSTEM_PROMPT`` is the last-resort fallback for the case
    where ``get_prompt_manager()`` itself fails (DB pool not yet wired
    during early bootstrap, test fixtures that monkey-patch the manager).
    Triage must produce SOME system prompt — an empty system slot is worse
    than the seeded default — so per ``feedback_self_heal_not_suppress`` we
    self-heal LOUDLY: the fallback logs at ERROR so a broken registry
    surfaces instead of silently serving the inline copy.
    """
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(_PROMPT_KEY)
    except Exception as exc:  # noqa: BLE001 — triage must not be gated on the registry
        logger.error(
            "[firefighter] prompt_manager lookup for %r failed (%s) — using "
            "inline fallback. Re-check the ops/triage SKILL.md pack + "
            "UnifiedPromptManager wiring.",
            _PROMPT_KEY, exc,
        )
        return _FALLBACK_SYSTEM_PROMPT

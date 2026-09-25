"""Failure episodes: page a broken brain probe once, not once per pass.

A probe that cannot do its job (bad credential, missing mount, a remote API
that keeps refusing it) fails on every pass until someone fixes it, and only
the operator can. Paging on every pass is spam: on 2026-09-24 the PR staleness
probe paged 286 times for one bad ``gh_token``. The only brake was the
notifier's in-memory ``operator_page_cooldown_minutes``, and every brain
restart reset it. Not paging at all is worse. From 2026-09-23 23:37 UTC the
branch-drift deploy canary failed on every pass for the same token and filed
its failures in audit_log only, so it was blind for two days and nobody was
told.

This module is the middle ground both probes share. An EPISODE is one
uninterrupted run of failing passes. It lives in ``brain_knowledge`` (one
entity/attribute row, JSON value), so a brain restart neither forgets that
it paged nor pages again. The same restart-safe shape is used by
``clock_skew_probe`` and ``data_freshness_probe``.

A failure is one of two kinds:

* LOUD: something only the operator can fix, such as a token GitHub rejects
  or a mount that is missing. It pages the first time it is seen, and after
  that only when it is news (below).
* QUIET: something that usually heals itself, such as a 5xx, a timeout or a
  rate limit. It is never news on its own. It pages once the probe has been
  failing for ``quiet_page_after`` with the operator told nothing, because a
  probe that has been blind that long is news whatever the cause.

The operator is paged when:

* ``new``: a loud failure, and nothing about this episode has reached them;
* ``persisting``: quiet failures have lasted ``quiet_page_after``;
* ``changed``: a loud failure differs from the one the last page reported;
* ``token_replaced``: the credential setting was rewritten after the last
  page and the failure is the same, so the fix did not take (read from
  ``app_settings.updated_at``, never the secret);
* ``undelivered``: the last page this episode decided on reached no channel.
  A failed send must not swallow the page; that is the notifier's own rule;
* ``reminder``: ``repage_hours`` have passed since the last page (0 means
  never).

Every other failing pass is still recorded by the caller (audit_log row,
WARNING log, ok=False). It just does not page. The first clean pass closes
the episode. A recovery note goes out only if a page did.

The comparisons are against what the operator was last TOLD
(``paged_signature`` / ``paged_token_changed_at``), not against the previous
pass. So a quiet blip in the middle of a loud failure (404, 503, 404) does
not page twice, and a changed or replaced-token page that failed to send is
sent again on the next pass. It is not left waiting for the reminder.

Stdlib only, no worker imports: the brain image ships ``poindexter/brain/``
alone (see ``scripts/ci/brain_import_isolation_lint.py``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("brain.failure_episode")

# page_reason values, as they appear in probe summaries and audit rows.
PAGE_NEW = "new"
PAGE_PERSISTING = "persisting"
PAGE_CHANGED = "changed"
PAGE_TOKEN_REPLACED = "token_replaced"
PAGE_UNDELIVERED = "undelivered"
PAGE_REMINDER = "reminder"


@dataclass(frozen=True)
class EpisodeKey:
    """Where one probe keeps one episode, and how its logs are tagged."""

    entity: str  # brain_knowledge.entity (and .source), e.g. "branch_drift_probe"
    attribute: str  # brain_knowledge.attribute, e.g. "failure_episode:<repo>"
    label: str  # log tag without brackets, e.g. "BRANCH_DRIFT"


@dataclass
class FailureOutcome:
    """What one failing pass did to the episode."""

    episode: dict[str, Any]
    reason: str | None  # the page this pass decided on; None = stayed quiet
    paged: bool  # the page reached an external channel


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _coerce_int(val: Any, default: int) -> int:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


def parse_iso8601_utc(raw: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp into a UTC-aware datetime; None on failure."""
    if not raw:
        return None
    try:
        s = str(raw)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        ts = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def fmt_utc(iso: Any) -> str:
    """Render a stored ISO timestamp as ``YYYY-MM-DD HH:MM UTC``."""
    ts = parse_iso8601_utc(iso)
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if ts else "unknown"


def _fmt_hours(td: timedelta) -> str:
    hours = td.total_seconds() / 3600
    return f"{hours:g}h"


# ---------------------------------------------------------------------------
# Persistence: one brain_knowledge row per episode. Never raises.
# ---------------------------------------------------------------------------


async def read_episode(pool: Any, key: EpisodeKey) -> dict[str, Any] | None:
    """Return the open episode, or None."""
    try:
        raw = await pool.fetchval(
            "SELECT value FROM brain_knowledge WHERE entity = $1 AND attribute = $2",
            key.entity, key.attribute,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[%s] failure-episode read failed for %s: %s. Treating the failure "
            "as new, so the operator may be paged again",
            key.label, key.attribute, exc,
        )
        return None
    if not raw:
        return None
    try:
        episode = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[%s] failure-episode row %s is not JSON (%.80r). Starting a new "
            "episode", key.label, key.attribute, raw,
        )
        return None
    return episode if isinstance(episode, dict) else None


async def write_episode(pool: Any, key: EpisodeKey, episode: dict[str, Any]) -> None:
    """Upsert the episode row."""
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, $2, $3, 1.0, $1)
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            key.entity, key.attribute, json.dumps(episode),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[%s] failure-episode write failed for %s: %s. The next failing "
            "pass cannot tell it already paged and may page again",
            key.label, key.attribute, exc,
        )


async def clear_episode(pool: Any, key: EpisodeKey) -> None:
    """Delete the episode row."""
    try:
        await pool.execute(
            "DELETE FROM brain_knowledge WHERE entity = $1 AND attribute = $2",
            key.entity, key.attribute,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[%s] failure-episode clear failed for %s: %s. The recovery note "
            "may be sent again on the next clean pass",
            key.label, key.attribute, exc,
        )


async def read_setting_changed_at(pool: Any, setting_key: str, *, label: str) -> str | None:
    """When an ``app_settings`` row's value was last written (ISO), or None.

    Reads the row's timestamp, never its value, so it is safe on a secret.
    ``updated_at`` moves only when the value is written
    (``app_settings_set_updated_at_trigger`` is ``BEFORE UPDATE OF value``),
    so read-telemetry stamps do not move it.
    """
    try:
        val = await pool.fetchval(
            "SELECT updated_at FROM app_settings WHERE key = $1", setting_key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[%s] could not read %s.updated_at: %s. A replaced %s that still "
            "fails will wait for the next reminder",
            label, setting_key, exc, setting_key,
        )
        return None
    if not isinstance(val, datetime):
        return None
    if val.tzinfo is None:
        val = val.replace(tzinfo=UTC)
    return val.isoformat()


# ---------------------------------------------------------------------------
# The decision (pure)
# ---------------------------------------------------------------------------


def decide_page(
    prev: dict[str, Any] | None,
    *,
    signature: str,
    now_utc: datetime,
    loud: bool = True,
    quiet_page_after: timedelta | None = None,
    token_changed_at: str | None = None,
    repage_hours: int = 0,
) -> tuple[dict[str, Any], str | None]:
    """Fold one failing pass into the episode.

    ``signature`` is the failure's identity: the same signature on the next
    pass is the same failure, not news. ``loud`` / ``quiet_page_after`` are
    described in the module docstring (``quiet_page_after=None`` means a quiet
    failure never pages as news). ``token_changed_at`` is the credential
    setting's ``updated_at`` (see :func:`read_setting_changed_at`).

    Returns ``(episode, page_reason)``; ``page_reason`` is None when this pass
    stays quiet. Pure: the caller persists the episode and, once the page is
    delivered, calls :func:`mark_delivered`.
    """
    now_iso = now_utc.isoformat()
    old = prev or {}
    told_at = parse_iso8601_utc(old.get("paged_at"))
    # Rows written before these three fields existed (#4041) are read as
    # follows. A delivered page reported the row's signature and token. A row
    # with no delivered page still owes its first page, because back then
    # every episode paged the moment it opened.
    told_signature = old.get("paged_signature", old.get("signature") if told_at else None)
    told_token = old.get("paged_token_changed_at", old.get("token_changed_at") if told_at else None)
    owed = old.get("owed", None if (told_at or not prev) else PAGE_NEW)

    episode = dict(old)
    episode["since"] = old.get("since") or now_iso
    episode["attempts"] = _coerce_int(old.get("attempts"), 0) + 1
    episode.setdefault("paged_at", None)
    episode["pages"] = _coerce_int(old.get("pages"), 0)
    episode["signature"] = signature
    if token_changed_at:
        episode["token_changed_at"] = token_changed_at
    else:
        episode.setdefault("token_changed_at", None)
    if told_at and told_token is None and token_changed_at:
        # The page went out while the timestamp was unreadable. Its first
        # successful read is bookkeeping, not evidence of a replacement.
        told_token = token_changed_at
    episode["paged_signature"] = told_signature
    episode["paged_token_changed_at"] = told_token
    episode["owed"] = owed

    since = parse_iso8601_utc(episode["since"]) or now_utc
    reason: str | None = None
    if told_at is None:
        if loud:
            reason = PAGE_UNDELIVERED if owed else PAGE_NEW
        elif quiet_page_after is not None and now_utc - since >= quiet_page_after:
            reason = PAGE_UNDELIVERED if owed else PAGE_PERSISTING
    elif loud and signature != told_signature:
        episode["previous_signature"] = told_signature
        reason = PAGE_CHANGED
    elif loud and token_changed_at and told_token and token_changed_at != told_token:
        reason = PAGE_TOKEN_REPLACED
    elif repage_hours > 0 and now_utc - told_at >= timedelta(hours=repage_hours):
        reason = PAGE_REMINDER
    if loud and reason is None:
        # The operator's last page describes exactly this failure, so an
        # undelivered page about something since superseded is no longer owed.
        episode["owed"] = None
    return episode, reason


def mark_delivered(episode: dict[str, Any], *, now_utc: datetime) -> None:
    """Record on the episode that a page reached the operator."""
    episode["paged_at"] = now_utc.isoformat()
    episode["pages"] = _coerce_int(episode.get("pages"), 0) + 1
    episode["paged_signature"] = episode.get("signature")
    episode["paged_token_changed_at"] = episode.get("token_changed_at")
    episode["owed"] = None


def page_delivered(results: Any) -> bool:
    """Did ``notify_operator`` get the page to an external channel?

    False only when a configured channel FAILED to send, because retrying
    next pass can fix that. No channel configured at all is not retryable
    (alerts.log already has it). A return value this function can't read
    (a test or custom notifier) counts as delivered rather than re-paging
    every pass.
    """
    if not isinstance(results, dict):
        return True
    statuses = [str(results.get(channel) or "") for channel in ("discord", "telegram")]
    if any(s in ("discord", "telegram") or s.startswith("suppressed") for s in statuses):
        return True
    return not any("send failed" in s for s in statuses)


# ---------------------------------------------------------------------------
# Page text shared by every probe
# ---------------------------------------------------------------------------


def episode_lines(
    episode: dict[str, Any],
    *,
    reason: str,
    retry_minutes: int,
    repage_hours: int,
    repage_setting_key: str,
    credential_key: str = "gh_token",
    quiet_page_after: timedelta | None = None,
) -> list[str]:
    """The lines a failure page carries after the probe's own description.

    Avoid ``<word>: value`` shapes for secret-sounding words here:
    ``operator_notifier._fmt_message`` masks ``token: x`` / ``password=x``,
    and a masked page tells the operator nothing.
    """
    lines: list[str] = []
    if reason == PAGE_CHANGED:
        lines.append(
            f"The failure changed (was {episode.get('previous_signature')}, "
            f"now {episode.get('signature')})."
        )
    elif reason == PAGE_TOKEN_REPLACED:
        lines.append(
            f"The {credential_key} was replaced at "
            f"{fmt_utc(episode.get('token_changed_at'))}, and the new token fails "
            f"the same way."
        )
    elif reason == PAGE_PERSISTING and quiet_page_after is not None:
        lines.append(
            f"A failure like this usually clears on its own, so the probe pages "
            f"only once it has lasted {_fmt_hours(quiet_page_after)}. This one has."
        )
    if reason == PAGE_UNDELIVERED or episode.get("owed"):
        lines.append("The previous page about this failure reached no channel.")
    attempts = _coerce_int(episode.get("attempts"), 1)
    lines.append(
        f"Failing since {fmt_utc(episode.get('since'))} "
        f"({attempts} attempt{'s' if attempts != 1 else ''}); the probe retries "
        f"every {retry_minutes} min and stays quiet while the failure is "
        f"unchanged."
    )
    if repage_hours > 0:
        lines.append(
            f"Next reminder in {repage_hours}h if it persists; a recovery note "
            f"follows when it clears."
        )
    else:
        lines.append(
            f"Reminders are off (app_settings.{repage_setting_key}=0); a "
            f"recovery note follows when it clears."
        )
    return lines


def recovery_summary(episode: dict[str, Any]) -> str:
    """``after N failed attempts since <when> (last failure: <signature>)``."""
    attempts = _coerce_int(episode.get("attempts"), 0)
    return (
        f"after {attempts} failed attempt{'s' if attempts != 1 else ''} since "
        f"{fmt_utc(episode.get('since'))} (last failure: "
        f"{episode.get('signature') or 'unknown'})"
    )


# ---------------------------------------------------------------------------
# Orchestration: one failing pass, one clean pass
# ---------------------------------------------------------------------------


def send_page(
    notify_fn: Callable[..., Any],
    *,
    label: str,
    title: str,
    detail: str,
    source: str,
    severity: str,
    dedup_key: str,
    if_undelivered: str = "retrying on the next pass",
) -> bool:
    """Call the notifier. True when the page reached a channel. Never raises.

    ``if_undelivered`` finishes the WARNING logged when it did not, saying
    what happens next.
    """
    try:
        results = notify_fn(
            title=title, detail=detail, source=source,
            severity=severity, dedup_key=dedup_key,
        )
    except Exception as exc:  # noqa: BLE001
        # This system is run from a phone via Telegram/Discord, so a dead
        # notifier reads as "all fine". Say so where someone might look.
        logger.warning(
            "[%s] page %r could not be delivered (%s: %s). The operator was "
            "NOT told; %s",
            label, title, type(exc).__name__, exc, if_undelivered,
        )
        return False
    if page_delivered(results):
        return True
    logger.warning(
        "[%s] page %r reached no channel (%s); %s",
        label, title, results, if_undelivered,
    )
    return False


async def record_failure(
    pool: Any,
    key: EpisodeKey,
    *,
    signature: str,
    detail: str,
    now_utc: datetime,
    notify_fn: Callable[..., Any],
    render: Callable[[dict[str, Any], str], tuple[str, str]],
    source: str,
    dedup_prefix: str,
    loud: bool = True,
    quiet_page_after: timedelta | None = None,
    repage_hours: int = 0,
    credential_key: str | None = None,
    severity: str = "warning",
) -> FailureOutcome:
    """Fold one failing pass into the episode and page if it is news.

    ``render(episode, reason) -> (title, body)`` writes the probe's page.
    The notifier's ``dedup_key`` is ``<dedup_prefix>:<signature>:<pages>``.
    ``pages`` counts the pages delivered in this episode, so every page this
    module decides on is new to the notifier's own cooldown, which would
    otherwise swallow a replaced-token page sent within 30 min of the first
    one and report it as delivered. If the episode cannot be persisted,
    ``pages`` stays 0 and the cooldown is the brake again.
    """
    prev = await read_episode(pool, key)
    token_changed_at = (
        await read_setting_changed_at(pool, credential_key, label=key.label)
        if credential_key else None
    )
    episode, reason = decide_page(
        prev,
        signature=signature,
        now_utc=now_utc,
        loud=loud,
        quiet_page_after=quiet_page_after,
        token_changed_at=token_changed_at,
        repage_hours=repage_hours,
    )
    episode["last_detail"] = detail[:500]

    paged = False
    if reason is None:
        # A loud failure the operator has not heard about always pages, so a
        # quiet pass is either already reported or a quiet failure.
        if episode.get("paged_at"):
            why = f"already paged at {episode.get('paged_at')}"
        elif quiet_page_after is None:
            why = "this kind of failure never pages on its own"
        else:
            why = f"it pages only after {_fmt_hours(quiet_page_after)} of failing"
        if episode.get("owed"):
            why += f"; the undelivered {episode['owed']!r} page waits for the next loud failure"
        logger.info(
            "[%s] not paging for %s (attempt %s since %s): %s",
            key.label, signature, episode.get("attempts"), episode.get("since"), why,
        )
    else:
        title, body = render(episode, reason)
        paged = send_page(
            notify_fn,
            label=key.label,
            title=title,
            detail=body,
            source=source,
            severity=severity,
            dedup_key=f"{dedup_prefix}:{signature}:{episode.get('pages', 0)}",
        )
        if paged:
            mark_delivered(episode, now_utc=now_utc)
        else:
            episode["owed"] = reason

    await write_episode(pool, key, episode)
    return FailureOutcome(episode=episode, reason=reason, paged=paged)


async def close_episode(pool: Any, key: EpisodeKey) -> dict[str, Any] | None:
    """End the open episode after a clean pass; return it, or None if none.

    The caller sends the recovery note, and only when
    ``episode.get("paged_at")`` is set. An episode nobody was told about has
    nothing to take back.
    """
    episode = await read_episode(pool, key)
    if not episode:
        return None
    await clear_episode(pool, key)
    return episode

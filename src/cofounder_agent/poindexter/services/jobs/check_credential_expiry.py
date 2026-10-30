"""CheckCredentialExpiryJob — warn before a stored API key expires.

The Lemon Squeezy API key was created with a one-year lifetime and expired at
20:00 on 2026-10-24, six months to the day after it was stored. Nothing knew
the date, so the first sign was ``sync_pro_subscriptions`` failing with 401 on
every run (81 findings before the key was replaced) while new Pro subscribers
went unsynced. The replacement expires 2027-10-25.

That key is a JWT, so its own ``exp`` claim says when it expires: no API call
and nothing to configure. Every key named in ``credential_expiry_watch_keys``
is read through the secret-aware ``get_secret`` and decoded locally, and a
``credential_expiring`` finding is emitted once it is within
``credential_expiry_warn_days`` of expiry (critical once it has expired). The
key is never logged or sent anywhere; only its expiry date and days left are.

A key that is unset is skipped (not configured is not a fault). A key that is
not a JWT, or has no ``exp``, has no readable expiry and is reported in the
job detail only, since nothing about it can be known in advance.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

DEFAULT_WATCH_KEYS = "lemon_squeezy_api_key"
DEFAULT_WARN_DAYS = 21


def jwt_expiry(token: str) -> datetime | None:
    """The ``exp`` of a JWT as an aware datetime, or None when there isn't one.

    Decodes the payload only; the signature is not checked, because the date is
    all that is wanted and the token came from our own settings.
    """
    parts = (token or "").strip().split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (ValueError, TypeError):
        return None
    exp = payload.get("exp") if isinstance(payload, dict) else None
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return None
    return datetime.fromtimestamp(exp, UTC)


def _int(sc: Any, key: str, default: int) -> int:
    try:
        return int(sc.get(key, default))
    except (TypeError, ValueError):
        return default


class CheckCredentialExpiryJob:
    name = "check_credential_expiry"
    description = "Warn before an API key stored as a JWT reaches its expiry date"
    schedule = "every 24 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no _site_config in job config — skipping", changes_made=0)
        keys = [
            k.strip() for k in str(sc.get("credential_expiry_watch_keys", DEFAULT_WATCH_KEYS) or "").split(",")
            if k.strip()
        ]
        warn_days = _int(sc, "credential_expiry_warn_days", DEFAULT_WARN_DAYS)
        now = datetime.now(UTC)
        notes: list[str] = []
        metrics: dict[str, Any] = {}
        warned = 0
        for key in keys:
            try:
                secret = (await sc.get_secret(key, "") or "").strip()
            except Exception as exc:  # noqa: BLE001 — one unreadable key must not stop the rest
                notes.append(f"{key}: unreadable ({describe_exception(exc)})")
                continue
            if not secret:
                notes.append(f"{key}: not set")
                continue
            expires = jwt_expiry(secret)
            if expires is None:
                notes.append(f"{key}: no readable expiry")
                continue
            days_left = (expires - now).total_seconds() / 86400
            metrics[f"{key}_days_left"] = round(days_left, 1)
            notes.append(f"{key}: expires {expires.date().isoformat()} ({days_left:.0f} days)")
            if days_left > warn_days:
                continue
            expired = days_left <= 0
            warned += 1
            emit_finding(
                source="check_credential_expiry",
                kind="credential_expiring",
                severity="critical" if expired else "warn",
                title=(
                    f"{key} expired on {expires.date().isoformat()}" if expired
                    else f"{key} expires in {days_left:.0f} day(s), on {expires.date().isoformat()}"
                ),
                body=(
                    f"The stored `{key}` is a key with an expiry date "
                    f"({expires.isoformat(timespec='minutes')}). "
                    + ("It has expired: every call that uses it now fails. "
                       if expired else "When it passes, every call that uses it will fail. ")
                    + f"Create a new key with the provider, then run `poindexter settings set {key} <new-key>`."
                ),
                dedup_key=f"credential_expiring:{key}",
                extra={"key": key, "expires_at": expires.isoformat(), "days_left": round(days_left, 1)},
            )
        detail = "; ".join(notes) or "no keys to watch"
        logger.info("[key_expiry] %s", detail)
        return JobResult(ok=True, detail=detail, changes_made=warned, metrics=metrics)


__all__ = ["CheckCredentialExpiryJob", "jwt_expiry"]

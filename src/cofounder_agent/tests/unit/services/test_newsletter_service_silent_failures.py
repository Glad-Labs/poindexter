"""Unit tests — newsletter send-log write visibility (idempotency ledger).

Silent-excepts OLD-debt burn-down (batch 21). ``_log_send`` writes each send
attempt to ``campaign_email_logs``. That table is not merely a delivery log — it
is the campaign's **idempotency ledger**: ``_already_delivered_ids()`` selects
``subscriber_id WHERE campaign_name=$1 AND delivery_status='delivered'``, and
``send_post_newsletter`` skips every subscriber in that set. The newsletter
fires from several go-live seams (immediate publish / promote / gate-clear
re-trigger / scheduled promote) and two can legitimately run for the same post.

So a swallowed ``_log_send(status="delivered")`` means the subscriber WAS
emailed but no ``delivered`` row exists — and the next legitimate re-fire mails
them AGAIN. Silent duplicate email to real subscribers, not merely lost
tracking. That is why this finding is ``warn`` (Discord-routable) rather than
the burn-down's usual non-paging ``info``: the consequence reaches real people
and is not self-correcting once sent.

Aggregated, never per-subscriber: ``_log_send`` takes an optional ``failures``
sink and ONE finding is emitted after the send loop. (``emit_finding``'s dedup
gates routing, not persistence — ``audit_log_bg`` still writes a row per call,
so emitting per subscriber would write-amplify audit_log.)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.newsletter_service import send_post_newsletter

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_KIND = "newsletter_send_log_write_failed"


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.get_bool.side_effect = lambda k, d=False: {
        "newsletter_enabled": True,
        "smtp_use_tls": True,
    }.get(k, d)
    cfg.get.side_effect = lambda k, d="": {
        "newsletter_provider": "resend",
        "newsletter_from_email": "x@y.com",
        "newsletter_from_name": "Test",
        "resend_api_key": "re_test_key",
        "smtp_host": "",
        "smtp_user": "",
    }.get(k, d)
    cfg.get_int.side_effect = lambda k, d=0: {
        "smtp_port": 587,
        "newsletter_batch_size": 50,
        "newsletter_batch_delay_seconds": 0,
    }.get(k, d)

    async def _get_secret(k, d=""):
        return {"smtp_password": "", "resend_api_key": "re_test_key"}.get(k, d)

    cfg.get_secret = AsyncMock(side_effect=_get_secret)
    return cfg


def _pool(log_write_raises: bool) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[
        [
            {"id": 1, "email": "a@b.com", "first_name": "A", "unsubscribe_token": "t1"},
            {"id": 2, "email": "c@d.com", "first_name": "C", "unsubscribe_token": "t2"},
        ],
        [],  # _already_delivered_ids -> nobody delivered yet
    ])
    if log_write_raises:
        pool.execute = AsyncMock(side_effect=RuntimeError("campaign_email_logs down"))
    else:
        pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool


async def test_send_log_write_failures_emit_one_warn_finding(monkeypatch):
    """Two lost 'delivered' rows produce ONE warn finding, not one per subscriber."""
    calls = _capture(monkeypatch)
    pool = _pool(log_write_raises=True)

    with patch(
        "services.newsletter_service._send_via_resend", new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = (True, None)
        result = await send_post_newsletter(
            pool, "New Post", "Excerpt", "new-post", site_config=_cfg(),
        )

    # The sends themselves succeeded — only the ledger writes failed.
    assert result["sent"] == 2

    findings = [c for c in calls if c["kind"] == _KIND]
    assert len(findings) == 1, "expected ONE aggregate finding, not one per subscriber"
    # Escalated above the usual info: this silently breaks send idempotency.
    assert findings[0].get("severity") == "warn"
    # The delivered-row count is what tells an operator re-mailing is possible.
    assert "2" in findings[0]["body"]


async def test_healthy_send_log_writes_emit_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _pool(log_write_raises=False)

    with patch(
        "services.newsletter_service._send_via_resend", new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = (True, None)
        result = await send_post_newsletter(
            pool, "New Post", "Excerpt", "new-post", site_config=_cfg(),
        )

    assert result["sent"] == 2
    assert [c for c in calls if c["kind"] == _KIND] == []

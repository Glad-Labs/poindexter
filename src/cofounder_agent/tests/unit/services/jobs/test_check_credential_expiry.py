"""CheckCredentialExpiryJob: warn before a stored JWT API key expires (2026-10-30)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from poindexter.services.jobs import check_credential_expiry as mod


def _jwt(exp: datetime | None) -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    payload = {"sub": "1"} if exp is None else {"sub": "1", "exp": exp.timestamp()}
    return f"{seg({'alg': 'RS256', 'typ': 'JWT'})}.{seg(payload)}.c2lnbmF0dXJl"


class _SC:
    def __init__(self, secrets, **values):
        self.secrets = secrets
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    async def get_secret(self, key, default=""):
        return self.secrets.get(key, default)


@pytest.fixture
def findings(monkeypatch):
    out = []
    monkeypatch.setattr(mod, "emit_finding", lambda **kw: out.append(kw))
    return out


def test_jwt_expiry_reads_exp_and_rejects_non_jwts():
    when = datetime(2027, 10, 25, tzinfo=UTC)
    assert mod.jwt_expiry(_jwt(when)) == when
    assert mod.jwt_expiry(_jwt(None)) is None
    assert mod.jwt_expiry("sk_live_plain_key") is None
    assert mod.jwt_expiry("a.!!!.c") is None


@pytest.mark.asyncio
async def test_a_key_far_from_expiry_is_quiet(findings):
    sc = _SC({"lemon_squeezy_api_key": _jwt(datetime.now(UTC) + timedelta(days=360))})
    result = await mod.CheckCredentialExpiryJob().run(None, {"_site_config": sc})
    assert result.ok and findings == []
    assert 359 < result.metrics["lemon_squeezy_api_key_days_left"] < 361


@pytest.mark.asyncio
async def test_a_key_inside_the_warning_window_warns(findings):
    sc = _SC({"lemon_squeezy_api_key": _jwt(datetime.now(UTC) + timedelta(days=10))})
    await mod.CheckCredentialExpiryJob().run(None, {"_site_config": sc})
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "credential_expiring" and f["severity"] == "warn"
    assert f["dedup_key"] == "credential_expiring:lemon_squeezy_api_key"
    assert "poindexter settings set lemon_squeezy_api_key" in f["body"]


@pytest.mark.asyncio
async def test_an_expired_key_is_critical(findings):
    sc = _SC({"lemon_squeezy_api_key": _jwt(datetime.now(UTC) - timedelta(days=1))})
    await mod.CheckCredentialExpiryJob().run(None, {"_site_config": sc})
    assert findings[0]["severity"] == "critical" and "expired" in findings[0]["title"]


@pytest.mark.asyncio
async def test_the_key_itself_never_appears_in_output(findings):
    token = _jwt(datetime.now(UTC) + timedelta(days=5))
    sc = _SC({"lemon_squeezy_api_key": token})
    result = await mod.CheckCredentialExpiryJob().run(None, {"_site_config": sc})
    blob = json.dumps([findings, result.detail, result.metrics], default=str)
    assert token not in blob and token.split(".")[1] not in blob


@pytest.mark.asyncio
async def test_unset_and_unreadable_keys_are_noted_not_alerted(findings):
    sc = _SC({"b": "plain-key"}, credential_expiry_watch_keys="a, b", credential_expiry_warn_days="30")
    result = await mod.CheckCredentialExpiryJob().run(None, {"_site_config": sc})
    assert findings == []
    assert "a: not set" in result.detail and "b: no readable expiry" in result.detail

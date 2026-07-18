"""Unit tests — media_assets URL stamp visibility.

Silent-excepts OLD-debt burn-down (batch 20). After a successful R2 upload,
``_update_media_asset_url`` stamps the public URL onto the existing
``media_assets`` row. Swallowed at DEBUG only (invisible in prod, INFO+), a
failure leaves the file uploaded but the row still pointing at a stale/absent
URL — the highest user-visible impact in this cohort, since the site renders
from that row (broken or outdated image) while every log looks healthy.

``media_assets`` is not ``audit_log``, so ``emit_finding`` is the correct signal
(contrast batches 17-19, where the failing write WAS audit_log and a finding
would have vanished in the same outage).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services.r2_upload_service import R2UploadService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _Conn:
    def __init__(self, raises: bool) -> None:
        self._raises = raises
        self.executed: list[Any] = []

    async def execute(self, *args: Any) -> str:
        if self._raises:
            raise RuntimeError("media_assets update boom")
        self.executed.append(args)
        return "UPDATE 1"


class _AcquireCtx:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_a) -> bool:
        return False


class _Pool:
    def __init__(self, raises: bool = False) -> None:
        self.conn = _Conn(raises)

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self.conn)


def _svc(pool: _Pool) -> R2UploadService:
    """Build the service without running __init__ — the method under test only
    reaches ``self._site_config._pool``."""
    svc = object.__new__(R2UploadService)
    # A stand-in SiteConfig: the method only reads ``._pool`` off it.
    svc._site_config = SimpleNamespace(_pool=pool)  # type: ignore[assignment]
    return svc


_KW = {
    "post_id": "post-1",
    "asset_type": "featured",
    "storage_path": "images/post-1/featured.png",
    "public_url": "https://cdn.example.test/images/post-1/featured.png",
}


async def test_media_asset_url_update_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)

    # Never raises — the upload already succeeded; stamping is best-effort.
    await _svc(_Pool(raises=True))._update_media_asset_url(**_KW)

    findings = [c for c in calls if c["kind"] == "media_asset_url_update_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "media_asset_url_update_failed" in findings[0]["dedup_key"]


async def test_media_asset_url_update_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _Pool(raises=False)

    await _svc(pool)._update_media_asset_url(**_KW)

    assert pool.conn.executed, "expected the media_assets UPDATE to run"
    assert calls == []

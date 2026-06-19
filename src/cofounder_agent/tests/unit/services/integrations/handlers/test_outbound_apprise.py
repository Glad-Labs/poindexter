"""Unit tests for the generic outbound.apprise_notify handler."""

from __future__ import annotations

import pytest

from services.integrations.handlers import outbound_apprise


class _FakeSiteConfig:
    def __init__(self, secrets: dict[str, str] | None = None):
        self._secrets = secrets or {}

    async def get_secret(self, key: str, default: str = "") -> str | None:
        return self._secrets.get(key)


class _FakeApprise:
    """Stand-in for apprise.Apprise — records add()/notify() calls."""

    instances: list[_FakeApprise] = []
    add_returns = True
    notify_returns = True

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.notified: list[dict[str, str]] = []
        _FakeApprise.instances.append(self)

    def add(self, url: str) -> bool:
        self.urls.append(url)
        return type(self).add_returns

    def notify(self, body: str = "", title: str = "", **kwargs) -> bool:
        self.notified.append({"body": body, "title": title})
        return type(self).notify_returns


@pytest.fixture(autouse=True)
def _patch_apprise(monkeypatch):
    _FakeApprise.instances = []
    _FakeApprise.add_returns = True
    _FakeApprise.notify_returns = True
    monkeypatch.setattr(outbound_apprise.apprise, "Apprise", _FakeApprise)
    return _FakeApprise


@pytest.mark.asyncio
async def test_telegram_template_builds_url(_patch_apprise):
    row = {
        "name": "telegram_ops",
        "secret_key_ref": "telegram_bot_token",
        "config": {"chat_id": "42", "apprise_url": "tgram://{secret}/{chat_id}/"},
    }
    result = await outbound_apprise.apprise_notify(
        "hi",
        site_config=_FakeSiteConfig({"telegram_bot_token": "TOKEN"}),
        row=row,
        pool=None,
    )
    assert result == {"delivered": True}
    assert _patch_apprise.instances[0].urls == ["tgram://TOKEN/42/"]
    assert _patch_apprise.instances[0].notified == [{"body": "hi", "title": ""}]


@pytest.mark.asyncio
async def test_discord_secret_passthrough(_patch_apprise):
    webhook = "https://discord.com/api/webhooks/123/abc"
    row = {
        "name": "discord_ops",
        "secret_key_ref": "discord_ops_webhook_url",
        "config": {"apprise_url": "{secret}"},
    }
    await outbound_apprise.apprise_notify(
        {"content": "ping"},
        site_config=_FakeSiteConfig({"discord_ops_webhook_url": webhook}),
        row=row,
        pool=None,
    )
    assert _patch_apprise.instances[0].urls == [webhook]
    assert _patch_apprise.instances[0].notified[0]["body"] == "ping"


@pytest.mark.asyncio
async def test_dict_payload_text_key(_patch_apprise):
    row = {"name": "r", "config": {"apprise_url": "json://localhost"}}
    await outbound_apprise.apprise_notify(
        {"text": "from-text-key"},
        site_config=_FakeSiteConfig(),
        row=row,
        pool=None,
    )
    assert _patch_apprise.instances[0].notified[0]["body"] == "from-text-key"


@pytest.mark.asyncio
async def test_missing_apprise_url_raises():
    row = {"name": "telegram_ops", "config": {}}
    with pytest.raises(RuntimeError, match="apprise_url"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_unknown_placeholder_raises():
    row = {"name": "r", "config": {"apprise_url": "x://{nope}"}}
    with pytest.raises(RuntimeError, match="nope"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_secret_placeholder_without_secret_raises():
    # No secret_key_ref on the row -> resolve_secret returns None.
    row = {"name": "r", "config": {"apprise_url": "{secret}"}}
    with pytest.raises(RuntimeError, match="secret"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_add_rejects_url_raises(_patch_apprise):
    _patch_apprise.add_returns = False
    row = {"name": "r", "config": {"apprise_url": "not-a-real-scheme://"}}
    with pytest.raises(RuntimeError, match="rejected"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_notify_failure_raises(_patch_apprise):
    _patch_apprise.notify_returns = False
    row = {"name": "r", "config": {"apprise_url": "json://localhost"}}
    with pytest.raises(RuntimeError, match="delivery failed"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_invalid_payload_type_raises():
    row = {"name": "r", "config": {"apprise_url": "json://localhost"}}
    with pytest.raises(TypeError):
        await outbound_apprise.apprise_notify(
            12345, site_config=_FakeSiteConfig(), row=row, pool=None
        )

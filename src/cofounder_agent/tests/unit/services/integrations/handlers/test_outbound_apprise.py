"""Unit tests for the generic outbound.apprise_notify handler."""

from __future__ import annotations

import pytest

from services.integrations.handlers import outbound_apprise


class _FakeSiteConfig:
    def __init__(
        self,
        secrets: dict[str, str] | None = None,
        settings: dict[str, str] | None = None,
    ):
        self._secrets = secrets or {}
        self._settings = settings or {}

    async def get_secret(self, key: str, default: str = "") -> str | None:
        return self._secrets.get(key)

    def get(self, key: str, default: str = "") -> str:
        # Mirrors SiteConfig.get — sync, reads the in-memory NON-secret cache.
        # Secrets are deliberately absent here (is_secret rows are filtered
        # out of the cache), so a placeholder can never resolve a secret via
        # this path — secrets must go through {secret} + secret_key_ref.
        return self._settings.get(key, default)


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


@pytest.mark.asyncio
async def test_chat_id_resolves_from_app_settings(_patch_apprise):
    """A placeholder absent from config resolves from app_settings (site_config).

    This is how telegram_ops works after the 2026-06-19 leak fix: the seed
    carries NO chat_id, and {telegram_chat_id} resolves from
    app_settings.telegram_chat_id at send time — the same per-operator path
    the bot token uses via secret_key_ref.
    """
    row = {
        "name": "telegram_ops",
        "secret_key_ref": "telegram_bot_token",
        "config": {"apprise_url": "tgram://{secret}/{telegram_chat_id}/"},
    }
    result = await outbound_apprise.apprise_notify(
        "hi",
        site_config=_FakeSiteConfig(
            secrets={"telegram_bot_token": "TOKEN"},
            settings={"telegram_chat_id": "987654321"},
        ),
        row=row,
        pool=None,
    )
    assert result == {"delivered": True}
    assert _patch_apprise.instances[0].urls == ["tgram://TOKEN/987654321/"]


@pytest.mark.asyncio
async def test_unconfigured_chat_id_fails_loud(_patch_apprise):
    """An empty (unconfigured) chat_id must fail loud — never send to a blank chat.

    A fresh install seeds telegram_ops with no chat_id; until the operator
    sets app_settings.telegram_chat_id, the placeholder resolves empty. The
    handler must raise rather than build ``tgram://TOKEN//`` (a send to a
    blank destination). Per feedback_no_silent_defaults.
    """
    row = {
        "name": "telegram_ops",
        "secret_key_ref": "telegram_bot_token",
        "config": {"apprise_url": "tgram://{secret}/{telegram_chat_id}/"},
    }
    with pytest.raises(RuntimeError, match="resolved empty"):
        await outbound_apprise.apprise_notify(
            "hi",
            site_config=_FakeSiteConfig(
                secrets={"telegram_bot_token": "TOKEN"},
                settings={"telegram_chat_id": ""},  # operator hasn't configured it
            ),
            row=row,
            pool=None,
        )
    # Nothing was constructed/sent — the failure is pre-delivery.
    assert _patch_apprise.instances == []


@pytest.mark.asyncio
async def test_config_value_takes_precedence_over_app_settings(_patch_apprise):
    """A non-empty config value wins over app_settings (legacy rows keep working).

    Matt's pre-existing prod telegram_ops row still carries
    ``{"chat_id": "<id>", "apprise_url": "tgram://{secret}/{chat_id}/"}``.
    Config precedence means that un-migrated row resolves from its own
    config exactly as before — the app_settings fallback is additive.
    """
    row = {
        "name": "telegram_ops",
        "secret_key_ref": "telegram_bot_token",
        "config": {"chat_id": "42", "apprise_url": "tgram://{secret}/{chat_id}/"},
    }
    await outbound_apprise.apprise_notify(
        "hi",
        site_config=_FakeSiteConfig(
            secrets={"telegram_bot_token": "TOKEN"},
            settings={"chat_id": "999"},  # must be ignored — config wins
        ),
        row=row,
        pool=None,
    )
    assert _patch_apprise.instances[0].urls == ["tgram://TOKEN/42/"]

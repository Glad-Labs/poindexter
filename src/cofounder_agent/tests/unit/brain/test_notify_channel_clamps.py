"""Unit tests — notification channel length clamps (GlitchTip triage 2026-07-02).

Telegram's sendMessage rejects text over 4096 chars and Discord webhooks
reject content over 2000 chars — both with HTTP 400. The brain had no
clamp on either channel, so the ~3.9K-char ``settings_zero_reader_keys``
finding silently killed its own page on BOTH channels every 6 hours from
06-30 (GlitchTip #561: 23 Telegram 400s; the Discord failure surfaced as
the misleading "no discord_ops_webhook_url" dispatch error).

These tests pin the clamps by capturing the exact JSON payload handed to
``urllib.request.urlopen`` — the assertion is on what would go over the
wire, not on an internal variable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_daemon_silent_failures.py.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402


class _FakeResponse:
    status = 200

    def read(self) -> bytes:
        return b'{"ok": true, "result": {"message_id": 42}}'


def _capture_urlopen(monkeypatch) -> list:
    """Patch urllib.request.urlopen to record Request payloads + return 2xx."""
    sent: list[dict] = []

    def _fake_urlopen(req, timeout=None):  # noqa: ARG001 — urllib signature
        sent.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return sent


def _telegram_pool() -> MagicMock:
    """Pool stub whose fetchrow satisfies secret_reader's row contract."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={"value": "test-token", "is_secret": False}
    )
    return pool


@pytest.mark.unit
@pytest.mark.asyncio
class TestTelegramClamp:
    async def test_oversized_message_is_clamped_to_telegram_limit(
        self, monkeypatch,
    ):
        sent = _capture_urlopen(monkeypatch)

        result = await bd.send_telegram("x" * 6000, pool=_telegram_pool())

        assert result == 42  # send still succeeds
        assert len(sent) == 1
        text = sent[0]["text"]
        assert len(text) <= 4096
        assert text.endswith("… [truncated]")

    async def test_normal_message_passes_through_unclamped(self, monkeypatch):
        sent = _capture_urlopen(monkeypatch)

        await bd.send_telegram("all good", pool=_telegram_pool())

        assert sent[0]["text"] == "🧠 Brain: all good"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDiscordClamp:
    async def test_oversized_message_is_clamped_to_discord_soft_cap(
        self, monkeypatch,
    ):
        sent = _capture_urlopen(monkeypatch)

        result = await bd.send_discord(
            "y" * 4000, webhook_url="https://discord.test/webhook",
        )

        assert result  # truthy — the send was accepted
        content = sent[0]["content"]
        # 1900-char soft cap (docstring contract) leaves room for suffixes,
        # comfortably under Discord's 2000 hard limit.
        assert len(content) <= 1900
        assert content.endswith("… [truncated]")

    async def test_normal_message_passes_through_unclamped(self, monkeypatch):
        sent = _capture_urlopen(monkeypatch)

        await bd.send_discord(
            "ops alert", webhook_url="https://discord.test/webhook",
        )

        assert sent[0]["content"] == "ops alert"

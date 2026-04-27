"""Unit tests for ``services.publish_adapters.youtube``.

The adapter ships **inert** until the operator opts in via OAuth (the
gating Glad-Labs/poindexter#40 issue). These tests cover Protocol
conformance, the gating matrix, publish() truncation rules,
scheduled-at rewrite logic, ImportError → actionable error,
status() one-shot semantics, and stream_progress() yielding exactly
one snapshot.

All Google APIs (google.oauth2, googleapiclient) are mocked via the
``_build_credentials``, ``_do_resumable_upload_blocking``, and
``_do_status_blocking`` static methods on the adapter — no real
HTTP traffic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.publish_adapter import PublishAdapter, PublishResult
from services.publish_adapters.youtube import YouTubePublishAdapter


class _StubSiteConfig:
    """Minimal site_config double — separate sync get + async get_secret."""

    def __init__(
        self,
        *,
        settings: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self._settings = {
            f"plugin.publish_adapter.youtube.{k}": v
            for k, v in (settings or {}).items()
        }
        self._secrets = {
            f"plugin.publish_adapter.youtube.{k}": v
            for k, v in (secrets or {}).items()
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    async def get_secret(self, key: str, default: str = "") -> str:
        return self._secrets.get(key, default)


def _make_adapter(
    *,
    enabled: bool = True,
    secrets: dict[str, str] | None = None,
    settings: dict[str, Any] | None = None,
) -> YouTubePublishAdapter:
    s = dict(settings or {})
    s["enabled"] = enabled
    return YouTubePublishAdapter(
        site_config=_StubSiteConfig(settings=s, secrets=secrets),
    )


_FULL_SECRETS = {
    "client_id": "cid",
    "client_secret": "csecret",
    "refresh_token": "rtok",
}


def _make_media_file(tmp_path) -> str:
    p = tmp_path / "video.mp4"
    p.write_bytes(b"\x00" * 1024)
    return str(p)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_publish_adapter_protocol(self):
        assert isinstance(YouTubePublishAdapter(), PublishAdapter)

    def test_class_attributes(self):
        a = YouTubePublishAdapter()
        assert a.name == "youtube"
        assert a.supports_short is True
        assert a.supports_long is True


# ---------------------------------------------------------------------------
# _check_gating
# ---------------------------------------------------------------------------


class TestCheckGating:
    @pytest.mark.asyncio
    async def test_disabled_returns_not_ready(self):
        adapter = _make_adapter(enabled=False, secrets=_FULL_SECRETS)
        ready, error, secrets = await adapter._check_gating()
        assert ready is False
        assert error is not None
        assert "disabled" in error
        assert secrets == {}

    @pytest.mark.asyncio
    async def test_missing_client_id_returns_not_ready(self):
        adapter = _make_adapter(
            enabled=True,
            secrets={"client_id": "", "client_secret": "x", "refresh_token": "y"},
        )
        ready, error, secrets = await adapter._check_gating()
        assert ready is False
        assert error is not None
        assert "OAuth" in error
        # Pointer to the gating ticket
        assert "#40" in error
        assert secrets == {}

    @pytest.mark.asyncio
    async def test_missing_client_secret_returns_not_ready(self):
        adapter = _make_adapter(
            enabled=True,
            secrets={"client_id": "x", "client_secret": "", "refresh_token": "y"},
        )
        ready, error, _ = await adapter._check_gating()
        assert ready is False
        assert "#40" in (error or "")

    @pytest.mark.asyncio
    async def test_missing_refresh_token_returns_not_ready(self):
        adapter = _make_adapter(
            enabled=True,
            secrets={"client_id": "x", "client_secret": "y", "refresh_token": ""},
        )
        ready, error, _ = await adapter._check_gating()
        assert ready is False
        assert "#40" in (error or "")

    @pytest.mark.asyncio
    async def test_all_secrets_present_returns_ready(self):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        ready, error, secrets = await adapter._check_gating()
        assert ready is True
        assert error is None
        assert secrets["client_id"] == "cid"
        assert secrets["client_secret"] == "csecret"
        assert secrets["refresh_token"] == "rtok"


# ---------------------------------------------------------------------------
# publish() — gating short-circuits
# ---------------------------------------------------------------------------


class TestPublishGating:
    @pytest.mark.asyncio
    async def test_disabled_returns_failure_without_calling_google(self, tmp_path):
        adapter = _make_adapter(enabled=False, secrets=_FULL_SECRETS)
        result = await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="x",
        )
        assert isinstance(result, PublishResult)
        assert result.platform == "youtube"
        assert result.success is False
        assert "disabled" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_secrets_returns_failure(self, tmp_path):
        adapter = _make_adapter(enabled=True, secrets={"client_id": "x"})
        result = await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="x",
        )
        assert result.success is False
        assert "#40" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_media_path_returns_failure(self, tmp_path):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        result = await adapter.publish(
            media_path=str(tmp_path / "missing.mp4"),
            title="x",
        )
        assert result.success is False
        assert "media_path" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_media_path_returns_failure(self):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        result = await adapter.publish(media_path="", title="x")
        assert result.success is False
        assert "media_path" in (result.error or "")


# ---------------------------------------------------------------------------
# publish() — happy path + body shape
# ---------------------------------------------------------------------------


class TestPublishHappyPath:
    @pytest.mark.asyncio
    async def test_returns_external_id_and_url(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        # Bypass the actual google-auth Credentials construction
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )

        captured: dict[str, Any] = {}

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            captured["media_path"] = media_path
            return {
                "id": "abc123",
                "snippet": {
                    "channelId": "UCxxxxx",
                    "publishedAt": "2026-04-26T00:00:00Z",
                },
                "status": {
                    "uploadStatus": "uploaded",
                    "privacyStatus": "public",
                },
            }

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        result = await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="My video",
            description="hello",
            tags=["a", "b"],
        )

        assert result.success is True
        assert result.external_id == "abc123"
        assert result.public_url == "https://www.youtube.com/watch?v=abc123"
        assert result.status in ("uploaded", "public")
        assert result.error is None
        assert result.metadata["channel_id"] == "UCxxxxx"
        # Cost = 0 (free quota), is_local=False on cost-guard side.
        assert result.cost_usd == 0.0
        # Body shape
        assert captured["body"]["snippet"]["title"] == "My video"
        assert captured["body"]["snippet"]["description"] == "hello"
        assert captured["body"]["snippet"]["tags"] == ["a", "b"]


# ---------------------------------------------------------------------------
# publish() — truncation + scheduling rules
# ---------------------------------------------------------------------------


class TestPublishTruncation:
    @pytest.mark.asyncio
    async def test_title_truncated_to_100_chars(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        captured: dict[str, Any] = {}

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            return {"id": "x", "snippet": {}, "status": {}}

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        long_title = "x" * 250
        await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title=long_title,
        )
        assert len(captured["body"]["snippet"]["title"]) == 100

    @pytest.mark.asyncio
    async def test_description_truncated_to_5000_chars(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        captured: dict[str, Any] = {}

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            return {"id": "x", "snippet": {}, "status": {}}

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        long_desc = "y" * 7000
        await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="t",
            description=long_desc,
        )
        assert len(captured["body"]["snippet"]["description"]) == 5000

    @pytest.mark.asyncio
    async def test_tags_capped_at_30(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        captured: dict[str, Any] = {}

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            return {"id": "x", "snippet": {}, "status": {}}

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        too_many = [f"tag{i}" for i in range(50)]
        await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="t",
            tags=too_many,
        )
        # 30-tag cap + empty/whitespace stripped earlier
        assert len(captured["body"]["snippet"]["tags"]) == 30
        # First tags preserved in order
        assert captured["body"]["snippet"]["tags"][0] == "tag0"

    @pytest.mark.asyncio
    async def test_blank_tags_filtered_before_cap(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        captured: dict[str, Any] = {}

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            return {"id": "x", "snippet": {}, "status": {}}

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="t",
            tags=["a", "", "  ", "b"],
        )
        assert captured["body"]["snippet"]["tags"] == ["a", "b"]


class TestPublishScheduling:
    @pytest.mark.asyncio
    async def test_scheduled_at_forces_private_with_publish_at(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        captured: dict[str, Any] = {}

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            return {"id": "x", "snippet": {}, "status": {"privacyStatus": "private"}}

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="t",
            scheduled_at="2026-12-25T12:00:00Z",
        )

        # YouTube only schedules when uploaded as private — adapter
        # should rewrite the privacy field even when "public" was the
        # default.
        assert captured["body"]["status"]["privacyStatus"] == "private"
        assert captured["body"]["status"]["publishAt"] == "2026-12-25T12:00:00Z"

    @pytest.mark.asyncio
    async def test_no_scheduled_at_leaves_privacy_as_default(self, tmp_path, monkeypatch):
        adapter = _make_adapter(
            enabled=True,
            secrets=_FULL_SECRETS,
            settings={"default_privacy": "unlisted"},
        )
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        captured: dict[str, Any] = {}

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            return {"id": "x", "snippet": {}, "status": {}}

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        await adapter.publish(media_path=_make_media_file(tmp_path), title="t")
        assert captured["body"]["status"]["privacyStatus"] == "unlisted"
        assert "publishAt" not in captured["body"]["status"]


# ---------------------------------------------------------------------------
# publish() — error paths
# ---------------------------------------------------------------------------


class TestPublishErrors:
    @pytest.mark.asyncio
    async def test_upload_exception_caught_and_truncated(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )

        def fake_upload(*, credentials, media_path, body):
            raise RuntimeError("z" * 1000)  # > 500 chars must be truncated

        monkeypatch.setattr(
            adapter, "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        result = await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="t",
        )
        assert result.success is False
        # Error includes type
        assert "RuntimeError" in (result.error or "")
        # Truncated to 500 chars (plus the "RuntimeError: " prefix)
        assert len(result.error or "") < 600

    @pytest.mark.asyncio
    async def test_credentials_importerror_returns_actionable_error(self, tmp_path, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)

        def boom(secrets):
            raise ImportError("No module named 'google.oauth2'")

        monkeypatch.setattr(
            adapter, "_build_credentials", staticmethod(boom),
        )

        result = await adapter.publish(
            media_path=_make_media_file(tmp_path),
            title="t",
        )
        assert result.success is False
        # Actionable: tells the operator what to install
        assert "pip install" in (result.error or "")
        assert "google" in (result.error or "")


# ---------------------------------------------------------------------------
# status() — gating + happy + error paths
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits(self):
        adapter = _make_adapter(enabled=False, secrets=_FULL_SECRETS)
        result = await adapter.status("abc123")
        assert result.success is False
        assert "disabled" in (result.error or "")
        assert result.external_id == "abc123"

    @pytest.mark.asyncio
    async def test_missing_secrets_short_circuits(self):
        adapter = _make_adapter(enabled=True, secrets={"client_id": ""})
        result = await adapter.status("abc123")
        assert result.success is False
        assert "#40" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_external_id_returns_failure(self):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        result = await adapter.status("")
        assert result.success is False
        assert "external_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_happy_path_returns_status(self, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda s: MagicMock()),
        )

        def fake_status(*, credentials, video_id):
            return {
                "items": [
                    {
                        "status": {
                            "uploadStatus": "processed",
                            "privacyStatus": "public",
                        },
                        "processingDetails": {
                            "processingStatus": "succeeded",
                        },
                    },
                ],
            }

        monkeypatch.setattr(
            adapter, "_do_status_blocking",
            staticmethod(fake_status),
        )
        result = await adapter.status("abc123")
        assert result.success is True
        assert result.external_id == "abc123"
        assert result.public_url == "https://www.youtube.com/watch?v=abc123"
        assert result.status == "processed"
        assert result.metadata["processing_status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_video_not_found_returns_failure(self, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda s: MagicMock()),
        )
        monkeypatch.setattr(
            adapter, "_do_status_blocking",
            staticmethod(lambda **k: {"items": []}),
        )
        result = await adapter.status("ghost")
        assert result.success is False
        assert "not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_status_call_exception_caught(self, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)
        monkeypatch.setattr(
            adapter, "_build_credentials",
            staticmethod(lambda s: MagicMock()),
        )

        def boom(**k):
            raise RuntimeError("api down")

        monkeypatch.setattr(
            adapter, "_do_status_blocking", staticmethod(boom),
        )
        result = await adapter.status("abc")
        assert result.success is False
        assert "RuntimeError" in (result.error or "")


# ---------------------------------------------------------------------------
# stream_progress() — one-shot semantics
# ---------------------------------------------------------------------------


class TestStreamProgress:
    @pytest.mark.asyncio
    async def test_yields_exactly_one_snapshot(self, monkeypatch):
        adapter = _make_adapter(enabled=True, secrets=_FULL_SECRETS)

        # Mock the status() call so stream_progress doesn't try to
        # build credentials. Use AsyncMock since status() is an async
        # coroutine.
        snapshot = PublishResult(
            platform="youtube", success=True, external_id="abc",
            status="processed",
        )
        monkeypatch.setattr(
            adapter, "status", AsyncMock(return_value=snapshot),
        )

        snapshots = []
        async for snap in adapter.stream_progress("abc"):
            snapshots.append(snap)

        assert len(snapshots) == 1
        assert snapshots[0] is snapshot

    @pytest.mark.asyncio
    async def test_stream_progress_passes_through_failure(self, monkeypatch):
        adapter = _make_adapter(enabled=False, secrets=_FULL_SECRETS)

        # Disabled gate produces a failure result; stream_progress
        # should still yield once and stop without raising.
        snapshots = []
        async for snap in adapter.stream_progress("abc"):
            snapshots.append(snap)

        assert len(snapshots) == 1
        assert snapshots[0].success is False
        assert "disabled" in (snapshots[0].error or "")

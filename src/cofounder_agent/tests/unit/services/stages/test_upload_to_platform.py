"""Unit tests for ``services/stages/upload_to_platform.py``.

UploadToPlatformStage discovers PublishAdapters via the registry and
fans long-form / short-form video out to each adapter that supports
the format. Tests cover discovery, capability filtering, exception
isolation per adapter, and the platform_video_ids JSONB merge on
success.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.publish_adapter import PublishResult
from plugins.stage import Stage
from services.stages.upload_to_platform import (
    UploadToPlatformStage,
    _build_publish_payload,
    _empty_result_dict,
    _result_to_dict,
)


def _make_site_config(pool: Any = None):
    return SimpleNamespace(
        get=lambda _k, _d="": _d,
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
        _pool=pool,
    )


def _ok_publish_result(platform: str, ext_id: str = "ext-123"):
    return PublishResult(
        platform=platform,
        success=True,
        external_id=ext_id,
        public_url=f"https://{platform}.example/{ext_id}",
        status="published",
    )


def _fail_publish_result(platform: str, error: str = "auth failed"):
    return PublishResult(
        platform=platform,
        success=False,
        external_id=None,
        public_url=None,
        status="",
        error=error,
    )


def _full_context(tmp_path):
    long_path = tmp_path / "long.mp4"
    long_path.write_bytes(b"long-bytes")
    short_path = tmp_path / "short.mp4"
    short_path.write_bytes(b"short-bytes")

    return {
        "task_id": "t1",
        "post_id": "post-1",
        "title": "Test Title",
        "content": "Test body content for the post.",
        "tags": ["tag1", "tag2"],
        "site_config": _make_site_config(),
        "video_outputs": {
            "long_form": {
                "output_path": str(long_path),
                "media_asset_id": "asset-long",
                "public_url": "",
            },
            "short_form": {
                "output_path": str(short_path),
                "media_asset_id": "asset-short",
                "public_url": "",
            },
        },
    }


class _FakeAdapter:
    """A minimal PublishAdapter test double."""

    def __init__(
        self,
        name: str,
        supports_long: bool = True,
        supports_short: bool = False,
        publish_result: PublishResult | None = None,
        raise_exc: Exception | None = None,
    ):
        self.name = name
        self.supports_long = supports_long
        self.supports_short = supports_short
        self._publish_result = publish_result
        self._raise_exc = raise_exc
        self.publish_calls: list[dict[str, Any]] = []

    async def publish(self, **kwargs):
        self.publish_calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._publish_result is None:
            return _ok_publish_result(self.name)
        return self._publish_result


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestEmptyResultDict:
    def test_shape(self):
        out = _empty_result_dict(
            platform="x", fmt="long_form", success=False, error="nope",
        )
        assert out["platform"] == "x"
        assert out["format"] == "long_form"
        assert out["success"] is False
        assert out["error"] == "nope"
        assert out["external_id"] is None
        assert out["public_url"] is None


class TestResultToDict:
    def test_serializes_publish_result(self):
        pr = PublishResult(
            platform="youtube", success=True,
            external_id="abc", public_url="https://yt.com/abc",
            status="published",
        )
        out = _result_to_dict(pr, "long_form")
        assert out["platform"] == "youtube"
        assert out["success"] is True
        assert out["external_id"] == "abc"
        assert out["public_url"] == "https://yt.com/abc"
        assert out["format"] == "long_form"


class TestBuildPublishPayload:
    def test_post_meta_wins_over_context(self):
        ctx = {"title": "ctx-title", "content": "ctx-body", "tags": []}
        post_meta = {
            "title": "post-title",
            "excerpt": "post-excerpt",
            "seo_description": "ignored",
        }
        title, desc, tags = _build_publish_payload(post_meta=post_meta, context=ctx)
        assert title == "post-title"
        assert desc == "post-excerpt"

    def test_falls_back_to_context_when_post_meta_empty(self):
        ctx = {"title": "ctx-title", "content": "Body content here.", "tags": []}
        title, desc, _ = _build_publish_payload(post_meta={}, context=ctx)
        assert title == "ctx-title"
        # Description fell back to the body slice
        assert "Body content here." in desc

    def test_uses_seo_description_when_no_excerpt(self):
        ctx = {"content": "x"}
        post_meta = {
            "title": "T", "excerpt": "", "seo_description": "seo desc",
        }
        _, desc, _ = _build_publish_payload(post_meta=post_meta, context=ctx)
        assert desc == "seo desc"

    def test_tags_normalized(self):
        # Source uses ``[str(t).strip() for t in raw_tags if str(t).strip()]``
        # — Nones become the literal string "None" (truthy after strip).
        # That's a legacy quirk in the source; tests document the actual
        # behavior, not the ideal.
        ctx = {"content": "", "tags": [" tag1 ", "", "tag2"]}
        _, _, tags = _build_publish_payload(post_meta={}, context=ctx)
        # Whitespace-stripped + empty-strings dropped
        assert tags == ["tag1", "tag2"]

    def test_tags_non_list_returns_empty(self):
        ctx = {"content": "", "tags": "not a list"}
        _, _, tags = _build_publish_payload(post_meta={}, context=ctx)
        assert tags == []


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms(self):
        assert isinstance(UploadToPlatformStage(), Stage)

    def test_metadata(self):
        s = UploadToPlatformStage()
        assert s.name == "video.upload"
        assert s.halts_on_failure is False


# ---------------------------------------------------------------------------
# Stage.execute — early returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteEarlyReturns:
    async def test_missing_site_config_returns_not_ok(self):
        result = await UploadToPlatformStage().execute({}, {})
        assert result.ok is False
        assert "site_config" in result.detail

    async def test_no_video_outputs_returns_not_ok(self):
        ctx = {"site_config": _make_site_config()}
        result = await UploadToPlatformStage().execute(ctx, {})
        assert result.ok is False
        assert "no video_outputs" in result.detail

    async def test_no_adapters_returns_not_ok(self, tmp_path):
        ctx = _full_context(tmp_path)
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[],
        ):
            result = await UploadToPlatformStage().execute(ctx, {})
        assert result.ok is False
        assert "no adapters enabled" in result.detail
        assert result.metrics.get("adapter_count") == 0


# ---------------------------------------------------------------------------
# Stage.execute — fan-out behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteFanOut:
    async def test_long_only_adapter_called_for_long_form(self, tmp_path):
        ctx = _full_context(tmp_path)
        adapter = _FakeAdapter(
            name="youtube", supports_long=True, supports_short=False,
            publish_result=_ok_publish_result("youtube", "yt-001"),
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ), patch(
            "services.stages.upload_to_platform._update_platform_video_ids",
            AsyncMock(),
        ) as update_mock:
            result = await UploadToPlatformStage().execute(ctx, {})

        # Adapter called exactly once with the long-form file.
        assert len(adapter.publish_calls) == 1
        assert adapter.publish_calls[0]["media_path"] == ctx["video_outputs"]["long_form"]["output_path"]
        # platform_video_ids merge attempted on success
        update_mock.assert_awaited_once()
        # Stage ok with one success
        assert result.ok is True
        assert result.metrics["succeeded"] == 1
        # Single-format → key is just the platform name
        assert "youtube" in result.context_updates["video_publish_results"]

    async def test_short_only_adapter_called_for_short_form(self, tmp_path):
        ctx = _full_context(tmp_path)
        adapter = _FakeAdapter(
            name="tiktok", supports_long=False, supports_short=True,
            publish_result=_ok_publish_result("tiktok", "tk-001"),
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ), patch(
            "services.stages.upload_to_platform._update_platform_video_ids",
            AsyncMock(),
        ):
            result = await UploadToPlatformStage().execute(ctx, {})

        assert len(adapter.publish_calls) == 1
        assert adapter.publish_calls[0]["media_path"] == ctx["video_outputs"]["short_form"]["output_path"]
        assert result.ok is True

    async def test_both_formats_published_when_adapter_supports_both(self, tmp_path):
        ctx = _full_context(tmp_path)
        adapter = _FakeAdapter(
            name="youtube", supports_long=True, supports_short=True,
            publish_result=_ok_publish_result("youtube", "yt-A"),
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ), patch(
            "services.stages.upload_to_platform._update_platform_video_ids",
            AsyncMock(),
        ):
            result = await UploadToPlatformStage().execute(ctx, {})

        # Adapter called twice — once per format.
        assert len(adapter.publish_calls) == 2
        # Both keyed under "platform:format" since adapter handled both.
        keys = set(result.context_updates["video_publish_results"].keys())
        assert "youtube:long_form" in keys
        assert "youtube:short_form" in keys

    async def test_adapter_with_no_capability_skipped(self, tmp_path):
        ctx = _full_context(tmp_path)
        # supports_long=False AND supports_short=False
        adapter = _FakeAdapter(
            name="oddball", supports_long=False, supports_short=False,
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ):
            result = await UploadToPlatformStage().execute(ctx, {})

        # publish() not called.
        assert len(adapter.publish_calls) == 0
        # Skipped → recorded but counted under skipped, not attempted.
        assert result.metrics["skipped"] >= 1
        assert result.metrics["attempted"] == 0
        assert result.ok is False  # No upload succeeded
        # The skipped adapter still gets a result entry with the reason.
        publish_results = result.context_updates["video_publish_results"]
        assert "oddball" in publish_results
        assert "capability mismatch" in publish_results["oddball"]["error"]

    async def test_adapter_exception_does_not_kill_other_adapters(self, tmp_path):
        ctx = _full_context(tmp_path)
        bad = _FakeAdapter(
            name="bad", supports_long=True,
            raise_exc=RuntimeError("auth boom"),
        )
        good = _FakeAdapter(
            name="good", supports_long=True,
            publish_result=_ok_publish_result("good", "g-001"),
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[bad, good],
        ), patch(
            "services.stages.upload_to_platform._update_platform_video_ids",
            AsyncMock(),
        ):
            result = await UploadToPlatformStage().execute(ctx, {})

        # Both adapters tried; bad one's exception captured in result dict
        publish_results = result.context_updates["video_publish_results"]
        assert publish_results["bad"]["success"] is False
        assert "RuntimeError" in publish_results["bad"]["error"]
        assert publish_results["good"]["success"] is True
        # Stage overall: at least one succeeded → ok=True
        assert result.ok is True
        assert result.metrics["succeeded"] == 1
        assert result.metrics["failed"] == 1

    async def test_adapter_publish_failure_recorded(self, tmp_path):
        ctx = _full_context(tmp_path)
        adapter = _FakeAdapter(
            name="youtube", supports_long=True,
            publish_result=_fail_publish_result("youtube", "auth gone"),
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ):
            result = await UploadToPlatformStage().execute(ctx, {})

        publish_results = result.context_updates["video_publish_results"]
        assert publish_results["youtube"]["success"] is False
        # The entire stage is ok=False since no successful uploads.
        assert result.ok is False
        assert result.metrics["failed"] == 1
        assert result.metrics["succeeded"] == 0

    async def test_missing_output_path_skips_adapter_call(self, tmp_path):
        # output_path doesn't exist on disk → skipped
        ctx = _full_context(tmp_path)
        ctx["video_outputs"]["long_form"]["output_path"] = "/nonexistent/x.mp4"
        adapter = _FakeAdapter(name="youtube", supports_long=True)
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ):
            result = await UploadToPlatformStage().execute(ctx, {})

        # publish() never called when file isn't on disk
        assert len(adapter.publish_calls) == 0
        publish_results = result.context_updates["video_publish_results"]
        assert "youtube" in publish_results
        assert "output_path missing" in publish_results["youtube"]["error"]
        assert result.metrics["skipped"] >= 1

    async def test_platform_video_ids_merged_on_success(self, tmp_path):
        ctx = _full_context(tmp_path)
        adapter = _FakeAdapter(
            name="youtube", supports_long=True,
            publish_result=_ok_publish_result("youtube", "yt-XYZ"),
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ), patch(
            "services.stages.upload_to_platform._update_platform_video_ids",
            AsyncMock(),
        ) as update_mock:
            await UploadToPlatformStage().execute(ctx, {})

        # _update_platform_video_ids called with the right kwargs
        update_mock.assert_awaited_once()
        call_kwargs = update_mock.await_args.kwargs
        assert call_kwargs["platform"] == "youtube"
        assert call_kwargs["external_id"] == "yt-XYZ"
        assert call_kwargs["media_asset_id"] == "asset-long"

    async def test_platform_video_ids_not_called_on_failure(self, tmp_path):
        ctx = _full_context(tmp_path)
        adapter = _FakeAdapter(
            name="x", supports_long=True,
            publish_result=_fail_publish_result("x"),
        )
        with patch(
            "services.stages.upload_to_platform._discover_publish_adapters",
            return_value=[adapter],
        ), patch(
            "services.stages.upload_to_platform._update_platform_video_ids",
            AsyncMock(),
        ) as update_mock:
            await UploadToPlatformStage().execute(ctx, {})

        update_mock.assert_not_awaited()


@pytest.mark.asyncio
class TestUpdatePlatformVideoIdsHelper:
    """Direct tests for the _update_platform_video_ids helper."""

    async def test_pool_none_no_op(self):
        from services.stages.upload_to_platform import _update_platform_video_ids
        # Should not raise
        await _update_platform_video_ids(
            pool=None, media_asset_id="x", platform="y", external_id="z",
        )

    async def test_missing_external_id_no_op(self):
        from services.stages.upload_to_platform import _update_platform_video_ids
        # Even with a pool, missing required args → no-op
        pool = MagicMock()
        await _update_platform_video_ids(
            pool=pool, media_asset_id="x", platform="y", external_id="",
        )
        # pool.acquire() should not have been called
        pool.acquire.assert_not_called()

    async def test_db_failure_swallowed(self):
        from services.stages.upload_to_platform import _update_platform_video_ids

        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=RuntimeError("db gone"))

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_a):
                return None

        pool = MagicMock()
        pool.acquire = lambda: _Ctx()

        # Should NOT raise — db failures are swallowed
        await _update_platform_video_ids(
            pool=pool, media_asset_id="m", platform="y", external_id="e",
        )

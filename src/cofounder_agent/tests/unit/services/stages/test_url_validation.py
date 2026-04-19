"""Unit tests for ``services/stages/url_validation.py``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage
from services.stages.url_validation import UrlValidationStage


class TestProtocol:
    def test_conforms(self):
        assert isinstance(UrlValidationStage(), Stage)

    def test_metadata(self):
        s = UrlValidationStage()
        assert s.name == "url_validation"
        assert s.halts_on_failure is False


def _patch_legacy_gate(enabled: bool):
    return patch(
        "services.content_router_service._is_stage_enabled",
        AsyncMock(return_value=enabled),
    )


def _patch_validator(urls, results):
    v = SimpleNamespace(
        extract_urls=lambda _c: urls,
        validate_urls=AsyncMock(return_value=results),
    )
    return patch("services.url_validator.get_url_validator", return_value=v)


@pytest.mark.asyncio
class TestExecute:
    async def test_empty_content_returns_zero_counts(self):
        result = await UrlValidationStage().execute({"content": ""}, {})
        assert result.ok is True
        assert result.context_updates["url_validation"]["total_urls"] == 0

    async def test_validates_and_summarizes_when_enabled(self):
        ctx: dict[str, Any] = {
            "task_id": "abc12345",
            "content": "check https://ok.example and https://bad.example",
            "database_service": MagicMock(pool=MagicMock()),
        }
        with _patch_legacy_gate(True), _patch_validator(
            urls=["https://ok.example", "https://bad.example"],
            results={"https://ok.example": "valid", "https://bad.example": "invalid"},
        ):
            result = await UrlValidationStage().execute(ctx, {})
        summary = result.context_updates["url_validation"]
        assert summary["total_urls"] == 2
        assert summary["valid"] == 1
        assert summary["invalid"] == 1
        assert summary["broken_urls"] == ["https://bad.example"]

    async def test_skipped_when_legacy_gate_disabled(self):
        ctx: dict[str, Any] = {
            "content": "has http://foo.example content",
            "database_service": MagicMock(pool=MagicMock()),
        }
        with _patch_legacy_gate(False):
            result = await UrlValidationStage().execute(ctx, {})
        assert result.context_updates["url_validation"] == {"skipped": True}

    async def test_no_urls_returns_zero_counts(self):
        ctx: dict[str, Any] = {
            "content": "no links here",
            "database_service": MagicMock(pool=MagicMock()),
        }
        with _patch_legacy_gate(True), _patch_validator(urls=[], results={}):
            result = await UrlValidationStage().execute(ctx, {})
        summary = result.context_updates["url_validation"]
        assert summary["total_urls"] == 0
        assert summary["broken_urls"] == []

    async def test_validator_raising_is_non_fatal(self):
        ctx: dict[str, Any] = {
            "content": "has urls",
            "database_service": MagicMock(pool=MagicMock()),
        }
        broken_v = SimpleNamespace(
            extract_urls=lambda _c: ["https://x.example"],
            validate_urls=AsyncMock(side_effect=RuntimeError("network down")),
        )
        with _patch_legacy_gate(True), patch(
            "services.url_validator.get_url_validator", return_value=broken_v,
        ):
            result = await UrlValidationStage().execute(ctx, {})
        # ok=False but halts_on_failure=False → runner continues
        assert result.ok is False
        assert "error" in result.context_updates["url_validation"]

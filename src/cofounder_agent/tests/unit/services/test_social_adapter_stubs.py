"""Unit tests for the LinkedIn / Reddit / YouTube stub adapters (GH-36).

These three platforms require OAuth flows Matt hasn't set up yet
(tracked in GH-40). Per the GH-36 acceptance criteria, the stubs must
raise ``NotImplementedError`` with a clear message pointing operators
at GH-40 — no silent no-op, no half-broken HTTP calls.
"""

from __future__ import annotations

import logging

import pytest

from services.social_adapters.linkedin import post_to_linkedin
from services.social_adapters.reddit import post_to_reddit
from services.social_adapters.youtube import upload_to_youtube


class TestLinkedInStub:
    @pytest.mark.asyncio
    async def test_raises_notimplemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            await post_to_linkedin("hi", "https://gladlabs.io/p/1")
        assert "GH-40" in str(exc_info.value)
        assert "OAuth" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_logs_warning_before_raising(self, caplog):
        with caplog.at_level(logging.WARNING, logger="services.social_adapters.linkedin"):
            with pytest.raises(NotImplementedError):
                await post_to_linkedin("hi", "https://gladlabs.io/p/1")
        assert any(
            "LINKEDIN" in r.message and "GH-40" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_accepts_arbitrary_kwargs(self):
        with pytest.raises(NotImplementedError):
            await post_to_linkedin(
                "hi",
                "https://gladlabs.io/p/1",
                org_id="urn:li:organization:123",
                visibility="PUBLIC",
            )

    @pytest.mark.asyncio
    async def test_raises_exact_notimplementederror_type(self):
        with pytest.raises(NotImplementedError) as exc_info:
            await post_to_linkedin("hi", "https://gladlabs.io/p/1")
        assert type(exc_info.value) is NotImplementedError


class TestRedditStub:
    @pytest.mark.asyncio
    async def test_raises_notimplemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            await post_to_reddit("Title", "https://gladlabs.io/p/1")
        assert "GH-40" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_logs_warning_before_raising(self, caplog):
        with caplog.at_level(logging.WARNING, logger="services.social_adapters.reddit"):
            with pytest.raises(NotImplementedError):
                await post_to_reddit("Title", "https://gladlabs.io/p/1")
        assert any(
            "REDDIT" in r.message and "GH-40" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_accepts_arbitrary_kwargs(self):
        with pytest.raises(NotImplementedError):
            await post_to_reddit(
                "Title",
                "https://gladlabs.io/p/1",
                subreddit="programming",
                flair="news",
            )

    @pytest.mark.asyncio
    async def test_error_mentions_oauth(self):
        with pytest.raises(NotImplementedError) as exc_info:
            await post_to_reddit("Title", "https://gladlabs.io/p/1")
        assert "OAuth" in str(exc_info.value)


class TestYouTubeStub:
    @pytest.mark.asyncio
    async def test_raises_notimplemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            await upload_to_youtube(
                video_path="/tmp/fake.mp4",
                title="Test",
                description="Desc",
            )
        assert "GH-40" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_logs_warning_before_raising(self, caplog):
        with caplog.at_level(logging.WARNING, logger="services.social_adapters.youtube"):
            with pytest.raises(NotImplementedError):
                await upload_to_youtube(
                    video_path="/tmp/fake.mp4",
                    title="Test",
                    description="Desc",
                )
        assert any(
            "YOUTUBE" in r.message and "GH-40" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_accepts_optional_args(self):
        with pytest.raises(NotImplementedError):
            await upload_to_youtube(
                video_path="/tmp/fake.mp4",
                title="Test",
                description="Desc",
                tags=["ai", "ml"],
                category_id="22",
                privacy="unlisted",
            )

    @pytest.mark.asyncio
    async def test_accepts_arbitrary_kwargs(self):
        with pytest.raises(NotImplementedError):
            await upload_to_youtube(
                video_path="/tmp/fake.mp4",
                title="Test",
                description="Desc",
                made_for_kids=False,
                publish_at="2026-12-31T00:00:00Z",
            )

    @pytest.mark.asyncio
    async def test_error_mentions_oauth(self):
        with pytest.raises(NotImplementedError) as exc_info:
            await upload_to_youtube(
                video_path="/tmp/fake.mp4",
                title="Test",
                description="Desc",
            )
        assert "OAuth" in str(exc_info.value)

"""Unit tests for the LinkedIn / Reddit / YouTube stub adapters (GH-36).

These three platforms require OAuth flows Matt hasn't set up yet
(tracked in GH-40). Per the GH-36 acceptance criteria, the stubs must
raise ``NotImplementedError`` with a clear message pointing operators
at GH-40 — no silent no-op, no half-broken HTTP calls.
"""

from __future__ import annotations

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


class TestRedditStub:
    @pytest.mark.asyncio
    async def test_raises_notimplemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            await post_to_reddit("Title", "https://gladlabs.io/p/1")
        assert "GH-40" in str(exc_info.value)


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

"""
Unit tests for social_schemas.py

Tests field validation and model behaviour for social media schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.social_schemas import (
    CrossPostRequest,
    GenerateContentRequest,
    SocialAnalytics,
    SocialPlatformConnection,
    SocialPlatformEnum,
    SocialPost,
    ToneEnum,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSocialPlatformEnum:
    def test_all_platforms(self):
        assert SocialPlatformEnum.TWITTER == "twitter"
        assert SocialPlatformEnum.FACEBOOK == "facebook"
        assert SocialPlatformEnum.INSTAGRAM == "instagram"
        assert SocialPlatformEnum.LINKEDIN == "linkedin"
        assert SocialPlatformEnum.TIKTOK == "tiktok"
        assert SocialPlatformEnum.YOUTUBE == "youtube"


@pytest.mark.unit
class TestToneEnum:
    def test_all_tones(self):
        assert ToneEnum.PROFESSIONAL == "professional"
        assert ToneEnum.CASUAL == "casual"
        assert ToneEnum.HUMOROUS == "humorous"
        assert ToneEnum.FORMAL == "formal"
        assert ToneEnum.INSPIRING == "inspiring"
        assert ToneEnum.EDUCATIONAL == "educational"


# ---------------------------------------------------------------------------
# SocialPlatformConnection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSocialPlatformConnection:
    def test_valid(self):
        conn = SocialPlatformConnection(platform="twitter")  # type: ignore[arg-type]
        assert conn.platform == SocialPlatformEnum.TWITTER

    def test_all_platforms(self):
        for platform in SocialPlatformEnum:
            conn = SocialPlatformConnection(platform=platform)
            assert conn.platform == platform

    def test_invalid_platform_raises(self):
        with pytest.raises(ValidationError):
            SocialPlatformConnection(platform="snapchat")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SocialPost
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSocialPost:
    def _valid(self, **kwargs):
        defaults = {
            "content": "Check out our latest blog post about AI and Machine Learning!",
            "platforms": ["twitter"],
        }
        defaults.update(kwargs)
        return SocialPost(**defaults)

    def test_valid_defaults(self):
        post = self._valid()
        assert post.tone == ToneEnum.PROFESSIONAL
        assert post.include_hashtags is True
        assert post.include_emojis is True
        assert post.scheduled_time is None

    def test_content_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(content="Short!")

    def test_content_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(content="x" * 5001)

    def test_multiple_platforms(self):
        post = self._valid(platforms=["twitter", "linkedin", "facebook"])
        assert len(post.platforms) == 3

    def test_too_many_platforms_raises(self):
        with pytest.raises(ValidationError):
            self._valid(
                platforms=[
                    "twitter",
                    "facebook",
                    "instagram",
                    "linkedin",
                    "tiktok",
                    "youtube",
                    "twitter",
                ]
            )

    def test_valid_scheduled_time(self):
        post = self._valid(scheduled_time="2026-03-15T14:30:00Z")
        assert post.scheduled_time == "2026-03-15T14:30:00Z"

    def test_invalid_scheduled_time_raises(self):
        with pytest.raises(ValidationError):
            self._valid(scheduled_time="March 15, 2026")

    def test_custom_tone(self):
        post = self._valid(tone="casual")
        assert post.tone == ToneEnum.CASUAL

    def test_no_hashtags(self):
        post = self._valid(include_hashtags=False)
        assert post.include_hashtags is False

    def test_no_emojis(self):
        post = self._valid(include_emojis=False)
        assert post.include_emojis is False


# ---------------------------------------------------------------------------
# SocialAnalytics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSocialAnalytics:
    def _valid(self, **kwargs):
        defaults = {
            "post_id": "post-123",
            "platform": "twitter",
        }
        defaults.update(kwargs)
        return SocialAnalytics(**defaults)  # type: ignore[arg-type]

    def test_valid_defaults(self):
        analytics = self._valid()
        assert analytics.views == 0
        assert analytics.likes == 0
        assert analytics.shares == 0
        assert analytics.comments == 0
        assert analytics.engagement_rate == 0.0

    def test_with_metrics(self):
        analytics = self._valid(
            views=1000,
            likes=50,
            shares=10,
            comments=5,
            engagement_rate=6.5,
        )
        assert analytics.views == 1000
        assert analytics.engagement_rate == 6.5

    def test_negative_views_raises(self):
        with pytest.raises(ValidationError):
            self._valid(views=-1)

    def test_engagement_rate_too_high_raises(self):
        with pytest.raises(ValidationError):
            self._valid(engagement_rate=100.1)

    def test_engagement_rate_negative_raises(self):
        with pytest.raises(ValidationError):
            self._valid(engagement_rate=-0.1)

    def test_post_id_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(post_id="x" * 256)

    def test_post_id_empty_raises(self):
        with pytest.raises(ValidationError):
            self._valid(post_id="")


# ---------------------------------------------------------------------------
# GenerateContentRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateContentRequest:
    def _valid(self, **kwargs):
        defaults = {
            "topic": "AI trends in 2026",
            "platform": "twitter",
        }
        defaults.update(kwargs)
        return GenerateContentRequest(**defaults)  # type: ignore[arg-type]

    def test_valid_defaults(self):
        req = self._valid()
        assert req.tone == ToneEnum.PROFESSIONAL
        assert req.include_hashtags is True
        assert req.include_emojis is True

    def test_topic_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(topic="AI")

    def test_topic_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(topic="x" * 201)

    def test_all_platforms(self):
        for platform in SocialPlatformEnum:
            req = self._valid(platform=platform)
            assert req.platform == platform

    def test_all_tones(self):
        for tone in ToneEnum:
            req = self._valid(tone=tone)
            assert req.tone == tone


# ---------------------------------------------------------------------------
# CrossPostRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossPostRequest:
    def _valid(self, **kwargs):
        defaults = {
            "content": "This is a cross-platform post with enough content for validation.",
            "platforms": ["twitter", "linkedin"],
        }
        defaults.update(kwargs)
        return CrossPostRequest(**defaults)

    def test_valid(self):
        req = self._valid()
        assert len(req.platforms) == 2

    def test_content_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(content="Short!")

    def test_fewer_than_two_platforms_raises(self):
        with pytest.raises(ValidationError):
            self._valid(platforms=["twitter"])

    def test_too_many_platforms_raises(self):
        with pytest.raises(ValidationError):
            self._valid(
                platforms=[
                    "twitter",
                    "facebook",
                    "instagram",
                    "linkedin",
                    "tiktok",
                    "youtube",
                    "twitter",
                ]
            )

    def test_all_platforms_cross_post(self):
        req = self._valid(
            platforms=["twitter", "facebook", "instagram", "linkedin", "tiktok", "youtube"]
        )
        assert len(req.platforms) == 6

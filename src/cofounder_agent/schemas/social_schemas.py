"""Social Media Management Models

Consolidated schemas for social media integration and content posting.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from enum import Enum


class SocialPlatformEnum(str, Enum):
    """Supported social media platforms"""
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"


class ToneEnum(str, Enum):
    """Content tone styles"""
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    HUMOROUS = "humorous"
    FORMAL = "formal"
    INSPIRING = "inspiring"
    EDUCATIONAL = "educational"


class SocialPlatformConnection(BaseModel):
    """Model for connecting social platforms"""
    platform: SocialPlatformEnum = Field(
        ...,
        description="Social media platform to connect"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "platform": "twitter"
            }
        }


class SocialPost(BaseModel):
    """Model for social media posts"""
    content: str = Field(
        ...,
        min_length=10,
        max_length=5000,
        description="Post content/caption"
    )
    platforms: List[SocialPlatformEnum] = Field(
        ...,
        min_items=1,
        max_items=6,
        description="Platforms to post to"
    )
    scheduled_time: Optional[str] = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?$",
        description="ISO 8601 formatted datetime for scheduled posting"
    )
    tone: ToneEnum = Field(
        default=ToneEnum.PROFESSIONAL,
        description="Content tone style"
    )
    include_hashtags: bool = Field(
        default=True,
        description="Whether to include hashtags in post"
    )
    include_emojis: bool = Field(
        default=True,
        description="Whether to include emojis in post"
    )


class SocialAnalytics(BaseModel):
    """Model for social media analytics"""
    post_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique post identifier"
    )
    platform: SocialPlatformEnum = Field(
        ...,
        description="Platform the post was published to"
    )
    views: int = Field(default=0, ge=0, description="Number of views")
    likes: int = Field(default=0, ge=0, description="Number of likes")
    shares: int = Field(default=0, ge=0, description="Number of shares")
    comments: int = Field(default=0, ge=0, description="Number of comments")
    engagement_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Engagement rate as percentage"
    )


class GenerateContentRequest(BaseModel):
    """Model for AI content generation"""
    topic: str = Field(
        ...,
        min_length=3,
        max_length=200,
        description="Content topic/subject"
    )
    platform: SocialPlatformEnum = Field(
        ...,
        description="Target social media platform"
    )
    tone: ToneEnum = Field(
        default=ToneEnum.PROFESSIONAL,
        description="Content tone style"
    )
    include_hashtags: bool = Field(default=True, description="Whether to include hashtags")
    include_emojis: bool = Field(default=True, description="Whether to include emojis")


class CrossPostRequest(BaseModel):
    """Model for cross-posting to multiple platforms"""
    content: str = Field(
        ...,
        min_length=10,
        max_length=5000,
        description="Content to cross-post"
    )
    platforms: List[SocialPlatformEnum] = Field(
        ...,
        min_items=2,
        max_items=6,
        description="Platforms to post to (minimum 2)"
    )

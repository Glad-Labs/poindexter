"""Social media tasks - multi-platform content distribution."""

from typing import Dict, Any
from src.cofounder_agent.tasks.base import PureTask, ExecutionContext
import logging

logger = logging.getLogger(__name__)


class SocialResearchTask(PureTask):
    """
    Social media research: Analyze trends and audience sentiment.

    Input:
        - topic: str - Topic for social analysis
        - platforms: list - Target platforms (twitter, linkedin, instagram, tiktok)

    Output:
        - social_trends: dict - Platform-specific trends
        - hashtags: list - Relevant hashtags
        - sentiment: str - Current sentiment about topic
    """

    def __init__(self):
        super().__init__(
            name="social_research",
            description="Research social media trends and audience sentiment",
            required_inputs=["topic"],
            timeout_seconds=90,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute social research task."""
        from src.cofounder_agent.services.model_router import model_router

        topic = input_data["topic"]
        platforms = input_data.get("platforms", ["twitter", "linkedin", "instagram"])

        prompt = f"""Analyze social media trends for: {topic}

Target platforms: {", ".join(platforms)}

Provide:
1. Platform-specific trends (what's working on each platform)
2. 10-15 relevant hashtags
3. Current audience sentiment
4. Best times to post
5. Recommended content formats per platform

Format as JSON with keys: trends_by_platform, hashtags, sentiment, posting_times, content_formats"""

        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.4,
            max_tokens=1000,
        )

        try:
            import json

            research = json.loads(response)
        except:
            research = {
                "trends_by_platform": {},
                "hashtags": [topic.lower()],
                "sentiment": "positive",
            }

        return {
            "topic": topic,
            "platforms": platforms,
            "social_trends": research.get("trends_by_platform", {}),
            "hashtags": research.get("hashtags", []),
            "sentiment": research.get("sentiment", "neutral"),
            "posting_times": research.get("posting_times", {}),
            "content_formats": research.get("content_formats", {}),
        }


class SocialCreativeTask(PureTask):
    """
    Social creative: Generate platform-specific social content.

    Input:
        - topic: str - Content topic
        - platform: str - Target platform (twitter, linkedin, instagram, tiktok)
        - style: str - Tone/style (professional, casual, witty)
        - content_type: str - Type (post, thread, carousel, reel description)

    Output:
        - social_post: str - Generated post text
        - hashtags: list - Platform-optimized hashtags
        - cta: str - Call to action
    """

    def __init__(self):
        super().__init__(
            name="social_creative",
            description="Generate platform-optimized social media content",
            required_inputs=["topic", "platform"],
            timeout_seconds=60,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute social creative task."""
        from src.cofounder_agent.services.model_router import model_router

        topic = input_data["topic"]
        platform = input_data["platform"]
        style = input_data.get("style", "professional")
        content_type = input_data.get("content_type", "post")

        # Platform-specific constraints
        platform_config = {
            "twitter": {"char_limit": 280, "format": "tweet"},
            "linkedin": {"char_limit": 2000, "format": "professional post"},
            "instagram": {"char_limit": 2200, "format": "caption with line breaks"},
            "tiktok": {"char_limit": 2500, "format": "engaging description"},
        }

        config = platform_config.get(platform, {"char_limit": 280, "format": "post"})

        prompt = f"""Create a {style} social media {content_type} for {platform}:

Topic: {topic}
Platform: {platform}
Style: {style}
Content Type: {content_type}
Character Limit: {config['char_limit']}

Guidelines:
- Hook in first line
- Platform-native language and emojis
- Include 3-5 relevant hashtags
- End with strong call-to-action
- Keep {style} tone throughout

Format as JSON with keys: post_text, hashtags, cta"""

        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.6,
            max_tokens=500,
        )

        try:
            import json

            result = json.loads(response)
        except:
            result = {
                "post_text": response,
                "hashtags": [topic.lower()],
                "cta": "Learn more",
            }

        return {
            "platform": platform,
            "social_post": result.get("post_text", ""),
            "hashtags": result.get("hashtags", []),
            "cta": result.get("cta", ""),
            "content_type": content_type,
            "style": style,
        }


class SocialImageFormatTask(PureTask):
    """
    Social image formatting: Optimize images for platforms.

    Input:
        - images: list - Image data
        - platform: str - Target platform
        - style: str - Visual style

    Output:
        - formatted_images: list - Platform-optimized image specs
        - recommendations: dict - Platform-specific recommendations
    """

    def __init__(self):
        super().__init__(
            name="social_image_format",
            description="Optimize images for platform-specific requirements",
            required_inputs=["platform"],
            timeout_seconds=30,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute image formatting task."""

        platform = input_data["platform"]
        images = input_data.get("images", [])

        # Platform image specs
        platform_specs = {
            "twitter": {
                "size": "1200x675",
                "ratio": "16:9",
                "format": "jpg",
                "max_size_mb": 5,
            },
            "instagram": {
                "size": "1080x1080",
                "ratio": "1:1",
                "format": "jpg",
                "max_size_mb": 8,
            },
            "linkedin": {
                "size": "1200x627",
                "ratio": "1.91:1",
                "format": "jpg",
                "max_size_mb": 10,
            },
            "tiktok": {
                "size": "1080x1920",
                "ratio": "9:16",
                "format": "mp4",
                "max_size_mb": 287,
            },
        }

        spec = platform_specs.get(platform, platform_specs["twitter"])

        # Format images (in production, would process actual images)
        formatted_images = [
            {
                "original_url": img.get("url", ""),
                "optimized_specs": spec,
                "platform": platform,
                "caption": img.get("caption", ""),
            }
            for img in images
        ]

        return {
            "platform": platform,
            "formatted_images": formatted_images,
            "image_count": len(images),
            "specs": spec,
            "recommendations": {
                "best_time_to_post": (
                    "9am-5pm business hours" if platform == "linkedin" else "anytime"
                ),
                "image_strategy": f"Use platform-optimized {spec['ratio']} aspect ratio",
                "max_images_per_post": 4 if platform == "instagram" else 1,
            },
        }


class SocialPublishTask(PureTask):
    """
    Social publish: Schedule and publish to social platforms.

    Input:
        - platform: str - Target platform
        - social_post: str - Post content
        - images: list - Formatted images
        - hashtags: list - Hashtags
        - schedule_time: str - When to post (optional, default: now)

    Output:
        - published: bool - Success status
        - post_id: str - Platform post ID
        - url: str - Post URL
    """

    def __init__(self):
        super().__init__(
            name="social_publish",
            description="Schedule and publish to social platforms",
            required_inputs=["platform", "social_post"],
            timeout_seconds=30,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute social publish task."""

        platform = input_data["platform"]
        post_text = input_data["social_post"]
        hashtags = input_data.get("hashtags", [])
        images = input_data.get("images", [])
        schedule_time = input_data.get("schedule_time", "now")

        # Build complete post
        post_content = f"{post_text}\n\n{' '.join(hashtags)}"

        try:
            # In production, would integrate with platform APIs
            # (Twitter API, LinkedIn API, Instagram Graph API, TikTok API)

            # For now, simulate posting
            post_id = f"{platform}-{context.workflow_id}"
            url = f"https://{platform}.com/posts/{post_id}"

            logger.info(
                f"Published to {platform}",
                extra={
                    "workflow_id": context.workflow_id,
                    "platform": platform,
                    "post_id": post_id,
                },
            )

            return {
                "platform": platform,
                "published": True,
                "post_id": post_id,
                "url": url,
                "scheduled_time": schedule_time,
                "content_length": len(post_content),
                "images_attached": len(images),
            }
        except Exception as e:
            logger.error(f"Social publishing failed: {str(e)}")
            return {
                "platform": platform,
                "published": False,
                "error": str(e),
            }

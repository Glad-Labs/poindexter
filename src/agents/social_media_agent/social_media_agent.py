"""
Social Media Agent
Manages social media operations across multiple platforms
"""

import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SocialMediaPost(BaseModel):
    """Model for social media post"""
    platform: str  # twitter, facebook, instagram, linkedin, tiktok, youtube
    content: str
    media_urls: List[str] = []
    scheduled_time: Optional[datetime] = None
    tags: List[str] = []
    mentions: List[str] = []
    status: str = "draft"  # draft, scheduled, published, failed


class SocialMediaAgent:
    """
    AI-powered social media management agent.
    
    Capabilities:
    - Content generation optimized per platform
    - Hashtag optimization
    - Post scheduling
    - Cross-platform posting
    - Engagement tracking
    - Trend analysis
    """
    
    SUPPORTED_PLATFORMS = [
        "twitter",
        "facebook", 
        "instagram",
        "linkedin",
        "tiktok",
        "youtube"
    ]
    
    PLATFORM_LIMITS = {
        "twitter": {"chars": 280, "images": 4, "videos": 1},
        "facebook": {"chars": 63206, "images": 10, "videos": 1},
        "instagram": {"chars": 2200, "images": 10, "videos": 1},
        "linkedin": {"chars": 3000, "images": 9, "videos": 1},
        "tiktok": {"chars": 150, "images": 0, "videos": 1},
        "youtube": {"chars": 5000, "images": 1, "videos": 1}
    }
    
    def __init__(
        self,
        model_router=None,
        firestore_client=None,
        pubsub_client=None
    ):
        """
        Initialize Social Media Agent.
        
        Args:
            model_router: Router for AI model selection
            firestore_client: Firestore client for data persistence
            pubsub_client: Pub/Sub client for async operations
        """
        self.model_router = model_router
        self.firestore_client = firestore_client
        self.pubsub_client = pubsub_client
        
        logger.info("Social Media Agent initialized")
    
    async def generate_post(
        self,
        topic: str,
        platform: str,
        tone: str = "professional",
        include_hashtags: bool = True,
        include_emojis: bool = True
    ) -> Dict[str, Any]:
        """
        Generate optimized social media post for a specific platform.
        
        Args:
            topic: Topic or subject of the post
            platform: Target platform (twitter, facebook, etc.)
            tone: Tone of voice (professional, casual, humorous, etc.)
            include_hashtags: Whether to include hashtags
            include_emojis: Whether to include emojis
        
        Returns:
            Dict with generated content, hashtags, and metadata
        """
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")
        
        # Get platform-specific limits
        limits = self.PLATFORM_LIMITS[platform]
        max_chars = limits["chars"]
        
        # Build prompt for content generation
        prompt = self._build_content_prompt(
            topic=topic,
            platform=platform,
            tone=tone,
            max_chars=max_chars,
            include_hashtags=include_hashtags,
            include_emojis=include_emojis
        )
        
        # Generate content using AI model
        if self.model_router:
            response = await self.model_router.generate(prompt)
            content = response.get("text", "")
        else:
            # Fallback: basic content generation
            content = f"Check out this {topic}! #trending"
        
        # Extract hashtags
        hashtags = self._extract_hashtags(content)
        
        # Validate length
        if len(content) > max_chars:
            content = content[:max_chars-3] + "..."
        
        return {
            "content": content,
            "hashtags": hashtags,
            "platform": platform,
            "char_count": len(content),
            "max_chars": max_chars,
            "generated_at": datetime.utcnow().isoformat()
        }
    
    async def optimize_hashtags(
        self,
        content: str,
        platform: str,
        max_hashtags: int = 5
    ) -> List[str]:
        """
        Generate optimized hashtags for content.
        
        Args:
            content: Post content
            platform: Target platform
            max_hashtags: Maximum number of hashtags
        
        Returns:
            List of optimized hashtags
        """
        prompt = f"""
        Generate {max_hashtags} optimized hashtags for this {platform} post:
        
        "{content}"
        
        Return only the hashtags, one per line, with # prefix.
        Focus on trending, relevant tags that will maximize reach.
        """
        
        if self.model_router:
            response = await self.model_router.generate(prompt)
            hashtags_text = response.get("text", "")
            hashtags = [
                line.strip() 
                for line in hashtags_text.split('\n') 
                if line.strip().startswith('#')
            ]
            return hashtags[:max_hashtags]
        
        # Fallback
        return ["#trending", "#socialmedia"]
    
    async def schedule_post(
        self,
        post: SocialMediaPost,
        schedule_time: datetime
    ) -> Dict[str, Any]:
        """
        Schedule a post for future publishing.
        
        Args:
            post: Social media post object
            schedule_time: When to publish
        
        Returns:
            Scheduled post metadata
        """
        post.scheduled_time = schedule_time
        post.status = "scheduled"
        
        # Store in Firestore
        if self.firestore_client:
            post_data = post.dict()
            doc_ref = await self.firestore_client.create_document(
                "social_media_posts",
                post_data
            )
            post_id = doc_ref.id
        else:
            post_id = f"post_{datetime.utcnow().timestamp()}"
        
        # Publish to Pub/Sub for scheduled execution
        if self.pubsub_client:
            await self.pubsub_client.publish_message(
                "social-media-scheduler",
                {
                    "post_id": post_id,
                    "platform": post.platform,
                    "scheduled_time": schedule_time.isoformat()
                }
            )
        
        logger.info(f"Post scheduled: {post_id} for {schedule_time}")
        
        return {
            "post_id": post_id,
            "status": "scheduled",
            "scheduled_time": schedule_time.isoformat(),
            "platform": post.platform
        }
    
    async def cross_post(
        self,
        content: str,
        platforms: List[str],
        adapt_content: bool = True
    ) -> Dict[str, Any]:
        """
        Create and publish content across multiple platforms.
        
        Args:
            content: Base content
            platforms: List of target platforms
            adapt_content: Whether to adapt content for each platform
        
        Returns:
            Dict with results for each platform
        """
        results = {}
        
        for platform in platforms:
            if platform not in self.SUPPORTED_PLATFORMS:
                results[platform] = {"status": "error", "message": "Unsupported platform"}
                continue
            
            # Adapt content if needed
            if adapt_content:
                platform_content = await self._adapt_content_for_platform(
                    content, 
                    platform
                )
            else:
                platform_content = content
            
            # Create post
            post = SocialMediaPost(
                platform=platform,
                content=platform_content,
                status="draft"
            )
            
            results[platform] = {
                "status": "created",
                "content": platform_content,
                "char_count": len(platform_content)
            }
        
        return results
    
    async def get_trending_topics(
        self,
        platform: str,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get trending topics for a platform.
        
        Args:
            platform: Platform to check
            category: Optional category filter
        
        Returns:
            List of trending topics with metadata
        """
        # This would integrate with platform APIs in production
        # For now, return mock trending topics
        
        mock_trends = [
            {"topic": "#AI", "volume": 125000, "sentiment": "positive"},
            {"topic": "#TechNews", "volume": 89000, "sentiment": "neutral"},
            {"topic": "#Innovation", "volume": 67000, "sentiment": "positive"},
            {"topic": "#Automation", "volume": 54000, "sentiment": "neutral"},
            {"topic": "#FutureTech", "volume": 43000, "sentiment": "positive"}
        ]
        
        return mock_trends
    
    async def analyze_engagement(
        self,
        post_id: str
    ) -> Dict[str, Any]:
        """
        Analyze engagement metrics for a post.
        
        Args:
            post_id: ID of the post to analyze
        
        Returns:
            Engagement metrics
        """
        # In production, this would fetch real metrics from platform APIs
        
        return {
            "post_id": post_id,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "impressions": 0,
            "engagement_rate": 0.0,
            "analyzed_at": datetime.utcnow().isoformat()
        }
    
    def _build_content_prompt(
        self,
        topic: str,
        platform: str,
        tone: str,
        max_chars: int,
        include_hashtags: bool,
        include_emojis: bool
    ) -> str:
        """Build prompt for content generation."""
        
        hashtag_instruction = "Include 3-5 relevant hashtags." if include_hashtags else "No hashtags."
        emoji_instruction = "Use appropriate emojis." if include_emojis else "No emojis."
        
        return f"""
        Create a {tone} social media post for {platform} about: {topic}
        
        Requirements:
        - Maximum {max_chars} characters
        - {hashtag_instruction}
        - {emoji_instruction}
        - Engaging and shareable
        - Platform-optimized format
        
        Generate the post content:
        """
    
    def _extract_hashtags(self, content: str) -> List[str]:
        """Extract hashtags from content."""
        import re
        hashtags = re.findall(r'#\w+', content)
        return hashtags
    
    async def _adapt_content_for_platform(
        self,
        content: str,
        platform: str
    ) -> str:
        """Adapt content for specific platform requirements."""
        
        limits = self.PLATFORM_LIMITS[platform]
        max_chars = limits["chars"]
        
        # Truncate if needed
        if len(content) > max_chars:
            content = content[:max_chars-3] + "..."
        
        # Platform-specific adaptations
        if platform == "twitter":
            # Add thread indicator if very long
            if len(content) > 250:
                content = content[:250] + "... 🧵"
        
        elif platform == "linkedin":
            # More professional tone
            content = content.replace("🔥", "").replace("💯", "")
        
        elif platform == "instagram":
            # More emojis and hashtags
            if "hashtags" not in content.lower():
                content += "\n\n#instagood #photooftheday"
        
        return content

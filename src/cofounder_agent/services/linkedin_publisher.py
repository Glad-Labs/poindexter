"""
LinkedIn Publishing Service

Handles publishing content to LinkedIn using LinkedIn Share API.

Requirements:
- LinkedIn App with Share API permission
- OAuth token with w_member_social scope
- Environment variables:
  - LINKEDIN_CLIENT_ID
  - LINKEDIN_CLIENT_SECRET
  - LINKEDIN_ACCESS_TOKEN (or obtain via OAuth flow)
"""

import os
import logging
from typing import Optional, Dict, Any
import httpx
import json

logger = logging.getLogger(__name__)


class LinkedInPublisher:
    """LinkedIn content publisher"""
    
    API_BASE_URL = "https://api.linkedin.com/v2"
    
    def __init__(self, access_token: Optional[str] = None):
        """
        Initialize LinkedIn publisher.
        
        Args:
            access_token: LinkedIn OAuth access token. If not provided,
                         will try to load from LINKEDIN_ACCESS_TOKEN env var
        """
        self.access_token = access_token or os.getenv("LINKEDIN_ACCESS_TOKEN")
        
        if not self.access_token:
            logger.warning(
                "⚠️  LinkedIn not configured. Set LINKEDIN_ACCESS_TOKEN environment variable"
            )
            self.available = False
        else:
            self.available = True
            logger.info("✅ LinkedIn publisher initialized")
    
    async def publish(
        self,
        title: str,
        content: str,
        image_url: Optional[str] = None,
        description: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish content to LinkedIn.
        
        Args:
            title: Post title
            content: Post content (max 3000 chars for text posts)
            image_url: Optional image URL for the post
            description: Optional brief description for article share
            **kwargs: Additional metadata
        
        Returns:
            Dictionary with LinkedIn post data:
            {
                "success": bool,
                "post_id": str,
                "url": str,
                "error": str (if failed)
            }
        """
        if not self.available:
            return {
                "success": False,
                "error": "LinkedIn not configured",
                "post_id": None,
                "url": None,
            }
        
        try:
            # Prepare share payload
            payload = {
                "content": {
                    "contentEntities": [],
                    "title": title[:200],  # LinkedIn limit
                    "description": description or content[:500],
                },
                "distribution": {
                    "feedDistribution": "LINKEDIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "owner": "urn:li:person:me",  # Current authenticated user
                "subject": "",
                "text": {
                    "text": content[:3000],  # LinkedIn text post limit
                },
            }
            
            # Add image if provided
            if image_url:
                payload["content"]["contentEntities"].append({
                    "entity": "urn:li:digitalmediaAsset:image",
                    "thumbnails": [{"url": image_url}],
                })
            
            # Publish to LinkedIn
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.API_BASE_URL}/posts",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                        "LinkedIn-Version": "202401",
                    },
                    timeout=30.0,
                )
            
            if response.status_code not in (200, 201):
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("message", f"Status {response.status_code}")
                logger.error(f"LinkedIn publish failed: {error_msg}")
                return {
                    "success": False,
                    "error": f"LinkedIn API error: {error_msg}",
                    "post_id": None,
                    "url": None,
                }
            
            data = response.json()
            post_id = data.get("id", "")
            
            logger.info(f"✅ Published to LinkedIn: {post_id}")
            
            return {
                "success": True,
                "post_id": post_id,
                "url": f"https://linkedin.com/feed/update/{post_id}",
                "error": None,
            }
            
        except Exception as e:
            logger.error(f"LinkedIn publishing error: {str(e)}")
            return {
                "success": False,
                "error": f"Publishing error: {str(e)}",
                "post_id": None,
                "url": None,
            }
    
    async def schedule(
        self,
        title: str,
        content: str,
        scheduled_time: str,
        image_url: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Schedule content to publish at future time.
        
        Args:
            title: Post title
            content: Post content
            scheduled_time: ISO format datetime (e.g., "2025-12-15T10:00:00Z")
            image_url: Optional image URL
            **kwargs: Additional metadata
        
        Returns:
            Dictionary with scheduling result
        """
        if not self.available:
            return {
                "success": False,
                "error": "LinkedIn not configured",
                "scheduled": False,
            }
        
        try:
            # Add scheduling to payload
            payload = {
                "content": {
                    "contentEntities": [],
                    "title": title[:200],
                    "description": content[:500],
                },
                "distribution": {
                    "feedDistribution": "LINKEDIN_FEED",
                },
                "owner": "urn:li:person:me",
                "text": {"text": content[:3000]},
                "publish": {
                    "scheduled": scheduled_time,
                },
            }
            
            if image_url:
                payload["content"]["contentEntities"].append({
                    "entity": "urn:li:digitalmediaAsset:image",
                    "thumbnails": [{"url": image_url}],
                })
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.API_BASE_URL}/posts",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                        "LinkedIn-Version": "202401",
                    },
                    timeout=30.0,
                )
            
            if response.status_code not in (200, 201):
                error_msg = response.json().get("message", f"Status {response.status_code}")
                logger.error(f"LinkedIn schedule failed: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "scheduled": False,
                }
            
            logger.info(f"✅ Scheduled LinkedIn post for {scheduled_time}")
            return {
                "success": True,
                "scheduled": True,
                "error": None,
            }
            
        except Exception as e:
            logger.error(f"LinkedIn scheduling error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "scheduled": False,
            }

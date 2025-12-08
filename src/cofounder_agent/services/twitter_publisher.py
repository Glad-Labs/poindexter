"""
Twitter Publishing Service

Handles publishing content to Twitter/X using Twitter API v2.

Requirements:
- Twitter API v2 access (Elevated access or higher)
- OAuth token with tweet.write scope
- Environment variables:
  - TWITTER_API_KEY (Consumer API Key)
  - TWITTER_API_SECRET (Consumer API Secret)
  - TWITTER_ACCESS_TOKEN
  - TWITTER_ACCESS_TOKEN_SECRET
  - Or: TWITTER_BEARER_TOKEN (for App-only auth)
"""

import os
import logging
from typing import Optional, Dict, Any
import httpx
import json

logger = logging.getLogger(__name__)


class TwitterPublisher:
    """Twitter content publisher"""
    
    API_BASE_URL = "https://api.twitter.com/2"
    
    def __init__(self, bearer_token: Optional[str] = None):
        """
        Initialize Twitter publisher.
        
        Args:
            bearer_token: Twitter API Bearer token. If not provided,
                         will try to load from TWITTER_BEARER_TOKEN env var
        """
        self.bearer_token = bearer_token or os.getenv("TWITTER_BEARER_TOKEN")
        
        if not self.bearer_token:
            logger.warning(
                "⚠️  Twitter not configured. Set TWITTER_BEARER_TOKEN environment variable"
            )
            self.available = False
        else:
            self.available = True
            logger.info("✅ Twitter publisher initialized")
    
    async def publish(
        self,
        text: str,
        image_url: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish a tweet.
        
        Args:
            text: Tweet text (max 280 characters)
            image_url: Optional image URL to attach
            reply_to_id: Optional tweet ID to reply to
            **kwargs: Additional metadata
        
        Returns:
            Dictionary with tweet data:
            {
                "success": bool,
                "tweet_id": str,
                "url": str,
                "error": str (if failed)
            }
        """
        if not self.available:
            return {
                "success": False,
                "error": "Twitter not configured",
                "tweet_id": None,
                "url": None,
            }
        
        try:
            # Truncate to Twitter limit if needed
            if len(text) > 280:
                text = text[:277] + "..."
            
            # Prepare payload
            payload = {"text": text}
            
            if reply_to_id:
                payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}
            
            # For image attachments, would need additional API calls
            # For now, we support text-only tweets
            # Image support would require uploading to Twitter Media API first
            
            # Create tweet
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.API_BASE_URL}/tweets",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.bearer_token}",
                        "Content-Type": "application/json",
                    },
                    timeout=30.0,
                )
            
            if response.status_code not in (200, 201):
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("detail", f"Status {response.status_code}")
                logger.error(f"Twitter publish failed: {error_msg}")
                return {
                    "success": False,
                    "error": f"Twitter API error: {error_msg}",
                    "tweet_id": None,
                    "url": None,
                }
            
            data = response.json().get("data", {})
            tweet_id = data.get("id")
            
            logger.info(f"✅ Published to Twitter: {tweet_id}")
            
            return {
                "success": True,
                "tweet_id": tweet_id,
                "url": f"https://twitter.com/i/web/status/{tweet_id}",
                "error": None,
            }
            
        except Exception as e:
            logger.error(f"Twitter publishing error: {str(e)}")
            return {
                "success": False,
                "error": f"Publishing error: {str(e)}",
                "tweet_id": None,
                "url": None,
            }
    
    async def publish_thread(
        self,
        tweets: list[str],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Publish a Twitter thread (multiple connected tweets).
        
        Args:
            tweets: List of tweet texts (each max 280 chars)
            **kwargs: Additional metadata
        
        Returns:
            Dictionary with thread data:
            {
                "success": bool,
                "tweet_ids": [str],
                "urls": [str],
                "error": str (if failed)
            }
        """
        if not self.available:
            return {
                "success": False,
                "error": "Twitter not configured",
                "tweet_ids": [],
                "urls": [],
            }
        
        tweet_ids = []
        urls = []
        reply_to_id = None
        
        try:
            async with httpx.AsyncClient() as client:
                for tweet_text in tweets:
                    # Truncate if needed
                    if len(tweet_text) > 280:
                        tweet_text = tweet_text[:277] + "..."
                    
                    payload = {"text": tweet_text}
                    
                    # Reply to previous tweet to create thread
                    if reply_to_id:
                        payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}
                    
                    response = await client.post(
                        f"{self.API_BASE_URL}/tweets",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self.bearer_token}",
                            "Content-Type": "application/json",
                        },
                        timeout=30.0,
                    )
                    
                    if response.status_code not in (200, 201):
                        error_msg = response.json().get("detail", f"Status {response.status_code}")
                        logger.error(f"Twitter thread publish failed: {error_msg}")
                        return {
                            "success": False,
                            "error": f"Failed at tweet {len(tweet_ids) + 1}: {error_msg}",
                            "tweet_ids": tweet_ids,
                            "urls": urls,
                        }
                    
                    data = response.json().get("data", {})
                    tweet_id = data.get("id")
                    tweet_ids.append(tweet_id)
                    urls.append(f"https://twitter.com/i/web/status/{tweet_id}")
                    
                    # Set this as reply_to for next tweet
                    reply_to_id = tweet_id
            
            logger.info(f"✅ Published Twitter thread: {len(tweet_ids)} tweets")
            
            return {
                "success": True,
                "tweet_ids": tweet_ids,
                "urls": urls,
                "error": None,
            }
            
        except Exception as e:
            logger.error(f"Twitter thread publishing error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "tweet_ids": tweet_ids,
                "urls": urls,
            }

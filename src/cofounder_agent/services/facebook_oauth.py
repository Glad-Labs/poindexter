"""
Facebook OAuth Provider

Implements OAuth 2.0 flow for Facebook authentication.

Setup:
1. Go to Facebook Developers: https://developers.facebook.com
2. Create a new app (type: Consumer)
3. Add Facebook Login product
4. In Settings > Basic, get App ID and App Secret
5. In Settings > Basic > App Domains, add your domain
6. In Facebook Login > Settings, add Valid OAuth Redirect URIs:
   - http://localhost:8000/api/auth/callback/facebook (dev)
   - https://yourdomain.com/api/auth/callback/facebook (prod)
7. Set environment variables:
   - FACEBOOK_CLIENT_ID (App ID)
   - FACEBOOK_CLIENT_SECRET (App Secret)
"""

import os
import logging
from typing import Optional
import httpx

from .oauth_provider import OAuthProvider, OAuthUser, OAuthException

logger = logging.getLogger(__name__)


class FacebookOAuthProvider(OAuthProvider):
    """Facebook OAuth 2.0 provider implementation"""
    
    # Facebook OAuth endpoints
    AUTHORIZATION_URL = "https://www.facebook.com/v18.0/dialog/oauth"
    TOKEN_URL = "https://graph.facebook.com/v18.0/oauth/access_token"
    USER_INFO_URL = "https://graph.facebook.com/me"
    
    def __init__(self):
        """Initialize Facebook OAuth provider from environment variables"""
        self.client_id = os.getenv("FACEBOOK_CLIENT_ID")
        self.client_secret = os.getenv("FACEBOOK_CLIENT_SECRET")
        self.redirect_uri = os.getenv(
            "FACEBOOK_REDIRECT_URI",
            "http://localhost:8000/api/auth/callback/facebook"
        )
        
        if not self.client_id or not self.client_secret:
            raise OAuthException(
                "Facebook OAuth not configured. Set FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET"
            )
        
        logger.info("✅ Facebook OAuth provider initialized")
    
    def get_authorization_url(self, state: str) -> str:
        """
        Generate Facebook authorization URL.
        
        Args:
            state: CSRF protection state
        
        Returns:
            Authorization URL to redirect user to
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "email,public_profile",  # Permissions
            "state": state,
            "auth_type": "rerequest",  # Allow re-authentication
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.AUTHORIZATION_URL}?{query_string}"
    
    def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange authorization code for access token.
        
        Args:
            code: Authorization code from Facebook callback
        
        Returns:
            Access token
        
        Raises:
            OAuthException: If exchange fails
        """
        try:
            response = httpx.get(
                self.TOKEN_URL,
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "error" in data:
                raise OAuthException(data.get("error", {}).get("message", "Unknown error"))
            
            access_token = data.get("access_token")
            
            if not access_token:
                raise OAuthException("No access token in response")
            
            logger.debug(f"✅ Facebook OAuth code exchange successful")
            return access_token
            
        except httpx.HTTPError as e:
            logger.error(f"Facebook OAuth token exchange failed: {e}")
            raise OAuthException(f"Failed to exchange code for token: {str(e)}")
    
    def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Fetch user information from Facebook.
        
        Args:
            access_token: Access token from exchange_code_for_token()
        
        Returns:
            OAuthUser with standardized user data
        
        Raises:
            OAuthException: If fetch fails
        """
        try:
            # Request user fields from Facebook Graph API
            response = httpx.get(
                self.USER_INFO_URL,
                params={
                    "fields": "id,email,name,picture.type(large)",
                    "access_token": access_token,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "error" in data:
                raise OAuthException(data.get("error", {}).get("message", "Unknown error"))
            
            # Extract picture URL if available
            avatar_url = None
            if "picture" in data and "data" in data["picture"]:
                avatar_url = data["picture"]["data"].get("url")
            
            user = OAuthUser(
                provider="facebook",
                provider_id=data.get("id"),
                email=data.get("email"),
                display_name=data.get("name"),
                avatar_url=avatar_url,
                raw_data=data,
            )
            
            logger.debug(f"✅ Fetched Facebook user info for {user.email}")
            return user
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch Facebook user info: {e}")
            raise OAuthException(f"Failed to fetch user info: {str(e)}")

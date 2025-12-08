"""
Google OAuth Provider

Implements OAuth 2.0 flow for Google authentication.

Setup:
1. Go to Google Cloud Console: https://console.cloud.google.com
2. Create a new project
3. Enable Google+ API
4. Create OAuth 2.0 credentials (Web application)
5. Add authorized redirect URIs:
   - http://localhost:8000/api/auth/callback/google (dev)
   - https://yourdomain.com/api/auth/callback/google (prod)
6. Set environment variables:
   - GOOGLE_CLIENT_ID
   - GOOGLE_CLIENT_SECRET
"""

import os
import logging
from typing import Optional
import httpx

from .oauth_provider import OAuthProvider, OAuthUser, OAuthException

logger = logging.getLogger(__name__)


class GoogleOAuthProvider(OAuthProvider):
    """Google OAuth 2.0 provider implementation"""
    
    # Google OAuth endpoints
    AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USER_INFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
    
    def __init__(self):
        """Initialize Google OAuth provider from environment variables"""
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.redirect_uri = os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://localhost:8000/api/auth/callback/google"
        )
        
        if not self.client_id or not self.client_secret:
            raise OAuthException(
                "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET"
            )
        
        logger.info("✅ Google OAuth provider initialized")
    
    def get_authorization_url(self, state: str) -> str:
        """
        Generate Google authorization URL.
        
        Args:
            state: CSRF protection state
        
        Returns:
            Authorization URL to redirect user to
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",  # Enable refresh token
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.AUTHORIZATION_URL}?{query_string}"
    
    def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange authorization code for access token.
        
        Args:
            code: Authorization code from Google callback
        
        Returns:
            Access token
        
        Raises:
            OAuthException: If exchange fails
        """
        try:
            response = httpx.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            access_token = data.get("access_token")
            
            if not access_token:
                raise OAuthException("No access token in response")
            
            logger.debug(f"✅ Google OAuth code exchange successful")
            return access_token
            
        except httpx.HTTPError as e:
            logger.error(f"Google OAuth token exchange failed: {e}")
            raise OAuthException(f"Failed to exchange code for token: {str(e)}")
    
    def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Fetch user information from Google.
        
        Args:
            access_token: Access token from exchange_code_for_token()
        
        Returns:
            OAuthUser with standardized user data
        
        Raises:
            OAuthException: If fetch fails
        """
        try:
            response = httpx.get(
                self.USER_INFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            user = OAuthUser(
                provider="google",
                provider_id=data.get("sub"),  # Google's unique user ID
                email=data.get("email"),
                display_name=data.get("name"),
                avatar_url=data.get("picture"),
                raw_data=data,
            )
            
            logger.debug(f"✅ Fetched Google user info for {user.email}")
            return user
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch Google user info: {e}")
            raise OAuthException(f"Failed to fetch user info: {str(e)}")

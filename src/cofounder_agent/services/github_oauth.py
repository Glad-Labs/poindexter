"""
GitHub OAuth Provider

Implements OAuth 2.0 flow for GitHub authentication.

Setup:
1. Go to GitHub Settings > Developer settings > OAuth Apps > New OAuth App
2. Fill in:
   - Application name: Your app name
   - Homepage URL: http://localhost:8000 (dev) or https://yourdomain.com (prod)
   - Authorization callback URL: http://localhost:8000/api/auth/callback/github (dev)
   - Authorization callback URL: https://yourdomain.com/api/auth/callback/github (prod)
3. Copy Client ID and Client Secret
4. Set environment variables:
   - GITHUB_CLIENT_ID (Client ID)
   - GITHUB_CLIENT_SECRET (Client Secret)
"""

import os
import logging
from typing import Optional
import httpx

from .oauth_provider import OAuthProvider, OAuthUser, OAuthException

logger = logging.getLogger(__name__)


class GitHubOAuthProvider(OAuthProvider):
    """GitHub OAuth 2.0 provider implementation"""
    
    # GitHub OAuth endpoints
    AUTHORIZATION_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USER_INFO_URL = "https://api.github.com/user"
    
    def __init__(self):
        """Initialize GitHub OAuth provider from environment variables"""
        self.client_id = os.getenv("GITHUB_CLIENT_ID")
        self.client_secret = os.getenv("GITHUB_CLIENT_SECRET")
        self.redirect_uri = os.getenv(
            "GITHUB_REDIRECT_URI",
            "http://localhost:8000/api/auth/callback/github"
        )
        
        if not self.client_id or not self.client_secret:
            raise OAuthException(
                "GitHub OAuth not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET"
            )
        
        logger.info("✅ GitHub OAuth provider initialized")
    
    def get_authorization_url(self, state: str) -> str:
        """
        Generate GitHub authorization URL.
        
        Args:
            state: CSRF protection state
        
        Returns:
            Authorization URL to redirect user to
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "user:email",  # Request user email
            "state": state,
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.AUTHORIZATION_URL}?{query_string}"
    
    def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange authorization code for access token.
        
        Args:
            code: Authorization code from GitHub callback
        
        Returns:
            Access token
        
        Raises:
            OAuthException: If exchange fails
        """
        try:
            response = httpx.post(
                self.TOKEN_URL,
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "error" in data:
                raise OAuthException(data.get("error_description", "Unknown error"))
            
            access_token = data.get("access_token")
            
            if not access_token:
                raise OAuthException("No access token in response")
            
            logger.debug(f"✅ GitHub OAuth code exchange successful")
            return access_token
            
        except httpx.HTTPError as e:
            logger.error(f"GitHub OAuth token exchange failed: {e}")
            raise OAuthException(f"Failed to exchange code for token: {str(e)}")
    
    def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Fetch user information from GitHub.
        
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
                headers={
                    "Authorization": f"token {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "message" in data and "error" in data:
                raise OAuthException(data.get("message", "Unknown error"))
            
            user = OAuthUser(
                provider="github",
                provider_id=str(data.get("id")),
                email=data.get("email") or data.get("login") + "@github.local",
                display_name=data.get("name") or data.get("login"),
                avatar_url=data.get("avatar_url"),
                raw_data=data,
            )
            
            logger.debug(f"✅ Fetched GitHub user info for {user.email}")
            return user
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch GitHub user info: {e}")
            raise OAuthException(f"Failed to fetch user info: {str(e)}")

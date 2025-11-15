"""
GitHub OAuth Provider Implementation.

This demonstrates the modular pattern. To add Google, Facebook, etc., 
create a new file like google_provider.py and follow this same pattern.
"""

import os
import httpx
from typing import Optional
from urllib.parse import urlencode

from .oauth_provider import OAuthProvider, OAuthUser, OAuthException


class GitHubOAuthProvider(OAuthProvider):
    """GitHub OAuth 2.0 implementation."""
    
    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    API_URL = "https://api.github.com/user"
    EMAILS_API_URL = "https://api.github.com/user/emails"
    
    @property
    def provider_name(self) -> str:
        return "github"
    
    @property
    def client_id(self) -> str:
        client_id = os.getenv("GITHUB_CLIENT_ID")
        if not client_id:
            raise OAuthException("GITHUB_CLIENT_ID environment variable not set")
        return client_id
    
    @property
    def client_secret(self) -> str:
        client_secret = os.getenv("GITHUB_CLIENT_SECRET")
        if not client_secret:
            raise OAuthException("GITHUB_CLIENT_SECRET environment variable not set")
        return client_secret
    
    @property
    def redirect_uri(self) -> str:
        base_url = os.getenv("BACKEND_URL", "http://localhost:8000")
        return f"{base_url}/api/auth/github/callback"
    
    def get_authorization_url(self, state: str) -> str:
        """
        Get GitHub OAuth authorization URL.
        
        Example:
            url = provider.get_authorization_url("random_state")
            # User is redirected to this URL in browser
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "scope": "user:email",  # Request email scope
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"
    
    def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange authorization code for GitHub access token.
        
        Args:
            code: Authorization code from GitHub callback
        
        Returns:
            Access token string
        
        Raises:
            OAuthException: If exchange fails
        """
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        
        headers = {"Accept": "application/json"}
        
        try:
            response = httpx.post(self.TOKEN_URL, json=payload, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            if "error" in data:
                raise OAuthException(f"GitHub error: {data['error_description']}")
            
            return data["access_token"]
        
        except httpx.HTTPError as e:
            raise OAuthException(f"Failed to exchange code for token: {str(e)}")
    
    def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Fetch authenticated user's GitHub profile.
        
        Args:
            access_token: GitHub access token
        
        Returns:
            OAuthUser with standardized user data
        
        Raises:
            OAuthException: If user info fetch fails
        """
        headers = {
            "Authorization": f"token {access_token}",
            "Accept": "application/json",
        }
        
        try:
            # Fetch user profile
            response = httpx.get(self.API_URL, headers=headers)
            response.raise_for_status()
            user_data = response.json()
            
            # GitHub might not have email in profile, fetch it separately if needed
            email = user_data.get("email")
            if not email:
                emails_response = httpx.get(self.EMAILS_API_URL, headers=headers)
                emails_response.raise_for_status()
                emails = emails_response.json()
                # Get primary email or first one
                email = next(
                    (e["email"] for e in emails if e.get("primary")),
                    emails[0]["email"] if emails else None
                )
            
            return OAuthUser(
                provider="github",
                provider_id=str(user_data["id"]),
                username=user_data["login"],
                email=email,
                avatar_url=user_data.get("avatar_url"),
                display_name=user_data.get("name"),
                extra_data={
                    "bio": user_data.get("bio"),
                    "location": user_data.get("location"),
                    "public_repos": user_data.get("public_repos"),
                    "followers": user_data.get("followers"),
                }
            )
        
        except httpx.HTTPError as e:
            raise OAuthException(f"Failed to fetch user info: {str(e)}")

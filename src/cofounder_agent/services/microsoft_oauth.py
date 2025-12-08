"""
Microsoft OAuth Provider

Implements OAuth 2.0 flow for Microsoft/Azure AD authentication.

Setup:
1. Go to Azure Portal: https://portal.azure.com
2. Register a new app in Azure AD
3. In Certificates & secrets, create a Client secret
4. In API permissions, add:
   - User.Read
   - openid
   - profile
   - email
5. In Authentication > Redirect URIs, add:
   - http://localhost:8000/api/auth/callback/microsoft (dev)
   - https://yourdomain.com/api/auth/callback/microsoft (prod)
6. Set environment variables:
   - MICROSOFT_CLIENT_ID (Application ID)
   - MICROSOFT_CLIENT_SECRET
   - MICROSOFT_TENANT_ID (Directory ID, or 'common' for multi-tenant)
"""

import os
import logging
from typing import Optional
import httpx

from .oauth_provider import OAuthProvider, OAuthUser, OAuthException

logger = logging.getLogger(__name__)


class MicrosoftOAuthProvider(OAuthProvider):
    """Microsoft OAuth 2.0 provider implementation"""
    
    def __init__(self):
        """Initialize Microsoft OAuth provider from environment variables"""
        self.client_id = os.getenv("MICROSOFT_CLIENT_ID")
        self.client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")
        self.redirect_uri = os.getenv(
            "MICROSOFT_REDIRECT_URI",
            "http://localhost:8000/api/auth/callback/microsoft"
        )
        
        if not self.client_id or not self.client_secret:
            raise OAuthException(
                "Microsoft OAuth not configured. Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET"
            )
        
        # Build endpoints with tenant
        self.authorization_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize"
        self.token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        self.user_info_url = "https://graph.microsoft.com/v1.0/me"
        
        logger.info(f"✅ Microsoft OAuth provider initialized (tenant: {self.tenant_id})")
    
    def get_authorization_url(self, state: str) -> str:
        """
        Generate Microsoft authorization URL.
        
        Args:
            state: CSRF protection state
        
        Returns:
            Authorization URL to redirect user to
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile User.Read offline_access",
            "state": state,
            "prompt": "select_account",  # Force account selection
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.authorization_url}?{query_string}"
    
    def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange authorization code for access token.
        
        Args:
            code: Authorization code from Microsoft callback
        
        Returns:
            Access token
        
        Raises:
            OAuthException: If exchange fails
        """
        try:
            response = httpx.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                    "scope": "openid email profile User.Read offline_access",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "error" in data:
                raise OAuthException(data.get("error_description", data.get("error")))
            
            access_token = data.get("access_token")
            
            if not access_token:
                raise OAuthException("No access token in response")
            
            logger.debug(f"✅ Microsoft OAuth code exchange successful")
            return access_token
            
        except httpx.HTTPError as e:
            logger.error(f"Microsoft OAuth token exchange failed: {e}")
            raise OAuthException(f"Failed to exchange code for token: {str(e)}")
    
    def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Fetch user information from Microsoft Graph.
        
        Args:
            access_token: Access token from exchange_code_for_token()
        
        Returns:
            OAuthUser with standardized user data
        
        Raises:
            OAuthException: If fetch fails
        """
        try:
            response = httpx.get(
                self.user_info_url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "error" in data:
                raise OAuthException(data.get("error", {}).get("message", "Unknown error"))
            
            user = OAuthUser(
                provider="microsoft",
                provider_id=data.get("id"),
                email=data.get("mail") or data.get("userPrincipalName"),
                display_name=data.get("displayName"),
                avatar_url=None,  # Microsoft Graph doesn't provide avatar in basic call
                raw_data=data,
            )
            
            logger.debug(f"✅ Fetched Microsoft user info for {user.email}")
            return user
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch Microsoft user info: {e}")
            raise OAuthException(f"Failed to fetch user info: {str(e)}")

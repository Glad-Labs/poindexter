"""
Google OAuth Provider Implementation

This is a template showing how to add a new OAuth provider.
Key point: Adding this file + 1 line to oauth_manager.py is all that's needed!
Routes don't change, models don't change, nothing else needs updating.

This demonstrates the perfect modularity of the architecture.

SETUP:
1. Create Google OAuth app: https://console.cloud.google.com/
2. Get Client ID and Secret
3. Add to .env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
4. Uncomment registration in oauth_manager.py
5. Routes automatically support Google OAuth!
"""

import httpx
import os
from typing import Dict, Any

from .oauth_provider import OAuthProvider, OAuthUser, OAuthException


class GoogleOAuthProvider(OAuthProvider):
    """
    Google OAuth 2.0 provider implementation.
    
    Supports login via Google accounts with email verification.
    """
    
    # OAuth endpoints
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
    
    # Required scopes for accessing user email and profile
    SCOPE = "openid email profile"
    
    @property
    def provider_name(self) -> str:
        """Provider name for routing and display"""
        return "google"
    
    def get_authorization_url(self, state: str) -> str:
        """
        Build Google OAuth authorization URL.
        
        Args:
            state: CSRF token for security
        
        Returns:
            URL to redirect user to Google OAuth
        
        Example:
            https://accounts.google.com/o/oauth2/v2/auth?client_id=...&redirect_uri=...&scope=...&state=...
        """
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        if not client_id:
            raise OAuthException(
                "GOOGLE_CLIENT_ID not found in environment. "
                "Create Google OAuth app at https://console.cloud.google.com/"
            )
        
        redirect_uri = os.getenv("BACKEND_URL", "http://localhost:8000") + "/api/auth/google/callback"
        
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPE,
            "state": state,
            "access_type": "offline",  # Request refresh token
            "prompt": "consent",  # Force consent screen
        }
        
        # Build URL manually (urllib.parse.urlencode)
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.AUTHORIZE_URL}?{query_string}"
    
    async def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange Google authorization code for access token.
        
        Args:
            code: Authorization code from Google
        
        Returns:
            Access token for API calls
        
        Raises:
            OAuthException: If token exchange fails
        """
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise OAuthException("Google OAuth credentials not configured in environment")
        
        redirect_uri = os.getenv("BACKEND_URL", "http://localhost:8000") + "/api/auth/google/callback"
        
        token_data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.TOKEN_URL, data=token_data)
                response.raise_for_status()
                
                token_response = response.json()
                return token_response.get("access_token")
        
        except httpx.HTTPError as e:
            raise OAuthException(f"Failed to exchange code for token: {str(e)}")
    
    async def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Fetch user information from Google.
        
        Args:
            access_token: Google access token
        
        Returns:
            Standardized OAuthUser with Google data
        
        Raises:
            OAuthException: If user info fetch fails
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.USERINFO_URL, headers=headers)
                response.raise_for_status()
                
                user_data = response.json()
                
                # Extract Google-specific fields
                google_user_id = user_data.get("id")
                email = user_data.get("email")
                name = user_data.get("name", "")
                picture = user_data.get("picture", "")
                
                if not google_user_id:
                    raise OAuthException("Failed to get Google user ID")
                
                # Return standardized OAuthUser
                return OAuthUser(
                    provider="google",
                    provider_id=google_user_id,
                    username=email.split("@")[0] if email else name,  # Use email prefix as username
                    email=email,
                    avatar_url=picture,
                    display_name=name,
                    extra_data={
                        "google_id": google_user_id,
                        "picture": picture,
                        "locale": user_data.get("locale"),
                    }
                )
        
        except httpx.HTTPError as e:
            raise OAuthException(f"Failed to fetch Google user info: {str(e)}")
        except KeyError as e:
            raise OAuthException(f"Invalid Google user data format: {str(e)}")


# ============================================================================
# SETUP INSTRUCTIONS
# ============================================================================

"""
HOW TO ACTIVATE GOOGLE OAUTH:

Step 1: Create Google OAuth App
   - Go to: https://console.cloud.google.com/
   - Create new project
   - Go to "Credentials" → "Create Credentials" → "OAuth 2.0 Client ID"
   - Choose "Web application"
   - Add authorized redirect URI: http://localhost:8000/api/auth/google/callback
   - Copy Client ID and Client Secret

Step 2: Add to .env.local
   GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your_client_secret_here

Step 3: Register in oauth_manager.py
   Add this line to oauth_manager.py:
   
   from .services.google_oauth import GoogleOAuthProvider
   
   Then in PROVIDERS dict:
   PROVIDERS = {
       "github": GitHubOAuthProvider,
       "google": GoogleOAuthProvider,  # ← ADD THIS LINE
   }

Step 4: Restart Backend
   python -m uvicorn src.cofounder_agent.main:app --reload

Step 5: Test
   curl http://localhost:8000/api/auth/providers
   # Response should include: ["github", "google"]
   
   Visit: http://localhost:8000/api/auth/google/login
   # Should redirect to Google OAuth

That's it! No route changes needed. All endpoints work automatically.

KEY ARCHITECTURAL BENEFITS:
✅ OAuth routes are completely provider-agnostic
✅ Adding provider = 1 new file + 1 registration line
✅ No modification to existing code
✅ Perfect for modular architecture
✅ Easy to add Facebook, Microsoft, GitHub App, etc.
"""

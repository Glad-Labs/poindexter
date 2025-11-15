"""
Abstract OAuth Provider - Base class for all OAuth providers.

This design allows you to add Google, Facebook, Microsoft, etc. by:
1. Creating a new class that inherits from OAuthProvider
2. Implementing the required abstract methods
3. Registering it in oauth_factory.py

Example: Adding Google OAuth
    class GoogleOAuthProvider(OAuthProvider):
        def get_authorization_url(self, state: str) -> str:
            ...
        def exchange_code_for_token(self, code: str) -> dict:
            ...
        def get_user_info(self, access_token: str) -> dict:
            ...
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class OAuthUser:
    """Standardized user data from any OAuth provider."""
    
    provider: str  # "github", "google", "facebook", etc.
    provider_id: str  # User ID from provider (e.g., GitHub user ID)
    username: str  # Username from provider
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    display_name: Optional[str] = None
    extra_data: Dict[str, Any] = None  # Provider-specific data
    
    def __post_init__(self):
        if self.extra_data is None:
            self.extra_data = {}


class OAuthProvider(ABC):
    """
    Abstract base class for OAuth providers.
    
    All OAuth providers must implement these three methods:
    1. get_authorization_url() - Step 1: Redirect user to provider
    2. exchange_code_for_token() - Step 2: Exchange code for access token
    3. get_user_info() - Step 3: Fetch user data using token
    """
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'github', 'google', 'facebook')."""
        pass
    
    @property
    @abstractmethod
    def client_id(self) -> str:
        """Return OAuth Client ID from environment."""
        pass
    
    @property
    @abstractmethod
    def client_secret(self) -> str:
        """Return OAuth Client Secret from environment."""
        pass
    
    @property
    @abstractmethod
    def redirect_uri(self) -> str:
        """Return OAuth Redirect URI."""
        pass
    
    @abstractmethod
    def get_authorization_url(self, state: str) -> str:
        """
        Step 1: Get the authorization URL to redirect user to.
        
        Args:
            state: Random string for CSRF protection
        
        Returns:
            Full URL to redirect user to provider's login page
        
        Example:
            url = github_provider.get_authorization_url("random_state_123")
            # Returns: "https://github.com/login/oauth/authorize?client_id=...&state=..."
        """
        pass
    
    @abstractmethod
    def exchange_code_for_token(self, code: str) -> str:
        """
        Step 2: Exchange authorization code for access token.
        
        Args:
            code: Authorization code from provider callback
        
        Returns:
            Access token string
        
        Raises:
            OAuthException: If token exchange fails
        
        Example:
            token = github_provider.exchange_code_for_token("abc123")
            # Returns: "gho_16C7e42F292c6912E7710c838347Ae178B4a"
        """
        pass
    
    @abstractmethod
    def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Step 3: Fetch authenticated user's information.
        
        Args:
            access_token: Access token from exchange_code_for_token()
        
        Returns:
            OAuthUser with standardized user data
        
        Raises:
            OAuthException: If user info fetch fails
        
        Example:
            user = github_provider.get_user_info("gho_16C7e42F292c6912E7710c838347Ae178B4a")
            # Returns: OAuthUser(provider='github', provider_id='12345', username='octocat', ...)
        """
        pass


class OAuthException(Exception):
    """Raised when OAuth flow fails at any step."""
    pass

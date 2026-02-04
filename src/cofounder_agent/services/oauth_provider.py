"""
Base OAuth Provider Interface

This module defines the abstract base class and data models for OAuth providers.
All OAuth implementations (GitHub, Google, Facebook, Microsoft) should inherit from OAuthProvider.

Exception Classes:
- OAuthException: Raised when OAuth operations fail

Data Models:
- OAuthUser: Standardized user data from any OAuth provider

Abstract Methods:
- get_authorization_url(state): Generate login URL
- exchange_code_for_token(code): Exchange auth code for access token
- get_user_info(access_token): Fetch user profile data
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


class OAuthException(Exception):
    """Raised when OAuth operations fail."""

    pass


@dataclass
class OAuthUser:
    """
    Standardized user data from any OAuth provider.

    All OAuth providers return data in this format, allowing the application
    to work uniformly across GitHub, Google, Facebook, Microsoft, etc.

    Attributes:
        provider: OAuth provider name ("github", "google", "facebook", etc.)
        provider_id: User's unique ID at the provider (e.g., GitHub user ID)
        email: User's email address
        display_name: User's display name or full name
        avatar_url: URL to user's profile picture (optional)
        raw_data: Complete raw response from provider API
    """

    provider: str
    provider_id: str
    email: str
    display_name: str
    avatar_url: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


class OAuthProvider(ABC):
    """
    Abstract base class for OAuth providers.

    All OAuth implementations must inherit from this class and implement
    the three abstract methods. This allows the OAuthManager to work with
    any provider transparently.

    Example Usage:
        class GoogleOAuthProvider(OAuthProvider):
            def __init__(self):
                self.client_id = os.getenv("GOOGLE_CLIENT_ID")
                self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

            def get_authorization_url(self, state: str) -> str:
                # Return Google's login URL
                ...

            def exchange_code_for_token(self, code: str) -> str:
                # Exchange code for Google access token
                ...

            def get_user_info(self, access_token: str) -> OAuthUser:
                # Fetch user data from Google Graph API
                ...
    """

    @abstractmethod
    def get_authorization_url(self, state: str) -> str:
        """
        Generate the OAuth provider's authorization URL.

        This URL should be used to redirect the user to the provider's login page.
        After login, the provider will redirect back to your callback endpoint
        with an authorization code.

        Args:
            state: A random string for CSRF protection. Must be verified
                   when the user is redirected back with the code.

        Returns:
            Full URL to redirect user to for OAuth login

        Example:
            url = provider.get_authorization_url("abc123state")
            redirect to url
        """
        pass

    @abstractmethod
    def exchange_code_for_token(self, code: str) -> str:
        """
        Exchange authorization code for access token.

        This is the backend-to-backend call that exchanges the code the user
        received from the provider for an actual access token that can be used
        to fetch user data.

        Args:
            code: Authorization code received in the callback

        Returns:
            Access token string

        Raises:
            OAuthException: If the exchange fails

        Example:
            token = provider.exchange_code_for_token("auth_code_abc123")
        """
        pass

    @abstractmethod
    def get_user_info(self, access_token: str) -> OAuthUser:
        """
        Fetch user information from the OAuth provider.

        Uses the access token to call the provider's user info endpoint
        and returns standardized user data.

        Args:
            access_token: Access token from exchange_code_for_token()

        Returns:
            OAuthUser object with standardized fields

        Raises:
            OAuthException: If the fetch fails

        Example:
            user = provider.get_user_info("access_token_xyz")
            print(user.email, user.display_name)
        """
        pass

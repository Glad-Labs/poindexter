"""
OAuth Manager - Factory for OAuth providers.

This is your registration point for new OAuth providers.
To add Google, Facebook, etc.:

1. Create google_oauth.py with GoogleOAuthProvider class
2. Import it here
3. Add it to PROVIDERS dict
4. That's it! Routes automatically support the new provider.
"""

import secrets
from typing import Dict, Optional, Type

from .facebook_oauth import FacebookOAuthProvider
from .github_oauth import GitHubOAuthProvider
from .google_oauth import GoogleOAuthProvider
from .microsoft_oauth import MicrosoftOAuthProvider
from .oauth_provider import OAuthException, OAuthProvider, OAuthUser


class OAuthManager:
    """
    Factory for managing multiple OAuth providers.

    Usage:
        manager = OAuthManager()

        # Get login URL for GitHub
        state = manager.generate_state()
        auth_url = manager.get_authorization_url("github", state)

        # Handle callback from GitHub
        token = manager.exchange_code_for_token("github", "auth_code_123")
        user = manager.get_user_info("github", token)
    """

    # Register all OAuth providers here
    # To add a new provider: import it, then add to this dict
    PROVIDERS: Dict[str, Type[OAuthProvider]] = {
        "github": GitHubOAuthProvider,
        "google": GoogleOAuthProvider,
        "facebook": FacebookOAuthProvider,
        "microsoft": MicrosoftOAuthProvider,
    }

    @classmethod
    def get_provider(cls, provider_name: str) -> OAuthProvider:
        """
        Get OAuth provider instance.

        Args:
            provider_name: "github", "google", "facebook", etc.

        Returns:
            OAuth provider instance

        Raises:
            OAuthException: If provider not found
        """
        if provider_name not in cls.PROVIDERS:
            available = ", ".join(cls.PROVIDERS.keys())
            raise OAuthException(
                f"Unknown OAuth provider: {provider_name}. " f"Available: {available}"
            )

        provider_class = cls.PROVIDERS[provider_name]
        return provider_class()

    @classmethod
    def list_providers(cls) -> list[str]:
        """Get list of available OAuth providers."""
        return list(cls.PROVIDERS.keys())

    @staticmethod
    def generate_state(length: int = 32) -> str:
        """
        Generate random state string for CSRF protection.

        Returns:
            Random hex string
        """
        return secrets.token_hex(length // 2)

    @classmethod
    def get_authorization_url(cls, provider_name: str, state: str) -> str:
        """
        Get authorization URL to redirect user to.

        Args:
            provider_name: "github", "google", etc.
            state: CSRF protection state

        Returns:
            Full URL to redirect user to

        Example:
            state = OAuthManager.generate_state()
            url = OAuthManager.get_authorization_url("github", state)
            # Redirect user to this URL
        """
        provider = cls.get_provider(provider_name)
        return provider.get_authorization_url(state)

    @classmethod
    def exchange_code_for_token(cls, provider_name: str, code: str) -> str:
        """
        Exchange authorization code for access token.

        Args:
            provider_name: "github", "google", etc.
            code: Authorization code from provider callback

        Returns:
            Access token

        Raises:
            OAuthException: If exchange fails

        Example:
            token = OAuthManager.exchange_code_for_token("github", "code_123")
        """
        provider = cls.get_provider(provider_name)
        return provider.exchange_code_for_token(code)

    @classmethod
    def get_user_info(cls, provider_name: str, access_token: str) -> OAuthUser:
        """
        Fetch user information from OAuth provider.

        Args:
            provider_name: "github", "google", etc.
            access_token: Access token from exchange_code_for_token()

        Returns:
            OAuthUser with standardized user data

        Raises:
            OAuthException: If fetch fails

        Example:
            user = OAuthManager.get_user_info("github", token)
            print(user.email, user.display_name)
        """
        provider = cls.get_provider(provider_name)
        return provider.get_user_info(access_token)

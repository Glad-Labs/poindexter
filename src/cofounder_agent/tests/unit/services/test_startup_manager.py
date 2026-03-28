"""
Unit tests for StartupManager._validate_secrets().

Tests that the secret validation logic correctly:
- Raises RuntimeError in production when known-default secrets are present
- Logs a warning (but does not raise) in development/staging
- Passes silently when secrets have been replaced with custom values
"""

import os
from unittest.mock import patch

import pytest

from utils.startup_manager import StartupManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> StartupManager:
    return StartupManager()


# ---------------------------------------------------------------------------
# Production environment — known defaults MUST raise
# ---------------------------------------------------------------------------


class TestValidateSecretsProduction:
    def test_jwt_secret_default_raises_in_production(self):
        """JWT_SECRET_KEY set to the known placeholder raises in production."""
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="Refusing to start in production"):
                _make_manager()._validate_secrets()

    def test_secret_key_default_raises_in_production(self):
        """SECRET_KEY set to 'your-secret-key-here' raises in production."""
        env = {
            "ENVIRONMENT": "production",
            "SECRET_KEY": "your-secret-key-here",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="Refusing to start in production"):
                _make_manager()._validate_secrets()

    def test_revalidate_secret_default_raises_in_production(self):
        """REVALIDATE_SECRET set to 'dev-secret-key' raises in production."""
        env = {
            "ENVIRONMENT": "production",
            "REVALIDATE_SECRET": "dev-secret-key",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="Refusing to start in production"):
                _make_manager()._validate_secrets()

    def test_custom_secret_passes_in_production(self):
        """A strong custom secret does not raise even in production."""
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "super-secure-random-value-xyz-987",
            "JWT_SECRET": "another-secure-value-abc-123",
            "SECRET_KEY": "yet-another-strong-secret-qrs-456",
            "REVALIDATE_SECRET": "my-real-revalidate-secret-789",
        }
        with patch.dict(os.environ, env, clear=False):
            # Should complete without raising
            _make_manager()._validate_secrets()

    def test_error_message_includes_violating_var_name(self):
        """RuntimeError message names the offending environment variable."""
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError) as exc_info:
                _make_manager()._validate_secrets()
            assert "JWT_SECRET_KEY" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Development / staging environments — known defaults warn but do NOT raise
# ---------------------------------------------------------------------------


class TestValidateSecretsDevelopment:
    @pytest.mark.parametrize("env_value", ["development", "staging", "test", "dev"])
    def test_default_secret_warns_but_does_not_raise(self, env_value):
        """In non-production environments, default secrets log a warning only."""
        env = {
            "ENVIRONMENT": env_value,
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
        }
        with patch.dict(os.environ, env, clear=False):
            # Must not raise
            _make_manager()._validate_secrets()

    def test_custom_secret_passes_in_development(self):
        """Custom secrets always pass in development."""
        env = {
            "ENVIRONMENT": "development",
            "JWT_SECRET_KEY": "my-custom-dev-key-12345",
        }
        with patch.dict(os.environ, env, clear=False):
            _make_manager()._validate_secrets()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestValidateSecretsEdgeCases:
    def test_unset_env_var_does_not_raise(self):
        """Unset vars (empty string) are not treated as violations — only log."""
        env = {"ENVIRONMENT": "production"}
        # Remove the known-default vars from the environment to simulate unset
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("JWT_SECRET_KEY", "JWT_SECRET", "SECRET_KEY", "REVALIDATE_SECRET")
        }
        clean_env["ENVIRONMENT"] = "production"
        with patch.dict(os.environ, clean_env, clear=True):
            # Unset vars → empty string → not a default value → should NOT raise
            _make_manager()._validate_secrets()

    def test_default_environment_treated_as_production(self):
        """When ENVIRONMENT is not set, the code defaults to 'production'."""
        env = {
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
        }
        clean_env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            # No ENVIRONMENT var → defaults to 'production' → should raise
            with pytest.raises(RuntimeError):
                _make_manager()._validate_secrets()

    def test_jwt_secret_alias_checked(self):
        """JWT_SECRET (alias without _KEY suffix) is also validated."""
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET": "development-secret-key-change-in-production",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError):
                _make_manager()._validate_secrets()

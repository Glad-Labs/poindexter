"""Smoke tests for the Mastodon social adapter.

These tests verify that:
1. Mastodon.py is installed and importable in the worker environment.
2. The mastodon adapter module itself can be imported without error.
3. The adapter short-circuits cleanly when credentials are absent.

poindexter#737: Mastodon.py was declared in the root dev-harness
pyproject.toml but missing from src/cofounder_agent/pyproject.toml,
so the worker Docker image could not import it.
"""

from __future__ import annotations

import pytest


def test_mastodon_package_importable() -> None:
    """Mastodon.py must be importable from the worker environment.

    If this fails, Mastodon.py is not installed in the current venv —
    run Installing dependencies from lock file

Package operations: 77 installs, 0 updates, 0 removals

  - Installing attrs (26.1.0)
  - Installing certifi (2026.4.22)
  - Installing colorama (0.4.6)
  - Installing h11 (0.16.0)
  - Installing idna (3.15)
  - Installing pycparser (3.0)
  - Installing pygments (2.20.0)
  - Installing rpds-py (0.30.0)
  - Installing typing-extensions (4.15.0)
  - Installing annotated-doc (0.0.4)
  - Installing annotated-types (0.7.0)
  - Installing anyio (4.13.0)
  - Installing click (8.3.3)
  - Installing httpcore (1.0.9)
  - Installing referencing (0.37.0)
  - Installing sniffio (1.3.1)
  - Installing pydantic-core (2.46.4)
  - Installing cffi (2.0.0)
  - Installing zipp (3.23.1)
  - Installing typing-inspection (0.4.2)
  - Installing cryptography (46.0.7)
  - Installing httpx (0.27.2)
  - Installing jsonschema-specifications (2025.9.1)
  - Installing importlib-metadata (8.7.1)
  - Installing python-dotenv (1.2.2)
  - Installing six (1.17.0)
  - Installing pydantic (2.13.4)
  - Installing packaging (26.2)
  - Installing soupsieve (2.8.3)
  - Installing webencodings (0.5.1)
  - Installing starlette (1.0.1)
  - Installing beautifulsoup4 (4.14.3)
  - Installing charset-normalizer (3.4.7)
  - Installing httpx-sse (0.4.3)
  - Installing html5lib (1.1)
  - Installing iniconfig (2.3.0)
  - Installing opentelemetry-api (1.41.1)
  - Installing jsonschema (4.26.0)
  - Installing pyjwt (2.12.1)
  - Installing lxml (6.1.0)
  - Installing pluggy (1.6.0)
  - Installing pydantic-settings (2.14.1)
  - Installing python-multipart (0.0.27)
  - Installing pywin32 (311)
  - Installing regex (2026.4.4)
  - Installing urllib3 (2.7.0)
  - Installing sse-starlette (3.4.2)
  - Installing uvicorn (0.46.0)
  - Installing coverage (7.13.5)
  - Installing decorator (5.2.1)
  - Installing dnspython (2.8.0)
  - Installing libipld (3.3.2)
  - Installing markdownify (1.2.2)
  - Installing fastapi (0.136.1)
  - Installing mcp (1.27.1)
  - Installing numpy (2.4.4)
  - Installing pytest (9.0.3)
  - Installing python-dateutil (2.9.0.post0)
  - Installing readabilipy (0.3.0)
  - Installing requests (2.33.1)
  - Installing protego (0.6.0)
  - Installing websockets (15.0.1)
  - Installing opentelemetry-semantic-conventions (0.62b1)
  - Installing aiofiles (25.1.0)
  - Installing asyncpg (0.31.0)
  - Installing atproto (0.0.65)
  - Installing markdown (3.10.2)
  - Installing email-validator (2.3.0)
  - Installing opencv-python-headless (4.13.0.92)
  - Installing opentelemetry-sdk (1.41.1)
  - Installing pytest-cov (5.0.0)
  - Installing sentry-sdk (2.59.0)
  - Installing mastodon-py (2.2.1)
  - Installing pytest-asyncio (1.3.0)
  - Installing structlog (25.5.0)
  - Installing mcp-server-fetch (2025.4.7)
  - Installing types-requests (2.33.0.20260508) from src/cofounder_agent/ (poindexter#737).
    """
    import mastodon  # noqa: F401


def test_mastodon_mastodon_class_accessible() -> None:
    """The top-level Mastodon client class must be accessible."""
    from mastodon import Mastodon  # noqa: F401

    assert callable(Mastodon)


@pytest.mark.asyncio
async def test_mastodon_adapter_missing_site_config() -> None:
    """Adapter returns a soft-skip dict when site_config is absent."""
    from services.social_adapters.mastodon import post_to_mastodon

    result = await post_to_mastodon("hello", "https://example.com")
    assert result["success"] is False
    assert result["post_id"] is None
    assert "site_config not provided" in (result["error"] or "")


@pytest.mark.asyncio
async def test_mastodon_adapter_missing_credentials() -> None:
    """Adapter returns a soft-skip dict when credentials are not configured."""
    from services.social_adapters.mastodon import post_to_mastodon

    class _FakeSiteConfig:
        def get(self, key: str, default: str = "") -> str:
            return default

        async def get_secret(self, key: str, default: str = "") -> str:
            return default

    result = await post_to_mastodon(
        "hello", "https://example.com", site_config=_FakeSiteConfig()
    )
    assert result["success"] is False
    assert result["post_id"] is None
    assert "not configured" in (result["error"] or "")

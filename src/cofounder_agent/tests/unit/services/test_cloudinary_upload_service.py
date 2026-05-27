"""Tests for the shared Cloudinary upload service.

Locks in the contract that pre-2026-05-27 was duplicated across
``flux_schnell.py`` and ``sdxl.py``:

- Missing ``site_config`` raises RuntimeError (caller forgot the DI seam)
- ``cloudinary_api_key`` / ``api_secret`` go through ``get_secret`` (not
  the sync ``.get()``), so encrypted ``enc:v1:`` values get decrypted
- ``provider_tag`` lands in the Cloudinary tags array so the operator
  can filter by source provider in the Cloudinary console
- Empty ``secure_url`` from Cloudinary raises (silent upload-failure
  guard)
- Successful upload returns the ``secure_url`` string
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_cloudinary(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub the cloudinary SDK so tests don't need the real package.

    The real ``cloudinary`` and ``cloudinary.uploader`` get registered
    as fake modules in ``sys.modules`` before the service-under-test
    imports them. ``cloudinary.uploader.upload`` is a MagicMock the
    test can configure per-case.
    """
    fake_cloudinary = types.ModuleType("cloudinary")
    fake_uploader = types.ModuleType("cloudinary.uploader")
    fake_cloudinary.config = MagicMock()  # type: ignore[attr-defined]
    fake_uploader.upload = MagicMock(  # type: ignore[attr-defined]
        return_value={"secure_url": "https://res.cloudinary.com/x/y.png"},
    )
    fake_cloudinary.uploader = fake_uploader  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cloudinary", fake_cloudinary)
    monkeypatch.setitem(sys.modules, "cloudinary.uploader", fake_uploader)
    return fake_uploader.upload  # type: ignore[attr-defined,return-value]


@pytest.fixture
def mock_site_config() -> MagicMock:
    """SiteConfig stub returning known cloud_name + secret credentials."""
    sc = MagicMock()
    sc.get = MagicMock(return_value="test-cloud")
    sc.get_secret = AsyncMock(side_effect=lambda key, default="": {
        "cloudinary_api_key": "test-api-key",
        "cloudinary_api_secret": "test-api-secret",
    }.get(key, default))
    return sc


async def test_missing_site_config_raises(mock_cloudinary: MagicMock) -> None:
    """Cloudinary upload requires site_config — DI seam is mandatory."""
    from services.cloudinary_upload_service import upload_to_cloudinary

    with pytest.raises(RuntimeError, match="requires site_config"):
        await upload_to_cloudinary(
            "/tmp/x.png",
            "prompt",
            site_config=None,
            provider_tag="flux_schnell",
        )


async def test_secret_credentials_use_get_secret(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """API key/secret must come through get_secret (not sync .get).

    Sync .get() returns ciphertext for is_secret=true rows
    (Glad-Labs/poindexter#334). Only get_secret decrypts.
    """
    from services.cloudinary_upload_service import upload_to_cloudinary

    await upload_to_cloudinary(
        "/tmp/x.png",
        "prompt",
        site_config=mock_site_config,
        provider_tag="flux_schnell",
    )

    # Both secret keys must have been fetched via get_secret.
    fetched_keys = {call.args[0] for call in mock_site_config.get_secret.call_args_list}
    assert "cloudinary_api_key" in fetched_keys
    assert "cloudinary_api_secret" in fetched_keys


async def test_provider_tag_lands_in_cloudinary_tags(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """provider_tag identifies the source provider in Cloudinary."""
    from services.cloudinary_upload_service import upload_to_cloudinary

    await upload_to_cloudinary(
        "/tmp/x.png",
        "prompt",
        site_config=mock_site_config,
        provider_tag="sdxl",
    )

    upload_kwargs = mock_cloudinary.call_args.kwargs
    assert "sdxl" in upload_kwargs["tags"]
    assert "provider" in upload_kwargs["tags"]


async def test_empty_secure_url_raises(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Silent upload-failure guard — empty secure_url is a failure mode."""
    mock_cloudinary.return_value = {"secure_url": ""}

    from services.cloudinary_upload_service import upload_to_cloudinary

    with pytest.raises(RuntimeError, match="empty secure_url"):
        await upload_to_cloudinary(
            "/tmp/x.png",
            "prompt",
            site_config=mock_site_config,
            provider_tag="flux_schnell",
        )


async def test_returns_secure_url(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Happy path — return the Cloudinary secure_url as a string."""
    mock_cloudinary.return_value = {
        "secure_url": "https://res.cloudinary.com/test/abc123.png",
    }

    from services.cloudinary_upload_service import upload_to_cloudinary

    url = await upload_to_cloudinary(
        "/tmp/x.png",
        "prompt text",
        site_config=mock_site_config,
        provider_tag="wan2_1",
    )
    assert url == "https://res.cloudinary.com/test/abc123.png"
    assert isinstance(url, str)


async def test_prompt_truncated_in_alt_context(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Prompts > 200 chars get truncated for Cloudinary's context field."""
    from services.cloudinary_upload_service import upload_to_cloudinary

    long_prompt = "x" * 500
    await upload_to_cloudinary(
        "/tmp/x.png",
        long_prompt,
        site_config=mock_site_config,
        provider_tag="flux_schnell",
    )

    upload_kwargs = mock_cloudinary.call_args.kwargs
    assert len(upload_kwargs["context"]["alt"]) == 200

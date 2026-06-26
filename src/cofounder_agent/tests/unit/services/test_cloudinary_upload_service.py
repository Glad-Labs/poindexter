"""Tests for the shared Cloudinary upload service.

Locks in the contract that pre-2026-05-27 was duplicated across
``flux_schnell.py`` and ``image_gen.py``:

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
        provider_tag="image_gen",
    )

    upload_kwargs = mock_cloudinary.call_args.kwargs
    assert "image_gen" in upload_kwargs["tags"]
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


async def test_cloud_name_uses_sync_get_not_get_secret(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """``cloudinary_cloud_name`` is non-secret and must come from sync ``.get``.

    Pre-#334 the cloud_name was incorrectly fetched through ``get_secret``
    too. That's wasteful (extra DB hit per upload) and architecturally
    wrong: only ``is_secret=true`` rows go through ``get_secret``.
    """
    from services.cloudinary_upload_service import upload_to_cloudinary

    await upload_to_cloudinary(
        "/tmp/x.png",
        "prompt",
        site_config=mock_site_config,
        provider_tag="flux_schnell",
    )

    sync_keys = {call.args[0] for call in mock_site_config.get.call_args_list}
    secret_keys = {call.args[0] for call in mock_site_config.get_secret.call_args_list}
    assert "cloudinary_cloud_name" in sync_keys
    assert "cloudinary_cloud_name" not in secret_keys


async def test_secret_values_propagate_to_cloudinary_config(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Decrypted secrets flow through ``get_secret`` into ``cloudinary.config``.

    Calling ``get_secret`` isn't enough — the returned plaintext values
    have to land in the SDK config. A future refactor that fetches but
    drops the values would not be caught by the existing "call_args_list"
    assertion.
    """
    import cloudinary  # type: ignore[import-not-found]

    from services.cloudinary_upload_service import upload_to_cloudinary

    await upload_to_cloudinary(
        "/tmp/x.png",
        "prompt",
        site_config=mock_site_config,
        provider_tag="flux_schnell",
    )

    config_kwargs = cloudinary.config.call_args.kwargs  # type: ignore[attr-defined]
    assert config_kwargs["cloud_name"] == "test-cloud"
    assert config_kwargs["api_key"] == "test-api-key"
    assert config_kwargs["api_secret"] == "test-api-secret"


async def test_upload_kwargs_pin_folder_and_resource_type(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Uploads always land in ``generated/`` folder as ``image`` resource.

    Operators rely on the folder to scope retention + CDN rules; the
    resource_type controls Cloudinary's pipeline (image vs video vs raw).
    A refactor that flips either silently would scatter assets across
    the operator's Cloudinary account.
    """
    from services.cloudinary_upload_service import upload_to_cloudinary

    await upload_to_cloudinary(
        "/tmp/x.png",
        "prompt",
        site_config=mock_site_config,
        provider_tag="image_gen",
    )

    upload_kwargs = mock_cloudinary.call_args.kwargs
    assert upload_kwargs["folder"] == "generated/"
    assert upload_kwargs["resource_type"] == "image"


async def test_missing_secure_url_key_raises(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Dict missing ``secure_url`` entirely triggers the empty-url guard.

    Existing test only covers ``secure_url=""``. If Cloudinary changes
    the response key (or returns ``{"error": ...}`` instead), the
    ``.get(key, "")`` fallback should still trigger the RuntimeError
    rather than returning the empty-string default to the caller.
    """
    mock_cloudinary.return_value = {"public_id": "x", "version": 1}

    from services.cloudinary_upload_service import upload_to_cloudinary

    with pytest.raises(RuntimeError, match="empty secure_url"):
        await upload_to_cloudinary(
            "/tmp/x.png",
            "prompt",
            site_config=mock_site_config,
            provider_tag="flux_schnell",
        )


async def test_short_prompt_passed_intact_to_alt(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Prompts ≤ 200 chars are NOT truncated — the slice is a cap, not a fix.

    Boundary check at 200 chars: should pass through untouched.
    """
    from services.cloudinary_upload_service import upload_to_cloudinary

    prompt_200 = "y" * 200
    await upload_to_cloudinary(
        "/tmp/x.png",
        prompt_200,
        site_config=mock_site_config,
        provider_tag="flux_schnell",
    )

    upload_kwargs = mock_cloudinary.call_args.kwargs
    assert upload_kwargs["context"]["alt"] == prompt_200
    assert len(upload_kwargs["context"]["alt"]) == 200


async def test_empty_prompt_does_not_crash(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """Empty prompt yields empty alt without raising.

    The logger.debug at the end takes ``prompt[:40]`` — needs to be
    safe on empty strings.
    """
    from services.cloudinary_upload_service import upload_to_cloudinary

    url = await upload_to_cloudinary(
        "/tmp/x.png",
        "",
        site_config=mock_site_config,
        provider_tag="flux_schnell",
    )

    upload_kwargs = mock_cloudinary.call_args.kwargs
    assert upload_kwargs["context"]["alt"] == ""
    assert url == "https://res.cloudinary.com/x/y.png"


async def test_path_passed_as_first_positional_arg(
    mock_cloudinary: MagicMock, mock_site_config: MagicMock,
) -> None:
    """The file path is the first positional arg to ``cloudinary.uploader.upload``.

    The Cloudinary SDK accepts either a file path string or a file-like
    object as the first positional. Pinning positional placement keeps
    a future refactor from accidentally moving it to a keyword arg
    (Cloudinary's SDK has no ``path=`` kwarg — that would silently break).
    """
    from services.cloudinary_upload_service import upload_to_cloudinary

    await upload_to_cloudinary(
        "/path/with/spaces and unicode-é.png",
        "prompt",
        site_config=mock_site_config,
        provider_tag="flux_schnell",
    )

    assert mock_cloudinary.call_args.args[0] == "/path/with/spaces and unicode-é.png"
    assert mock_cloudinary.call_count == 1

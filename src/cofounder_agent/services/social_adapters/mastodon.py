"""Mastodon adapter — posts to any Mastodon/Fediverse instance.

Mastodon instances are free (public, self-hosted, or pick your server).
We use ``Mastodon.py`` (MIT-licensed) instead of hand-rolling HTTP so
status length trimming, visibility enums, and 429 retries are handled
by a library the whole Fediverse has vetted.

Credentials live in ``app_settings`` (DB-first config, GH-93):

    ``mastodon_instance_url``  — e.g. ``https://mastodon.social``. Plain
                                 config row — not a secret.
    ``mastodon_access_token``  — from Preferences > Development >
                                 New Application (needs ``write:statuses``
                                 scope). ``is_secret=true``.

Rotation: revoke the old application on your Mastodon instance, create
a new one, paste the token into the secret row. Next post picks it up.

GH-36: replaces dlvr.it with direct Fediverse publishing.
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

# Mastodon's default toot length. Some instances allow more; we conservatively
# truncate at 500 so the post lands on any server.
_MAX_POST_CHARS = 500


def _truncate(text: str, limit: int = _MAX_POST_CHARS) -> str:
    """Hard-truncate with an ellipsis when over ``limit`` characters."""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


async def post_to_mastodon(text: str, url: str, **kwargs: Any) -> dict:
    """Post a status to Mastodon.

    Returns::

        {"success": bool, "post_id": str | None, "error": str | None}

    On missing credentials the call short-circuits with a clear
    "not configured" message — callers (social_poster) treat this as a
    soft skip, not a crash.
    """
    instance_url = site_config.get("mastodon_instance_url", "").rstrip("/")
    access_token = await site_config.get_secret("mastodon_access_token", "")

    if not instance_url or not access_token:
        msg = "mastodon_instance_url or mastodon_access_token not configured"
        logger.info("[MASTODON] Skipped — %s", msg)
        return {"success": False, "post_id": None, "error": msg}

    # Lazy import so pytest collection doesn't require Mastodon.py to be
    # installed everywhere. Production installs pick it up via poetry/pip.
    try:
        from mastodon import Mastodon  # type: ignore[import-not-found]
    except ImportError as e:
        logger.error(
            "[MASTODON] Mastodon.py package is not installed; install with "
            "`pip install Mastodon.py` or `poetry install`. Error: %s", e
        )
        return {
            "success": False,
            "post_id": None,
            "error": f"Mastodon.py package not installed: {e}",
        }

    post_text = text if url in text else f"{text}\n\n{url}"
    post_text = _truncate(post_text)

    try:
        import asyncio

        def _post() -> Any:
            client = Mastodon(
                access_token=access_token,
                api_base_url=instance_url,
            )
            return client.status_post(status=post_text, visibility="public")

        # Mastodon.py is sync — push to a thread so the event loop stays free.
        status = await asyncio.to_thread(_post)
        post_id = str(status.get("id", "")) if isinstance(status, dict) else str(getattr(status, "id", ""))
        post_url = status.get("url", "") if isinstance(status, dict) else getattr(status, "url", "")
        logger.info("[MASTODON] Posted: %s", post_url or post_id)
        return {"success": True, "post_id": post_id, "error": None}

    except Exception as e:  # noqa: BLE001 — adapter boundary
        logger.exception("[MASTODON] Post failed: %s", e)
        return {"success": False, "post_id": None, "error": str(e)}

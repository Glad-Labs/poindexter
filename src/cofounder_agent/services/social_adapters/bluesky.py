"""Bluesky adapter — posts to Bluesky via the AT Protocol.

Bluesky is free, open, and has no paid API tier. We use the ``atproto``
Python SDK (MIT-licensed) rather than hand-rolling HTTP calls so rich-text
facets, session refresh, and error handling come "for free".

Credentials live in ``app_settings`` (DB-first config, GH-93):

    ``bluesky_identifier``    — handle or DID, e.g. ``gladlabs.bsky.social``.
                                ``is_secret=true`` — stored encrypted via pgcrypto.
    ``bluesky_app_password``  — app password from bsky.app/settings/app-passwords.
                                ``is_secret=true`` — NEVER the account password.

Rotation: generate a new app password on bsky.app, update the row via
``mcp__poindexter__set_setting`` or ``site_config.set_secret``, and the
next post picks it up (no restart required — secrets aren't cached).

GH-36: replaces dlvr.it RSS bridge with direct AT Protocol posting.
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

# Bluesky post text limit (graphemes, but conservatively measured as chars).
_MAX_POST_CHARS = 300


def _truncate(text: str, limit: int = _MAX_POST_CHARS) -> str:
    """Hard-truncate with an ellipsis when over ``limit`` characters."""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


async def post_to_bluesky(text: str, url: str, **kwargs: Any) -> dict:
    """Post a status to Bluesky.

    Returns::

        {"success": bool, "post_id": str | None, "error": str | None}

    On missing credentials the call short-circuits with a clear
    "not configured" message — callers (social_poster) treat this as a
    soft skip, not a crash.
    """
    # DI seam (glad-labs-stack#330) — site_config is passed by the
    # social_poster orchestrator. ``site_config=None`` is treated as
    # "credentials unconfigured" and falls through to the soft-skip path.
    site_config = kwargs.get("site_config")
    if site_config is None:
        msg = "site_config not provided to bluesky adapter"
        logger.info("[BLUESKY] Skipped — %s", msg)
        return {"success": False, "post_id": None, "error": msg}
    identifier = await site_config.get_secret("bluesky_identifier", "")
    password = await site_config.get_secret("bluesky_app_password", "")

    if not identifier or not password:
        msg = "bluesky_identifier or bluesky_app_password not configured"
        logger.info("[BLUESKY] Skipped — %s", msg)
        return {"success": False, "post_id": None, "error": msg}

    # Lazy import so pytest collection doesn't require atproto to be
    # installed in every environment. The library is declared in
    # pyproject.toml; production installs pick it up via poetry/pip.
    try:
        from atproto import Client  # type: ignore[import-not-found]
    except ImportError as e:
        logger.error(
            "[BLUESKY] atproto package is not installed; install with "
            "`pip install atproto` or `poetry install`. Error: %s", e
        )
        return {
            "success": False,
            "post_id": None,
            "error": f"atproto package not installed: {e}",
        }

    post_text = text if url in text else f"{text}\n\n{url}"
    post_text = _truncate(post_text)

    try:
        client = Client()
        # atproto's Client is sync — run in a thread so we don't block
        # the event loop during login/post.
        import asyncio

        def _login_and_post() -> Any:
            client.login(identifier, password)
            return client.send_post(text=post_text)

        response = await asyncio.to_thread(_login_and_post)
        post_uri = getattr(response, "uri", "") or ""
        logger.info("[BLUESKY] Posted: %s", post_uri)
        return {"success": True, "post_id": post_uri, "error": None}

    except Exception as e:  # noqa: BLE001 — adapter boundary; caller logs/metrics
        logger.exception("[BLUESKY] Post failed: %s", e)
        return {"success": False, "post_id": None, "error": str(e)}

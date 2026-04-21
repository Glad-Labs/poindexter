"""YouTube adapter — STUB.

YouTube uploads go through the Google Data API v3, which requires a
Google Cloud project, OAuth 2.0 consent screen approval (with video
upload scope, ``youtube.upload``), and an interactive browser flow to
mint the first refresh token. Matt hasn't set that up yet (see GH-40),
so this adapter is intentionally a stub — calling it raises
``NotImplementedError`` instead of silently no-op'ing.

When we're ready to wire this up (GH-40):

1. Create a Google Cloud project, enable the YouTube Data API v3.
2. Configure the OAuth consent screen (external, unverified is fine for
   a single channel's own uploads — stays under the 100-user cap).
3. Run the OAuth flow locally, capture the refresh token.
4. Seed ``youtube_client_id``, ``youtube_client_secret``,
   ``youtube_refresh_token`` (all is_secret=true) into ``app_settings``.
5. Replace this stub with a real implementation.

GH-36 retired dlvr.it as the cross-poster. YouTube stays off until GH-40
unblocks it.
"""

from __future__ import annotations

from typing import Any, NoReturn

from services.logger_config import get_logger

logger = get_logger(__name__)


async def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "28",
    privacy: str = "public",
    **kwargs: Any,
) -> NoReturn:
    """Intentionally unimplemented — see module docstring / GH-40."""
    logger.warning(
        "[YOUTUBE] upload_to_youtube called but YouTube OAuth is not set up. "
        "See GH-40 for the setup checklist. Skipping."
    )
    raise NotImplementedError(
        "YouTube adapter requires OAuth setup — see GH-40"
    )

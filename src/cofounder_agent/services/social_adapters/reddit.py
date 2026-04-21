"""Reddit adapter — STUB.

Reddit's API requires a registered "script" or "web" application, OAuth
credentials (client_id + client_secret + username + password, or OAuth
refresh token), and subreddit-specific posting rules that differ per
community. None of that is wired up yet (see GH-40), so this adapter
is intentionally a stub — calling it raises ``NotImplementedError``
rather than posting garbage or silently no-op'ing.

When we're ready to wire this up (GH-40):

1. Create a script-type app at https://www.reddit.com/prefs/apps.
2. Seed ``reddit_client_id``, ``reddit_client_secret``, ``reddit_username``,
   ``reddit_password`` (all is_secret=true) + ``reddit_subreddits`` into
   ``app_settings``.
3. Replace this stub with a real implementation (PRAW or asyncpraw are
   both fine; keep it free).

GH-36 retired dlvr.it as the cross-poster. Reddit stays off until GH-40
unblocks it.
"""

from __future__ import annotations

from typing import Any, NoReturn

from services.logger_config import get_logger

logger = get_logger(__name__)


async def post_to_reddit(title: str, url: str, **kwargs: Any) -> NoReturn:
    """Intentionally unimplemented — see module docstring / GH-40."""
    logger.warning(
        "[REDDIT] post_to_reddit called but Reddit OAuth is not set up. "
        "See GH-40 for the setup checklist. Skipping."
    )
    raise NotImplementedError(
        "Reddit adapter requires OAuth setup — see GH-40"
    )

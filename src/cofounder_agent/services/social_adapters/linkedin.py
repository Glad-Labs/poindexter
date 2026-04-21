"""LinkedIn adapter — STUB.

LinkedIn's Marketing Developer Platform + Community Management API
require a LinkedIn-approved Company Page, an OAuth 2.0 flow against the
Developer Portal, and periodic token refresh. Matt hasn't set any of
that up yet (see GH-40), so this adapter is intentionally a stub —
calling it will raise ``NotImplementedError`` rather than silently
no-op.

When we're ready to wire this up (GH-40):

1. Create a LinkedIn app in https://www.linkedin.com/developers/
2. Request the ``w_organization_social`` / ``rw_organization_admin`` scope.
3. Run the 3-legged OAuth flow, capture the refresh token.
4. Seed ``linkedin_access_token`` (is_secret=true), ``linkedin_refresh_token``
   (is_secret=true), and ``linkedin_org_id`` into ``app_settings``.
5. Replace this stub with a real implementation.

GH-36 retired dlvr.it as the cross-poster. LinkedIn stays off until GH-40
unblocks it.
"""

from __future__ import annotations

from typing import Any, NoReturn

from services.logger_config import get_logger

logger = get_logger(__name__)


async def post_to_linkedin(text: str, url: str, **kwargs: Any) -> NoReturn:
    """Intentionally unimplemented — see module docstring / GH-40."""
    logger.warning(
        "[LINKEDIN] post_to_linkedin called but LinkedIn OAuth is not set up. "
        "See GH-40 for the setup checklist. Skipping."
    )
    raise NotImplementedError(
        "LinkedIn adapter requires OAuth setup — see GH-40"
    )

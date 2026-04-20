"""LinkedIn adapter — posts to LinkedIn via the Community Management API.

Free for org pages. Requires:
    app_settings:
        linkedin_access_token  — OAuth 2.0 token (from LinkedIn Developer Portal)
        linkedin_org_id        — organization URN ID (numeric)

Usage:
    from services.social_adapters.linkedin import post_to_linkedin
    result = await post_to_linkedin("Check out this post!", "https://gladlabs.io/posts/my-post")

Note: LinkedIn OAuth tokens expire. You'll need to refresh periodically
via the LinkedIn Developer Portal or implement token refresh.
"""

import httpx

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

LINKEDIN_API = "https://api.linkedin.com/v2"


async def post_to_linkedin(text: str, url: str, **kwargs) -> dict:
    """Post to LinkedIn org page. Returns {"success": bool, "post_id": str | None, "error": str | None}."""
    access_token = await site_config.get_secret("linkedin_access_token", "")
    org_id = site_config.get("linkedin_org_id", "")

    if not access_token or not org_id:
        return {"success": False, "post_id": None, "error": "linkedin_access_token or linkedin_org_id not configured"}

    try:
        author = f"urn:li:organization:{org_id}"

        post_body = {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "ARTICLE",
                    "media": [
                        {
                            "status": "READY",
                            "originalUrl": url,
                        }
                    ],
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{LINKEDIN_API}/ugcPosts",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Content-Type": "application/json",
                },
                json=post_body,
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                post_id = data.get("id", "")
                logger.info("[LINKEDIN] Posted: %s", post_id)
                return {"success": True, "post_id": post_id, "error": None}
            else:
                err = resp.text[:200]
                logger.warning("[LINKEDIN] Post failed: %s %s", resp.status_code, err)
                return {"success": False, "post_id": None, "error": err}

    except Exception as e:
        logger.exception("[LINKEDIN] Error: %s", e)
        return {"success": False, "post_id": None, "error": str(e)}

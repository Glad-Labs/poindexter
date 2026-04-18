"""Reddit adapter — posts to subreddits via the Reddit API.

Free (OAuth, rate-limited to 60 req/min). Requires:
    app_settings:
        reddit_client_id       — from reddit.com/prefs/apps (script type)
        reddit_client_secret   — from the same app
        reddit_username        — your Reddit account
        reddit_password        — your Reddit account password
        reddit_subreddits      — comma-separated target subs (e.g. "programming,Python")

Usage:
    from services.social_adapters.reddit import post_to_reddit
    result = await post_to_reddit(
        title="How Indie Hackers Actually Make Money in 2026",
        url="https://gladlabs.io/posts/how-indie-hackers-make-money",
    )
"""

import httpx

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
USER_AGENT = "poindexter:v0.2.0 (by /u/{username})"


async def _get_access_token() -> tuple[str | None, str]:
    """Get OAuth token via password grant. Returns (token, username)."""
    client_id = await site_config.get_secret("reddit_client_id", "")
    client_secret = await site_config.get_secret("reddit_client_secret", "")
    username = await site_config.get_secret("reddit_username", "")
    password = await site_config.get_secret("reddit_password", "")

    if not all([client_id, client_secret, username, password]):
        return None, ""

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            REDDIT_TOKEN_URL,
            auth=(client_id, client_secret),
            data={
                "grant_type": "password",
                "username": username,
                "password": password,
            },
            headers={"User-Agent": USER_AGENT.format(username=username)},
        )
        if resp.status_code == 200:
            return resp.json().get("access_token"), username
        logger.warning("[REDDIT] Auth failed: %s", resp.text[:200])
        return None, username


async def post_to_reddit(title: str, url: str, **kwargs) -> dict:
    """Post a link to configured subreddits. Returns {"success", "post_id", "error"}."""
    subreddits_str = site_config.get("reddit_subreddits", "")
    if not subreddits_str:
        return {"success": False, "post_id": None, "error": "reddit_subreddits not configured"}

    access_token, username = await _get_access_token()
    if not access_token:
        return {"success": False, "post_id": None, "error": "Reddit auth not configured or failed"}

    subreddits = [s.strip() for s in subreddits_str.split(",") if s.strip()]
    results = []

    async with httpx.AsyncClient(timeout=15) as client:
        for sub in subreddits:
            try:
                resp = await client.post(
                    f"{REDDIT_API}/api/submit",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "User-Agent": USER_AGENT.format(username=username),
                    },
                    data={
                        "sr": sub,
                        "kind": "link",
                        "title": title[:300],
                        "url": url,
                        "resubmit": "true",
                    },
                )
                data = resp.json()
                if resp.status_code == 200 and not data.get("json", {}).get("errors"):
                    post_url = data.get("json", {}).get("data", {}).get("url", "")
                    logger.info("[REDDIT] Posted to r/%s: %s", sub, post_url)
                    results.append({"sub": sub, "success": True, "url": post_url})
                else:
                    errors = data.get("json", {}).get("errors", [])
                    err_str = str(errors[:2]) if errors else resp.text[:100]
                    logger.warning("[REDDIT] r/%s failed: %s", sub, err_str)
                    results.append({"sub": sub, "success": False, "error": err_str})
            except Exception as e:
                results.append({"sub": sub, "success": False, "error": str(e)})

    any_success = any(r.get("success") for r in results)
    return {
        "success": any_success,
        "post_id": None,
        "results": results,
        "error": None if any_success else "All subreddit posts failed",
    }

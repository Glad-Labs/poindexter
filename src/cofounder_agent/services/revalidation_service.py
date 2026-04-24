"""ISR revalidation service — triggers Next.js cache invalidation.

Extracted from routes/revalidate_routes.py to break the circular
import: publish_service → routes was violating the one-way dependency
rule (routes should import services, not vice versa).
"""

from typing import Any

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)


async def trigger_nextjs_revalidation(
    paths: list | None = None,
    tags: list | None = None,
    *,
    site_config: Any,
) -> bool:
    """Trigger Next.js ISR revalidation on the public site.

    Args:
        paths: List of paths to revalidate. Defaults to ["/", "/archive"].
        tags: List of cache tags to revalidate. Defaults to ["posts", "post-index"].
        site_config: SiteConfig instance (DI — Phase H). Keyword-only so
            callers don't pass it positionally by mistake.

    Returns:
        True if revalidation succeeded, False otherwise.
    """
    if paths is None:
        paths = ["/", "/archive"]
    if tags is None:
        tags = ["posts", "post-index"]

    from services.bootstrap_defaults import DEFAULT_PUBLIC_SITE_URL
    nextjs_url = (
        site_config.get("public_site_url")
        or site_config.get("site_url")
        or site_config.get("next_public_public_site_url")
        or site_config.get("next_public_api_base_url", DEFAULT_PUBLIC_SITE_URL)
    )
    if nextjs_url.endswith("/api"):
        nextjs_url = nextjs_url[:-4]

    revalidate_url = f"{nextjs_url}/api/revalidate"

    # revalidate_secret is is_secret=true in app_settings — must use
    # get_secret() for decryption. Previously we did a raw SELECT here
    # (which bypassed decryption entirely) and a .get() fallback (which
    # returned the enc:v1:<ciphertext> blob); both shipped the wrong
    # value as the x-revalidate-secret header and the public site 401'd
    # every revalidation (GH-107, prod incident 2026-04-23).
    revalidate_secret = await site_config.get_secret("revalidate_secret", "")
    environment = (
        site_config.get("environment", "development") or "development"
    ).lower()

    if not revalidate_secret:
        logger.error("REVALIDATE_SECRET is not set — refusing to revalidate in %s", environment)
        return False

    try:
        logger.info("Triggering Next.js ISR revalidation...")
        logger.info("   URL: %s", revalidate_url)
        logger.info("   Paths: %s", paths)
        logger.info("   Tags: %s", tags)

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                revalidate_url,
                json={"paths": paths, "tags": tags},
                headers={
                    "x-revalidate-secret": revalidate_secret,
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                logger.info("ISR revalidation successful")
                return True
            else:
                logger.warning("ISR revalidation returned %s", response.status_code)
                logger.warning("   Response: %s", response.text[:200])
                return False

    except httpx.TimeoutException:
        logger.warning("ISR revalidation timed out (10s)", exc_info=True)
        return False
    except Exception as e:
        logger.warning(
            "Failed to trigger ISR revalidation: %s: %s", type(e).__name__, e, exc_info=True
        )
        return False

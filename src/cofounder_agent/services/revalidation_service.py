"""ISR revalidation service — triggers Next.js cache invalidation.

Extracted from routes/revalidate_routes.py to break the circular
import: publish_service → routes was violating the one-way dependency
rule (routes should import services, not vice versa).

Glad-Labs/poindexter#327: hardened so EVERY publish path (canonical
publish_service, the /go-live admin route, the scheduled_publisher
that promotes scheduled→published) shares the same revalidation
helper. Previously the bypass paths inserted directly into ``posts``
and never told Vercel to bust its 5-minute ISR cache.

Two helpers are exposed:

* ``trigger_nextjs_revalidation(paths, tags, *, site_config=None)`` —
  the low-level POST to the Next.js ``/api/revalidate`` endpoint.
* ``trigger_isr_revalidate(slug, paths, tags, site_config)`` — the
  publish-time wrapper. Always includes the canonical site routes
  (``/``, ``/archive``, ``/posts``, ``/sitemap.xml``) and the
  slug-specific ``/posts/<slug>`` path + ``post:<slug>`` cache tag.
  Idempotent, never raises — revalidation failure must not roll back
  a publish.
"""

import httpx

from services.bootstrap_defaults import DEFAULT_PUBLIC_SITE_URL
from services.logger_config import get_logger
# Module-level import kept under the historical ``site_config`` name so
# existing tests that ``patch("services.revalidation_service.site_config",
# mock)`` keep working without churn. New code paths should accept a
# ``site_config`` parameter (DI) instead of relying on the singleton.
from services.site_config import site_config  # noqa: F401  # dynamic use via test patches

logger = get_logger(__name__)


# Default fallback for the public revalidate URL — used only when the
# DB lookup of `public_site_revalidate_url` returns empty AND no other
# `*_site_url` setting is wired up. Migration 0126 seeds the live value.
DEFAULT_REVALIDATE_URL = "https://www.gladlabs.io/api/revalidate"


def _resolve_revalidate_url(site_cfg) -> str:
    """Pick the Next.js /api/revalidate endpoint from settings.

    Priority:
    1. ``public_site_revalidate_url`` (explicit setting, seeded by
       migration 0126 to ``https://www.gladlabs.io/api/revalidate``).
    2. ``public_site_url`` / ``site_url`` / ``next_public_public_site_url``
       / ``next_public_api_base_url`` with ``/api/revalidate`` appended
       (back-compat with the pre-#327 resolution chain).
    3. ``DEFAULT_REVALIDATE_URL`` constant.
    """
    explicit = (site_cfg.get("public_site_revalidate_url", "") or "").strip()
    if explicit:
        return explicit

    base = (
        site_cfg.get("public_site_url")
        or site_cfg.get("site_url")
        or site_cfg.get("next_public_public_site_url")
        or site_cfg.get("next_public_api_base_url", DEFAULT_PUBLIC_SITE_URL)
    )
    base = (base or "").rstrip("/")
    if base.endswith("/api"):
        base = base[:-4].rstrip("/")
    if not base:
        return DEFAULT_REVALIDATE_URL
    return f"{base}/api/revalidate"


async def _resolve_revalidate_secret(site_cfg) -> str:
    """Fetch the shared revalidate secret.

    The secret is stored as ``revalidate_secret`` in ``app_settings``
    with ``is_secret=true`` (so it's filtered out of the in-memory
    site_config cache). It MUST be fetched via the async
    ``get_secret`` method — the user spec for #327 is explicit on
    this.
    """
    try:
        secret = await site_cfg.get_secret("revalidate_secret", "")
    except Exception as e:
        logger.warning("[revalidation] get_secret failed: %s", e)
        secret = ""

    if secret:
        return secret

    # Back-compat fallback: legacy plaintext row that some installs
    # still have under is_secret=false. Only used if the async path
    # returned empty.
    legacy = site_cfg.get("revalidate_secret", "")
    return legacy or ""


async def trigger_nextjs_revalidation(
    paths: list | None = None,
    tags: list | None = None,
    *,
    site_config: object | None = None,
) -> bool:
    """Trigger Next.js ISR revalidation on the public site.

    Args:
        paths: List of paths to revalidate. Defaults to ``["/", "/archive"]``.
        tags: List of cache tags to revalidate. Defaults to
            ``["posts", "post-index"]``.
        site_config: Optional ``SiteConfig`` instance for DI. Falls
            back to the module-level singleton when omitted (the
            singleton is still populated at app startup via
            ``main.py``'s lifespan).

    Returns:
        True if revalidation succeeded (HTTP 200), False otherwise.
        Never raises — a publish must not be rolled back by a
        revalidation failure.
    """
    if paths is None:
        paths = ["/", "/archive"]
    if tags is None:
        tags = ["posts", "post-index"]

    # The parameter `site_config` shadows the module-level singleton
    # imported above. globals() always returns the module dict, so we
    # can fall back to the singleton when no DI instance was passed.
    site_cfg = site_config if site_config is not None else globals().get("site_config")

    revalidate_url = _resolve_revalidate_url(site_cfg)
    revalidate_secret = await _resolve_revalidate_secret(site_cfg)

    if not revalidate_secret:
        # Skip — but warn loudly. Per the no-silent-defaults rule,
        # operators get told that revalidation was skipped instead of
        # silently swallowing the misconfiguration.
        environment = (site_cfg.get("environment", "development") or "development").lower()
        logger.warning(
            "[revalidation] revalidate_secret is unset — skipping ISR revalidation in %s",
            environment,
        )
        return False

    try:
        logger.info(
            "[revalidation] POST %s paths=%s tags=%s",
            revalidate_url, paths, tags,
        )

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
                logger.info("[revalidation] ISR revalidation successful")
                return True
            logger.warning(
                "[revalidation] ISR revalidation returned %s: %s",
                response.status_code, response.text[:200],
            )
            return False

    except httpx.TimeoutException:
        logger.warning("[revalidation] ISR revalidation timed out (10s)")
        return False
    except Exception as e:
        logger.warning(
            "[revalidation] Failed to trigger ISR revalidation: %s: %s",
            type(e).__name__, e,
        )
        return False


# Canonical paths that EVERY publish must revalidate, in addition to
# the slug-specific path. These are the indexes that list/feature the
# new post. /feed.xml was missing pre-2026-05-01 — without it, dlvr.it /
# IFTTT / RSS subscribers saw 17+ days of stale content because the
# Vercel ISR cache for /feed.xml only refreshed on its natural 5-min
# inner fetch TTL, never on publish.
_CANONICAL_PATHS = ("/", "/archive", "/posts", "/sitemap.xml", "/feed.xml")
# Canonical tags that EVERY publish must invalidate. These match the
# `next: { tags: [...] }` keys set in web/public-site/lib/posts.ts.
_CANONICAL_TAGS = ("posts", "post-index")


async def trigger_isr_revalidate(
    slug: str,
    paths: list[str] | None = None,
    tags: list[str] | None = None,
    site_config: object | None = None,
) -> bool:
    """Publish-time ISR revalidation helper used by every publish path.

    Glad-Labs/poindexter#327: this is the ONE function that any code
    path which materializes a published post must call. It is safe to
    call from the canonical ``publish_service.publish_post_from_task``
    flow, the ``/go-live`` admin endpoint, and the
    ``scheduled_publisher`` background loop.

    Always merges the canonical site routes (``/``, ``/archive``,
    ``/posts``, ``/sitemap.xml``) and tags (``posts``, ``post-index``)
    with the slug-specific path (``/posts/<slug>``) and tag
    (``post:<slug>``). Extra ``paths`` / ``tags`` from the caller are
    union'd in.

    Idempotent + safe to call from anywhere:
    * Reads ``revalidate_secret`` via the async ``get_secret``.
    * Skips with a ``logger.warning`` if the secret is empty (no
      raise).
    * httpx call has a 10s timeout; success/failure is logged but
      ``trigger_isr_revalidate`` never raises — a revalidation
      failure must not roll back a publish.

    Args:
        slug: Post slug (e.g. ``my-great-post-aaaaaaaa``).
        paths: Optional extra paths to revalidate beyond the canonical
            set + the slug path.
        tags: Optional extra tags to revalidate beyond the canonical
            set + ``post:<slug>``.
        site_config: ``SiteConfig`` instance (DI-preferred).

    Returns:
        True on HTTP 200, False on any failure (skipped, error, etc.).
    """
    # Build the merged path list. dict-with-None preserves insertion
    # order while de-duping — set() would shuffle output and make
    # logs/tests harder to reason about.
    merged_paths: dict[str, None] = {}
    for p in _CANONICAL_PATHS:
        merged_paths[p] = None
    if slug:
        merged_paths[f"/posts/{slug}"] = None
    for p in (paths or []):
        if p:
            merged_paths[p] = None

    merged_tags: dict[str, None] = {}
    for t in _CANONICAL_TAGS:
        merged_tags[t] = None
    if slug:
        merged_tags[f"post:{slug}"] = None
    for t in (tags or []):
        if t:
            merged_tags[t] = None

    return await trigger_nextjs_revalidation(
        list(merged_paths),
        list(merged_tags),
        site_config=site_config,
    )

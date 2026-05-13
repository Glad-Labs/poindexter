"""ISR revalidation service — triggers Next.js cache invalidation.

Extracted from routes/revalidate_routes.py to break the circular
import: publish_service → routes was violating the one-way dependency
rule (routes should import services, not vice versa).

Glad-Labs/poindexter#327: hardened so EVERY publish path (canonical
publish_service, the /go-live admin route, the scheduled_publisher
that promotes scheduled→published) shares the same revalidation
helper. Previously the bypass paths inserted directly into ``posts``
and never told Vercel to bust its 5-minute ISR cache.

Three helpers are exposed:

* ``trigger_nextjs_revalidation_detailed(paths, tags, *, site_config)``
  — the low-level POST. Returns a ``RevalidationResult`` carrying
  ``success`` + the upstream HTTP status + the truncated error body
  + the request duration. Callers who need to surface the underlying
  failure (e.g. the operator-facing ``/api/revalidate-cache`` route)
  use this one.
* ``trigger_nextjs_revalidation(paths, tags, *, site_config=None)`` —
  thin wrapper that returns the boolean ``success`` for backwards
  compatibility with every existing caller. Internal failure context
  is captured in the structured log line.
* ``trigger_isr_revalidate(slug, paths, tags, site_config)`` — the
  publish-time wrapper. Always includes the canonical site routes
  (``/``, ``/archive``, ``/posts``, ``/sitemap.xml``) and the
  slug-specific ``/posts/<slug>`` path + ``post:<slug>`` cache tag.
  Idempotent, never raises — revalidation failure must not roll back
  a publish.
"""

import time
from dataclasses import dataclass
from typing import Any

import httpx

from services.bootstrap_defaults import DEFAULT_PUBLIC_SITE_URL
from services.logger_config import get_logger
from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# set_site_config() for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


logger = get_logger(__name__)


# Default fallback for the public revalidate URL — used only when the
# DB lookup of `public_site_revalidate_url` returns empty AND no other
# `*_site_url` setting is wired up. Migration 0126 seeds the live value.
DEFAULT_REVALIDATE_URL = "https://www.gladlabs.io/api/revalidate"


# ---------------------------------------------------------------------------
# Shared httpx client — pool reuse across publish bursts.
#
# Every revalidation call used to build a fresh ``httpx.AsyncClient`` for one
# POST. When a newsletter blast or batch approval flips multiple posts to
# published in quick succession (and we hit /api/revalidate for each), every
# call paid the TCP + TLS handshake to www.gladlabs.io. With a module-level
# shared client, the underlying connection pool keeps the TLS session warm
# across the burst. Hot enough to matter — multi-publish runs ship 5-20
# revalidate requests within a second.
# ---------------------------------------------------------------------------

_shared_http_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    """Lazily build the shared client. Per-request timeouts override the
    conservative module default."""
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(timeout=10)
    return _shared_http_client


async def aclose() -> None:
    """Close the shared httpx client. Idempotent. Wired into the FastAPI
    lifespan shutdown so process exit doesn't leak the connection pool."""
    global _shared_http_client
    if _shared_http_client is not None and not _shared_http_client.is_closed:
        await _shared_http_client.aclose()
    _shared_http_client = None


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


@dataclass(frozen=True)
class RevalidationResult:
    """Outcome of a single revalidation POST.

    Fields populated even on failure so callers (operator-facing route,
    monitoring, tests) can surface the underlying cause instead of just
    a blank ``success=false``.

    poindexter#458 — previously the worker route returned
    ``{"success": false, "message": "Cache revalidation failed"}`` with
    no upstream detail, sending the operator to grep `docker logs` to
    find out whether the public site 401'd, 5xx'd, or timed out.
    """

    success: bool
    skipped: bool  # True when the secret was unset and the POST was never attempted.
    status_code: int | None  # Upstream HTTP status, or None when the request never landed.
    error: str  # Truncated upstream body, exception class+message, or skip reason. "" on success.
    error_kind: str  # Categorical tag: '', 'skipped', 'timeout', 'http', 'exception'.
    duration_ms: int
    url: str  # The fully resolved revalidate URL we POSTed against.


async def _post_revalidate(
    paths: list,
    tags: list,
    site_cfg: Any,
) -> RevalidationResult:
    """Resolve config + execute the actual httpx POST. Always returns a result."""
    revalidate_url = _resolve_revalidate_url(site_cfg)
    revalidate_secret = await _resolve_revalidate_secret(site_cfg)

    if not revalidate_secret:
        environment = (site_cfg.get("environment", "development") or "development").lower()
        logger.warning(
            "[revalidation] revalidate_secret is unset — skipping ISR revalidation in %s",
            environment,
            extra={"revalidate_url": revalidate_url, "paths": paths, "tags": tags},
        )
        return RevalidationResult(
            success=False,
            skipped=True,
            status_code=None,
            error=f"revalidate_secret unset (environment={environment})",
            error_kind="skipped",
            duration_ms=0,
            url=revalidate_url,
        )

    started = time.monotonic()
    try:
        logger.info(
            "[revalidation] POST %s paths=%s tags=%s",
            revalidate_url, paths, tags,
        )

        client = _get_shared_client()
        response = await client.post(
            revalidate_url,
            json={"paths": paths, "tags": tags},
            headers={
                "x-revalidate-secret": revalidate_secret,
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        duration_ms = int((time.monotonic() - started) * 1000)
        if response.status_code == 200:
            logger.info(
                "[revalidation] ISR revalidation successful in %dms",
                duration_ms,
            )
            return RevalidationResult(
                success=True,
                skipped=False,
                status_code=200,
                error="",
                error_kind="",
                duration_ms=duration_ms,
                url=revalidate_url,
            )

        body_excerpt = response.text[:500]
        logger.warning(
            "[revalidation] ISR revalidation returned %s in %dms: %s",
            response.status_code, duration_ms, body_excerpt[:200],
            extra={
                "revalidate_url": revalidate_url,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "paths": paths,
                "tags": tags,
            },
        )
        return RevalidationResult(
            success=False,
            skipped=False,
            status_code=response.status_code,
            error=body_excerpt,
            error_kind="http",
            duration_ms=duration_ms,
            url=revalidate_url,
        )

    except httpx.TimeoutException:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "[revalidation] ISR revalidation timed out after %dms (10s budget)",
            duration_ms,
            extra={"revalidate_url": revalidate_url, "paths": paths, "tags": tags},
        )
        return RevalidationResult(
            success=False,
            skipped=False,
            status_code=None,
            error="timeout (10s)",
            error_kind="timeout",
            duration_ms=duration_ms,
            url=revalidate_url,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "[revalidation] Failed to trigger ISR revalidation: %s: %s",
            type(exc).__name__, exc,
            extra={
                "revalidate_url": revalidate_url,
                "duration_ms": duration_ms,
                "exception_type": type(exc).__name__,
            },
        )
        return RevalidationResult(
            success=False,
            skipped=False,
            status_code=None,
            error=f"{type(exc).__name__}: {exc}",
            error_kind="exception",
            duration_ms=duration_ms,
            url=revalidate_url,
        )


async def trigger_nextjs_revalidation_detailed(
    paths: list | None = None,
    tags: list | None = None,
    *,
    site_config: object | None = None,
) -> RevalidationResult:
    """Trigger Next.js ISR revalidation and return the full result struct.

    Use this when the caller wants to surface the failure reason
    (operator-facing route, monitoring, tests). Never raises.
    """
    if paths is None:
        paths = ["/", "/archive"]
    if tags is None:
        tags = ["posts", "post-index"]

    site_cfg = site_config if site_config is not None else globals()["site_config"]
    return await _post_revalidate(paths, tags, site_cfg)


async def trigger_nextjs_revalidation(
    paths: list | None = None,
    tags: list | None = None,
    *,
    site_config: object | None = None,
) -> bool:
    """Trigger Next.js ISR revalidation on the public site (legacy bool API).

    Thin wrapper over ``trigger_nextjs_revalidation_detailed``. Existing
    callers that only care about success/failure stay untouched. Code
    that needs the upstream status code / error body uses the detailed
    variant instead.

    Returns:
        True if revalidation succeeded (HTTP 200), False otherwise.
        Never raises — a publish must not be rolled back by a
        revalidation failure.
    """
    result = await trigger_nextjs_revalidation_detailed(
        paths, tags, site_config=site_config,
    )
    return result.success


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

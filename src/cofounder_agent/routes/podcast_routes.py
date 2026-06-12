"""
Podcast Routes — Serve podcast RSS feed and MP3 episode files.

Endpoints:
    GET /api/podcast/feed.xml     — Podcast RSS feed (Apple/Spotify compatible)
    GET /api/podcast/episodes     — JSON list of all episodes
    GET /api/podcast/episodes/{post_id}.mp3 — Stream an episode MP3
    POST /api/podcast/generate/{post_id} — Manually trigger episode generation
"""

from datetime import datetime, timezone
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.podcast_service import PODCAST_DIR, PodcastService
from utils.rate_limiter import _settings_limit, limiter
from utils.route_utils import get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/api/podcast", tags=["podcast"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 2026-05-12 (poindexter#485): removed the "_R2_FALLBACK" constant that
# baked Matt's specific R2 bucket URL into a public OSS file. Forks
# without R2 configured would have served podcast feeds pointing at
# Matt's bucket. Now the helper returns the configured value or empty
# string; routes detect empty and bail with 503.


def _site_url(site_config: Any) -> str:
    return site_config.require("site_url")


def _r2_url(site_config: Any) -> str:
    """Get object-store CDN base URL. Returns '' when unconfigured — caller
    must check before composing media URLs (use ``_r2_url_or_503``)."""
    return (site_config.get("storage_public_url", "") or "").rstrip("/")


def _r2_url_or_503(site_config: Any) -> str:
    """Variant that raises HTTP 503 when storage_public_url is unset. Use
    this in feed/route handlers where the URL is mandatory."""
    url = _r2_url(site_config)
    if not url:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail=(
                "storage_public_url not configured — podcast feed unavailable. "
                "Set via `poindexter set-setting storage_public_url 'https://<bucket>.r2.dev'`."
            ),
        )
    return url


def _format_duration(seconds: int) -> str:
    """Format seconds as HH:MM:SS for iTunes duration tag."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _rfc2822(dt: datetime) -> str:
    """Format datetime as RFC 2822 for RSS pubDate."""
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ---------------------------------------------------------------------------
# RSS Feed
# ---------------------------------------------------------------------------


@router.get("/feed.xml", response_class=Response)
async def podcast_feed(
    site_config: Any = Depends(get_site_config_dependency),
):
    """Generate a valid podcast RSS feed (Apple Podcasts / Spotify compatible)."""
    # Lazy import to avoid circular deps
    from utils.route_utils import get_services

    db = get_services().get_database()
    pool = getattr(db, "cloud_pool", None) or (db.pool if db else None)

    # Source episodes from media_assets (#689 Stage-3) — the canonical file
    # registry — NOT a local-disk scan, so atom-produced (task-keyed) episodes
    # surface the same as legacy post-keyed ones. The R2 enclosure URL, byte
    # size, and duration all ride on the asset row. Two stacked gates remain:
    #
    # 1. ``'podcast' = ANY(media_to_generate)`` — the canonical niche-policy
    #    seam (``feedback_filter_on_seams_not_slugs``). dev_diary's policy is
    #    ``{}``, excluding those posts even if a stray asset exists.
    # 2. ``media_approvals.status='approved'`` (medium='podcast') — the
    #    operator-approval gate (``feedback_human_approval``). Pending/rejected
    #    audio never reaches Apple/Spotify; the fix for a missing episode is to
    #    approve the row, not strip the gate.
    #
    # DISTINCT ON (p.id) collapses multiple podcast assets per post (e.g. a
    # reconciliation row + a pipeline row) to the newest one.
    episodes: list[dict] = []
    if pool:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (p.id)
                           p.id::text AS post_id, p.title, p.slug, p.excerpt,
                           p.seo_keywords, p.published_at,
                           mas.url, mas.file_size_bytes, mas.duration_ms
                    FROM posts p
                    JOIN media_assets mas
                      ON mas.post_id = p.id AND mas.type = 'podcast'
                    JOIN media_approvals ma
                      ON ma.post_id = p.id
                     AND ma.medium = 'podcast'
                     AND ma.status = 'approved'
                    WHERE p.status = 'published'
                      AND 'podcast' = ANY(media_to_generate)
                    ORDER BY p.id, mas.created_at DESC NULLS LAST
                    """,
                )
                for r in rows:
                    episodes.append({
                        "post_id": r["post_id"],
                        "title": r["title"] or "Untitled",
                        "slug": r["slug"] or r["post_id"],
                        "description": r["excerpt"] or "",
                        "keywords": r["seo_keywords"] or "",
                        "published_at": r["published_at"],
                        "file_size_bytes": r["file_size_bytes"] or 0,
                        "duration_seconds": int((r["duration_ms"] or 0) // 1000),
                        "enclosure_url": r["url"] or "",
                    })
        except Exception as e:
            logger.warning("[PODCAST] Failed to load feed episodes: %s", e)

    # DISTINCT ON forced p.id ordering; present newest-first by publish date.
    episodes.sort(
        key=lambda ep: ep.get("published_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return Response(
        content=_build_rss_xml(episodes, site_config),
        media_type="application/rss+xml; charset=utf-8",
    )


def _build_rss_xml(episodes: list[dict], site_config: Any) -> str:
    """Build a podcast RSS XML string following Apple Podcasts spec."""
    rss = Element("rss")
    rss.set("version", "2.0")
    rss.set("xmlns:itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")

    channel = SubElement(rss, "channel")

    # Channel metadata
    SubElement(channel, "title").text = site_config.get("podcast_name", "Podcast")
    SubElement(channel, "link").text = _site_url(site_config)
    SubElement(channel, "language").text = "en-us"
    SubElement(channel, "description").text = site_config.get(
        "podcast_description", "Podcast feed"
    )
    SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}author"
    ).text = site_config.get("site_name", "Author")
    SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}summary"
    ).text = (
        "AI development insights and guides from an AI-operated content business."
    )

    # itunes:owner
    owner = SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}owner"
    )
    SubElement(
        owner, "{http://www.itunes.com/dtds/podcast-1.0.dtd}name"
    ).text = site_config.get("owner_name", "Owner")
    SubElement(
        owner, "{http://www.itunes.com/dtds/podcast-1.0.dtd}email"
    ).text = site_config.get("owner_email", "")

    # itunes:category
    cat = SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}category"
    )
    cat.set("text", "Technology")

    SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit"
    ).text = "no"

    # itunes:image — Spotify + Apple require a square cover (1400-3000px).
    # Sourced from podcast_cover_url in app_settings so operators can swap
    # branding without a deploy. When the row is empty we skip the element
    # entirely rather than emit a broken href — podcast validators are
    # stricter about a missing href than they are about a missing image.
    cover_url = (site_config.get("podcast_cover_url", "") or "").strip()
    if cover_url:
        img = SubElement(
            channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}image"
        )
        img.set("href", cover_url)
        # Apple wants a top-level <image> too — same URL.
        top_img = SubElement(channel, "image")
        SubElement(top_img, "url").text = cover_url
        SubElement(top_img, "title").text = site_config.get(
            "podcast_name", "Podcast"
        )
        SubElement(top_img, "link").text = _site_url(site_config)

    # Atom self-link — points at the public proxy route on the site, not
    # the worker's internal /api/podcast/feed.xml (which 404s on the
    # public origin). Spotify's crawler validates this self-reference.
    atom_link = SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set(
        "href", f"{_site_url(site_config)}/podcast-feed.xml",
    )
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # Hoisted lookups (constant per feed render — read once, not per-episode)
    _domain = site_config.get("site_domain", "podcast")
    # Bump via: UPDATE app_settings SET value = 'v3' WHERE key = 'podcast_cdn_version';
    _cdn_ver = site_config.get("podcast_cdn_version", "v2")
    _r2 = _r2_url_or_503(site_config)
    _site = _site_url(site_config)

    # Episodes
    for ep in episodes:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = ep["title"]
        SubElement(item, "link").text = f"{_site}/posts/{ep['slug']}"
        SubElement(item, "description").text = ep.get("description", "")
        # itunes:summary is what Apple Podcasts / Spotify actually surface
        # for the episode body; mirror the SEO description.
        SubElement(
            item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}summary"
        ).text = ep.get("description", "")
        # itunes:keywords — SEO keywords (stored comma-joined in
        # posts.seo_keywords). Omit the element entirely when there are none.
        _kw = (ep.get("keywords", "") or "").strip()
        if _kw:
            SubElement(
                item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}keywords"
            ).text = _kw
        SubElement(item, "guid").text = f"{_domain}-podcast-{ep['post_id']}"

        pub_date = ep.get("published_at")
        if pub_date:
            if isinstance(pub_date, str):
                pub_date = datetime.fromisoformat(pub_date)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            SubElement(item, "pubDate").text = _rfc2822(pub_date)

        # Enclosure (the MP3 file). Prefer the media_assets R2 URL (#689
        # Stage-3 source of truth); fall back to the deterministic CDN path for
        # callers that build episodes without an asset row (legacy/tests).
        enclosure = SubElement(item, "enclosure")
        enclosure.set(
            "url",
            ep.get("enclosure_url") or f"{_r2}/podcast/{_cdn_ver}/{ep['post_id']}.mp3",
        )
        enclosure.set("length", str(ep.get("file_size_bytes", 0)))
        enclosure.set("type", "audio/mpeg")

        # iTunes-specific
        duration = ep.get("duration_seconds", 0)
        if duration:
            SubElement(
                item,
                "{http://www.itunes.com/dtds/podcast-1.0.dtd}duration",
            ).text = _format_duration(duration)

        SubElement(
            item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit"
        ).text = "no"

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        rss, encoding="unicode"
    )


# ---------------------------------------------------------------------------
# Episode streaming
# ---------------------------------------------------------------------------


@router.get("/episodes/{post_id}.mp3")
async def stream_episode(post_id: str):
    """Stream a podcast episode MP3 file."""
    # Sanitize post_id to prevent path traversal
    safe_id = post_id.replace("/", "").replace("\\", "").replace("..", "")
    path = PODCAST_DIR / f"{safe_id}.mp3"

    # Defense-in-depth: verify resolved path is under PODCAST_DIR
    if not path.resolve().is_relative_to(PODCAST_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Episode not found")

    if not path.exists():
        raise HTTPException(status_code=404, detail="Episode not found")

    return FileResponse(
        path=str(path),
        media_type="audio/mpeg",
        filename=f"{safe_id}.mp3",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400",
        },
    )


# ---------------------------------------------------------------------------
# Episode listing (JSON)
# ---------------------------------------------------------------------------


@router.get("/episodes")
async def list_episodes(
    site_config: Any = Depends(get_site_config_dependency),
    limit: int = Query(50, ge=1, le=200, description="Max episodes to return"),
    offset: int = Query(0, ge=0, description="Episodes to skip"),
):
    """List podcast episodes as JSON, paginated (closes #746 — the endpoint was
    previously unbounded and grew linearly with content volume forever)."""
    svc = PodcastService(site_config=site_config)
    all_episodes = svc.list_episodes()
    total = len(all_episodes)
    page = all_episodes[offset : offset + limit]
    return {"episodes": page, "count": len(page), "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Manual generation trigger
# ---------------------------------------------------------------------------


@router.post("/generate/{post_id}", dependencies=[Depends(verify_api_token)])
@limiter.limit(_settings_limit("rate_limit_podcast_generate_per_ip", "5/minute"))
async def generate_episode(
    request: Request,
    post_id: str,
    site_config: Any = Depends(get_site_config_dependency),
):
    """Manually trigger podcast episode generation for a published post."""
    from utils.route_utils import get_services

    db = get_services().get_database()
    pool = getattr(db, "cloud_pool", None) or (db.pool if db else None)
    if not pool:
        raise HTTPException(status_code=503, detail="Database not available")

    # Fetch post content from cloud DB where published posts live
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, title, content
                FROM posts
                WHERE id::text = $1 AND status = 'published'
                """,
                post_id,
            )
    except Exception as e:
        logger.error("Podcast generate DB error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Database error") from e

    if not row:
        raise HTTPException(status_code=404, detail="Published post not found")

    svc = PodcastService(site_config=site_config)
    result = await svc.generate_episode(
        post_id=row["id"],
        title=row["title"],
        content=row["content"] or "",
        force=True,
    )

    if not result.success:
        logger.error("Podcast generation failed for %s: %s", post_id, result.error)
        raise HTTPException(status_code=500, detail="Episode generation failed")

    return {
        "success": True,
        "post_id": post_id,
        "file_path": result.file_path,
        "duration_seconds": result.duration_seconds,
        "file_size_bytes": result.file_size_bytes,
    }

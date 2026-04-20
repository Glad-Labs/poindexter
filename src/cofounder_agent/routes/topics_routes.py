"""Topics routes — URL-based topic seeding (#230).

Lets users create content tasks from URLs. Two modes:
- POST /api/topics/from-url — one URL → one task
- POST /api/topics/from-urls — multiple URLs → top-N ranked tasks

Both modes fetch the URLs, extract title + content, and queue content
tasks with the URL as the research seed so the research stage uses it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.url_scraper import URLScrapeError, scrape_url
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/api/topics", tags=["topics"])


class FromUrlRequest(BaseModel):
    """Request to seed a single content task from a URL."""

    url: str = Field(..., description="URL to scrape for topic seed")
    category: str | None = Field(None, description="Optional category override")
    style: str | None = Field(None, description="Writing style override")
    tone: str | None = Field(None, description="Tone override")
    target_length: int | None = Field(None, description="Target word count")
    angle: str | None = Field(
        None,
        description=(
            "Optional writing angle hint — e.g. 'rebut this', 'summarize for "
            "beginners', 'explain the trade-offs'. Added to research context."
        ),
    )


class FromUrlsRequest(BaseModel):
    """Request to seed multiple content tasks from a list of URLs."""

    urls: list[str] = Field(..., min_length=1, max_length=20, description="List of URLs to process")
    max_topics: int = Field(5, ge=1, le=10, description="Max topics to produce")
    category: str | None = Field(None, description="Default category for all tasks")


@router.post(
    "/from-url",
    response_model=dict[str, Any],
    summary="Seed a content task from a single URL",
    status_code=201,
)
async def from_url(
    request: FromUrlRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Scrape a URL and queue a content task with it as the research seed.

    The research stage of the pipeline will use this URL as a primary
    source instead of doing general web search.
    """
    try:
        scraped = await scrape_url(request.url)
    except URLScrapeError as e:
        raise HTTPException(status_code=400, detail=f"Could not scrape URL: {e}") from e
    except Exception as e:
        logger.error("URL scrape crashed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="URL scrape failed") from e

    if not scraped.get("title") or scraped["title"] == "Untitled":
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract a title from {request.url}",
        )

    # Build the topic from the scraped title
    topic = scraped["title"]

    # Classify category if not provided
    category = request.category or _guess_category(scraped)

    metadata = {
        "source_url": request.url,
        "source_type": scraped.get("content_type", "article"),
        "source_preview": scraped.get("content_preview", "")[:500],
        "source_author": scraped.get("author"),
        "source_published_at": scraped.get("published_at"),
        "angle_hint": request.angle,
        "discovered_by": "url_seed",
    }

    # #231 fixed: add_task now only inserts columns that exist in the
    # content_tasks view. URL-seeded tasks share the same code path as
    # every other task creation site.
    returned_task_id = await db_service.add_task({
        "task_type": "blog_post",
        "content_type": "blog_post",
        "status": "pending",
        "topic": topic,
        "category": category,
        "style": request.style or "narrative",
        "tone": request.tone or "professional",
        "target_length": request.target_length or 1500,
        "metadata": metadata,
    })
    logger.info(
        "[URL_SEED] Task created: %s from %s (%d words scraped)",
        returned_task_id, request.url, scraped.get("word_count", 0),
    )

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "status": "pending",
        "topic": topic,
        "category": category,
        "source_url": request.url,
        "content_type": scraped.get("content_type"),
        "word_count": scraped.get("word_count"),
        "message": "Content task queued from URL",
    }


@router.post(
    "/from-urls",
    response_model=dict[str, Any],
    summary="Seed multiple content tasks from a URL list",
    status_code=201,
)
async def from_urls(
    request: FromUrlsRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Scrape multiple URLs and queue the top N as content tasks.

    Scrapes all URLs in parallel, ranks by content richness (word count
    + presence of excerpt/author metadata), and queues the top N.
    Skips URLs that fail to scrape.
    """
    import asyncio

    async def safe_scrape(u: str) -> tuple[str, dict | None, str | None]:
        try:
            return (u, await scrape_url(u), None)
        except URLScrapeError as e:
            return (u, None, str(e))
        except Exception as e:
            return (u, None, f"crash: {e}")

    results = await asyncio.gather(*[safe_scrape(u) for u in request.urls])

    scraped_ok: list[tuple[str, dict]] = []
    errors: list[dict] = []
    for url, data, err in results:
        if data and data.get("title") and data["title"] != "Untitled":
            scraped_ok.append((url, data))
        else:
            errors.append({"url": url, "error": err or "no title extracted"})

    # Rank by richness: word_count + has_excerpt + has_author
    def richness(item: tuple[str, dict]) -> float:
        _, d = item
        score = float(d.get("word_count", 0))
        if d.get("excerpt"):
            score += 100
        if d.get("author"):
            score += 50
        return score

    scraped_ok.sort(key=richness, reverse=True)
    top = scraped_ok[: request.max_topics]

    # #231 fixed: use add_task() instead of the former direct-INSERT
    # workaround — the column mismatch that forced the workaround is
    # resolved (dead columns dropped, functional columns moved to
    # task_metadata JSONB).
    task_ids: list[dict] = []
    for url, data in top:
        metadata = {
            "source_url": url,
            "source_type": data.get("content_type", "article"),
            "source_preview": data.get("content_preview", "")[:500],
            "source_author": data.get("author"),
            "source_published_at": data.get("published_at"),
            "discovered_by": "url_list",
        }
        returned_task_id = await db_service.add_task({
            "task_type": "blog_post",
            "content_type": "blog_post",
            "status": "pending",
            "topic": data["title"],
            "category": request.category or _guess_category(data),
            "style": "narrative",
            "tone": "professional",
            "target_length": 1500,
            "metadata": metadata,
        })
        task_ids.append({
            "task_id": returned_task_id,
            "topic": data["title"],
            "source_url": url,
        })

    logger.info(
        "[URL_LIST] Queued %d tasks from %d URLs (%d errors)",
        len(task_ids), len(request.urls), len(errors),
    )

    return {
        "queued": len(task_ids),
        "scraped": len(scraped_ok),
        "requested": len(request.urls),
        "tasks": task_ids,
        "errors": errors,
        "message": f"Queued {len(task_ids)} content tasks from {len(request.urls)} URLs",
    }


def _guess_category(scraped: dict) -> str:
    """Best-effort category guess from scraped content."""
    content_type = scraped.get("content_type", "")
    title = scraped.get("title", "").lower()
    excerpt = (scraped.get("excerpt") or "").lower()
    text = f"{title} {excerpt}"

    if content_type == "github" or "github" in text or "repo" in text:
        return "technology"
    if content_type == "arxiv" or "paper" in text or "research" in text:
        return "technology"
    if any(kw in text for kw in ["game", "gaming", "console", "esports"]):
        return "gaming"
    if any(kw in text for kw in ["gpu", "cpu", "hardware", "rtx", "ryzen"]):
        return "hardware"
    if any(kw in text for kw in ["ai", "machine learning", "llm", "neural"]):
        return "technology"
    return "technology"  # default

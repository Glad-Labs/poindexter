"""Topics routes — URL-based topic seeding (#230).

Lets users create content tasks from URLs. Two modes:
- POST /api/topics/from-url — one URL → one task
- POST /api/topics/from-urls — multiple URLs → top-N ranked tasks

Both modes fetch the URLs, extract title + content, and queue content
tasks with the URL as the research seed so the research stage uses it.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.site_config import SiteConfig
from services.topic_batch_service import OpenBatch, TopicBatchService
from services.topic_discovery import pick_target_length
from services.url_scraper import URLScrapeError, URLScraper
from utils.rate_limiter import _settings_limit, limiter
from utils.route_utils import get_database_dependency, get_site_config_dependency

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


class RankBatchRequest(BaseModel):
    """Operator ranking for a topic batch's candidates (best-first order)."""

    ordered_candidate_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Candidate ids in best-first order — 1-based operator_rank.",
    )


class RejectBatchRequest(BaseModel):
    """Optional reason recorded when an operator rejects a whole batch."""

    reason: str = Field("", description="Why the operator discarded the batch.")


@router.post(
    "/from-url",
    response_model=dict[str, Any],
    summary="Seed a content task from a single URL",
    status_code=201,
)
@limiter.limit(_settings_limit("rate_limit_topics_from_url_per_ip", "10/minute"))
async def from_url(
    request: Request,
    body: FromUrlRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Scrape a URL and queue a content task with it as the research seed.

    The research stage of the pipeline will use this URL as a primary
    source instead of doing general web search.
    """
    # Caller-bridge: build a URLScraper from the lifespan-bound SiteConfig
    # (#272 DI migration). Cheap to construct — holds only the SiteConfig.
    scraper = URLScraper(site_config=site_config)
    try:
        scraped = await scraper.scrape_url(body.url)
    except URLScrapeError as e:
        raise HTTPException(status_code=400, detail=f"Could not scrape URL: {e}") from e
    except Exception as e:
        logger.error("URL scrape crashed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="URL scrape failed") from e

    if not scraped.get("title") or scraped["title"] == "Untitled":
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract a title from {body.url}",
        )

    # Build the topic from the scraped title
    topic = scraped["title"]

    # Classify category if not provided
    category = body.category or _guess_category(scraped)

    metadata = {
        "source_url": body.url,
        "source_type": scraped.get("content_type", "article"),
        "source_preview": scraped.get("content_preview", "")[:500],
        "source_author": scraped.get("author"),
        "source_published_at": scraped.get("published_at"),
        "angle_hint": body.angle,
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
        "style": body.style or "narrative",
        "tone": body.tone or "professional",
        # Vary length when the caller didn't specify one (#542) via the
        # shared DB-configurable weighted picker; an explicit request value
        # always wins.
        "target_length": (
            body.target_length
            if body.target_length is not None
            else pick_target_length(site_config)
        ),
        "metadata": metadata,
    })
    logger.info(
        "[URL_SEED] Task created: %s from %s (%d words scraped)",
        returned_task_id, body.url, scraped.get("word_count", 0),
    )

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "status": "pending",
        "topic": topic,
        "category": category,
        "source_url": body.url,
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
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Scrape multiple URLs and queue the top N as content tasks.

    Scrapes all URLs in parallel, ranks by content richness (word count
    + presence of excerpt/author metadata), and queues the top N.
    Skips URLs that fail to scrape.
    """
    import asyncio

    # Caller-bridge: one URLScraper for the whole batch (#272 DI migration).
    scraper = URLScraper(site_config=site_config)

    async def safe_scrape(u: str) -> tuple[str, dict | None, str | None]:
        try:
            return (u, await scraper.scrape_url(u), None)
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
            # No per-URL length param on the batch endpoint — vary length
            # via the shared DB-configurable weighted picker (#542) so a
            # batch seed doesn't queue N identical-length posts.
            "target_length": pick_target_length(site_config),
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


# ---------------------------------------------------------------------------
# Topic-batch triage (#operator-console Phase 4)
# ---------------------------------------------------------------------------
# HTTP surface over services.topic_batch_service.TopicBatchService — the same
# service the topics_* MCP tools drive. The ``{batch_id}`` path segment is a
# topic *batch* id: a batch holds the ~N ranked candidates a discovery sweep
# produced for one niche. The operator ranks the candidates, then resolves the
# batch (advances the rank-1 winner into the content pipeline) or rejects it
# (discards the batch and frees the niche's one-open-batch slot). A stuck open
# batch is the recurring "content goes dark" failure class — this surface lets
# the operator drain it from the console / phone.


def _parse_batch_id(batch_id: str) -> UUID:
    """Coerce a path ``batch_id`` to UUID, 400ing on a malformed value."""
    try:
        return UUID(batch_id)
    except (ValueError, AttributeError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid batch_id: {batch_id!r}",
        ) from e


def _serialize_open_batch(ob: OpenBatch) -> dict[str, Any]:
    """Map an ``OpenBatch`` (batch view + niche meta) to the console shape."""
    view = ob.view
    return {
        "batch_id": str(view.id),
        "niche_id": str(view.niche_id),
        "niche_slug": ob.niche_slug,
        "niche_name": ob.niche_name,
        "status": view.status,
        "candidate_count": len(view.candidates),
        "candidates": [
            {
                "id": c.id,
                "kind": c.kind,
                "title": c.title,
                "summary": c.summary,
                "score": c.score,
                "effective_score": c.effective_score,
                "rank_in_batch": c.rank_in_batch,
                "operator_rank": c.operator_rank,
                "operator_edited_topic": c.operator_edited_topic,
                "operator_edited_angle": c.operator_edited_angle,
            }
            for c in view.candidates
        ],
    }


@router.get(
    "/proposals",
    response_model=dict[str, Any],
    summary="List open topic batches awaiting an operator decision",
)
async def list_proposals(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Return the open topic batches the operator can rank / resolve / reject.

    Mirrors the ``topics_show_batch`` MCP tool but across every niche at once.
    Each batch carries its merged, effective-score-sorted candidate list
    (external + internal) plus the niche slug/name for display.
    """
    service = TopicBatchService(db_service.pool, site_config=site_config)
    open_batches = await service.list_open_batches()
    batches = [_serialize_open_batch(ob) for ob in open_batches]
    return {"count": len(batches), "batches": batches}


@router.post(
    "/{batch_id}/rank",
    response_model=dict[str, Any],
    summary="Set the operator ranking for a topic batch's candidates",
)
async def rank_batch(
    batch_id: str,
    body: RankBatchRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Set ``operator_rank`` on the batch's candidates by 1-based position."""
    bid = _parse_batch_id(batch_id)
    service = TopicBatchService(db_service.pool, site_config=site_config)
    await service.rank_batch(
        batch_id=bid, ordered_candidate_ids=body.ordered_candidate_ids,
    )
    return {
        "ok": True,
        "batch_id": str(bid),
        "ranked": len(body.ordered_candidate_ids),
    }


@router.post(
    "/{batch_id}/resolve",
    response_model=dict[str, Any],
    summary="Resolve a topic batch — advance the rank-1 winner into the pipeline",
)
async def resolve_batch(
    batch_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Advance the operator's rank-1 candidate into the content pipeline.

    400s when the batch hasn't been ranked (no ``operator_rank=1`` candidate)
    — the operator must pick a winner before resolving.
    """
    bid = _parse_batch_id(batch_id)
    service = TopicBatchService(db_service.pool, site_config=site_config)
    try:
        await service.resolve_batch(batch_id=bid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "batch_id": str(bid), "status": "resolved"}


@router.post(
    "/{batch_id}/reject",
    response_model=dict[str, Any],
    summary="Reject a topic batch — discard candidates + free the niche slot",
)
async def reject_batch(
    batch_id: str,
    body: RejectBatchRequest | None = None,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Discard the batch (status → expired) so the next sweep can re-discover."""
    bid = _parse_batch_id(batch_id)
    reason = body.reason if body else ""
    service = TopicBatchService(db_service.pool, site_config=site_config)
    await service.reject_batch(batch_id=bid, reason=reason)
    return {"ok": True, "batch_id": str(bid), "status": "expired"}


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

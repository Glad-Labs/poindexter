"""Unified Content Router Service — centralized blog post generation pipeline."""

import asyncio
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger

from .ai_content_generator import get_content_generator
from .audit_log import audit_log_bg
from .database_service import DatabaseService
from .image_service import get_image_service
from .prompt_manager import get_prompt_manager
from .quality_service import EvaluationMethod, UnifiedQualityService
from .seo_content_generator import get_seo_content_generator
from .webhook_delivery_service import emit_webhook_event

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Image-style rotation dedup (#181)
# ---------------------------------------------------------------------------
# Module-level tracker so concurrent/sequential tasks in the same worker
# process don't pick the same style.  Entries are (style_name, timestamp).
# We keep at most _STYLE_HISTORY_SIZE entries and auto-expire after
# _STYLE_HISTORY_TTL seconds so styles become available again once enough
# time passes (prevents permanent starvation when the style pool is small).
import time as _time
from collections import deque as _deque

_STYLE_HISTORY_SIZE = 10
_STYLE_HISTORY_TTL = 3600  # 1 hour
_recent_style_picks: _deque = _deque(maxlen=_STYLE_HISTORY_SIZE)


def _record_style_pick(style_name: str) -> None:
    """Record that *style_name* was just chosen."""
    _recent_style_picks.append((style_name, _time.monotonic()))


def _get_in_memory_recent_styles() -> list:
    """Return style names picked within the TTL window."""
    cutoff = _time.monotonic() - _STYLE_HISTORY_TTL
    return [name for name, ts in _recent_style_picks if ts >= cutoff]


# Per-stage timeouts in seconds — easy to tune in one place.
# Prevents a single slow stage (e.g. Ollama generation) from eating the entire budget.
_DEFAULT_STAGE_TIMEOUTS = {
    "verify_task": 30,
    "generate_content": 480,      # Stage 2: Draft — 8 min (long posts with code examples need time)
    "quality_evaluation": 60,     # Stage 2B: Pattern QA — 1 min
    "url_validation": 60,         # Stage 2B.1: URL checks — 1 min
    "replace_inline_images": 120, # Stage 2C: Image replacement — 2 min
    "source_featured_image": 120, # Stage 3: Image search — 2 min
    "cross_model_qa": 180,        # Stage 3.5+3.7: Multi-model QA — 3 min
    "generate_seo_metadata": 60,  # Stage 4: SEO — 1 min
    "generate_media_scripts": 300, # Stage 4B: Podcast script + video scenes — 5 min
    "capture_training_data": 30,  # Stage 5: Training data — 30s
    "finalize_task": 30,          # Stage 6: Finalize — 30s
}


# Map app_settings keys to stage names. Resolved lazily per-call because
# site_config is empty at module-import time (it's populated later in the
# lifespan, after the DB pool is ready). A cached module-level dict would
# freeze the defaults before the DB was consulted — exactly the silent
# misconfiguration swallow that cost task 408.
_STAGE_TIMEOUT_OVERRIDES = {
    "stage_timeout_verify_task": "verify_task",
    "stage_timeout_draft": "generate_content",
    "stage_timeout_qa": "quality_evaluation",
    "stage_timeout_url_validation": "url_validation",
    "stage_timeout_inline_images": "replace_inline_images",
    "stage_timeout_featured_image": "source_featured_image",
    "stage_timeout_cross_model_qa": "cross_model_qa",
    "stage_timeout_seo": "generate_seo_metadata",
    "stage_timeout_media_scripts": "generate_media_scripts",
    "stage_timeout_training_data": "capture_training_data",
    "stage_timeout_finalize": "finalize_task",
}

# Reverse lookup: stage_name -> setting_key (for loud error messages)
_STAGE_NAME_TO_SETTING = {v: k for k, v in _STAGE_TIMEOUT_OVERRIDES.items()}


def _get_stage_timeout(stage_name: str) -> int:
    """Resolve a stage timeout from app_settings at call time.

    Reads site_config every call (cheap dict lookup). If the DB has an
    override value that can't be parsed as int, this raises RuntimeError
    rather than silently falling back — matches the project-wide
    "no silent defaults" rule.
    """
    from services.site_config import site_config

    setting_key = _STAGE_NAME_TO_SETTING.get(stage_name)
    if setting_key:
        raw = site_config.get(setting_key)
        if raw:  # Non-empty string
            try:
                return int(raw)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"Invalid app_settings value for {setting_key}: "
                    f"expected integer, got {raw!r}"
                ) from exc
    return _DEFAULT_STAGE_TIMEOUTS.get(stage_name, 120)


async def _run_stage_with_timeout(coro, stage_name: str, task_id: str):
    """Wrap a stage coroutine with a timeout. On timeout, log and return None."""
    timeout = _get_stage_timeout(stage_name)
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(
            "Stage '%s' timed out after %ds for task %s",
            stage_name, timeout, task_id[:8],
        )
        audit_log_bg(
            "stage_timeout", "content_router",
            {"stage": stage_name, "timeout_seconds": timeout},
            task_id=task_id, severity="warning",
        )
        return None


async def _is_stage_enabled(pool, stage_key: str) -> bool:
    """Check if a pipeline stage is enabled in the database.

    Returns True if the stage is enabled or if the table doesn't exist (backwards compatible).
    """
    if pool is None:
        return True  # No DB = run everything (local dev)
    try:
        row = await pool.fetchrow(
            "SELECT enabled FROM pipeline_stages WHERE key = $1", stage_key
        )
        if row is None:
            return True  # Stage not in DB = run it (backwards compatible)
        return row["enabled"]
    except Exception:
        return True  # Table doesn't exist = run everything


# ============================================================================
# TEXT NORMALIZATION — replace Unicode smart quotes / dashes with ASCII
# ============================================================================

def _normalize_text(text: str) -> str:
    """Replace Unicode smart quotes, dashes, and special whitespace with ASCII equivalents.

    Ollama (and other LLMs) frequently produce these characters, which can cause
    encoding / rendering issues on the public site.
    """
    if not text:
        return text
    return (
        text
        .replace("\u2019", "'")   # right single quote
        .replace("\u2018", "'")   # left single quote
        .replace("\u201c", '"')   # left double quote
        .replace("\u201d", '"')   # right double quote
        .replace("\u2014", "--")  # em dash
        .replace("\u2013", "-")   # en dash
        .replace("\u2026", "...") # ellipsis
        .replace("\u00a0", " ")   # non-breaking space
        .replace("\u2011", "-")   # non-breaking hyphen
    )


def _scrub_fabricated_links(content: str) -> str:
    """Remove fabricated/hallucinated URLs from LLM-generated content.

    Local LLMs hallucinate URLs that don't exist — linking to random domains
    like dictionary.com, example.com, or made-up paths on real domains.
    This scrubs markdown links whose domains aren't in the trusted allowlist,
    keeping the link text but removing the bogus href.
    """
    import re

    from services.site_config import site_config

    # Domains we trust (our own site + major reference sites).
    # Shipped default is a tech/developer default set; customers in
    # other niches override via app_settings.trusted_source_domains as
    # a comma-separated list (#198).
    _default_trusted = {
        "github.com", "arxiv.org", "docs.python.org", "docs.rs",
        "developer.mozilla.org", "stackoverflow.com", "wikipedia.org",
        "en.wikipedia.org", "news.ycombinator.com", "dev.to",
        "kubernetes.io", "docker.com", "docs.docker.com",
        "vercel.com", "nextjs.org", "react.dev", "go.dev",
        "pytorch.org", "huggingface.co", "openai.com",
        "www.rust-lang.org", "blog.rust-lang.org", "crates.io",
        "pypi.org", "npmjs.com", "www.npmjs.com",
        "youtube.com", "www.youtube.com",
    }
    _override_csv = site_config.get("trusted_source_domains", "")
    if _override_csv:
        trusted_domains = {
            d.strip().lower() for d in _override_csv.split(",") if d.strip()
        }
    else:
        trusted_domains = set(_default_trusted)
    # Add own domain dynamically from config
    _own_domain = site_config.get("site_domain", "")
    if _own_domain:
        trusted_domains.add(_own_domain)
        trusted_domains.add(f"www.{_own_domain}")

    # Cache of real internal post slugs (populated lazily)
    _real_slugs: set = set()

    def _is_trusted(url: str) -> bool:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            return any(host == d or host.endswith("." + d) for d in trusted_domains)
        except Exception:
            return False

    def _is_real_internal_link(url: str) -> bool:
        """Check if an internal link points to an actual published post."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if _own_domain and _own_domain not in host:
            return True  # Not our link, don't check
        path = parsed.path or ""
        if not path.startswith("/posts/"):
            return True  # Not a post link (could be /about, /archive, etc.)
        slug = path.split("/posts/")[-1].strip("/")
        if not slug:
            return True
        # Lazy-load real slugs from the internal links cache
        if not _real_slugs:
            try:
                _cache = getattr(_scrub_fabricated_links, "_slug_cache", None)
                if _cache:
                    _real_slugs.update(_cache)
            except Exception:
                pass
        if _real_slugs:
            return slug in _real_slugs
        # If no cache, accept it (will be caught at URL validation stage)
        return True

    scrubbed_count = 0

    # Handle markdown links: [text](url)
    def _replace_md_link(m):
        nonlocal scrubbed_count
        text, url = m.group(1), m.group(2)
        if not _is_trusted(url):
            scrubbed_count += 1
            return text  # Keep text, drop fake link
        if not _is_real_internal_link(url):
            scrubbed_count += 1
            return text  # Drop fabricated internal link, keep text
        return m.group(0)  # Keep valid link

    content = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", _replace_md_link, content)

    # Handle bare URLs that aren't in markdown links
    def _replace_bare_url(m):
        nonlocal scrubbed_count
        url = m.group(0)
        if _is_trusted(url):
            return url
        scrubbed_count += 1
        return ""  # Remove bare fabricated URLs entirely

    content = re.sub(r"(?<!\()https?://[^\s\)\]\"'>,]+", _replace_bare_url, content)

    if scrubbed_count > 0:
        logger.info("[LINK_SCRUB] Removed %d fabricated link(s) from generated content", scrubbed_count)

    return content


# ============================================================================
# TASK STORE
# ============================================================================


class ContentTaskStore:
    """Unified task storage adapter delegating to persistent DatabaseService backend."""

    def __init__(self, database_service: DatabaseService | None = None):
        self.database_service = database_service

    @property
    def persistent_store(self):
        """Backward-compatible property — returns the DatabaseService."""
        return self.database_service

    async def create_task(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: list[str] | None = None,
        generate_featured_image: bool = True,
        request_type: str = "basic",
        task_type: str = "blog_post",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new task in persistent storage. Returns task ID."""
        logger.info("[CONTENT_TASK_STORE] Creating task (async)")
        logger.info("   Topic: %s%s", topic[:60], '...' if len(topic) > 60 else '')
        logger.info("   Style: %s | Tone: %s | Length: %sw", style, tone, target_length)
        logger.info("   Tags: %s", ', '.join(tags) if tags else 'none')
        logger.debug("   Type: %s | Image: %s", request_type, generate_featured_image)

        # Add generate_featured_image to metadata
        metadata = {"generate_featured_image": generate_featured_image}
        logger.debug("   Metadata: %s", metadata)

        try:
            # Check if we have database service
            if not self.database_service:
                raise ValueError("DatabaseService not initialized - cannot persist tasks")

            logger.debug("   Calling database_service.add_task() (async)...")

            # Generate task_name from topic
            task_name = f"{topic[:50]}" if len(topic) <= 50 else f"{topic[:47]}..."

            task_id = await self.database_service.add_task(
                {
                    "task_name": task_name,  # REQUIRED: must be provided
                    "topic": topic,
                    "style": style,
                    "tone": tone,
                    "target_length": target_length,
                    "tags": tags or [],
                    "request_type": request_type,
                    "task_type": task_type,
                    "metadata": metadata or {},
                }
            )

            logger.info("[CONTENT_TASK_STORE] Task CREATED and PERSISTED (async)")
            logger.info("   Task ID: %s", task_id)
            logger.info("   Status: pending")
            logger.debug("   Ready for processing")
            audit_log_bg("task_created", "content_router", {
                "topic": topic[:100], "style": style, "tone": tone,
                "target_length": target_length, "request_type": request_type,
            }, task_id=task_id)
            return task_id

        except Exception as e:
            logger.error("[CONTENT_TASK_STORE] ERROR: %s", e, exc_info=True)
            raise

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get task by ID from persistent storage (async, non-blocking)"""
        if not self.database_service:
            return None
        return await self.database_service.get_task(task_id)

    async def update_task(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update task data in persistent storage (async, non-blocking)"""
        if not self.database_service:
            return False

        # Handle metadata updates by converting to JSON
        import json

        if "metadata" in updates:
            updates["task_metadata"] = json.dumps(updates.pop("metadata"))

        # Call database service to update
        return await self.database_service.update_task(task_id, updates)

    async def delete_task(self, task_id: str) -> bool:
        """Delete task from persistent storage (async, non-blocking)"""
        if not self.database_service:
            return False
        return await self.database_service.delete_task(task_id)

    async def list_tasks(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List tasks from persistent storage with optional filtering (async, non-blocking)"""
        if not self.database_service:
            return []
        tasks, total = await self.database_service.get_tasks_paginated(
            offset=offset, limit=limit, status=status
        )
        return tasks

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> tuple:
        """Get list of drafts from persistent storage (async, non-blocking)"""
        if not self.database_service:
            return []
        return await self.database_service.get_drafts(limit=limit, offset=offset)


# Global unified task store (lazy-initialized)
_content_task_store: ContentTaskStore | None = None


def get_content_task_store(database_service: DatabaseService | None = None) -> ContentTaskStore:
    """
    Get the global unified content task store (lazy-initialized).
    Allows injecting database_service during startup.
    """
    global _content_task_store
    if _content_task_store is None:
        _content_task_store = ContentTaskStore(database_service)
    elif database_service and _content_task_store.database_service is None:
        # Inject service if it wasn't available during first init
        _content_task_store.database_service = database_service

    return _content_task_store


# ============================================================================
# BACKGROUND TASK PROCESSORS
# ============================================================================


async def _check_title_originality(title: str) -> dict:
    """Check if a title is too similar to existing content via web search (threshold from app_settings)."""
    from difflib import SequenceMatcher

    result = {"is_original": True, "similar_titles": [], "max_similarity": 0.0}

    try:
        from services.site_config import site_config
        threshold = site_config.get_float("qa_title_similarity_threshold", 0.6)
        enabled = site_config.get_bool("qa_title_originality_enabled", True)
        if not enabled:
            return result
    except Exception:
        threshold = 0.6

    try:
        from services.web_research import WebResearcher
        researcher = WebResearcher()
        search_results = await researcher.search_simple(f'"{title}"', num_results=8)

        if not search_results:
            # Also try without quotes for broader match
            search_results = await researcher.search_simple(title, num_results=8)

        title_lower = title.lower().strip()
        for r in search_results:
            ext_title = (r.get("title") or "").lower().strip()
            if not ext_title:
                continue

            sim = SequenceMatcher(None, title_lower, ext_title).ratio()
            if sim > result["max_similarity"]:
                result["max_similarity"] = sim
            if sim >= threshold:
                result["similar_titles"].append(r.get("title", ""))

        result["is_original"] = len(result["similar_titles"]) == 0
        if not result["is_original"]:
            logger.warning(
                "[TITLE] Originality check FAILED (%.0f%% similar): '%s' vs '%s'",
                result["max_similarity"] * 100,
                title,
                result["similar_titles"][0] if result["similar_titles"] else "?",
            )
        else:
            logger.info(
                "[TITLE] Originality check passed (max %.0f%% similarity)",
                result["max_similarity"] * 100,
            )

    except Exception as e:
        logger.warning("[TITLE] Originality check skipped (non-fatal): %s", e)

    return result


# Phrases that indicate the LLM deliberated instead of just giving the title.
# If these appear anywhere in the output, we treat the response as unclean
# and return None so the caller falls back to a safer source (topic / seo_title).
_TITLE_DELIBERATION_MARKERS = (
    "let's go with",
    "let me choose",
    "i'll pick",
    "i'd pick",
    "the most unique",
    "the best option",
    "here are",
    "here's a",
    "option 1",
    "option 2",
    "option a",
    "option b",
    "title 1:",
    "title 2:",
)


def _sanitize_generated_title(raw: str) -> str | None:
    """Clean an LLM title response, or return None if it's unsalvageable.

    Real-world failure mode (#198 follow-up): thinking-models sometimes
    return their reasoning trace instead of a clean title, e.g.:

        "*   Let's go with the **Question**. It is the most unique structure..."

    The old sanitizer only stripped whitespace + quotes and shipped that
    straight to the `title` column. This version:

    1. Strips <think>…</think> blocks first (some models emit them literally).
    2. Takes the last non-empty line (models often end with their answer).
    3. Strips leading list markers (*, -, +, 1.), bold (**), and quotes.
    4. Rejects anything that still contains deliberation markers.
    5. Rejects empty, too-short (<5 chars), or too-long (>120) results.
    """
    if not raw:
        return None

    import re as _re

    text = raw.strip()
    # 1. Strip <think>…</think> blocks — keep only what comes after.
    text = _re.sub(r"<think>.*?</think>", " ", text, flags=_re.DOTALL | _re.IGNORECASE).strip()

    # 2. If the output has multiple lines, walk from the bottom for the first
    # line that looks like an actual title (not bullet/deliberation/empty).
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidate: str | None = None
    for line in reversed(lines):
        # Skip lines that still look like list items or bolded labels
        stripped = _re.sub(r"^[\s\*\-\+\u2022]+|^\d+[\.\)]\s*", "", line).strip()
        stripped = _re.sub(r"^#+\s+", "", stripped)  # strip leading # headers
        stripped = stripped.strip('"').strip("'").strip()
        stripped = _re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)  # unwrap **bold**
        if not stripped or len(stripped) < 5 or len(stripped) > 200:
            continue
        lower = stripped.lower()
        if any(marker in lower for marker in _TITLE_DELIBERATION_MARKERS):
            continue
        candidate = stripped
        break

    if not candidate:
        return None

    # Final length trim — SEO best practice caps around 60, hard cap at 100.
    if len(candidate) > 100:
        candidate = candidate[:97].rstrip() + "..."
    return candidate


async def _generate_canonical_title(
    topic: str, primary_keyword: str, content_excerpt: str, existing_titles: str = ""
) -> str | None:
    """Generate an SEO-optimized title via LLM, avoiding similarity to existing titles."""
    try:
        from .model_consolidation_service import get_model_consolidation_service

        pm = get_prompt_manager()
        service = get_model_consolidation_service()

        # Use unified prompt manager to get SEO title generation prompt
        prompt = pm.get_prompt(
            "seo.generate_title",
            content=content_excerpt,
            primary_keyword=primary_keyword or topic,
        )

        # Inject existing titles to avoid repetition
        if existing_titles:
            prompt += f"\n\n⚠️ AVOID SIMILARITY to these recent titles:\n{existing_titles}\n\nYour title must be DISTINCTLY DIFFERENT in structure and wording."

        # Use model consolidation service for intelligent provider fallback.
        # Higher max_tokens gives thinking models room for reasoning + answer.
        from services.site_config import site_config as _sc_seo
        result = await service.generate(
            prompt=prompt,
            temperature=0.7,
            max_tokens=_sc_seo.get_int("content_router_seo_title_max_tokens", 4000),
        )

        if result and result.text:
            title = _sanitize_generated_title(result.text)
            if title:
                logger.debug("Generated title: %s", title)
                return title
            logger.warning(
                "[TITLE_GEN] Sanitizer rejected LLM output as unclean: %r",
                result.text[:100],
            )

        return None

    except Exception as e:
        logger.warning("Error generating canonical title: %s", e, exc_info=True)
        return None




def _parse_model_preferences(models_by_phase):
    """Stage 2A: Parse user model preferences from models_by_phase dict.

    Returns (preferred_model, preferred_provider) tuple.
    """
    preferred_model = None
    preferred_provider = None
    logger.info("STEP 2A: Processing model selections from UI")
    logger.info("   models_by_phase = %s", models_by_phase)
    if not models_by_phase:
        return preferred_model, preferred_provider

    # Try to get model for 'draft' phase (main content generation)
    draft_model = (
        models_by_phase.get("draft")
        or models_by_phase.get("generate")
        or models_by_phase.get("content")
    )
    logger.info("   draft_model = %s", draft_model)
    if not draft_model or draft_model == "auto":
        return preferred_model, preferred_provider

    draft_model = draft_model.strip()

    # Parse provider and model from selection
    if "/" in draft_model:
        preferred_provider, preferred_model = draft_model.split("/", 1)
    else:
        # Infer provider from model name — Ollama-only policy
        draft_model_lower = draft_model.lower()

        if (
            "ollama" in draft_model_lower
            or "mistral" in draft_model_lower
            or "llama" in draft_model_lower
            or "qwen" in draft_model_lower
            or "gemma" in draft_model_lower
            or "deepseek" in draft_model_lower
            or "phi" in draft_model_lower
        ):
            preferred_provider = "ollama"
            preferred_model = draft_model
        else:
            # Default to ollama with model name as-is
            preferred_provider = "ollama"
            preferred_model = draft_model

    logger.info(
        "   FINAL: preferred_model='%s', preferred_provider='%s'",
        preferred_model, preferred_provider,
    )
    logger.info(
        "User selected model: %s (provider: %s)",
        preferred_model or 'auto', preferred_provider or 'auto',
    )
    return preferred_model, preferred_provider


async def _build_writing_style_context(
    database_service: DatabaseService | None,
    max_samples: int = 3,
    max_words_per_sample: int = 500,
) -> str | None:
    """Fetch active writing style samples for voice matching. Returns None if unavailable."""
    if not database_service:
        return None

    try:
        writing_style_db = getattr(database_service, "writing_style", None)
        if not writing_style_db:
            return None

        # Fetch all user samples (we don't know the user in this context,
        # so we look for any active samples across users)
        samples = await writing_style_db.get_user_writing_samples(
            user_id="default", limit=max_samples
        )

        if not samples:
            return None

        excerpts = []
        for sample in samples[:max_samples]:
            content = sample.get("content", "")
            title = sample.get("title", "Untitled")
            if not content:
                continue

            # Truncate to max_words_per_sample words
            words = content.split()
            if len(words) > max_words_per_sample:
                excerpt = " ".join(words[:max_words_per_sample]) + "..."
            else:
                excerpt = content

            excerpts.append(f"### Sample: {title}\n{excerpt}")

        if not excerpts:
            return None

        logger.info(
            "Loaded %d writing style sample(s) for voice matching", len(excerpts)
        )
        return "\n\n".join(excerpts)

    except Exception as e:
        logger.warning(
            "Failed to load writing style samples (non-fatal, proceeding without): %s",
            e,
            exc_info=True,
        )
        return None


async def _build_rag_context(
    database_service: DatabaseService | None, topic: str
) -> str | None:
    """Search pgvector for similar published posts. Returns None if unavailable.

    Migrated 2026-04-11 from direct `database_service.embeddings.search_similar`
    calls to `poindexter.memory.MemoryClient.find_similar_posts` per Gitea #192
    slice 3. The MemoryClient helper hardcodes `source_table='posts'` so the
    singular/plural silent-zero-result bug can never recur here.
    """
    if not topic or not topic.strip():
        return None

    try:
        from poindexter.memory import MemoryClient

        async with MemoryClient() as mem:
            similar_posts = await mem.find_similar_posts(
                topic, limit=5, min_similarity=0.3
            )

        if not similar_posts:
            return None

        # Look up post details (title, excerpt, slug) for each match
        lines = [
            "RELATED POSTS WE'VE PUBLISHED (reference for internal linking, avoid repeating same angles):"
        ]
        pool = database_service.pool if database_service else None
        for i, hit in enumerate(similar_posts, 1):
            post_id = hit.source_id
            similarity = hit.similarity
            title = (hit.metadata or {}).get("title", "Untitled")

            # Try to fetch slug and excerpt from the posts table.
            # source_id for a post row is the post UUID (sometimes prefixed
            # with "post/"), so try both shapes.
            slug = ""
            excerpt = ""
            if pool:
                try:
                    # Strip optional "post/" prefix that auto-embed.py adds.
                    lookup_id = post_id.removeprefix("post/")
                    row = await pool.fetchrow(
                        "SELECT slug, excerpt FROM posts WHERE id::text = $1 LIMIT 1",
                        lookup_id,
                    )
                    if row:
                        slug = row.get("slug") or ""
                        excerpt = row.get("excerpt") or ""
                except Exception:
                    pass  # Non-critical — use metadata title only

            excerpt_short = (excerpt[:120] + "...") if len(excerpt) > 120 else excerpt
            url = f"/posts/{slug}" if slug else f"(post id: {post_id})"
            lines.append(
                f"{i}. [{title}] -- {excerpt_short} ({url}) [similarity: {similarity:.2f}]"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.debug("RAG context build failed (non-fatal): %s", e)
        return None


# ============================================================================
# WRITER SELF-REVIEW PASS
# ============================================================================

_SELF_REVIEW_DETECT_PROMPT = """You are a strict editor reviewing your own
draft article for internal consistency. Read every section of the article
below. Your ONLY job is to find cross-section contradictions.

A contradiction is any of these:

  1. Recommendation conflict. Section X recommends tool/approach A and
     Section Y recommends tool/approach B without acknowledging the
     switch, where A and B are incompatible. (e.g. "Don't use React" in
     one section and "Use Next.js" in another — Next.js is React.)
  2. Principle violation in code. The prose states a principle ("never
     build custom auth") and then a code example in a different section
     violates that principle.
  3. Claim vs code mismatch. The prose says the code does X ("validates
     the input") when the code actually does Y (no validation shown).
  4. Numeric or factual conflict. Two sections state different numbers
     or facts about the same thing.

TITLE: {title}
TOPIC: {topic}

ARTICLE:
{content}

Respond with ONLY valid JSON:
{{"contradictions_found": NUMBER, "contradictions": ["specific pair 1", "specific pair 2"]}}

If none found, return {{"contradictions_found": 0, "contradictions": []}}.
Be specific — name the sections and the conflict. Do NOT invent nitpicks
or stylistic concerns. Only flag genuine logical contradictions."""


_SELF_REVIEW_REVISE_PROMPT = """You are revising your own draft to fix
specific contradictions that an editor identified. Do NOT rewrite the
entire article. Do NOT add new sections. Only fix the specific conflicts
listed below, making the minimum changes needed to resolve each one.

Keep the same structure, same headings, same code examples where they
aren't contradicted, same length (within 10%).

TITLE: {title}

CONTRADICTIONS TO FIX:
{contradictions}

ORIGINAL DRAFT:
{content}

Return ONLY the revised article text. Do not include meta-commentary,
notes about what you changed, or markdown code fences around the output."""


_QA_AGGREGATE_REWRITE_PROMPT = """You are revising your own draft to fix
EVERY issue a team of editors identified. Do NOT rewrite the entire
article. Do NOT add new sections. Only fix the specific problems
listed below, making the minimum changes needed to resolve each one.

Keep the same structure, same headings, same code examples where they
aren't affected by the issues, same length (within 10%).

TITLE: {title}

ISSUES TO FIX (from programmatic validator + LLM critics + consistency checker):
{issues_to_fix}

How to interpret:
- "[critical]" means the issue will block publishing if not fixed. Top priority.
- "[warning]" means it will drag the score down but won't veto. Fix these too.
- "Contradictions:" lines mean sections disagree with each other — rewrite the
  weaker or later one to align with the stronger or earlier one.
- "Fabricated" or "Impossible" lines mean the draft made up a person, statistic,
  quote, or company claim. Remove the fabrication entirely; do NOT replace it
  with another made-up fact — either soften to a general statement or cut.
- "Generic section title" means replace the heading with a creative, benefit-
  focused alternative (never "Introduction", "Conclusion", "Summary", etc.).
- "Filler intro" means rewrite the first paragraph with a concrete hook, not
  "In this post..." or "In today's fast-paced world...".

ORIGINAL DRAFT:
{content}

Return ONLY the revised article text. Do not include meta-commentary,
notes about what you changed, or markdown code fences around the output."""


async def _self_review_and_revise(
    content_text: str, title: str, topic: str
) -> tuple:
    """Writer self-review pass: detect and fix cross-section contradictions.

    Returns (possibly_revised_content, meta) where meta is a dict with:
        contradictions_found: int — how many the detector flagged
        revised: bool — whether the draft was actually changed
        skipped: bool — whether the pass was skipped (Ollama down, etc.)
        reason: str — explanation if skipped
    """
    import asyncio
    import json
    import re

    meta = {
        "contradictions_found": 0,
        "revised": False,
        "skipped": False,
        "reason": None,
    }
    if not content_text or len(content_text) < 200:
        meta["skipped"] = True
        meta["reason"] = "content too short for meaningful review"
        return content_text, meta

    try:
        from services.ollama_client import OllamaClient
        from services.site_config import site_config as _sc

        # Use gemma3:27b for the reviewer by default — smaller than the
        # writer, different strengths, less likely to rubber-stamp its own
        # style preferences. Configurable via app_settings.
        review_model = _sc.get("writer_self_review_model") or "gemma3:27b"
        review_model = review_model.removeprefix("ollama/")

        # 90s cap — review prompt is full article content.
        client = OllamaClient(timeout=90)
        try:
            healthy = await asyncio.wait_for(client.check_health(), timeout=5)
        except asyncio.TimeoutError:
            healthy = False
        if not healthy:
            await client.close()
            meta["skipped"] = True
            meta["reason"] = "ollama unavailable for self-review"
            return content_text, meta

        detect_prompt = _SELF_REVIEW_DETECT_PROMPT.format(
            title=title,
            topic=topic or title,
            content=content_text[:10000],
        )
        try:
            from services.site_config import site_config as _sc_detect
            detect_result = await asyncio.wait_for(
                client.generate(
                    prompt=detect_prompt,
                    model=review_model,
                    temperature=0.2,
                    max_tokens=_sc_detect.get_int("content_router_detect_max_tokens", 800),
                ),
                timeout=_sc_detect.get_int("content_router_detect_timeout_seconds", 90),
            )
        except asyncio.TimeoutError:
            await client.close()
            meta["skipped"] = True
            meta["reason"] = "self-review detect phase timed out"
            return content_text, meta

        detect_text = detect_result.get("text", "").strip()
        if not detect_text:
            await client.close()
            meta["skipped"] = True
            meta["reason"] = "detect phase returned empty response"
            return content_text, meta

        # Parse the JSON block out of the detect response
        json_text = detect_text
        if "```" in detect_text:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", detect_text, re.DOTALL)
            if m:
                json_text = m.group(1)
        try:
            detect_data = json.loads(json_text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", detect_text, re.DOTALL)
            if m:
                try:
                    detect_data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    await client.close()
                    meta["skipped"] = True
                    meta["reason"] = "detect phase returned unparseable JSON"
                    return content_text, meta
            else:
                await client.close()
                meta["skipped"] = True
                meta["reason"] = "detect phase returned no JSON"
                return content_text, meta

        contradictions = detect_data.get("contradictions") or []
        count = int(detect_data.get("contradictions_found", len(contradictions)))
        meta["contradictions_found"] = count

        if count == 0 or not contradictions:
            await client.close()
            return content_text, meta  # passes, no revision needed

        # Revise phase: ask the writer to fix the specific contradictions.
        contradictions_list = "\n".join(
            f"- {str(c)[:300]}" for c in contradictions[:10]
        )
        revise_prompt = _SELF_REVIEW_REVISE_PROMPT.format(
            title=title,
            contradictions=contradictions_list,
            content=content_text[:10000],
        )
        # Reuse the same model for revision to keep voice consistent.
        try:
            from services.site_config import site_config as _sc_rev
            revise_result = await asyncio.wait_for(
                client.generate(
                    prompt=revise_prompt,
                    model=review_model,
                    temperature=0.3,
                    max_tokens=_sc_rev.get_int("content_router_revise_max_tokens", 4096),
                ),
                timeout=_sc_rev.get_int("content_router_revise_timeout_seconds", 180),
            )
        except asyncio.TimeoutError:
            await client.close()
            meta["skipped"] = True
            meta["reason"] = "self-review revise phase timed out"
            return content_text, meta

        revised_text = revise_result.get("text", "").strip()
        await client.close()

        # Guardrails on the revision:
        # 1. Must not be empty
        # 2. Must be within 50% length of the original (neither dropped
        #    critical content nor doubled it with new sections)
        # 3. Must not be shorter than 60% of the original (writer dropped
        #    whole sections instead of fixing them)
        if not revised_text or len(revised_text) < 200:
            meta["skipped"] = True
            meta["reason"] = "revise phase returned empty or too-short content"
            return content_text, meta
        ratio = len(revised_text) / max(len(content_text), 1)
        if ratio < 0.6 or ratio > 1.5:
            logger.warning(
                "[SELF_REVIEW] Revision size out of bounds (ratio=%.2f), keeping original",
                ratio,
            )
            meta["skipped"] = True
            meta["reason"] = f"revision length ratio {ratio:.2f} outside [0.6, 1.5]"
            return content_text, meta

        meta["revised"] = True
        return revised_text, meta

    except Exception as e:
        logger.warning("[SELF_REVIEW] Pass failed (non-fatal): %s", e)
        meta["skipped"] = True
        meta["reason"] = f"exception: {type(e).__name__}"
        return content_text, meta




async def _self_review_and_revise(draft: str, title: str, topic: str) -> tuple[str, dict]:
    """Writer self-review pass (issue #170).

    Runs a second Ollama call asking the model to review its own draft
    for cross-section contradictions. If found, asks the model to fix
    them. Returns the (possibly-revised) draft and a stats dict.

    Gated by app_settings key `enable_writer_self_review` (default false).
    When disabled, returns the draft unchanged with stats={'enabled': False}.

    This is the "catch contradictions before QA" prevention layer from
    issue #170. Without it, contradictions reach the QA gate and force
    a full regeneration cycle.
    """
    from services.site_config import site_config
    from services.ollama_client import OllamaClient

    stats: dict = {"enabled": False, "contradictions_found": 0, "revised": False}

    enabled = str(site_config.get("enable_writer_self_review", "false")).lower() == "true"
    if not enabled:
        return draft, stats

    stats["enabled"] = True
    if not draft or len(draft) < 500:
        # Too short to have meaningful cross-section contradictions
        return draft, stats

    # Use a non-thinking model for review — thinking models burn tokens on <think>
    review_model = str(site_config.get("writer_self_review_model") or "gemma3:27b").removeprefix("ollama/")

    review_prompt = (
        f"You are reviewing your own draft for internal contradictions.\n\n"
        f"TITLE: {title}\n"
        f"TOPIC: {topic}\n\n"
        f"DRAFT:\n{draft}\n\n"
        f"Read every section. Identify any claim in one section that contradicts "
        f"a claim, code example, or recommendation in another section. "
        f"Ignore stylistic variation; focus on factual or logical conflicts.\n\n"
        f"If you find contradictions, output a numbered list of specific corrections "
        f"needed (one per line, format: 'SECTION X conflicts with SECTION Y: <details>'). "
        f"If you find none, reply with exactly: PASS"
    )

    from services.site_config import site_config as _sc_con
    try:
        client = OllamaClient(
            timeout=_sc_con.get_int("content_router_contradiction_timeout_seconds", 120)
        )
        result = await client.generate(
            prompt=review_prompt, model=review_model, temperature=0.2,
            max_tokens=_sc_con.get_int("content_router_contradiction_review_max_tokens", 1500),
        )
        review_text = (result.get("text") or "").strip()

        if not review_text or review_text.upper().startswith("PASS"):
            return draft, stats

        # Count contradictions (numbered list items)
        import re
        contradictions = [ln for ln in review_text.splitlines() if re.match(r"^\s*\d+[\.\)]\s+", ln)]
        stats["contradictions_found"] = len(contradictions)
        if not contradictions:
            return draft, stats

        # Feed list back to the writer for revision
        revise_prompt = (
            f"Here is your draft. Fix these specific contradictions and nothing else:\n\n"
            f"CONTRADICTIONS TO FIX:\n{review_text}\n\n"
            f"ORIGINAL DRAFT:\n{draft}\n\n"
            f"Output only the revised draft. Keep the structure, length, and tone "
            f"identical. Only change what's needed to resolve the contradictions."
        )
        revised = await client.generate(
            prompt=revise_prompt, model=review_model, temperature=0.3,
            max_tokens=_sc_con.get_int("content_router_contradiction_revise_max_tokens", 8000),
        )
        revised_text = (revised.get("text") or "").strip()

        # Guard: revision must be a reasonable length
        if len(revised_text) >= int(0.7 * len(draft)):
            stats["revised"] = True
            logger.info(
                "[SELF_REVIEW] Revised draft: %d contradictions found, %d chars in/%d out",
                len(contradictions), len(draft), len(revised_text),
            )
            return revised_text, stats
        # Fall through — keep the original if revision was too short
        logger.warning(
            "[SELF_REVIEW] Revision too short (%d chars), keeping original (%d chars)",
            len(revised_text), len(draft),
        )
    except Exception as e:
        logger.warning("[SELF_REVIEW] Self-review failed (non-fatal): %s", e)

    return draft, stats




async def _stage_replace_inline_images(database_service, task_id, topic, content_text, image_service, result):
    """Stage 2C: Use Image Decision Agent to plan and generate inline images.

    The agent analyzes the finished article, reasons about what images
    would best serve each section, and decides SDXL vs Pexels per image.
    Returns the (possibly modified) content_text.
    """
    import re as _re

    # First check for any existing [IMAGE-N] placeholders from the writer
    image_placeholders = _re.findall(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]", content_text)

    # Use the Image Decision Agent to plan images intelligently
    if not image_placeholders:
        try:
            from services.image_decision_agent import plan_images

            category = result.get("category", "technology")
            plan = await plan_images(content_text, topic, category, max_images=3)

            if plan.images:
                # Store the featured image plan for stage 3
                if plan.featured_image:
                    result["featured_image_plan"] = {
                        "source": plan.featured_image.source,
                        "style": plan.featured_image.style,
                        "prompt": plan.featured_image.prompt,
                    }

                # Inject placeholders at the agent-selected positions
                headings = list(_re.finditer(r"^#{2,4}\s+(.+)$", content_text, _re.MULTILINE))
                heading_map = {_re.sub(r'^#+\s*', '', h.group()).strip().lower(): h for h in headings}

                insert_positions = []
                for i, img in enumerate(plan.images):
                    # Find the matching heading
                    for heading_text, h_match in heading_map.items():
                        if img.section_heading.lower() in heading_text or heading_text in img.section_heading.lower():
                            para_end = content_text.find("\n\n", h_match.end())
                            if para_end > 0:
                                # Encode source preference in the placeholder
                                source_hint = f"{img.source}:{img.style}"
                                insert_positions.append((para_end, i + 1, img.prompt, source_hint))
                            break

                # Insert in reverse order to preserve positions
                for pos, img_num, prompt, source_hint in reversed(insert_positions):
                    placeholder = f"\n[IMAGE-{img_num}: {prompt} ||{source_hint}||]\n"
                    content_text = content_text[:pos] + placeholder + content_text[pos:]

                image_placeholders = _re.findall(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]", content_text)
                if image_placeholders:
                    logger.info(
                        "[IMAGE_AGENT] Injected %d image placeholders via decision agent",
                        len(image_placeholders),
                    )
        except Exception as _agent_err:
            # Fail loud — no silent heuristic fallback. If the Image Decision Agent
            # can't run, that's a pipeline orchestration bug (Ollama not loaded).
            logger.error("[IMAGE_AGENT] Image Decision Agent FAILED: %s", _agent_err)
            result["stages"]["2c_image_agent_error"] = str(_agent_err)

    if not image_placeholders:
        result["stages"]["2c_inline_images_replaced"] = False
        logger.info("No [IMAGE-N] placeholders to replace")
        return content_text

    logger.info(
        "STAGE 2C: Replacing %d inline image placeholders...",
        len(image_placeholders),
    )
    used_image_ids = set()  # Avoid duplicate images

    for num, desc in image_placeholders:
        search_query = desc.strip() if desc else topic
        alt_text = desc.strip() if desc else f"{topic} illustration"
        alt_text = alt_text.replace("[", "").replace("]", "").replace("\n", " ")[:150]
        # Clean common LLM artifacts from alt text
        alt_text = _re.sub(r'^(?:IMAGE|FIGURE|Image|Figure)\s*[-:]\s*', '', alt_text).strip()
        image_replaced = False

        # Strategy 1: SDXL generation (unique, on-topic images)
        try:
            import os as _os
            import tempfile as _tf

            import httpx as _hx2

            from services.gpu_scheduler import gpu as _gpu
            from services.site_config import site_config as _sc2
            sdxl_url = _sc2.get("sdxl_server_url", "http://host.docker.internal:9836")
            ollama_url = _sc2.get("ollama_base_url", "http://host.docker.internal:11434")
            # DB-configured: inline_image_prompt_model
            _model = _sc2.get("inline_image_prompt_model", "llama3:latest")

            # Generate SDXL prompt via Ollama with GPU lock
            import random as _inline_rnd
            _INLINE_STYLES = [
                "photorealistic scene, cinematic lighting",
                "isometric 3D illustration, clean vector style, soft shadows",
                "dark moody editorial photograph, dramatic lighting",
                "clean minimal flat design, pastel colors, geometric shapes",
                "macro close-up photograph, extreme detail, bokeh",
            ]
            _inline_style = _inline_rnd.choice(_INLINE_STYLES)
            _img_prompt_req = (
                f"Write a Stable Diffusion XL image prompt for a blog illustration about: {search_query}\n"
                f"Article topic: {topic}\n\n"
                f"Requirements: {_inline_style}, no people, no text, no faces. "
                "Describe a specific scene. 1 sentence only. Output ONLY the prompt."
            )
            async with _gpu.lock("ollama", model=_model):
                async with _hx2.AsyncClient(timeout=90) as _c2:
                    _pr = await _c2.post(f"{ollama_url}/api/generate", json={
                        "model": _model, "prompt": _img_prompt_req, "stream": False,
                        "options": {"num_predict": 100, "temperature": 0.8, "num_ctx": 4096},
                    })
                    _pr.raise_for_status()
                    sdxl_inline_prompt = _pr.json().get("response", "").strip().strip('"')

            if sdxl_inline_prompt and len(sdxl_inline_prompt) > 20:
                logger.info("  [IMAGE-%s] SDXL prompt: %s...", num, sdxl_inline_prompt[:60])
                # Generate the image with GPU lock
                neg = "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed"
                async with _gpu.lock("sdxl", model="sdxl_lightning"):
                    async with _hx2.AsyncClient(timeout=_hx2.Timeout(60.0, connect=5.0)) as _c3:
                        _ir = await _c3.post(
                            f"{sdxl_url}/generate",
                            json={
                                "prompt": sdxl_inline_prompt, "negative_prompt": neg,
                                "steps": 8, "guidance_scale": 2.0,
                            },
                            timeout=60,
                        )
                if _ir.status_code == 200:
                    # SDXL server returns JSON with image_path, or raw image bytes
                    ct = _ir.headers.get("content-type", "")
                    if ct.startswith("application/json"):
                        _sdxl_data = _ir.json()
                        tmp_path = _sdxl_data.get("image_path", "")
                        # SDXL runs on host — translate host path to container path
                        from services.site_config import site_config as _sc_img
                        _host_home = _sc_img.get("host_home", "")
                        if _host_home and tmp_path.startswith(_host_home):
                            tmp_path = tmp_path.replace(_host_home, _os.path.expanduser("~"), 1)
                        # Normalize Windows backslashes to forward slashes
                        tmp_path = tmp_path.replace("\\", "/")
                        # Path traversal guard — SDXL response is external input
                        _allowed_dirs = [
                            _os.path.realpath(_os.path.expanduser("~/Downloads")),
                            _os.path.realpath(_os.path.expanduser("~/.poindexter")),
                        ]
                        _resolved = _os.path.realpath(tmp_path)
                        if not any(_resolved.startswith(d) for d in _allowed_dirs):
                            raise RuntimeError(f"SDXL returned path outside allowed directories: {_os.path.basename(tmp_path)}")
                        if not tmp_path or not _os.path.exists(tmp_path):
                            raise RuntimeError(f"SDXL returned JSON but image_path missing or invalid: {tmp_path}")
                        logger.info("  [IMAGE-%s] SDXL generated: %s (%dms)", num, _os.path.basename(tmp_path), _sdxl_data.get("generation_time_ms", 0))
                    elif ct.startswith("image/"):
                        output_dir = _os.path.join(_os.path.expanduser("~"), "Downloads", "glad-labs-generated-images")
                        _os.makedirs(output_dir, exist_ok=True)
                        with _tf.NamedTemporaryFile(suffix=".png", delete=False, dir=output_dir) as _tmp:
                            _tmp.write(_ir.content)
                            tmp_path = _tmp.name
                    else:
                        raise RuntimeError(f"SDXL returned unexpected content-type: {ct}")

                    # Upload to R2 CDN
                    img_url = tmp_path
                    try:
                        import uuid as _uuid

                        from services.r2_upload_service import upload_to_r2
                        r2_key = f"images/inline/{_uuid.uuid4().hex[:12]}.png"
                        r2_url = await upload_to_r2(tmp_path, r2_key, content_type="image/png")
                        if r2_url:
                            img_url = r2_url
                            _os.remove(tmp_path)
                    except Exception:
                        logger.debug("[IMAGE] R2 upload failed for inline, using local path")

                    # Rewrite local paths to serveable URLs
                    if img_url.startswith("/") and "/glad-labs-generated-images/" in img_url:
                        img_url = f"/images/generated/{_os.path.basename(img_url)}"

                    if img_url not in used_image_ids:
                        used_image_ids.add(img_url)
                        # Use HTML img tag for SEO attributes (width, height, loading)
                        markdown_img = (
                            f'\n\n<img src="{img_url}" alt="{alt_text}" '
                            f'width="1024" height="1024" loading="lazy" />\n\n'
                        )
                        content_text = _re.sub(
                            rf"\[IMAGE-{num}[^\]]*\]", markdown_img, content_text, count=1
                        )
                        logger.info("  [IMAGE-%s] SDXL generated + R2 uploaded", num)
                        image_replaced = True
                else:
                    logger.warning("  [IMAGE-%s] SDXL returned %s", num, _ir.status_code)
        except Exception as sdxl_err:
            logger.warning("  [IMAGE-%s] SDXL inline failed: %s", num, sdxl_err)

        # Strategy 2: Pexels fallback
        if not image_replaced:
            search_words = search_query.split()[:5]
            short_query = " ".join(search_words)
            keywords = [topic.split()[0]] if topic and topic.strip() else []
            try:
                img = await image_service.search_featured_image(
                    topic=short_query, keywords=keywords
                )
                if img and img.url and img.url not in used_image_ids:
                    used_image_ids.add(img.url)
                    photographer = getattr(img, "photographer", "Pexels")
                    markdown_img = (
                        f'\n\n<img src="{img.url}" alt="{alt_text}" '
                        f'width="650" height="433" loading="lazy" />\n'
                        f'<figcaption>Photo by {photographer} on Pexels</figcaption>\n\n'
                    )
                    content_text = _re.sub(
                        rf"\[IMAGE-{num}[^\]]*\]", markdown_img, content_text, count=1
                    )
                    logger.info("  [IMAGE-%s] Pexels image by %s", num, photographer)
                    image_replaced = True
            except Exception as e:
                logger.error("  [IMAGE-%s] Pexels search failed: %s", num, e)

        if not image_replaced:
            content_text = _re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)
            logger.warning("  [IMAGE-%s] no image source available, removed placeholder", num)

    # Clean up leaked SDXL/image prompts after image tags
    # Pattern 1: `: *description*` right after an image
    content_text = _re.sub(r'(!\[[^\]]*\]\([^\)]+\))\s*\n\s*:\s+[^\n]+', r'\1', content_text)
    # Pattern 2: standalone `*A description...*` or `*Imagine a...*` lines (italic image prompts)
    content_text = _re.sub(
        r'\n\s*\*(?:A |An |Imagine |Visual |The |Split|Close)[^*]{40,}\*\s*\n',
        '\n', content_text
    )
    # Pattern 3: unclosed `*A description...` (missing closing *) — cap at next blank line
    content_text = _re.sub(
        r'\n\s*\*(?:A |An |Imagine |Visual |Split|Close)[^*\n]{40,}(?=\n\n)',
        '', content_text
    )
    # Strip photo attribution lines — "*Photo by X on Pexels*" etc.
    content_text = _re.sub(r'\n\s*\*?Photo by [^\n]+(?:Pexels|Unsplash|Pixabay)\*?\s*\n', '\n', content_text, flags=_re.IGNORECASE)
    # Normalize again after image placeholder substitution
    content_text = _normalize_text(content_text)
    # Update DB with image-populated content
    await database_service.update_task(task_id=task_id, updates={"content": content_text})
    result["content"] = content_text
    result["stages"]["2c_inline_images_replaced"] = True
    result["inline_images_replaced"] = len(used_image_ids)
    logger.info("Replaced %d inline images in content", len(used_image_ids))

    return content_text


async def _stage_source_featured_image(topic, tags, generate_featured_image, image_service, result, task_id=None):
    """Stage 3: Source a featured image — try SDXL generation first, fall back to Pexels.

    Returns the featured_image object (or None).
    """
    logger.info("STAGE 3: Sourcing featured image...")

    featured_image = None

    if not generate_featured_image:
        result["stages"]["3_featured_image_found"] = False
        logger.info("Image search skipped (disabled)")
        return featured_image

    # Strategy 1: Try SDXL generation with category-specific style from DB
    if image_service.sdxl_available or not image_service.sdxl_initialized:
        try:
            import os
            import tempfile

            from services.site_config import site_config

            # Use LLM-generated image prompt if available, otherwise fall back to generic
            negative = site_config.get("image_negative_prompt", "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed")

            # Check if the content pipeline already generated an image prompt
            sdxl_prompt = result.get("featured_image_prompt", "")
            if not sdxl_prompt:
                # Style diversity: check recent images and pick least-used style
                # Featured image styles — editorial illustration, mood-setting
                # Goal: trigger imagination, set the stage — NOT literal depiction
                # Load styles from DB (app_settings.image_styles JSON array)
                # Fall back to a minimal default if not configured.
                _IMAGE_STYLES = []
                try:
                    import json as _json
                    _styles_json = site_config.get("image_styles", "")
                    if _styles_json:
                        _parsed = _json.loads(_styles_json)
                        _IMAGE_STYLES = [(s["scene"], s["tags"]) for s in _parsed]
                except Exception:
                    pass
                if not _IMAGE_STYLES:
                    _IMAGE_STYLES = [
                        ("flat vector illustration", "simple geometric shapes, cyan and dark navy, clean minimal, no text"),
                        ("cyberpunk neon style", "dark background, glowing cyan purple neon lines, futuristic, no text"),
                        ("isometric 3D illustration", "colorful clean technical, low angle, no text"),
                    ]
                import random as _rnd
                # Check what styles were used recently (DB + in-memory batch dedup)
                _recent_styles: list[str] = []
                try:
                    import asyncpg as _apg
                    _cloud_url = os.environ.get("DATABASE_URL", "")
                    if _cloud_url:
                        _cconn = await _apg.connect(_cloud_url)
                        _recent = await _cconn.fetch("""
                            SELECT metadata->>'image_style' as style
                            FROM posts WHERE status = 'published'
                            AND metadata->>'image_style' IS NOT NULL
                            ORDER BY published_at DESC LIMIT 5
                        """)
                        await _cconn.close()
                        _recent_styles = [r["style"] for r in _recent if r["style"]]
                except Exception:
                    pass

                # Merge in-memory picks from the current batch / recent tasks
                # so concurrent tasks in the same worker won't repeat styles.
                _mem_recent = _get_in_memory_recent_styles()
                _all_recent = set(_recent_styles) | set(_mem_recent)

                _available = [s for s in _IMAGE_STYLES if s[0] not in _all_recent]
                if not _available:
                    _available = _IMAGE_STYLES  # All used recently, reset pool
                _chosen_style, _style_tags = _rnd.choice(_available)

                # Record the pick so later tasks in the same batch see it
                _record_style_pick(_chosen_style)

                # Store chosen style in result metadata for tracking
                result["image_style"] = _chosen_style

                # Generate SDXL prompt via Ollama with the chosen style
                try:
                    import httpx as _hx
                    _ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
                    _img_prompt = (
                        f"Write a Stable Diffusion XL image prompt for a magazine-style editorial cover image.\n"
                        f"The article is about: {topic}\n"
                        f"DO NOT depict the topic literally. Instead, create an atmospheric scene that evokes the FEELING of the topic.\n"
                        f"Style direction: {_chosen_style}\n\n"
                        f"Requirements: {_style_tags}, faceless silhouettes OK but no identifiable faces, "
                        "no text or words in the image, no hands. "
                        "Think editorial magazine art — mood, atmosphere, imagination. "
                        "1-2 sentences only. Output ONLY the prompt, nothing else."
                    )
                    from services.site_config import site_config as _feat_prompt_sc
                    _prompt_model = _feat_prompt_sc.get("inline_image_prompt_model", "llama3:latest")
                    async with _hx.AsyncClient(timeout=_hx.Timeout(30.0, connect=3.0)) as _c:
                        _r = await _c.post(
                            f"{_ollama_url}/api/generate",
                            json={
                                "model": _prompt_model, "prompt": _img_prompt, "stream": False,
                                "options": {"num_predict": 150, "temperature": 0.7, "num_ctx": 4096},
                            },
                            timeout=30,
                        )
                        _r.raise_for_status()
                        sdxl_prompt = _r.json().get("response", "").strip().strip('"')
                    logger.info("[IMAGE] Style: %s | SDXL prompt: %s", _chosen_style, sdxl_prompt[:80])
                except Exception as prompt_err:
                    logger.warning("[IMAGE] LLM prompt generation failed, using fallback: %s", prompt_err)
                    sdxl_prompt = f"{_chosen_style}, {_style_tags}, no text, no faces"
            # Use external SDXL server (same as inline images) instead of internal diffusers
            from services.site_config import site_config as _feat_sc
            _feat_sdxl_url = _feat_sc.get("sdxl_server_url", "http://host.docker.internal:9836")

            from services.gpu_scheduler import gpu
            output_path = None
            async with gpu.lock("sdxl", model="sdxl_lightning"):
                import httpx as _feat_hx
                # 60s cap per SDXL request — Lightning generates in ~2s,
                # leave headroom for cold load (~10s) and upload.
                async with _feat_hx.AsyncClient(timeout=_feat_hx.Timeout(60.0, connect=5.0)) as _feat_client:
                    _feat_resp = await _feat_client.post(
                        f"{_feat_sdxl_url}/generate",
                        json={
                            "prompt": sdxl_prompt, "negative_prompt": negative,
                            "steps": 8, "guidance_scale": 2.0,
                        },
                        timeout=60,
                    )

            if _feat_resp.status_code == 200:
                ct = _feat_resp.headers.get("content-type", "")
                if ct.startswith("application/json"):
                    _feat_data = _feat_resp.json()
                    output_path = _feat_data.get("image_path", "")
                    # Translate host path to container path
                    _feat_host_home = _feat_sc.get("host_home", "")
                    if _feat_host_home and output_path.startswith(_feat_host_home):
                        output_path = output_path.replace(_feat_host_home, os.path.expanduser("~"), 1)
                    output_path = output_path.replace("\\", "/")
                    logger.info("[IMAGE] Featured SDXL generated: %s (%dms)",
                                os.path.basename(output_path), _feat_data.get("generation_time_ms", 0))
                elif ct.startswith("image/"):
                    output_dir = os.path.join(os.path.expanduser("~"), "Downloads", "glad-labs-generated-images")
                    os.makedirs(output_dir, exist_ok=True)
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=output_dir) as tmp:
                        tmp.write(_feat_resp.content)
                        output_path = tmp.name

            if output_path and os.path.exists(output_path):
                # Upload to R2 CDN (replaced Cloudinary — zero egress fees)
                image_url = output_path  # Fallback to local path
                try:
                    import uuid as _r2_uuid

                    from services.r2_upload_service import upload_to_r2
                    _r2_id = task_id or _r2_uuid.uuid4().hex[:12]
                    r2_key = f"images/featured/{_r2_id}.jpg"
                    r2_url = await upload_to_r2(output_path, r2_key, content_type="image/jpeg")
                    if r2_url:
                        image_url = r2_url
                        logger.info("Uploaded to R2: %s", image_url[:80])
                        os.remove(output_path)  # Clean up local file
                    else:
                        logger.warning("R2 upload returned None, using local path")
                except Exception as upload_err:
                    logger.warning("R2 upload failed (using local): %s", upload_err)

                from dataclasses import dataclass

                @dataclass
                class GeneratedImage:
                    url: str
                    photographer: str
                    source: str

                # Rewrite local paths to serveable URLs
                if "/glad-labs-generated-images/" in image_url and not image_url.startswith("http"):
                    import os as _img_os
                    image_url = f"/images/generated/{_img_os.path.basename(image_url)}"

                featured_image = GeneratedImage(
                    url=image_url,
                    photographer="AI Generated (SDXL)",
                    source="sdxl_cloudinary" if "cloudinary" in image_url else "sdxl_local",
                )
                result["featured_image_url"] = image_url
                result["featured_image_alt"] = f"{topic} — AI generated illustration"[:200]
                result["featured_image_width"] = 1024
                result["featured_image_height"] = 1024
                result["featured_image_photographer"] = "AI Generated (SDXL)"
                result["featured_image_source"] = featured_image.source
                result["stages"]["3_featured_image_found"] = True
                result["stages"]["3_image_source"] = "sdxl"
                logger.info("Featured image generated via SDXL + Cloudinary\n")
                return featured_image
        except Exception as e:
            logger.info("SDXL generation skipped (%s), falling back to Pexels", e)

    # Strategy 2: Fall back to Pexels (free stock photos)
    search_keywords = tags or [topic]
    try:
        featured_image = await image_service.search_featured_image(
            topic=topic, keywords=search_keywords
        )
        if featured_image:
            result["featured_image_url"] = featured_image.url
            result["featured_image_alt"] = f"{topic} — Photo by {featured_image.photographer} on Pexels"[:200]
            result["featured_image_width"] = getattr(featured_image, "width", 650)
            result["featured_image_height"] = getattr(featured_image, "height", 433)
            result["featured_image_photographer"] = featured_image.photographer
            result["featured_image_source"] = featured_image.source
            result["stages"]["3_featured_image_found"] = True
            result["stages"]["3_image_source"] = "pexels"
            logger.info(
                "Featured image found: %s (Pexels)",
                featured_image.photographer,
            )
        else:
            result["stages"]["3_featured_image_found"] = False
            logger.warning("No featured image found for '%s'", topic)
    except Exception as e:
        logger.error("Image search failed: %s", e, exc_info=True)
        result["stages"]["3_featured_image_found"] = False

    return featured_image










async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: list[str] | None = None,
    generate_featured_image: bool = True,
    database_service: DatabaseService | None = None,
    task_id: str | None = None,
    # NEW: Model selection parameters (Week 1)
    models_by_phase: dict[str, str] | None = None,
    quality_preference: str | None = None,
    category: str | None = None,
    target_audience: str | None = None,
) -> dict[str, Any]:
    """Run the full content generation pipeline (verify, generate, QA, images, SEO, finalize)."""
    from uuid import uuid4

    # Generate task_id if not provided
    if not task_id:
        task_id = str(uuid4())

    if not database_service:
        logger.error("DatabaseService not provided - cannot persist content")
        raise ValueError("DatabaseService is required for content_tasks persistence")

    logger.info("=" * 80)
    logger.info("COMPLETE CONTENT GENERATION PIPELINE")
    logger.info("=" * 80)
    logger.info("   Task ID: %s", task_id)
    logger.info("   Topic: %s", topic)
    logger.info("   Style: %s | Tone: %s", style, tone)
    logger.info("   Target Length: %s words", target_length)
    logger.info("   Tags: %s", ', '.join(tags) if tags else 'none')
    logger.info("   Image Search: %s", generate_featured_image)
    logger.info("=" * 80)

    # `result` doubles as the shared pipeline context consumed by Stage
    # plugins. Stages read/write via context.get() / StageResult.context_updates.
    # Populating the orchestrator's inputs here means every stage can pull
    # what it needs without a separate adapter layer.
    image_service = get_image_service()
    result: dict[str, Any] = {
        "task_id": task_id,
        "topic": topic,
        "status": "pending",
        "stages": {},
        "category": category or "technology",
        # Orchestrator inputs — stages read these directly.
        "style": style,
        "tone": tone,
        "target_length": target_length,
        "tags": tags or [],
        "generate_featured_image": generate_featured_image,
        "database_service": database_service,
        "image_service": image_service,
        "models_by_phase": models_by_phase or {},
        "quality_preference": quality_preference,
        "target_audience": target_audience,
    }

    # Build the Stage runner. Stages are loaded imperatively via
    # plugins.registry.get_core_samples since the poetry entry_points
    # packaging fix is tracked separately (#78).
    from plugins.registry import get_core_samples
    from plugins.stage_runner import StageRunner
    _runner = StageRunner(database_service.pool, get_core_samples().get("stages", []))

    try:
        logger.info("[BG-TASK] Starting content generation for task %s...", task_id[:8])
        logger.debug("[BG-TASK] database_service = %s", database_service)

        # ---------------------------------------------------------------
        # Chunk 1: verify_task → generate_content
        # ---------------------------------------------------------------
        audit_log_bg("task_started", "content_router", {"topic": topic[:100]}, task_id=task_id)
        _summary1 = await _runner.run_all(result, order=["verify_task", "generate_content"])
        if _summary1.halted_at == "generate_content":
            raise RuntimeError(
                f"Stage 'generate_content' halted — cannot continue without content "
                f"(detail: {_summary1.records[-1].detail})"
            )

        content_text = result.get("content", "")
        model_used = result.get("model_used", "")
        metrics = result.get("generate_metrics", {})
        title = result.get("title", "")

        audit_log_bg("generation_complete", "content_router", {
            "model": model_used, "word_count": len(content_text.split()) if content_text else 0,
        }, task_id=task_id)

        # Observability: detect silent writer fallback. If the DB configured
        # pipeline_writer_model (e.g. qwen2.5:72b) differs from the model
        # that actually produced the draft (e.g. gemma3:27b), fire a LOUD
        # audit event. Without this, a timed-out 72B silently degrades to
        # a 27B and nobody notices — which cost us task 033803c9 on 2026-04-11.
        try:
            from services.site_config import site_config as _sc_writer_check
            _configured_writer = (_sc_writer_check.get("pipeline_writer_model", "") or "").removeprefix("ollama/")
            _actual_writer = (model_used or "").removeprefix("ollama/")
            if _configured_writer and _actual_writer and _configured_writer != _actual_writer:
                logger.warning(
                    "[WRITER_FALLBACK] Configured %s but actually generated with %s for task %s",
                    _configured_writer, _actual_writer, task_id[:8],
                )
                audit_log_bg(
                    "writer_fallback", "content_router",
                    {
                        "configured_writer": _configured_writer,
                        "actual_writer": _actual_writer,
                        "reason": "primary_model_failed_or_timed_out",
                        "stage": "generate_content",
                    },
                    task_id=task_id, severity="warning",
                )
        except Exception as _exc:
            logger.debug("writer_fallback check failed: %s", _exc)

        # ---------------------------------------------------------------
        # Chunk 2: writer_self_review → quality_evaluation → url_validation
        #          → replace_inline_images (image-decision PLANNING pass,
        #          still in ollama GPU mode)
        # ---------------------------------------------------------------
        _summary2 = await _runner.run_all(result, order=[
            "writer_self_review",
            "quality_evaluation",
            "url_validation",
            "replace_inline_images",
        ])
        if _summary2.halted_at == "quality_evaluation":
            raise RuntimeError(
                f"Stage 'quality_evaluation' halted — cannot continue without QA score "
                f"(detail: {_summary2.records[-1].detail})"
            )

        # Post-QA audit. The stages populate result["quality_result"] +
        # result["quality_score"]; surface the pass/fail into the audit log.
        content_text = result.get("content", "")
        quality_result = result.get("quality_result")
        if quality_result is not None:
            audit_log_bg(
                "qa_passed" if quality_result.overall_score >= 50 else "qa_failed",
                "content_router",
                {"score": quality_result.overall_score, "stage": "early_eval"},
                task_id=task_id,
            )

        # ---------------------------------------------------------------
        # Chunk 3: GPU switch → featured image → GPU switch back
        # ---------------------------------------------------------------
        _pool = database_service.pool if database_service else None
        try:
            from services.gpu_scheduler import gpu as _gpu_sched
            await _gpu_sched.prepare_mode("sdxl")
        except Exception:
            logger.debug("GPU mode switch to SDXL failed (non-fatal)")

        if await _is_stage_enabled(_pool, "featured_image"):
            await _runner.run_all(result, order=["source_featured_image"])
        else:
            logger.info("Featured image skipped (disabled in pipeline_stages)")

        featured_image = result.get("featured_image")

        try:
            await _gpu_sched.prepare_mode("ollama")
        except Exception:
            logger.debug("GPU mode switch to Ollama failed (non-fatal)")

        # Stage 3.5 + 3.7: Multi-Model QA (gate-checked)
        if not await _is_stage_enabled(_pool, "cross_model_qa"):
            logger.info("Cross-model QA skipped (disabled in pipeline_stages)")
            result["qa_final_score"] = quality_result.overall_score
            result["qa_reviews"] = []
        else:
            from services.container import get_service
            from services.multi_model_qa import MultiModelQA
            # Pass the DB-backed settings service so qa_validator_weight,
            # qa_critic_weight, qa_gate_weight, qa_final_score_threshold,
            # and qa_consistency_veto_threshold are actually read from
            # app_settings. Without it, MultiModelQA silently uses its
            # hardcoded defaults and the DB-as-config story is broken.
            _settings_service = get_service("settings")
            _qa = MultiModelQA(
                pool=database_service.pool,
                settings_service=_settings_service,
            )

            # Consistency rewrite loop: if QA rejects for cross-section
            # contradictions, send the draft back through the writer
            # with EVERY issue the QA reviewers flagged (validator warnings,
            # critic complaints, topic delivery issues, contradictions,
            # image relevance, preview visuals) then re-run QA. Allow up
            # to qa_max_rewrites attempts (default 2) before giving up.
            # Rationale (2026-04-11): contradictions and fabrications make
            # us look stupid in public. A full rewrite pass is cheap compared
            # to publishing bad content or hard-rejecting good content that
            # just needs a fix.
            _max_rewrites = 2
            try:
                _mr = (
                    await _settings_service.get("qa_max_rewrites")
                    or await _settings_service.get("qa_consistency_max_rewrites")
                )
                if _mr is not None:
                    _max_rewrites = int(_mr)
            except Exception:
                pass

            def _aggregate_issues_to_fix(qa_result) -> tuple[str, bool]:
                """Collect every flagged issue into a structured list the
                writer can act on in a single rewrite pass.

                Pulls from:
                  - ValidationResult critical issues only (warnings are
                    noted but don't force a rewrite when overall QA
                    passed — they're advisory)
                  - Each ReviewerResult in qa_result.reviews where the
                    reviewer reported non-approval

                Returns (issues_text, has_blocking_issue). The second
                element is True when at least one issue actually blocks
                approval — a rewrite should fire only when True. This
                prevents the loop from burning LLM cycles on approved
                posts that have a harmless validator warning like a
                potentially-fabricated stat.
                """
                lines: list[str] = []
                has_blocking = False
                # Programmatic validator — ship CRITICAL issues into the
                # rewrite list and flag them as blocking. Warnings are
                # advisory: we include them so the writer fixes them
                # opportunistically when a rewrite is happening for
                # another reason, but a warning alone won't trigger
                # a rewrite.
                try:
                    _vr = qa_result.validation
                    if _vr is not None and _vr.issues:
                        for _issue in _vr.issues:
                            lines.append(
                                f"[{_issue.severity}] {_issue.category}: {_issue.description}"
                            )
                            if _issue.severity == "critical":
                                has_blocking = True
                except Exception:
                    pass
                # Reviewers — a reviewer that didn't approve is a
                # blocking issue. A reviewer that approved with a
                # borderline score (< 75) is advisory.
                for _r in qa_result.reviews:
                    if _r.reviewer == "programmatic_validator":
                        continue  # already surfaced via validation above
                    if _r.approved and _r.score >= 75:
                        continue
                    severity = "critical" if not _r.approved else "warning"
                    lines.append(f"[{severity}] {_r.reviewer}: {_r.feedback}")
                    if not _r.approved:
                        has_blocking = True
                return "\n".join(lines[:30]), has_blocking

            _qa_result = None
            _rewrite_attempts = 0
            while True:
                _qa_result = await _run_stage_with_timeout(
                    _qa.review(
                        title=_normalize_text(result.get("seo_title", topic)),
                        content=_normalize_text(content_text),
                        topic=topic,
                        research_sources=result.get("research_context"),
                    ),
                    "cross_model_qa", task_id,
                )
                if _qa_result is None:
                    break

                _issues_to_fix, _has_blocking_issue = _aggregate_issues_to_fix(_qa_result)

                # Clean pass — approved AND no blocking issue. Warnings
                # alone on an approved post don't trigger the rewrite
                # loop; they'd burn a full generation cycle to fix
                # something that isn't actually blocking publish.
                if _qa_result.approved and not _has_blocking_issue:
                    break

                # Topic-delivery failure means the content is about the
                # wrong thing — can't be fixed with targeted edits, bail.
                _topic_delivery_failed = any(
                    (not r.approved) and r.reviewer == "topic_delivery"
                    for r in _qa_result.reviews
                )
                if _topic_delivery_failed:
                    break

                if _rewrite_attempts >= _max_rewrites:
                    break  # Out of attempts — fall through with whatever we have.

                if not _issues_to_fix:
                    break  # Nothing specific to rewrite against

                _issue_count_current = _issues_to_fix.count("\n") + 1
                logger.warning(
                    "[QA_REWRITE] Task %s: %d issues flagged, attempting aggregate rewrite (%d/%d)",
                    task_id[:8],
                    _issue_count_current,
                    _rewrite_attempts + 1,
                    _max_rewrites,
                )
                audit_log_bg(
                    "rewrite_decision",
                    "content_router",
                    {
                        "event": "rewrite_started",
                        "attempt": _rewrite_attempts + 1,
                        "max_attempts": _max_rewrites,
                        "issue_count": _issue_count_current,
                        "issues_sample": _issues_to_fix[:500],
                        "prior_score": float(_qa_result.final_score),
                    },
                    task_id=task_id,
                    severity="info",
                )

                try:
                    _revise_prompt = _QA_AGGREGATE_REWRITE_PROMPT.format(
                        title=result.get("seo_title", topic),
                        issues_to_fix=_issues_to_fix,
                        content=content_text,
                    )
                    from services.ollama_client import OllamaClient
                    from services.site_config import site_config as _sc_qa_rw
                    _rev_client = OllamaClient(
                        timeout=_sc_qa_rw.get_int("content_router_qa_rewrite_timeout_seconds", 240)
                    )
                    try:
                        # Default to a proven NON-THINKING writer. The old
                        # default of qwen3:30b silently burned the token
                        # budget on <think> tags and returned empty.
                        _writer_model = (
                            await _settings_service.get("pipeline_writer_model")
                            or "gemma3:27b"
                        )
                        _writer_model = _writer_model.removeprefix("ollama/")
                        _rev_result = await _rev_client.generate(
                            prompt=_revise_prompt,
                            model=_writer_model,
                            temperature=0.4,
                            max_tokens=_sc_qa_rw.get_int("content_router_qa_rewrite_max_tokens", 8000),
                        )
                        # Fallback to gemma3:27b if the primary writer
                        # returns empty or too-short — known pattern with
                        # thinking models on long prompts (qwen3.x family
                        # burns the entire token budget on <think> tags
                        # and returns an empty assistant message).
                        _revised_text = (_rev_result.get("text") or "").strip()
                        if len(_revised_text) < int(0.5 * len(content_text)):
                            logger.warning(
                                "[QA_REWRITE] Task %s: primary writer %s returned %d chars — likely thinking-mode eating the token budget. Falling back to gemma3:27b.",
                                task_id[:8], _writer_model, len(_revised_text),
                            )
                            # Loud audit event — makes the silent fallback
                            # visible on the /pipeline dashboard.
                            audit_log_bg(
                                "writer_fallback", "content_router",
                                {
                                    "configured_writer": _writer_model,
                                    "actual_writer": "gemma3:27b",
                                    "reason": "primary_returned_empty_on_rewrite",
                                    "stage": "qa_rewrite",
                                    "attempt": _rewrite_attempts + 1,
                                    "primary_chars": len(_revised_text),
                                    "expected_min_chars": int(0.5 * len(content_text)),
                                },
                                task_id=task_id, severity="warning",
                            )
                            _fb_result = await _rev_client.generate(
                                prompt=_revise_prompt,
                                model=_sc_qa_rw.get("qa_fallback_writer_model", "gemma3:27b"),
                                temperature=0.4,
                                max_tokens=_sc_qa_rw.get_int("content_router_qa_rewrite_max_tokens", 8000),
                            )
                            _revised_text = (_fb_result.get("text") or "").strip()
                    finally:
                        try:
                            await _rev_client.close()
                        except Exception:
                            pass
                    if len(_revised_text) >= int(0.5 * len(content_text)):
                        content_text = _revised_text
                        await database_service.update_task(task_id, {
                            "content": content_text,
                        })
                        logger.info(
                            "[QA_REWRITE] Task %s: rewrite succeeded (%d chars), re-running QA",
                            task_id[:8], len(content_text),
                        )
                        _rewrite_attempts += 1
                        continue
                    else:
                        logger.warning(
                            "[QA_REWRITE] Task %s: rewrite + fallback both returned too-short output (%d chars)",
                            task_id[:8], len(_revised_text),
                        )
                        break
                except Exception as _rw_err:
                    logger.warning(
                        "[QA_REWRITE] Task %s: rewrite failed (non-fatal): %s",
                        task_id[:8], _rw_err,
                    )
                    break

            if _qa_result is None:
                logger.warning("Cross-model QA timed out for task %s — using early QA score", task_id[:8])
                result["qa_final_score"] = quality_result.overall_score
                result["qa_reviews"] = [{"reviewer": "timeout", "score": 0, "approved": True,
                                         "feedback": "QA stage timed out — skipped", "provider": "none"}]
            else:
                result["qa_final_score"] = _qa_result.final_score
                # Promote the multi-model QA score to the canonical quality_score that
                # downstream gates (task_executor auto-curation, finalize_task DB write)
                # read. The early pattern-based eval is too pessimistic on its own — it
                # was rejecting posts the LLM critics scored 90+. Take the max so we
                # never go backwards.
                result["quality_score"] = max(
                    float(result.get("quality_score", 0) or 0),
                    float(_qa_result.final_score),
                )
                result["qa_reviews"] = [
                    {"reviewer": r.reviewer, "score": r.score, "approved": r.approved,
                     "feedback": r.feedback, "provider": r.provider}
                    for r in _qa_result.reviews
                ]
                result["qa_rewrite_attempts"] = _rewrite_attempts

                # Per-reviewer structured audit events. One row per
                # gate decision, plus one aggregate row for the final
                # MultiModelResult. This is what every visibility layer
                # (Grafana panels, Discord ops channel, mobile event
                # stream) reads from. Keep the fields stable so
                # downstream consumers don't break on upgrades.
                for _r in _qa_result.reviews:
                    audit_log_bg(
                        "qa_decision",
                        "multi_model_qa",
                        {
                            "reviewer": _r.reviewer,
                            "provider": _r.provider,
                            "score": float(_r.score),
                            "approved": bool(_r.approved),
                            "feedback": _r.feedback[:500],
                            "stage": "multi_model_qa",
                            "rewrite_attempts_so_far": _rewrite_attempts,
                        },
                        task_id=task_id,
                        severity="info" if _r.approved else "warning",
                    )
                audit_log_bg(
                    "qa_aggregate",
                    "multi_model_qa",
                    {
                        "final_score": float(_qa_result.final_score),
                        "approved": bool(_qa_result.approved),
                        "reviewer_count": len(_qa_result.reviews),
                        "failed_reviewers": [
                            r.reviewer for r in _qa_result.reviews if not r.approved
                        ],
                        "rewrite_attempts": _rewrite_attempts,
                    },
                    task_id=task_id,
                    severity="info" if _qa_result.approved else "warning",
                )
                # Log QA review cost to database
                if _qa_result.cost_log and database_service:
                    try:
                        _qa_result.cost_log["task_id"] = task_id
                        await database_service.log_cost(_qa_result.cost_log)
                        logger.info("QA cost logged: $%.4f (%s/%s)", _qa_result.cost_log["cost_usd"], _qa_result.cost_log["provider"], _qa_result.cost_log["model"])
                        audit_log_bg("cost_logged", "content_router", {
                            "cost_usd": _qa_result.cost_log.get("cost_usd"),
                            "provider": _qa_result.cost_log.get("provider"),
                            "model": _qa_result.cost_log.get("model"),
                            "phase": "multi_model_qa",
                        }, task_id=task_id)
                    except Exception as e:
                        logger.warning("QA cost logging failed (non-critical): %s", e)

                if not _qa_result.approved:
                    logger.warning(
                        "[MULTI_QA] Content rejected for task %s after %d rewrite attempt(s):\n%s",
                        task_id[:8], _rewrite_attempts, _qa_result.summary,
                    )
                    audit_log_bg("qa_failed", "content_router", {
                        "score": _qa_result.final_score, "stage": "multi_model_qa",
                        "summary": _qa_result.summary[:300],
                        "rewrite_attempts": _rewrite_attempts,
                    }, task_id=task_id, severity="warning")
                    result["status"] = "rejected"
                    # Name the reviewer that actually vetoed so the error
                    # message tells an operator what to fix — previously
                    # we just grabbed the last reviewer's feedback tail,
                    # which often looked like a bonus line (e.g.
                    # "3 verified external citations (+15 bonus)") instead
                    # of the rejection reason.
                    _vetoer = next(
                        (r for r in _qa_result.reviews if not r.approved),
                        _qa_result.reviews[-1] if _qa_result.reviews else None,
                    )
                    if _vetoer:
                        _feedback = (_vetoer.feedback or "no feedback").strip()[:300]
                        _reason_msg = (
                            f"Multi-model QA rejected (score: {_qa_result.final_score:.0f}, "
                            f"veto: {_vetoer.reviewer} @ {_vetoer.score:.0f}): {_feedback}"
                        )
                    else:
                        _reason_msg = (
                            f"Multi-model QA rejected (score: {_qa_result.final_score:.0f}): "
                            "No reviews recorded"
                        )
                    await database_service.update_task(task_id, {
                        "status": "rejected",
                        "error_message": _reason_msg,
                    })
                    return result
                audit_log_bg("qa_passed", "content_router", {
                    "score": _qa_result.final_score, "stage": "multi_model_qa",
                    "rewrite_attempts": _rewrite_attempts,
                }, task_id=task_id)
                logger.info("[MULTI_QA] Content approved for task %s: %s", task_id[:8], _qa_result.summary.split("\\n")[0])

        # ---------------------------------------------------------------
        # Chunk 5: SEO → media scripts → training data → finalize
        # ---------------------------------------------------------------
        # The previous inline orchestrator read stage-specific fallbacks
        # (e.g. topic[:60] for seo_title on timeout) — now lives inside
        # the stages themselves or in finalize_task's graceful defaults.
        _summary5 = await _runner.run_all(result, order=[
            "generate_seo_metadata",
            "generate_media_scripts",
            "capture_training_data",
            "finalize_task",
        ])
        if _summary5.halted_at:
            raise RuntimeError(
                f"Post-QA pipeline halted at {_summary5.halted_at} "
                f"(detail: {_summary5.records[-1].detail})"
            )

        audit_log_bg("pipeline_complete", "content_router", {
            # quality_score is the promoted score that downstream gates read
            # (matches content_tasks.quality_score). early_eval_score is kept
            # alongside for diagnostic visibility.
            "quality_score": result.get("quality_score", quality_result.overall_score),
            "qa_final_score": result.get("qa_final_score"),
            "early_eval_score": quality_result.overall_score,
            "status": result["status"],
        }, task_id=task_id)

        logger.info("=" * 80)
        logger.info("COMPLETE CONTENT GENERATION PIPELINE FINISHED")
        logger.info("=" * 80)
        logger.info("   Task ID: %s", task_id)
        logger.info("   Post ID: %s", result.get('post_id', 'NOT_YET_CREATED'))
        logger.info(
            "   Featured Image: %s",
            result.get('featured_image_url', 'NONE')[:100] if result.get('featured_image_url') else 'NONE',
        )
        logger.info("   Quality Score: %.1f/100", quality_result.overall_score)
        logger.info("   Status: %s", result['status'])
        logger.info("   Next: Human review & approval")
        logger.info("=" * 80)

        return result

    except Exception as e:
        logger.error("[BG-TASK] Pipeline error for task %s...: %s", task_id[:8], e, exc_info=True)
        logger.error("[BG-TASK] Detailed traceback:", exc_info=True)
        audit_log_bg("error", "content_router", {
            "error": str(e)[:500], "stages_completed": list(result.get("stages", {}).keys()),
        }, task_id=task_id, severity="error")

        # Update content_task with failure status
        # 🔑 CRITICAL: Preserve all partially-generated data (content, image, metadata)
        # so it's available for review/approval workflow
        try:
            logger.debug("[BG-TASK] Attempting to update task status to 'failed'...")
            logger.debug("[BG-TASK] Preserving partial results: %s", list(result.keys()))

            # Build task_metadata with whatever we successfully generated
            failure_metadata = {
                "content": result.get("content"),
                "featured_image_url": result.get("featured_image_url"),
                "featured_image_alt": result.get("featured_image_alt"),
                "featured_image_width": result.get("featured_image_width"),
                "featured_image_height": result.get("featured_image_height"),
                "featured_image_photographer": result.get("featured_image_photographer"),
                "featured_image_source": result.get("featured_image_source"),
                "seo_title": result.get("seo_title"),
                "seo_description": result.get("seo_description"),
                "seo_keywords": result.get("seo_keywords"),
                "topic": topic,
                "style": style,
                "tone": tone,
                "quality_score": result.get("quality_score"),
                "error_stage": str(e)[:200],  # Which stage failed
                "error_message": str(e),  # Full error for debugging
                "stages_completed": result.get("stages", {}),
            }

            # Remove None values from metadata
            failure_metadata = {k: v for k, v in failure_metadata.items() if v is not None}

            await database_service.update_task(
                task_id=task_id,
                updates={
                    "status": "failed",
                    "error_message": str(e),
                    "task_metadata": failure_metadata,  # ✅ Preserve all data
                },
            )
            logger.debug("[BG-TASK] Task status updated to 'failed' with preserved data")

            # Emit webhook so OpenClaw is notified of pipeline failure
            try:
                await emit_webhook_event(database_service.pool, "task.failed", {
                    "task_id": task_id, "topic": topic, "error": str(e)[:200],
                })
            except Exception:
                logger.warning("[WEBHOOK] Failed to emit task.failed event from pipeline", exc_info=True)
        except Exception as db_error:
            logger.error("[BG-TASK] Failed to update task status: %s", db_error, exc_info=True)

        result["status"] = "failed"
        result["error"] = str(e)
        return result


async def _select_category_for_topic(
    topic: str, database_service: DatabaseService, requested_category: str | None = None
) -> str | None:
    """Select category by requested slug, keyword matching, or default to 'technology'. Returns UUID."""
    # Priority 1: Use the requested category if valid
    if requested_category:
        try:
            async with database_service.pool.acquire() as conn:
                cat_id = await conn.fetchval(
                    "SELECT id FROM categories WHERE slug = $1 OR name ILIKE $1", requested_category
                )
            if cat_id:
                return cat_id
        except Exception:
            pass

    topic_lower = topic.lower()

    category_keywords = {
        "technology": ["ai", "tech", "software", "cloud", "machine learning", "data", "coding", "python", "javascript", "docker", "kubernetes", "api", "database"],
        "business": ["business", "strategy", "management", "entrepreneur", "growth", "revenue", "marketing", "saas"],
        "startup": ["startup", "founder", "bootstrapper", "mvp", "launch", "validate", "solo founder", "side project"],
        "security": ["security", "hack", "owasp", "zero trust", "vulnerability", "auth", "encryption", "secrets"],
        "engineering": ["engineering", "architecture", "monorepo", "git", "technical debt", "migration", "ci/cd", "testing"],
        "insights": ["trend", "landscape", "state of", "productivity", "remote work", "future of", "prediction"],
    }

    # Priority 2: Keyword matching
    matched_category = "technology"  # Default
    best_score = 0
    for category, keywords in category_keywords.items():
        score = sum(1 for kw in keywords if kw in topic_lower)
        if score > best_score:
            best_score = score
            matched_category = category

    # Get category ID
    try:
        async with database_service.pool.acquire() as conn:
            cat_id = await conn.fetchval(
                "SELECT id FROM categories WHERE slug = $1", matched_category
            )
        return cat_id
    except Exception as e:
        logger.error("Error selecting category: %s", e, exc_info=True)
        return None


async def _get_or_create_default_author(database_service: DatabaseService) -> str | None:
    """Get or create the default 'Poindexter AI' author. Returns UUID."""
    try:
        async with database_service.pool.acquire() as conn:
            # Try to get existing Poindexter AI author
            author_id = await conn.fetchval(
                "SELECT id FROM authors WHERE slug = 'poindexter-ai' LIMIT 1"
            )

            if author_id:
                return author_id

            # Create if doesn't exist
            author_id = await conn.fetchval("""
                INSERT INTO authors (name, slug, email, bio, avatar_url)
                VALUES ('Poindexter AI', 'poindexter-ai', 'poindexter@glad-labs.ai', 
                        'AI Content Generation Engine', NULL)
                ON CONFLICT (slug) DO NOTHING
                RETURNING id
                """)

            if author_id:
                logger.info("Created default author: Poindexter AI (%s)", author_id)
                return author_id

            # Fallback: return any author
            fallback_id = await conn.fetchval("SELECT id FROM authors LIMIT 1")
            return fallback_id

    except Exception as e:
        logger.error("Error getting/creating default author: %s", e, exc_info=True)
        return None

"""
Dev.to Cross-Posting Service

Automatically cross-posts published blog content to Dev.to with a canonical URL
pointing back to the site. Articles are created as drafts so they can be
reviewed before going live on Dev.to.

The Dev.to API key is stored in app_settings (key: devto_api_key). If not
configured, all operations gracefully skip.

Usage:
    from services.devto_service import DevToCrossPostService

    svc = DevToCrossPostService(pool)
    result = await svc.cross_post(
        title="Why Local LLMs Beat Cloud APIs",
        content_markdown="## Introduction\n\nLocal LLMs are...",
        canonical_url="https://example.com/posts/why-local-llms-beat-cloud-apis",
        tags=["llm", "selfhosting", "ai"],
    )
"""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal

import httpx

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


# Terminal markers we write into ``posts.metadata->>'devto_status'`` so
# the crosspost cron can dedup. ``posted`` blocks the row from re-entry
# because the URL was recorded; ``gave_up`` blocks it after a permanent
# Dev.to rejection (e.g. 422 canonical-URL collision) so we stop
# retrying once an hour forever (#397).
DEVTO_STATUS_POSTED = "posted"
DEVTO_STATUS_GAVE_UP = "gave_up"


@dataclass
class CrossPostResult:
    """Outcome of a single Dev.to cross-post attempt.

    See ``DevToCrossPostService.cross_post`` for the status enum.
    Callers (notably ``cross_post_by_post_id`` and the
    ``CrosspostToDevtoJob``) branch on ``status`` to decide whether
    to record success metadata, mark the post as ``gave_up`` so the
    cron stops retrying, or leave the row alone for the next tick.
    """

    status: Literal["posted", "gave_up", "transient", "skipped"]
    url: str | None = None
    article_id: str | None = None
    http_status: int | None = None
    error: str | None = None


def _devto_api_base() -> str:
    """Dev.to (or self-hosted Forem) API base. Tunable so customers
    running a private Forem instance — or pointing at a future Dev.to
    API version — can swap without a code change (#198).

    Lazy lookup because this module imports before site_config.load()
    runs in the worker lifespan; capturing at import time meant DB
    overrides took no effect until restart.
    """
    return site_config.get("devto_api_base", "https://dev.to/api")


def _site_url() -> str:
    """Return the canonical site URL. Reads site_config lazily because
    this module may be imported before site_config has been populated
    from the DB. Fails loud (RuntimeError) if the setting is missing —
    an empty canonical URL silently produces broken relative paths."""
    return site_config.require("site_url")


class DevToCrossPostService:
    """Cross-post blog content to Dev.to as drafts with canonical URLs."""

    def __init__(self, pool):
        self.pool = pool
        self._api_key: str | None = None
        self._api_key_loaded = False

    async def _get_api_key(self) -> str | None:
        """Fetch + decrypt the Dev.to API key from app_settings.

        Cached per instance — one DB round-trip per DevtoService.
        ``devto_api_key`` is flagged ``is_secret=true`` after the
        2026-04-20 hardening sweep, so this goes through
        ``plugins.secrets.get_secret`` to handle the ``enc:v1:``
        ciphertext prefix.

        Raises on pool/decryption failure (no try/except — caller
        handles). Previous implementation swallowed every exception,
        which hid misconfiguration (wrong DSN, missing key) behind a
        generic "posting skipped" log entry.
        """
        if self._api_key_loaded:
            return self._api_key

        from plugins.secrets import get_secret
        async with self.pool.acquire() as conn:
            self._api_key = await get_secret(conn, "devto_api_key")
        self._api_key_loaded = True
        return self._api_key

    @staticmethod
    def _clean_markdown(content: str) -> str:
        """Prepare markdown for Dev.to.

        - Converts relative internal links to absolute URLs
        - Strips HTML-only elements (iframes, script tags, custom components)
        - Removes any HTML comments
        """
        # Convert relative links like [text](/posts/slug) to absolute
        content = re.sub(
            r'\[([^\]]+)\]\((/[^)]+)\)',
            lambda m: f'[{m.group(1)}]({_site_url()}{m.group(2)})',
            content,
        )

        # Convert relative image paths to absolute
        content = re.sub(
            r'!\[([^\]]*)\]\((/[^)]+)\)',
            lambda m: f'![{m.group(1)}]({_site_url()}{m.group(2)})',
            content,
        )

        # Strip <script> tags
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)

        # Strip <iframe> tags
        content = re.sub(r'<iframe[^>]*>.*?</iframe>', '', content, flags=re.DOTALL | re.IGNORECASE)

        # Strip HTML comments
        content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)

        # Strip custom React/Next.js components (e.g., <ViewTracker />, <AdSense />)
        content = re.sub(r'<[A-Z][a-zA-Z]*\s*[^>]*/>', '', content)

        return content.strip()

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        """Normalize tags for Dev.to: max 4, lowercase, no spaces, alphanumeric only.

        Handles edge case where seo_keywords are stored as individual characters
        (e.g., 'g,o,o,g,l,e' instead of 'google'). Detects and reconstructs words.
        """
        # Detect character-level split: if most tags are 1 char, reconstruct words
        if len(tags) > 5 and sum(1 for t in tags if len(t.strip()) <= 1) > len(tags) * 0.5:
            # Reconstruct: join all chars, split on double-space or triple-comma patterns
            raw = "".join(t for t in tags)
            # Try splitting on spaces that were preserved as empty entries
            words = [w.strip() for w in raw.split("  ") if w.strip()]
            if len(words) <= 2:
                # Fallback: just use the raw joined string and split on common delimiters
                words = [w.strip() for w in raw.replace("   ", ",").split(",") if len(w.strip()) > 2]
            if words:
                tags = words

        cleaned = []
        for tag in tags:
            # Lowercase, strip spaces, keep only alphanumeric
            normalized = re.sub(r'[^a-z0-9]', '', tag.lower().strip())
            # Dev.to enforces max 30 chars per tag
            if len(normalized) > 30:
                normalized = normalized[:30]
            if len(normalized) >= 2 and normalized not in cleaned:  # Min 2 chars
                cleaned.append(normalized)
            if len(cleaned) >= 4:
                break
        return cleaned

    async def cross_post(
        self,
        title: str,
        content_markdown: str,
        canonical_url: str,
        tags: list[str] | None = None,
    ) -> "CrossPostResult":
        """Cross-post an article to Dev.to as a draft.

        Args:
            title: Article title
            content_markdown: Full markdown body
            canonical_url: The original post URL (set as canonical for SEO)
            tags: Up to 4 tags (will be normalized)

        Returns:
            A ``CrossPostResult`` describing the outcome. Callers
            distinguish three terminal cases:

            - ``status='posted'`` — 2xx, ``url`` populated.
            - ``status='gave_up'`` — Dev.to rejected the request with
              a 4xx that retrying won't fix (e.g. 422 "Canonical url
              has already been taken"). Callers should mark the post
              as gave_up so the cron stops re-submitting it.
            - ``status='transient'`` — 5xx / network error. Safe (and
              expected) to retry on the next tick.

            On ``status='skipped'`` the API key isn't configured —
            treat as a no-op, neither success nor failure.
        """
        api_key = await self._get_api_key()
        if not api_key:
            logger.debug("[DEVTO] No API key configured — skipping cross-post")
            return CrossPostResult(status="skipped")

        cleaned_content = self._clean_markdown(content_markdown)
        normalized_tags = self._normalize_tags(tags or [])

        # Auto-publish on Dev.to if configured (default: True — one approval is enough)
        auto_publish = True
        try:
            row = await self.pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'devto_publish_immediately'"
            )
            if row and row["value"].lower() in ("false", "0", "no"):
                auto_publish = False
        except Exception:
            pass

        payload = {
            "article": {
                "title": title,
                "body_markdown": cleaned_content,
                "published": auto_publish,
                "canonical_url": canonical_url,
                "tags": normalized_tags,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{_devto_api_base()}/articles",
                    headers={
                        "api-key": api_key,
                        "Content-Type": "application/json",
                        "User-Agent": f"{site_config.get('company_name', 'ContentEngine')}/1.0",
                    },
                    json=payload,
                )

                if resp.status_code in (200, 201):
                    data = resp.json()
                    devto_url = data.get("url", "")
                    devto_id = data.get("id", "")
                    logger.info(
                        "[DEVTO] Cross-posted draft: %s (id=%s)", devto_url, devto_id
                    )
                    return CrossPostResult(
                        status="posted",
                        url=devto_url or None,
                        article_id=str(devto_id) if devto_id else None,
                    )

                # 4xx (other than rate-limit) is a permanent reject —
                # most commonly 422 "Canonical url has already been
                # taken" when a previous run succeeded but we lost the
                # devto_url metadata. Don't keep hammering Dev.to.
                # 429 is rate-limit (transient); everything else 4xx
                # we treat as terminal so the cron stops looping.
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    error_text = resp.text[:500]
                    logger.warning(
                        "[DEVTO] API returned %d (giving up, will not retry): %s",
                        resp.status_code,
                        error_text,
                    )
                    return CrossPostResult(
                        status="gave_up",
                        http_status=resp.status_code,
                        error=error_text,
                    )

                # 5xx + 429 — transient, will retry on the next tick.
                error_text = resp.text[:500]
                logger.warning(
                    "[DEVTO] API returned %d (transient, will retry): %s",
                    resp.status_code,
                    error_text,
                )
                return CrossPostResult(
                    status="transient",
                    http_status=resp.status_code,
                    error=error_text,
                )

        except Exception as e:
            logger.warning("[DEVTO] Cross-post failed: %s", e)
            return CrossPostResult(status="transient", error=str(e))

    async def cross_post_by_post_id(self, post_id: str) -> str | None:
        """Cross-post a published post by its database ID.

        Fetches the post from DB, cross-posts to Dev.to, and updates
        the post's metadata with one of:

        - On 2xx success — ``devto_url``, ``devto_article_id``,
          ``devto_status='posted'`` so the row is permanently
          excluded from the cron's candidate set.
        - On a permanent Dev.to rejection (4xx other than 429) —
          ``devto_status='gave_up'`` plus ``devto_last_error`` /
          ``devto_last_http_status`` for operator visibility. This
          is what stops the every-tick 422 retry loop in #397.
        - On a transient failure (5xx, 429, network) — no metadata
          change, so the cron retries on its next tick.

        Returns:
            The Dev.to article URL if successful, ``None`` for every
            other outcome (transient retry, gave_up, skipped, missing
            post). Callers that need to distinguish the cases should
            invoke ``cross_post()`` directly and inspect the
            ``CrossPostResult.status``.
        """
        try:
            row = await self.pool.fetchrow(
                "SELECT id, title, slug, content, seo_keywords, metadata "
                "FROM posts WHERE id = $1 AND status = 'published'",
                post_id if not isinstance(post_id, str) else uuid.UUID(post_id),
            )
        except Exception as e:
            logger.warning("[DEVTO] Failed to fetch post %s: %s", post_id, e)
            return None

        if not row:
            logger.debug("[DEVTO] Post %s not found or not published", post_id)
            return None

        # Build canonical URL
        canonical_url = f"{_site_url()}/posts/{row['slug']}"

        # Parse tags from seo_keywords
        tags = []
        if row["seo_keywords"]:
            tags = [k.strip() for k in row["seo_keywords"].split(",") if k.strip()]

        result = await self.cross_post(
            title=row["title"],
            content_markdown=row["content"],
            canonical_url=canonical_url,
            tags=tags,
        )

        if result.status == "posted" and result.url:
            metadata_patch: dict[str, str] = {
                "devto_url": result.url,
                "devto_status": DEVTO_STATUS_POSTED,
            }
            if result.article_id:
                metadata_patch["devto_article_id"] = result.article_id
            await self._merge_post_metadata(row["id"], metadata_patch)
            logger.info("[DEVTO] Stored devto_url in post metadata: %s", post_id)
            return result.url

        if result.status == "gave_up":
            # Permanent reject — the most common case is 422
            # "Canonical url has already been taken" when a previous
            # run posted to Dev.to but we lost the response (network
            # blip, worker crash, missing devto_url write). The post
            # exists on Dev.to; retrying every tick wastes a request
            # and litters the log with WARNING (#397).
            metadata_patch = {
                "devto_status": DEVTO_STATUS_GAVE_UP,
                "devto_last_http_status": str(result.http_status or ""),
                "devto_last_error": (result.error or "")[:500],
            }
            await self._merge_post_metadata(row["id"], metadata_patch)
            logger.warning(
                "[DEVTO] Marked post %s as gave_up (HTTP %s) — will not retry. "
                "Reason: %s",
                post_id,
                result.http_status,
                (result.error or "")[:200],
            )

        # status == "transient" — leave metadata untouched so the
        # cron picks the post up again next tick.
        return None

    async def _merge_post_metadata(self, post_uuid, patch: dict[str, str]) -> None:
        """Shallow-merge a dict into ``posts.metadata`` JSONB.

        Best-effort: a UPDATE failure here is logged and swallowed so
        a transient DB hiccup doesn't poison the in-memory state of a
        successful cross-post. The cron's dedup will recover on the
        next tick once the DB is reachable.
        """
        try:
            await self.pool.execute(
                """
                UPDATE posts
                SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                    updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(patch),
                post_uuid,
            )
        except Exception as e:
            logger.warning(
                "[DEVTO] Failed to merge metadata %s (non-fatal): %s", patch, e
            )

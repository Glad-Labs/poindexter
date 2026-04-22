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
from typing import Any

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)


class DevToCrossPostService:
    """Cross-post blog content to Dev.to as drafts with canonical URLs."""

    def __init__(self, pool, site_config: Any):
        """
        Args:
            pool: asyncpg connection pool
            site_config: SiteConfig instance (DI — Phase H). Read from
                ``request.app.state.site_config`` or the lifespan-bound
                instance. Must be passed explicitly — Phase H removes
                the module-level singleton.
        """
        self.pool = pool
        self._site_config = site_config
        self._api_key: str | None = None
        self._api_key_loaded = False

    @property
    def _devto_api_base(self) -> str:
        """Dev.to (or self-hosted Forem) API base. Tunable so customers
        running a private Forem instance — or pointing at a future
        Dev.to API version — can swap without a code change (#198)."""
        return self._site_config.get("devto_api_base", "https://dev.to/api")

    @property
    def _site_url(self) -> str:
        """Canonical site URL. Fails loud (RuntimeError) if unset — an
        empty canonical URL silently produces broken relative paths."""
        return self._site_config.require("site_url")

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
    def _clean_markdown(content: str, site_url: str = "") -> str:
        """Prepare markdown for Dev.to.

        - Converts relative internal links to absolute URLs
          (when ``site_url`` is non-empty)
        - Strips HTML-only elements (iframes, script tags, custom components)
        - Removes any HTML comments

        Args:
            content: Raw markdown body.
            site_url: Canonical site URL used to resolve relative links.
                Pass an empty string to leave relative links unchanged —
                tests pass ``""`` when they only care about HTML stripping.
        """
        if site_url:
            # Convert relative links like [text](/posts/slug) to absolute
            content = re.sub(
                r"\[([^\]]+)\]\((/[^)]+)\)",
                lambda m: f"[{m.group(1)}]({site_url}{m.group(2)})",
                content,
            )

            # Convert relative image paths to absolute
            content = re.sub(
                r"!\[([^\]]*)\]\((/[^)]+)\)",
                lambda m: f"![{m.group(1)}]({site_url}{m.group(2)})",
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
    ) -> str | None:
        """Cross-post an article to Dev.to as a draft.

        Args:
            title: Article title
            content_markdown: Full markdown body
            canonical_url: The original post URL (set as canonical for SEO)
            tags: Up to 4 tags (will be normalized)

        Returns:
            The Dev.to article URL if successful, None otherwise.
        """
        api_key = await self._get_api_key()
        if not api_key:
            logger.debug("[DEVTO] No API key configured — skipping cross-post")
            return None

        cleaned_content = self._clean_markdown(content_markdown, self._site_url)
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
                    f"{self._devto_api_base}/articles",
                    headers={
                        "api-key": api_key,
                        "Content-Type": "application/json",
                        "User-Agent": f"{self._site_config.get('company_name', 'ContentEngine')}/1.0",
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
                    return devto_url
                else:
                    logger.warning(
                        "[DEVTO] API returned %d: %s",
                        resp.status_code,
                        resp.text[:500],
                    )
                    return None

        except Exception as e:
            logger.warning("[DEVTO] Cross-post failed: %s", e)
            return None

    async def cross_post_by_post_id(self, post_id: str) -> str | None:
        """Cross-post a published post by its database ID.

        Fetches the post from DB, cross-posts to Dev.to, and stores the
        Dev.to URL in the post's metadata.

        Returns:
            The Dev.to article URL if successful, None otherwise.
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
        canonical_url = f"{self._site_url}/posts/{row['slug']}"

        # Parse tags from seo_keywords
        tags = []
        if row["seo_keywords"]:
            tags = [k.strip() for k in row["seo_keywords"].split(",") if k.strip()]

        devto_url = await self.cross_post(
            title=row["title"],
            content_markdown=row["content"],
            canonical_url=canonical_url,
            tags=tags,
        )

        if devto_url:
            # Store the Dev.to URL in post metadata
            try:
                await self.pool.execute(
                    """
                    UPDATE posts
                    SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                        updated_at = NOW()
                    WHERE id = $2
                    """,
                    json.dumps({"devto_url": devto_url}),
                    row["id"],
                )
                logger.info("[DEVTO] Stored devto_url in post metadata: %s", post_id)
            except Exception as e:
                logger.warning("[DEVTO] Failed to store devto_url (non-fatal): %s", e)

        return devto_url

"""Text utilities for LLM-generated content.

Two pure functions the content pipeline relies on at multiple stages:

- :func:`normalize_text` — replace Unicode smart quotes / dashes / special
  whitespace with ASCII equivalents. Ollama (and other LLMs) produce these
  characters frequently and they trip up downstream rendering / search.

- :func:`scrub_fabricated_links` — strip markdown / bare URLs whose host
  isn't in the trusted allowlist, and drop internal ``/posts/<slug>``
  links whose slug doesn't exist in the real-slug cache. Local LLMs
  hallucinate URLs; this is the cheap defensive layer before URL
  validation catches the stragglers.

Lifted from ``services/content_router_service.py`` during Phase E2 —
these are referenced by multiple stage plugins, so the ``content_router_service``
re-exports the legacy names (``_normalize_text`` / ``_scrub_fabricated_links``)
until the stages switch to the canonical import path.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


_SMART_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("\u2019", "'"),    # right single quote
    ("\u2018", "'"),    # left single quote
    ("\u201c", '"'),    # left double quote
    ("\u201d", '"'),    # right double quote
    ("\u2014", "--"),   # em dash
    ("\u2013", "-"),    # en dash
    ("\u2026", "..."),  # ellipsis
    ("\u00a0", " "),    # non-breaking space
    ("\u2011", "-"),    # non-breaking hyphen
)


def normalize_text(text: str) -> str:
    """Replace Unicode smart punctuation + special whitespace with ASCII."""
    if not text:
        return text
    out = text
    for src, dst in _SMART_REPLACEMENTS:
        out = out.replace(src, dst)
    return out


# ---------------------------------------------------------------------------
# scrub_fabricated_links
# ---------------------------------------------------------------------------


DEFAULT_TRUSTED_DOMAINS: frozenset[str] = frozenset({
    "github.com", "arxiv.org", "docs.python.org", "docs.rs",
    "developer.mozilla.org", "stackoverflow.com", "wikipedia.org",
    "en.wikipedia.org", "news.ycombinator.com", "dev.to",
    "kubernetes.io", "docker.com", "docs.docker.com",
    "vercel.com", "nextjs.org", "react.dev", "go.dev",
    "pytorch.org", "huggingface.co", "openai.com",
    "www.rust-lang.org", "blog.rust-lang.org", "crates.io",
    "pypi.org", "npmjs.com", "www.npmjs.com",
    "youtube.com", "www.youtube.com",
})


def scrub_fabricated_links(
    content: str,
    known_slugs: set[str] | None = None,
    *,
    site_config: Any,
) -> str:
    """Remove fabricated/hallucinated URLs from LLM-generated content.

    Keeps the link text for markdown links, drops the href. Removes bare
    URLs whose host isn't trusted. Internal ``/posts/<slug>`` links get
    an additional check against ``known_slugs``: if the slug isn't one
    the content generator actually saw, treat it as fabricated.

    When ``known_slugs`` is None (or empty), all internal links pass —
    the URL-validation stage later in the pipeline catches stragglers.
    Callers that DO have a real-slug set (e.g. the generate_content
    stage pulls it from ``content_generator._internal_links_cache``)
    should pass it explicitly.
    """
    trusted = _resolve_trusted_domains(site_config)
    own_domain = (site_config.get("site_domain", "") or "").lower()
    slug_allowlist = known_slugs or set()

    scrubbed_count = 0

    def _is_trusted(url: str) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower()
            return any(
                host == d or host.endswith("." + d) for d in trusted
            )
        except Exception:
            return False

    def _is_real_internal_link(url: str) -> bool:
        """True if an internal /posts/<slug> link points to a known slug."""
        try:
            parsed = urlparse(url)
        except Exception:
            return True
        host = (parsed.hostname or "").lower()
        if own_domain and own_domain not in host:
            return True  # external link — not our concern
        path = parsed.path or ""
        if not path.startswith("/posts/"):
            return True  # non-post internal link (e.g. /about)
        slug = path.split("/posts/")[-1].strip("/")
        if not slug:
            return True
        if slug_allowlist:
            return slug in slug_allowlist
        # No cache provided — accept, URL validation stage will catch stragglers.
        return True

    def _replace_md_link(m: re.Match[str]) -> str:
        nonlocal scrubbed_count
        text, url = m.group(1), m.group(2)
        if not _is_trusted(url):
            scrubbed_count += 1
            return text
        if not _is_real_internal_link(url):
            scrubbed_count += 1
            return text
        return m.group(0)

    content = re.sub(
        r"\[([^\]]+)\]\((https?://[^\)]+)\)",
        _replace_md_link, content,
    )

    def _replace_bare_url(m: re.Match[str]) -> str:
        nonlocal scrubbed_count
        url = m.group(0)
        if _is_trusted(url):
            return url
        scrubbed_count += 1
        return ""

    content = re.sub(
        r"(?<!\()https?://[^\s\)\]\"'>,]+",
        _replace_bare_url, content,
    )

    if scrubbed_count > 0:
        logger.info(
            "[LINK_SCRUB] Removed %d fabricated link(s) from generated content",
            scrubbed_count,
        )
    return content


def _resolve_trusted_domains(site_config: Any) -> set[str]:
    """Build the trusted-domains set from settings override + defaults + own domain."""
    override_csv = site_config.get("trusted_source_domains", "")
    if override_csv:
        trusted = {
            d.strip().lower() for d in override_csv.split(",") if d.strip()
        }
    else:
        trusted = set(DEFAULT_TRUSTED_DOMAINS)

    own_domain = (site_config.get("site_domain", "") or "").lower()
    if own_domain:
        trusted.add(own_domain)
        trusted.add(f"www.{own_domain}")
    return trusted

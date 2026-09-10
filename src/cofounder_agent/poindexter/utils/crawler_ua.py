"""Shared builder for the project's outbound crawler ``User-Agent``.

Several jobs/services issue automated HEAD/GET probes against *external*
hosts (citation reachability, published-link health, broken-link repair).
Default library UAs (``python-httpx/x.y``) are fast-path-rejected by many
WAFs — Wikipedia 403s them outright — so every probe should send a
``browser-ish + compatible`` UA that maximizes reachability-signal accuracy.

The convention is the standard crawler form::

    Mozilla/5.0 (compatible; {product}/{version}; +{contact})

where ``{contact}`` is the operator's contact URL from
``app_settings.crawler_contact_url``. When that setting is unset/empty the
``; +{contact}`` portion is **omitted entirely** so OSS forks never ship the
source operator's contact URL as a baked-in default (the leak this guard
closes — see ``feedback_no_operator_info_to_public_repo``).

This is the single source of truth for that UA shape. Callers pass their own
``product`` token (``PoindexterCitationVerifier`` / ``PoindexterLinkCheck`` /
…) so traffic is attributable per-component while the framing stays uniform.
"""

from __future__ import annotations

from typing import Any


def build_crawler_ua(
    site_config: Any,
    *,
    product: str,
    version: str = "1.0",
) -> str:
    """Return the standard crawler ``User-Agent`` for outbound probes.

    Args:
        site_config: Any object with a ``.get(key, default)`` method (a
            ``SiteConfig``); ``None`` is tolerated and yields the
            contact-less form, so standalone/test callers without a
            configured instance still get a valid UA.
        product: Per-component token, e.g. ``"PoindexterLinkCheck"``.
        version: Product version segment (default ``"1.0"``).

    The ``; +<contact>`` suffix is appended only when
    ``app_settings.crawler_contact_url`` is set — keeping the source
    operator's contact URL out of OSS forks' defaults.
    """
    contact = ""
    if site_config is not None:
        try:
            contact = (site_config.get("crawler_contact_url", "") or "").strip()
        except Exception:  # noqa: BLE001 — observability, not a contract
            contact = ""
    if contact:
        return f"Mozilla/5.0 (compatible; {product}/{version}; +{contact})"
    return f"Mozilla/5.0 (compatible; {product}/{version})"

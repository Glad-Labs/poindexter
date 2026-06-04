"""Shared detector for CDN bot-challenge responses.

When a request to a Cloudflare-fronted host is answered with a *managed
challenge* / Bot-Fight-Mode interstitial (HTTP 403/429/503 carrying a
``cf-mitigated`` header, body "Just a moment…"), it means the **edge**
blocked our automated request — NOT that the resource is down or the link
is dead. A real browser solves the challenge and gets 200.

Several jobs/services hit Cloudflare-fronted hosts and must not mistake a
challenge for a content outage / broken link / hard failure:

- ``services/jobs/verify_published_posts.py`` — a challenge ≠ "post not
  reachable" (don't fire the critical page).
- ``services/jobs/check_published_links.py`` — an external link behind
  Cloudflare that challenges our HEAD is NOT a broken link.
- ``services/revalidation_service.py`` — a challenged ``/api/revalidate``
  POST is an edge-block (the cause of stale ISR content on 2026-06-04 when
  Bot Fight Mode was enabled), distinct from a genuine 4xx/5xx/timeout.

Keyed on the ``cf-mitigated`` header because it's the unambiguous
"Cloudflare mitigated this request" signal — a plain origin 4xx proxied
through Cloudflare does NOT carry it, so genuine failures still surface.
"""

from __future__ import annotations

from typing import Any

# Status codes Cloudflare uses for managed challenge / JS challenge / rate
# mitigation. The header is the real signal; the status guard just avoids
# treating an unrelated 2xx with a stray header as a challenge.
_CHALLENGE_STATUSES = (403, 429, 503)


def is_edge_challenge(resp: Any) -> bool:
    """True when ``resp`` is a CDN bot-challenge, not a real outage.

    Accepts anything with ``.status_code`` and ``.headers`` (httpx
    ``Response`` or a test double). ``.headers`` is read case-insensitively
    when it's an ``httpx.Headers``; a plain ``dict`` should use the
    lower-cased key ``"cf-mitigated"``.
    """
    if getattr(resp, "status_code", None) not in _CHALLENGE_STATUSES:
        return False
    headers = getattr(resp, "headers", None) or {}
    return bool(headers.get("cf-mitigated"))

"""Machine-readable deprecation headers (poindexter#752 item 4).

FastAPI's ``deprecated=True`` marks the *OpenAPI schema* — that's what Swagger
strikes through and what client codegen reads. This helper is the
complementary *runtime* signal: standard HTTP response headers a live client
can detect at call time to learn it's on a sunsetting endpoint.

Standards emitted:

* ``Deprecation`` — RFC 8594. Either the literal ``true`` (deprecated, no
  firm removal date) or an IMF-fixdate. We emit ``true``.
* ``Sunset`` — RFC 8594. An IMF-fixdate after which the endpoint may be
  removed. Optional.
* ``Link; rel="deprecation"`` — RFC 8594. URL of human migration docs.
  Optional.
* ``Warning: 299`` — RFC 7234 "miscellaneous persistent warning". Carries the
  free-text "deprecated; use X instead" message for humans and log scrapers.

Designed for machine consumers (operator memory ``feedback_design_for_llm_consumers``):
a client/agent can branch on ``Deprecation``/``Sunset`` without parsing prose.
"""

from __future__ import annotations

from datetime import datetime, timezone

# RFC 7231 §7.1.1.1 IMF-fixdate, e.g. "Sun, 06 Nov 1994 08:49:37 GMT".
_IMF_FIXDATE = "%a, %d %b %Y %H:%M:%S GMT"


def deprecation_headers(
    *,
    message: str,
    sunset: datetime | None = None,
    link: str | None = None,
) -> dict[str, str]:
    """Build RFC 8594 deprecation headers for a deprecated endpoint.

    Args:
        message: Human-readable "deprecated; use X instead" note. Emitted as
            an RFC 7234 ``Warning: 299`` header (safe to surface in logs).
        sunset: Optional datetime after which the endpoint may be removed.
            Converted to UTC and formatted as an RFC 8594 ``Sunset``
            IMF-fixdate. A naive datetime is assumed to already be UTC.
        link: Optional URL to migration docs, emitted as RFC 8594
            ``Link; rel="deprecation"``.

    Returns:
        A header mapping suitable for ``JSONResponse(headers=...)`` or
        ``response.headers.update(...)``.
    """
    headers: dict[str, str] = {
        "Deprecation": "true",
        # warn-code 299, warn-agent "-" (anonymous), quoted warn-text.
        "Warning": f'299 - "{message}"',
    }
    if sunset is not None:
        if sunset.tzinfo is None:
            sunset = sunset.replace(tzinfo=timezone.utc)
        headers["Sunset"] = sunset.astimezone(timezone.utc).strftime(_IMF_FIXDATE)
    if link is not None:
        headers["Link"] = f'<{link}>; rel="deprecation"'
    return headers

"""Machine-readable deprecation headers and retired-endpoint responses (poindexter#752 item 4).

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

A deprecated endpoint still works. A *retired* one does not, and
:func:`retired_endpoint_response` is its answer: ``410 Gone`` rather than a
bare 404, so a client that was never updated learns where the work moved
instead of guessing at a typo. The replacements ride in
``Link; rel="successor-version"`` (RFC 5829) and again in the JSON body.

Designed for machine consumers (operator memory ``feedback_design_for_llm_consumers``):
a client/agent can branch on ``Deprecation``/``Sunset`` without parsing prose.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from fastapi.responses import JSONResponse

# RFC 7231 §7.1.1.1 IMF-fixdate, e.g. "Sun, 06 Nov 1994 08:49:37 GMT".
_IMF_FIXDATE = "%a, %d %b %Y %H:%M:%S GMT"

# The error envelope's code for a retired endpoint. utils/exception_handlers.py
# maps an HTTPException(410) to the same code, so a 410 reads the same however
# it was produced.
RETIRED_ERROR_CODE = "GONE"


def _quoted_string(text: str) -> str:
    """Render ``text`` as an RFC 9110 quoted-string for a header value.

    Header values reach the wire as latin-1, and a character outside it raises
    inside the response constructor, so a stray em-dash in a message would turn
    the whole response into a 500. Refuse non-ASCII here instead, where the
    error names the text. Quotes and backslashes are escaped so a message that
    contains them cannot end the string early.
    """
    if not text.isascii():
        raise ValueError(f"header text must be ASCII: {text!r}")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
            Must be ASCII, because it travels in a header.
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
        "Warning": f"299 - {_quoted_string(message)}",
    }
    if sunset is not None:
        if sunset.tzinfo is None:
            sunset = sunset.replace(tzinfo=timezone.utc)
        headers["Sunset"] = sunset.astimezone(timezone.utc).strftime(_IMF_FIXDATE)
    if link is not None:
        headers["Link"] = f'<{link}>; rel="deprecation"'
    return headers


@dataclass(frozen=True)
class Successor:
    """One endpoint that took over part of a retired endpoint's job.

    Attributes:
        method: HTTP method of the successor, e.g. ``"POST"``.
        href: Where it lives, as a URI reference. A path is fine: RFC 8288
            resolves it against the request URL. Percent-encode anything taken
            from the request before it goes here.
        replaces: Which use of the retired endpoint this covers, e.g.
            ``"source=image_gen"``. Rides in the Link header's ``title``, so
            ASCII only.
        cli: The same action as a ``poindexter`` command, if there is one.
        mcp_tool: The same action as an MCP tool name, if there is one.
    """

    method: str
    href: str
    replaces: str
    cli: str | None = None
    mcp_tool: str | None = None


def retired_endpoint_response(
    *,
    message: str,
    successors: Sequence[Successor],
    request_id: str | None = None,
) -> JSONResponse:
    """Answer a call to a retired endpoint: ``410 Gone``, pointing at its successors.

    Retiring an endpoint means answering 410 with a pointer to the replacement,
    never a 404. A 404 reads as a typo to the client that made the call, and it
    leaves an agent with nothing to try next.

    The response carries:

    * ``Deprecation: true`` and ``Warning: 299`` with ``message``, as for any
      deprecated endpoint (:func:`deprecation_headers`).
    * ``Link``: one ``rel="successor-version"`` entry per successor, in order,
      titled with what it replaces.
    * ``X-Request-ID``, matching the body's ``request_id``.
    * A JSON body in the app's error envelope (``utils/exception_handlers.py``):
      ``error_code`` (:data:`RETIRED_ERROR_CODE`), ``message`` and
      ``request_id``. ``detail`` repeats the message for clients written
      against FastAPI's default error shape. The operator console reads
      ``detail`` first and the MCP server reads ``message`` first, so each
      finds it. Then ``retired: true`` and ``successors``, each with its method,
      href, what it replaces and its CLI / MCP spelling.

    The response is returned rather than raised as an ``HTTPException``: the
    app's HTTPException handler rebuilds the response without the exception's
    headers, which would drop the ``Link`` this exists to send.

    Args:
        message: What happened and where to go instead. ASCII, because it
            also rides in the ``Warning`` header.
        successors: At least one. An endpoint with nowhere to send its callers
            has not been retired under this contract, it has been deleted.
        request_id: The request's ID, as ``middleware.request_id`` assigns it.
            A fresh UUID when there is none, as the exception handlers do.

    Raises:
        ValueError: No successors, header text that is not ASCII, or an
            ``href`` that is not a bare URI reference.
    """
    if not successors:
        raise ValueError("a retired endpoint must name at least one successor")
    links = []
    for successor in successors:
        href = successor.href
        if not href.isascii() or any(c in href for c in ' <>"\r\n\t'):
            raise ValueError(f"successor href must be a percent-encoded URI reference: {href!r}")
        links.append(
            f'<{href}>; rel="successor-version"; title={_quoted_string(successor.replaces)}'
        )
    request_id = request_id or str(uuid.uuid4())
    headers = deprecation_headers(message=message)
    headers["Link"] = ", ".join(links)
    headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=410,
        content={
            "error_code": RETIRED_ERROR_CODE,
            "message": message,
            "request_id": request_id,
            "detail": message,
            "retired": True,
            "successors": [asdict(successor) for successor in successors],
        },
        headers=headers,
    )

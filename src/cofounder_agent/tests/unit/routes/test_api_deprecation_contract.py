"""Machine-readable deprecation contract (poindexter#752 item 4).

A route or query param can be deprecated two ways:

* **In prose** — ``[DEPRECATED]`` in the route summary, or "(deprecated …)"
  in a param description. Human-readable only.
* **Machine-readably** — FastAPI ``deprecated=True`` on the decorator / the
  ``Query(...)``, which sets the OpenAPI ``deprecated: true`` field.

Prose-only deprecation is invisible to Swagger's strike-through, to generated
API clients, and to LLM consumers reading the OpenAPI schema. These tests pin
the invariant that the two never drift apart, so a future deprecation can't
ship as a comment that no machine ever sees.

The audit (poindexter#752 item 4) found two prose-only surfaces:
``PUT /{task_id}/status/validated`` and the legacy ``skip`` alias on
``GET /api/posts``.

A route that no longer works at all is *retired*, not deprecated: it answers
410 Gone with a pointer to its replacement (``retired_endpoint_response``,
pinned below), and a summary that says so must be machine-deprecated too.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

import poindexter.routes as routes_pkg

pytestmark = pytest.mark.unit


def _all_api_routes() -> list[tuple[str, APIRoute]]:
    """Every ``APIRoute`` on every ``APIRouter`` under the ``routes`` package.

    Walks the package rather than a hand-maintained manifest so a newly added
    route module is covered automatically.
    """
    out: list[tuple[str, APIRoute]] = []
    for mod_info in pkgutil.iter_modules(routes_pkg.__path__, "poindexter.routes."):
        module = importlib.import_module(mod_info.name)
        for attr in vars(module).values():
            if isinstance(attr, APIRouter):
                for route in attr.routes:
                    if isinstance(route, APIRoute):
                        out.append((mod_info.name, route))
    return out


def _summary_marks_deprecated(route: APIRoute) -> bool:
    """True if the route advertises deprecation, or retirement, in its
    (human-only) summary. A retired route is the stronger case: it answers
    410 Gone (``utils.deprecation.retired_endpoint_response``)."""
    summary = (route.summary or "").lower()
    return "deprecated" in summary or "retired" in summary


def test_prose_deprecated_routes_are_machine_deprecated() -> None:
    """A route whose summary says DEPRECATED or RETIRED must also carry
    FastAPI ``deprecated=True`` so OpenAPI / Swagger / client codegen see it."""
    drifted = [
        f"{','.join(sorted(route.methods or []))} {route.path}  ({mod})"
        for mod, route in _all_api_routes()
        if _summary_marks_deprecated(route) and route.deprecated is not True
    ]
    assert not drifted, (
        "Routes deprecated in their summary but missing machine-readable "
        "`deprecated=True` (poindexter#752 item 4 — add `deprecated=True` to "
        "the route decorator so OpenAPI/Swagger/codegen see it, not just "
        "humans reading the summary):\n  " + "\n  ".join(sorted(drifted))
    )


def test_legacy_skip_alias_is_machine_deprecated() -> None:
    """The ``skip`` pagination alias on ``GET /api/posts`` is a deprecated
    fallback for ``offset`` — it must be ``Query(deprecated=True)`` so the
    OpenAPI param carries ``deprecated: true``."""
    from poindexter.routes.cms_routes import router as cms_router

    app = FastAPI()
    app.include_router(cms_router)
    schema = app.openapi()

    params = schema["paths"]["/api/posts"]["get"].get("parameters", [])
    skip = next((p for p in params if p.get("name") == "skip"), None)
    assert skip is not None, "`skip` param missing from /api/posts OpenAPI schema"
    assert skip.get("deprecated") is True, (
        "the legacy `skip` alias must be `Query(deprecated=True)` so OpenAPI "
        "marks it deprecated, not just its description text (poindexter#752 "
        "item 4)"
    )


def test_settings_page_per_page_are_machine_deprecated() -> None:
    """`page`/`per_page` on `GET /api/settings` are superseded by the
    canonical `offset`/`limit` pair (#635) but were never marked
    `deprecated=True` on the Query() declarations (poindexter#746 item 2),
    so OpenAPI/Swagger/codegen presented both systems as equally current."""
    from poindexter.routes.settings_routes import router as settings_router

    app = FastAPI()
    app.include_router(settings_router)
    schema = app.openapi()

    params = schema["paths"]["/api/settings"]["get"].get("parameters", [])
    for name in ("page", "per_page"):
        param = next((p for p in params if p.get("name") == name), None)
        assert param is not None, f"`{name}` param missing from /api/settings OpenAPI schema"
        assert param.get("deprecated") is True, (
            f"the legacy `{name}` alias must be `Query(deprecated=True)` so "
            "OpenAPI marks it deprecated, not just its description text "
            "(poindexter#746 item 2)"
        )


class TestDeprecationHeaders:
    """``utils.deprecation.deprecation_headers`` — the reusable RFC 8594 /
    RFC 7234 header mechanism (poindexter#752 item 4). Schema ``deprecated:
    true`` is for docs/codegen; these headers are the *runtime* signal a live
    HTTP client can detect on a sunsetting endpoint."""

    def test_minimal_emits_rfc8594_deprecation_true_and_warning(self) -> None:
        from poindexter.utils.deprecation import deprecation_headers

        headers = deprecation_headers(message="use PUT /x instead")
        # RFC 8594: bare deprecation (no firm date) is the literal "true".
        assert headers["Deprecation"] == "true"
        # RFC 7234 Warning: 299 ("miscellaneous persistent warning") carries
        # the human message; warn-agent "-" = anonymous.
        assert headers["Warning"].startswith("299 - ")
        assert "use PUT /x instead" in headers["Warning"]
        # Optional fields stay absent when not supplied.
        assert "Sunset" not in headers
        assert "Link" not in headers

    def test_sunset_formatted_as_imf_fixdate(self) -> None:
        from datetime import datetime, timezone

        from poindexter.utils.deprecation import deprecation_headers

        sunset = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        headers = deprecation_headers(message="x", sunset=sunset)
        # RFC 8594 Sunset is an IMF-fixdate (RFC 7231 §7.1.1.1) — assert it
        # round-trips rather than hardcoding a weekday literal.
        parsed = datetime.strptime(headers["Sunset"], "%a, %d %b %Y %H:%M:%S GMT")
        assert parsed.replace(tzinfo=timezone.utc) == sunset
        assert "31 Dec 2026" in headers["Sunset"]

    def test_sunset_is_converted_to_utc(self) -> None:
        from datetime import datetime, timedelta, timezone

        from poindexter.utils.deprecation import deprecation_headers

        # 2026-01-01 04:00 +05:00 is 2025-12-31 23:00 UTC — the header must
        # carry the UTC instant, not the local wall-clock time.
        sunset = datetime(2026, 1, 1, 4, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        headers = deprecation_headers(message="x", sunset=sunset)
        assert "31 Dec 2025 23:00:00 GMT" in headers["Sunset"]

    def test_link_uses_rel_deprecation(self) -> None:
        from poindexter.utils.deprecation import deprecation_headers

        headers = deprecation_headers(
            message="x", link="https://example.test/migrate"
        )
        assert headers["Link"] == '<https://example.test/migrate>; rel="deprecation"'

    def test_warning_text_is_an_escaped_quoted_string(self) -> None:
        """A quote in the message must not end the RFC 7234 warn-text early."""
        from poindexter.utils.deprecation import deprecation_headers

        headers = deprecation_headers(message='use "PUT /x" \\ not this')
        assert headers["Warning"] == '299 - "use \\"PUT /x\\" \\\\ not this"'

    def test_non_ascii_message_is_refused_up_front(self) -> None:
        """Header values go out as latin-1, so an em-dash would otherwise blow
        up inside the response constructor as a 500 that names nothing."""
        from poindexter.utils.deprecation import deprecation_headers

        with pytest.raises(ValueError, match="ASCII"):
            deprecation_headers(message="deprecated — use PUT /x")


class TestRetiredEndpointResponse:
    """``utils.deprecation.retired_endpoint_response``: the answer for a route
    that no longer works. 410 Gone, never a 404, with the replacement named in
    an RFC 8288 ``Link; rel="successor-version"`` and again in the body, so a
    client that was never updated learns where to go (the backcompat rule)."""

    @staticmethod
    def _successors():
        from poindexter.utils.deprecation import Successor

        return [
            Successor(
                method="POST",
                href="/api/things/1/new",
                replaces="mode=a",
                cli="poindexter things new 1",
                mcp_tool="new_thing",
            ),
            Successor(method="GET", href="/api/things/1/other", replaces="mode=b"),
        ]

    def test_answers_410_in_the_error_envelope_with_structured_successors(self) -> None:
        """The body is the app's error envelope (error_code / message /
        request_id, as utils/exception_handlers.py builds it), with ``detail``
        repeating the message for FastAPI-shaped clients, then the successors."""
        import json

        from poindexter.utils.deprecation import retired_endpoint_response

        resp = retired_endpoint_response(
            message="POST /api/things/{id} is retired",
            successors=self._successors(),
            request_id="req-123",
        )

        assert resp.status_code == 410
        assert resp.headers["X-Request-ID"] == "req-123"
        assert json.loads(resp.body) == {
            "error_code": "GONE",
            "message": "POST /api/things/{id} is retired",
            "request_id": "req-123",
            "detail": "POST /api/things/{id} is retired",
            "retired": True,
            "successors": [
                {
                    "method": "POST",
                    "href": "/api/things/1/new",
                    "replaces": "mode=a",
                    "cli": "poindexter things new 1",
                    "mcp_tool": "new_thing",
                },
                {
                    "method": "GET",
                    "href": "/api/things/1/other",
                    "replaces": "mode=b",
                    "cli": None,
                    "mcp_tool": None,
                },
            ],
        }

    def test_request_id_is_generated_when_none_is_given(self) -> None:
        """Outside the request-id middleware there is no ID to reuse. A fresh
        UUID goes in both the body and the header, as the exception handlers
        do."""
        import json
        import uuid

        from poindexter.utils.deprecation import retired_endpoint_response

        resp = retired_endpoint_response(message="gone", successors=self._successors())

        request_id = json.loads(resp.body)["request_id"]
        assert str(uuid.UUID(request_id)) == request_id
        assert resp.headers["X-Request-ID"] == request_id

    def test_error_code_matches_the_http_exception_handlers_410(self) -> None:
        """A retired endpoint's 410 and any HTTPException(410) must carry the
        same error_code, or a client branching on it sees two kinds of Gone."""
        from poindexter.utils.deprecation import RETIRED_ERROR_CODE
        from poindexter.utils.exception_handlers import _STATUS_TO_ERROR_CODE

        assert _STATUS_TO_ERROR_CODE[410] == RETIRED_ERROR_CODE

    def test_headers_carry_deprecation_warning_and_ordered_successor_links(self) -> None:
        from poindexter.utils.deprecation import retired_endpoint_response

        resp = retired_endpoint_response(message="gone", successors=self._successors())

        assert resp.headers["Deprecation"] == "true"
        assert resp.headers["Warning"] == '299 - "gone"'
        assert resp.headers["Link"] == (
            '</api/things/1/new>; rel="successor-version"; title="mode=a", '
            '</api/things/1/other>; rel="successor-version"; title="mode=b"'
        )

    def test_refuses_to_retire_without_a_successor(self) -> None:
        from poindexter.utils.deprecation import retired_endpoint_response

        with pytest.raises(ValueError, match="successor"):
            retired_endpoint_response(message="gone", successors=[])

    @pytest.mark.parametrize(
        "href",
        ["/api/a b/new", "/api/a\r\nX-Injected: 1/new", "/api/<a>/new", "/api/é/new"],
    )
    def test_refuses_an_href_that_is_not_percent_encoded(self, href) -> None:
        """Whatever came from the request must be encoded before it reaches the
        Link header, or a crafted value could break out of it."""
        from poindexter.utils.deprecation import Successor, retired_endpoint_response

        with pytest.raises(ValueError, match="percent-encoded"):
            retired_endpoint_response(
                message="gone",
                successors=[Successor(method="POST", href=href, replaces="x")],
            )

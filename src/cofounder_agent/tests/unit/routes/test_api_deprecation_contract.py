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
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

import routes as routes_pkg

pytestmark = pytest.mark.unit


def _all_api_routes() -> list[tuple[str, APIRoute]]:
    """Every ``APIRoute`` on every ``APIRouter`` under the ``routes`` package.

    Walks the package rather than a hand-maintained manifest so a newly added
    route module is covered automatically.
    """
    out: list[tuple[str, APIRoute]] = []
    for mod_info in pkgutil.iter_modules(routes_pkg.__path__, "routes."):
        module = importlib.import_module(mod_info.name)
        for attr in vars(module).values():
            if isinstance(attr, APIRouter):
                for route in attr.routes:
                    if isinstance(route, APIRoute):
                        out.append((mod_info.name, route))
    return out


def _summary_marks_deprecated(route: APIRoute) -> bool:
    """True if the route advertises deprecation in its (human-only) summary."""
    return "deprecated" in (route.summary or "").lower()


def test_prose_deprecated_routes_are_machine_deprecated() -> None:
    """A route whose summary says DEPRECATED must also carry FastAPI
    ``deprecated=True`` so OpenAPI / Swagger / client codegen see it."""
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
    from routes.cms_routes import router as cms_router

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


class TestDeprecationHeaders:
    """``utils.deprecation.deprecation_headers`` — the reusable RFC 8594 /
    RFC 7234 header mechanism (poindexter#752 item 4). Schema ``deprecated:
    true`` is for docs/codegen; these headers are the *runtime* signal a live
    HTTP client can detect on a sunsetting endpoint."""

    def test_minimal_emits_rfc8594_deprecation_true_and_warning(self) -> None:
        from utils.deprecation import deprecation_headers

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

        from utils.deprecation import deprecation_headers

        sunset = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        headers = deprecation_headers(message="x", sunset=sunset)
        # RFC 8594 Sunset is an IMF-fixdate (RFC 7231 §7.1.1.1) — assert it
        # round-trips rather than hardcoding a weekday literal.
        parsed = datetime.strptime(headers["Sunset"], "%a, %d %b %Y %H:%M:%S GMT")
        assert parsed.replace(tzinfo=timezone.utc) == sunset
        assert "31 Dec 2026" in headers["Sunset"]

    def test_sunset_is_converted_to_utc(self) -> None:
        from datetime import datetime, timedelta, timezone

        from utils.deprecation import deprecation_headers

        # 2026-01-01 04:00 +05:00 is 2025-12-31 23:00 UTC — the header must
        # carry the UTC instant, not the local wall-clock time.
        sunset = datetime(2026, 1, 1, 4, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        headers = deprecation_headers(message="x", sunset=sunset)
        assert "31 Dec 2025 23:00:00 GMT" in headers["Sunset"]

    def test_link_uses_rel_deprecation(self) -> None:
        from utils.deprecation import deprecation_headers

        headers = deprecation_headers(
            message="x", link="https://example.test/migrate"
        )
        assert headers["Link"] == '<https://example.test/migrate>; rel="deprecation"'

"""Tests for the OTel partial-match 500 shim (services/telemetry.py).

opentelemetry-instrumentation-fastapi 0.62b1's ``_get_route_details`` does an
unguarded ``route.path`` on PARTIAL matches; FastAPI ≥ 0.138's
``_IncludedRouter`` entries partial-match method-mismatched requests (CORS
preflight OPTIONS, unknown methods) and have no ``.path``. Unpatched, that
AttributeError escapes the OTel ASGI wrapper — which sits outside every user
middleware — and 500s the whole request before CORS can answer. Found live
2026-08-02 on the first preflight the worker ever received.

The canary test reproduces the upstream bug against a REAL FastAPI app so
that the day a dependency bump fixes it, the canary goes red — that is the
signal to delete ``_patch_otel_fastapi_partial_match_crash``, this file, and
nothing else.
"""

import pytest
from starlette.routing import Match

otel_fastapi = pytest.importorskip(
    "opentelemetry.instrumentation.fastapi",
    reason="opentelemetry instrumentation is an optional dependency",
)

from services.telemetry import _patch_otel_fastapi_partial_match_crash  # noqa: E402


def _pristine_original():
    """The unpatched upstream function, regardless of test-order patching."""
    fn = otel_fastapi._get_route_details
    return getattr(fn, "_px_original", fn)


def _real_app_scope(method: str):
    """An ASGI scope for a real FastAPI app whose route lives in a router.

    ``include_router`` is what creates the path-less ``_IncludedRouter``
    entries in ``app.routes`` on FastAPI ≥ 0.138 — the exact prod shape.
    """
    from fastapi import APIRouter, FastAPI

    app = FastAPI()
    router = APIRouter()

    @router.get("/api/probe")
    async def _probe():  # pragma: no cover — never dispatched
        return {}

    app.include_router(router)
    return {
        "type": "http",
        "app": app,
        "method": method,
        "path": "/api/probe",
        "root_path": "",
        "headers": [],
    }


class _PartialPathlessRoute:
    """Minimal stand-in for an _IncludedRouter: partial-matches, no .path."""

    def matches(self, scope):
        return Match.PARTIAL, {}


class _FullRoute:
    path = "/api/full"

    def matches(self, scope):
        return Match.FULL, {}


def _fake_scope(*routes, path="/api/anything"):
    app = type("App", (), {"routes": list(routes)})()
    return {"type": "http", "app": app, "method": "OPTIONS", "path": path}


class TestUpstreamCanary:
    def test_upstream_partial_branch_is_still_unguarded(self):
        # Reproduces the live 2026-08-02 preflight 500 against a REAL app.
        # WHEN THIS FAILS: the pinned opentelemetry-instrumentation-fastapi
        # has gained the missing PARTIAL-branch guard — delete
        # _patch_otel_fastapi_partial_match_crash and this whole file.
        with pytest.raises(AttributeError, match="path"):
            _pristine_original()(_real_app_scope("OPTIONS"))

    def test_upstream_full_match_still_works(self):
        # Sanity: the ordinary span-naming path resolves the route template.
        assert _pristine_original()(_real_app_scope("GET")) == "/api/probe"


class TestPatchedBehaviour:
    def test_partial_match_returns_raw_path_instead_of_raising(self):
        _patch_otel_fastapi_partial_match_crash()
        assert otel_fastapi._get_route_details(_real_app_scope("OPTIONS")) == "/api/probe"

    def test_full_match_still_returns_route_template(self):
        # The guard must delegate, not replace: healthy requests keep their
        # low-cardinality route template for span naming.
        _patch_otel_fastapi_partial_match_crash()
        assert otel_fastapi._get_route_details(_real_app_scope("GET")) == "/api/probe"

    def test_fake_route_shapes_are_covered_too(self):
        # Version-independent contract, decoupled from what the installed
        # FastAPI happens to put in app.routes.
        _patch_otel_fastapi_partial_match_crash()
        assert (
            otel_fastapi._get_route_details(
                _fake_scope(_PartialPathlessRoute(), path="/raw")
            )
            == "/raw"
        )
        assert (
            otel_fastapi._get_route_details(_fake_scope(_FullRoute())) == "/api/full"
        )

    def test_patch_is_idempotent(self):
        _patch_otel_fastapi_partial_match_crash()
        first = otel_fastapi._get_route_details
        _patch_otel_fastapi_partial_match_crash()
        assert otel_fastapi._get_route_details is first
        # And the pristine original stays reachable for the canary.
        assert getattr(first, "_px_original", None) is not None

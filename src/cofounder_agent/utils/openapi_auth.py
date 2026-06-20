"""Auth-gated OpenAPI catalog for production (poindexter#745).

FastAPI's built-in ``/api/openapi.json`` route is public. In production
``main.py`` sets ``openapi_url=None`` so the full surface isn't anonymously
enumerable — but per the ADR
(``docs/architecture/2026-06-20-api-response-contracts.md``) the machine-readable
catalog must stay reachable to authenticated API/LLM consumers, not be removed.

This helper registers an auth-gated equivalent of the built-in route in
production, and is a no-op otherwise (the built-in public route serves
non-prod). The route returns ``app.openapi()`` — which FastAPI generates
regardless of ``openapi_url`` — and is excluded from the schema it emits.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from middleware.api_token_auth import verify_api_token


def register_authed_openapi(app: FastAPI, *, is_production: bool) -> None:
    """Expose ``GET /api/openapi.json`` behind ``verify_api_token`` in production.

    No-op outside production, where FastAPI's built-in public ``openapi_url``
    already serves the spec. ``verify_api_token`` is wired as a route dependency
    so the ``TokenValidationMiddleware`` "let unmatched paths through to
    dependency-level auth" path still enforces a token here.
    """
    if not is_production:
        return

    async def authed_openapi(
        _token: str = Depends(verify_api_token),
    ) -> JSONResponse:
        return JSONResponse(app.openapi())

    app.add_api_route(
        "/api/openapi.json",
        authed_openapi,
        methods=["GET"],
        include_in_schema=False,
        name="authed_openapi",
    )

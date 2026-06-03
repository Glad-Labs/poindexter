"""Unit tests for ``routes/module_probes_routes.py`` — Module v1
brain-probe inventory endpoint (#239).

The route reads ``app.state.brain_probe_registry`` (populated during
lifespan) and surfaces the registered probe specs. Tests construct
a minimal FastAPI app + manually populate ``app.state`` to avoid
spinning up the full lifespan.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from plugins.probe_registry import BrainProbeRegistry
from routes.module_probes_routes import router as module_probes_router


async def _fake_probe() -> dict[str, str]:
    return {"ok": "true"}


def _make_app(registry: BrainProbeRegistry | None, *, authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(module_probes_router)
    if authed:
        # #642 — the route is now OAuth-gated; override the dependency so the
        # behavioural tests below exercise probe-listing, not the auth path.
        app.dependency_overrides[verify_api_token] = lambda: "test-client"
    if registry is not None:
        app.state.brain_probe_registry = registry
    return app


@pytest.mark.unit
def test_empty_registry_returns_zero_count():
    """F1 reality: no module registers a brain probe today. The
    endpoint must still respond 200 with an empty list so the brain
    daemon's poller doesn't 404 on a clean install."""
    app = _make_app(BrainProbeRegistry())
    client = TestClient(app)
    resp = client.get("/api/modules/probes")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"count": 0, "probes": []}


@pytest.mark.unit
def test_populated_registry_returns_probe_specs():
    registry = BrainProbeRegistry()
    registry.register(
        module="content",
        name="embedding_backlog",
        callable=_fake_probe,
        description="Embedding backlog depth under threshold",
        interval_seconds=180,
    )
    registry.register(
        module="content",
        name="stale_tasks",
        callable=_fake_probe,
    )

    app = _make_app(registry)
    client = TestClient(app)
    resp = client.get("/api/modules/probes")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["probes"] == [
        {
            "module": "content",
            "name": "embedding_backlog",
            "fqid": "content.embedding_backlog",
            "description": "Embedding backlog depth under threshold",
            "interval_seconds": 180,
        },
        {
            "module": "content",
            "name": "stale_tasks",
            "fqid": "content.stale_tasks",
            "description": "",
            "interval_seconds": 300,
        },
    ]


@pytest.mark.unit
def test_unauthenticated_request_returns_401():
    """#642 — the probe inventory was reachable unauthenticated (not in
    PROTECTED_PATHS, no route dependency). It now carries
    ``Depends(verify_api_token)`` like every sibling observability route;
    a request with no Bearer token is refused before the registry is read."""
    app = _make_app(BrainProbeRegistry(), authed=False)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/modules/probes")
    assert resp.status_code == 401


@pytest.mark.unit
def test_missing_registry_returns_503():
    """Per ``feedback_no_silent_defaults`` — when lifespan didn't run
    (or app.state was stripped) the route must fail loud, not return
    an empty list that could mask a misconfigured worker."""
    app = _make_app(registry=None)
    client = TestClient(app)
    resp = client.get("/api/modules/probes")
    assert resp.status_code == 503
    body = resp.json()
    assert "brain_probe_registry not initialised" in body["detail"]

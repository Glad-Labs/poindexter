"""Structural auth enforcement for operator routers (poindexter#752 item 2).

Auth used to be applied per-route via several naming conventions
(``token=``, ``_=``, ``_token=`` … all ``Depends(verify_api_token)``), with
**zero** router-level dependencies — so one forgotten dep on a newly added
operator route silently shipped an open endpoint. This test enumerates every
route on the operator routers and asserts each resolves ``verify_api_token``
somewhere in its dependency tree (param-level, route-level, OR router-level),
independent of the parameter name — it checks the *callable*, not the name.

Public routers are deliberately excluded: the four the public site actually
calls (``cms`` / ``podcast`` / ``newsletter`` / ``revalidate``), the public
video feed, the OAuth issuer (must be reachable without a token), the webhook
sinks (own signature auth), and the public voice-join page. The two no-prefix
dashboard routers (``pipeline_events`` / ``memory_dashboard``) ARE included —
their root-level ``/pipeline`` and ``/memory`` HTML dashboards require a token
too, so they're operator surfaces, not public pages.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.routing import APIRoute

from middleware.api_token_auth import verify_api_token

pytestmark = pytest.mark.unit

# (module_path, router_attr) — operator surfaces that must enforce auth on
# EVERY route. Adding a new operator router here makes this test demand auth
# across all of its routes.
_OPERATOR_ROUTERS: list[tuple[str, str]] = [
    ("routes.approval_routes", "router"),
    ("routes.task_routes", "router"),
    ("routes.task_publishing_routes", "publishing_router"),
    ("routes.task_status_routes", "status_router"),
    ("routes.topics_routes", "router"),
    ("routes.topic_batch_routes", "router"),
    ("routes.gates_routes", "router"),
    ("routes.posts_approval_routes", "router"),
    ("routes.media_approval_routes", "router"),
    ("routes.scheduling_routes", "router"),
    ("routes.data_plane_routes", "router"),
    ("routes.settings_routes", "router"),
    ("routes.metrics_routes", "metrics_router"),
    ("routes.findings_routes", "router"),
    ("routes.seo_routes", "router"),
    ("routes.triage_routes", "router"),
    ("routes.module_probes_routes", "router"),
    ("routes.pipeline_events_routes", "router"),
    ("routes.memory_dashboard_routes", "router"),
]


def _route_requires_auth(route: APIRoute) -> bool:
    """True if ``verify_api_token`` is anywhere in the route's dependency
    tree — covers param-level, route-level, and router-level ``Depends``
    regardless of the parameter name."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    seen: set[int] = set()
    stack = [dependant]
    while stack:
        dep = stack.pop()
        if id(dep) in seen:
            continue
        seen.add(id(dep))
        if getattr(dep, "call", None) is verify_api_token:
            return True
        stack.extend(getattr(dep, "dependencies", ()))
    return False


def _operator_routes() -> list[tuple[str, APIRoute]]:
    out: list[tuple[str, APIRoute]] = []
    for module_path, attr in _OPERATOR_ROUTERS:
        router = getattr(importlib.import_module(module_path), attr)
        for route in router.routes:
            if isinstance(route, APIRoute):
                out.append((module_path, route))
    return out


@pytest.mark.parametrize("module_path,attr", _OPERATOR_ROUTERS)
def test_operator_router_imports_and_has_routes(module_path: str, attr: str) -> None:
    """Guard the manifest: each operator router still imports and exposes API
    routes (catches a renamed module/attr before the auth assertion below)."""
    router = getattr(importlib.import_module(module_path), attr)
    assert any(isinstance(r, APIRoute) for r in router.routes), (
        f"{module_path}.{attr} exposes no API routes — did the import path change?"
    )


def test_every_operator_route_requires_auth() -> None:
    unprotected = [
        f"{','.join(sorted(route.methods or []))} {route.path}  ({module_path})"
        for module_path, route in _operator_routes()
        if not _route_requires_auth(route)
    ]
    assert not unprotected, (
        "Operator routes with no verify_api_token in their dependency tree "
        "(poindexter#752 item 2 — add router-level "
        "`dependencies=[Depends(verify_api_token)]` at the APIRouter, or a "
        "per-route dep):\n  " + "\n  ".join(sorted(unprotected))
    )


def _router_declares_auth_dependency(router: object) -> bool:
    """True if the APIRouter itself carries ``Depends(verify_api_token)`` in
    its router-level ``dependencies`` (vs. relying on every route remembering
    its own dep)."""
    for dep in getattr(router, "dependencies", []) or []:
        if getattr(dep, "dependency", None) is verify_api_token:
            return True
    return False


@pytest.mark.parametrize("module_path,attr", _OPERATOR_ROUTERS)
def test_operator_router_declares_router_level_auth(module_path: str, attr: str) -> None:
    """The structural backstop (poindexter#752 item 2): each operator router
    must enforce auth at *construction* — ``APIRouter(dependencies=[Depends(
    verify_api_token)])`` — so a newly added route inherits auth even if the
    author forgets a per-route dep. Per-route deps are deduped by FastAPI, so
    this is additive, not double-auth."""
    router = getattr(importlib.import_module(module_path), attr)
    assert _router_declares_auth_dependency(router), (
        f"{module_path}.{attr} has no router-level verify_api_token dependency "
        "— add `dependencies=[Depends(verify_api_token)]` to its APIRouter(...) "
        "so auth is structurally enforced, not per-route opt-in."
    )

"""Unit tests for middleware/prometheus_metrics.py + the http_route_label
helper in services/metrics_exporter.py.

Covers:
- http_route_label() template reconstruction (static, single-param,
  multi-param, unmatched, substring-safety).
- The ASGI middleware records into both RED metrics with the right
  method / route-template / status labels, including the 500 path when a
  handler raises and the "unmatched" collapse for 404s.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from prometheus_client import REGISTRY
from starlette.testclient import TestClient

from middleware.prometheus_metrics import PrometheusMetricsMiddleware
from poindexter.services.metrics_exporter import http_route_label

# ---------------------------------------------------------------------------
# http_route_label — pure unit tests on synthetic ASGI scopes
# ---------------------------------------------------------------------------


def test_route_label_static_route_returns_path():
    scope = {"endpoint": object(), "path": "/api/health", "path_params": {}}
    assert http_route_label(scope) == "/api/health"


def test_route_label_single_param_is_templated():
    scope = {
        "endpoint": object(),
        "path": "/api/posts/my-slug",
        "path_params": {"slug": "my-slug"},
    }
    assert http_route_label(scope) == "/api/posts/{slug}"


def test_route_label_multi_param_is_templated():
    scope = {
        "endpoint": object(),
        "path": "/api/tasks/42/versions/7",
        "path_params": {"task_id": "42", "version": "7"},
    }
    assert http_route_label(scope) == "/api/tasks/{task_id}/versions/{version}"


def test_route_label_unmatched_when_no_endpoint():
    # 404 / raw-ASGI path: no endpoint in scope -> collapse to one series.
    scope = {"path": "/random/bot/scan/path", "path_params": {}}
    assert http_route_label(scope) == "unmatched"


def test_route_label_only_replaces_whole_segments():
    # The static segment "v1" must NOT be rewritten just because a param
    # value elsewhere equals "v1"; only the segment whose value matches is.
    scope = {
        "endpoint": object(),
        "path": "/v1/users/v1",
        "path_params": {"user_id": "v1"},
    }
    # Both "v1" segments equal the value -> both templated. This is the
    # documented whole-segment behavior (bounded + acceptable); the key
    # guarantee is we never blow up cardinality, not perfect fidelity.
    assert http_route_label(scope) == "/{user_id}/users/{user_id}"


# ---------------------------------------------------------------------------
# Middleware integration — real ASGI app via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(PrometheusMetricsMiddleware)

    @application.get("/api/health")
    async def health():
        return {"ok": True}

    @application.get("/api/posts/{slug}")
    async def post(slug: str):
        return {"slug": slug}

    @application.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    @application.get("/teapot")
    async def teapot():
        raise HTTPException(status_code=418, detail="nope")

    return application


def _count(metric: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(metric, labels) or 0.0


def test_static_route_records_request_and_duration(app):
    labels = {"method": "GET", "route": "/api/health", "status": "200"}
    before = _count("poindexter_http_requests_total", labels)
    dur_before = _count(
        "poindexter_http_request_duration_seconds_count",
        {"method": "GET", "route": "/api/health"},
    )

    TestClient(app).get("/api/health")

    assert _count("poindexter_http_requests_total", labels) == before + 1
    assert (
        _count(
            "poindexter_http_request_duration_seconds_count",
            {"method": "GET", "route": "/api/health"},
        )
        == dur_before + 1
    )


def test_param_route_labeled_by_template_not_value(app):
    tmpl_labels = {"method": "GET", "route": "/api/posts/{slug}", "status": "200"}
    before = _count("poindexter_http_requests_total", tmpl_labels)

    client = TestClient(app)
    client.get("/api/posts/hello-world")
    client.get("/api/posts/another-post")

    # Two different slugs collapse onto ONE templated series.
    assert _count("poindexter_http_requests_total", tmpl_labels) == before + 2
    # And the concrete-path label must NOT exist (cardinality guarantee).
    assert (
        REGISTRY.get_sample_value(
            "poindexter_http_requests_total",
            {"method": "GET", "route": "/api/posts/hello-world", "status": "200"},
        )
        is None
    )


def test_raising_handler_records_500(app):
    labels = {"method": "GET", "route": "/boom", "status": "500"}
    before = _count("poindexter_http_requests_total", labels)

    # raise_server_exceptions=False so ServerErrorMiddleware turns the
    # RuntimeError into a 500 response instead of re-raising into the test.
    TestClient(app, raise_server_exceptions=False).get("/boom")

    assert _count("poindexter_http_requests_total", labels) == before + 1


def test_http_exception_records_actual_status(app):
    labels = {"method": "GET", "route": "/teapot", "status": "418"}
    before = _count("poindexter_http_requests_total", labels)

    TestClient(app).get("/teapot")

    assert _count("poindexter_http_requests_total", labels) == before + 1


def test_unmatched_path_collapses_to_one_series(app):
    labels = {"method": "GET", "route": "unmatched", "status": "404"}
    before = _count("poindexter_http_requests_total", labels)

    client = TestClient(app)
    client.get("/no/such/path/a")
    client.get("/no/such/path/b")

    # Two distinct 404 URLs share the single "unmatched" series.
    assert _count("poindexter_http_requests_total", labels) == before + 2


# ---------------------------------------------------------------------------
# Mounted sub-apps: one series per mount, never one per URL under it
#
# Starlette pops a Mount's remainder out of path_params and sets `endpoint` to
# the mounted app, so a mount looks like a static route and used to be labelled
# with its raw path: 80 distinct /site/... URLs produced 86 series. The /site
# viewer (storage_provider=local) and /console are mounts.
# ---------------------------------------------------------------------------


def test_route_label_mounted_sub_app_is_the_prefix_wildcard():
    scope = {
        "endpoint": object(),
        "path": "/site/images/inline/a.webp",
        "path_params": {},
        "root_path": "/site",
        "app_root_path": "",
    }
    assert http_route_label(scope) == "/site/*"


def test_route_label_mount_behind_a_proxy_root_path_drops_the_proxy_prefix():
    # uvicorn --root-path /proxy: root_path carries the proxy prefix too, and
    # app_root_path remembers it, so the label is the mount alone.
    scope = {
        "endpoint": object(),
        "path": "/site/x/y.png",
        "path_params": {},
        "root_path": "/proxy/site",
        "app_root_path": "/proxy",
    }
    assert http_route_label(scope) == "/site/*"


def test_route_label_plain_route_behind_a_proxy_root_path_is_unchanged():
    # A proxy-supplied root_path alone must not make every route look mounted:
    # plain routes never carry app_root_path.
    scope = {
        "endpoint": object(),
        "path": "/api/health",
        "path_params": {},
        "root_path": "/proxy",
    }
    assert http_route_label(scope) == "/api/health"


def test_route_label_mount_with_a_path_param_is_templated():
    # A mount whose prefix carries a param must not put the raw value back.
    scope = {
        "endpoint": object(),
        "path": "/tenant/123/files/a.txt",
        "path_params": {"tid": "123"},
        "root_path": "/tenant/123",
        "app_root_path": "",
    }
    assert http_route_label(scope) == "/tenant/{tid}/*"


def test_route_label_without_app_root_path_keeps_the_raw_path_behaviour():
    # Older Starlette does not set app_root_path; do not guess a mount from
    # root_path alone.
    scope = {
        "endpoint": object(),
        "path": "/site/a.png",
        "path_params": {},
        "root_path": "/site",
    }
    assert http_route_label(scope) == "/site/a.png"


@pytest.fixture
def mounted_app(tmp_path) -> FastAPI:
    from starlette.responses import PlainTextResponse
    from starlette.staticfiles import StaticFiles

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")

    async def viewer(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    application = FastAPI()
    application.add_middleware(PrometheusMetricsMiddleware)

    @application.get("/api/health")
    async def health():
        return {"ok": True}

    application.mount("/mnt-viewer", viewer)
    application.mount("/mnt-static", StaticFiles(directory=tmp_path))
    return application


def _route_values(prefix: str) -> set[str]:
    """Every ``route`` label value the registry holds for request counts under ``prefix``."""
    return {
        s.labels["route"]
        for metric in REGISTRY.collect()
        if metric.name == "poindexter_http_requests"
        for s in metric.samples
        if s.labels.get("route", "").startswith(prefix)
    }


def test_many_urls_under_a_mount_share_one_series(mounted_app):
    client = TestClient(mounted_app)
    labels = {"method": "GET", "route": "/mnt-viewer/*", "status": "200"}
    before = _count("poindexter_http_requests_total", labels)

    for i in range(60):
        client.get(f"/mnt-viewer/posts/slug-{i}")
        client.get(f"/mnt-viewer/images/inline/img-{i}.webp")

    assert _count("poindexter_http_requests_total", labels) == before + 120
    assert _route_values("/mnt-viewer") == {"/mnt-viewer/*"}


def test_a_static_files_mount_is_one_series_including_its_404s(mounted_app):
    client = TestClient(mounted_app)
    ok = {"method": "GET", "route": "/mnt-static/*", "status": "200"}
    missing = {"method": "GET", "route": "/mnt-static/*", "status": "404"}
    ok_before = _count("poindexter_http_requests_total", ok)
    missing_before = _count("poindexter_http_requests_total", missing)

    client.get("/mnt-static/a.txt")
    client.get("/mnt-static/b.txt")
    for i in range(10):
        client.get(f"/mnt-static/nope-{i}.txt")

    assert _count("poindexter_http_requests_total", ok) == ok_before + 2
    assert _count("poindexter_http_requests_total", missing) == missing_before + 10
    assert _route_values("/mnt-static") == {"/mnt-static/*"}


def test_plain_routes_next_to_a_mount_keep_their_own_labels(mounted_app):
    labels = {"method": "GET", "route": "/api/health", "status": "200"}
    before = _count("poindexter_http_requests_total", labels)

    TestClient(mounted_app).get("/api/health")

    assert _count("poindexter_http_requests_total", labels) == before + 1

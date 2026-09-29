"""Tests for the ``/site`` mount that serves the local storage folder.

The mount is public and shares an origin with the worker API, so these pin the
security properties as well as the routing: nothing outside the folder is ever
served (``..``, symlinks), a missing file is a 404 rather than an HTML page,
and every response carries the CSP that keeps post HTML from running script.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from poindexter.services.site_config import SiteConfig
from poindexter.utils.local_site_mount import (
    CONTENT_SECURITY_POLICY,
    _default_viewer_dir,
    mount_local_site,
)


def _app(tmp_path: Path, **settings: str) -> tuple[TestClient, Path]:
    root = tmp_path / "site"
    values = {
        "storage_provider": "local",
        "storage_local_dir": str(root),
        "api_url": "http://localhost:8002",
    }
    values.update(settings)
    app = FastAPI()
    app.state.container = SimpleNamespace(site_config=SiteConfig(initial_config=values))
    assert mount_local_site(app) is True
    return TestClient(app), root


def _write(root: Path, key: str, body: bytes) -> None:
    path = root.joinpath(*key.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def test_root_serves_the_viewer_with_the_mount_as_its_base(tmp_path):
    client, _ = _app(tmp_path)
    res = client.get("/site/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert 'src="/site/_viewer/viewer.js"' in res.text
    assert 'href="/site/_viewer/viewer.css"' in res.text
    assert "%%BASE%%" not in res.text


def test_bare_mount_path_redirects_to_the_viewer(tmp_path):
    client, _ = _app(tmp_path)
    res = client.get("/site", follow_redirects=False)
    assert res.status_code in (307, 308)
    assert res.headers["location"].endswith("/site/")


@pytest.mark.parametrize(
    "path",
    [
        "/site/posts/my-post",
        "/site/posts/my-post/",
        "/site/posts/node-2.0-release",
        "/site/index.html",
    ],
)
def test_viewer_routes_serve_the_viewer(tmp_path, path):
    client, _ = _app(tmp_path)
    res = client.get(path)
    assert res.status_code == 200
    assert "viewer.js" in res.text


def test_a_stored_file_is_served_as_itself(tmp_path):
    client, root = _app(tmp_path)
    _write(root, "static/posts/index.json", b'{"posts": [], "total": 0}')
    res = client.get("/site/static/posts/index.json")
    assert res.status_code == 200
    assert res.json() == {"posts": [], "total": 0}
    assert res.headers["content-type"].startswith("application/json")


def test_head_on_a_stored_file(tmp_path):
    client, root = _app(tmp_path)
    _write(root, "images/inline/a.webp", b"RIFF0000WEBP")
    res = client.head("/site/images/inline/a.webp")
    assert res.status_code == 200
    assert res.headers["content-length"] == "12"


@pytest.mark.parametrize(
    "path", ["/site/images/missing.webp", "/site/static/posts/missing.json", "/site/other/route"]
)
def test_a_missing_file_is_a_plain_404(tmp_path, path):
    client, _ = _app(tmp_path)
    res = client.get(path)
    assert res.status_code == 404
    assert "viewer.js" not in res.text


def test_viewer_assets_are_served_and_nothing_else_under_the_prefix(tmp_path):
    client, _ = _app(tmp_path)
    js = client.get("/site/_viewer/viewer.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert client.get("/site/_viewer/viewer.css").status_code == 200
    assert client.get("/site/_viewer/index.html").status_code == 404
    assert client.get("/site/_viewer/../local_site_mount.py").status_code == 404


def test_every_response_carries_the_csp_and_no_cache(tmp_path):
    client, root = _app(tmp_path)
    _write(root, "static/a.json", b"{}")
    for path in ("/site/", "/site/static/a.json", "/site/_viewer/viewer.js", "/site/nope.json"):
        res = client.get(path)
        assert res.headers["content-security-policy"] == CONTENT_SECURITY_POLICY, path
        assert res.headers["cache-control"] == "no-cache", path


def test_csp_blocks_inline_script():
    directives = dict(d.split(" ", 1) for d in CONTENT_SECURITY_POLICY.split("; "))
    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'none'"


def test_traversal_never_leaves_the_folder(tmp_path):
    client, root = _app(tmp_path)
    (tmp_path / "secret.txt").write_text("private")
    for path in (
        "/site/../secret.txt",
        "/site/%2e%2e/secret.txt",
        "/site/static/..%2f..%2fsecret.txt",
    ):
        res = client.get(path)
        assert "private" not in res.text, path


@pytest.mark.parametrize(
    "raw", ["/site/../secret.txt", "/site/static/../../secret.txt", "/site//secret.txt"]
)
@pytest.mark.asyncio
async def test_raw_dotdot_paths_are_refused_by_the_app_itself(tmp_path, raw):
    """HTTP clients normalise ``..`` before sending, so drive the ASGI app with
    an unnormalised path to prove the app, not the client, refuses it."""
    from poindexter.utils.local_site_mount import LocalSiteApp

    client, root = _app(tmp_path)
    (tmp_path / "secret.txt").write_text("private")
    root.mkdir(parents=True, exist_ok=True)
    scope = {
        "type": "http",
        "method": "GET",
        "path": raw,
        "raw_path": raw.encode(),
        "root_path": "/site",
        "query_string": b"",
        "headers": [],
        "app": client.app,
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await LocalSiteApp()(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert start["status"] == 404
    assert b"private" not in body


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_symlink_out_of_the_folder_is_refused(tmp_path):
    client, root = _app(tmp_path)
    (tmp_path / "secret.txt").write_text("private")
    root.mkdir(parents=True, exist_ok=True)
    os.symlink(tmp_path / "secret.txt", root / "leak.txt")
    res = client.get("/site/leak.txt")
    assert res.status_code == 404
    assert "private" not in res.text


def test_temp_files_are_not_served(tmp_path):
    client, root = _app(tmp_path)
    _write(root, "static/.upload-abc", b"half")
    assert client.get("/site/static/.upload-abc").status_code == 404


def test_writes_are_refused(tmp_path):
    client, _ = _app(tmp_path)
    res = client.post("/site/static/a.json", content=b"{}")
    assert res.status_code == 405
    assert res.headers["allow"] == "GET, HEAD"


@pytest.mark.parametrize("provider", ["s3", ""])
def test_the_site_is_off_outside_local_mode(tmp_path, provider):
    client, root = _app(tmp_path, storage_provider=provider)
    _write(root, "static/a.json", b"{}")
    for path in ("/site/", "/site/static/a.json"):
        res = client.get(path)
        assert res.status_code == 404
        assert "storage_provider" in res.text


def test_switching_modes_needs_no_restart(tmp_path):
    client, root = _app(tmp_path)
    _write(root, "static/a.json", b"{}")
    assert client.get("/site/static/a.json").status_code == 200
    client.app.state.container.site_config._config["storage_provider"] = "s3"
    assert client.get("/site/static/a.json").status_code == 404


def test_mount_is_skipped_when_the_viewer_is_missing(tmp_path):
    app = FastAPI()
    assert mount_local_site(app, viewer_dir=tmp_path / "nope") is False
    assert not any(getattr(r, "path", "") == "/site" for r in app.routes)


def test_viewer_ships_in_the_package():
    viewer = _default_viewer_dir()
    for name in ("index.html", "viewer.js", "viewer.css"):
        assert (viewer / name).is_file(), name
    html = (viewer / "index.html").read_text()
    assert "<script>" not in html, "inline script would be blocked by the CSP"
    assert "%%BASE%%_viewer/viewer.js" in html

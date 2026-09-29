"""Serve the local-storage folder, with a viewer, at ``/site/``.

With ``storage_provider=local`` every upload lands in a folder instead of a
bucket (``poindexter.services.local_site``). This module is how a person sees
it: the worker mounts :class:`LocalSiteApp` at ``/site`` and a browser pointed
at ``{api_url}/site/`` gets a small reader for the published posts. Nothing has
to be deployed and no account is needed.

What the app answers, per request:

* **Mode off** (``storage_provider`` is not ``local``): 404 with a one-line
  reason. The mount is registered unconditionally at import time, before
  settings load, so it checks the mode on every request. Switching modes needs
  no restart.
* ``/site/_viewer/viewer.js`` and ``viewer.css``: the viewer's own assets,
  shipped in the package (``poindexter/local_site_viewer/``) rather than
  written into the folder, so an upgrade takes effect immediately.
* A file in the folder (``/site/static/posts/index.json``,
  ``/site/images/inline/<hex>.webp``, …): that file. The path goes through the
  store's key validation and must resolve (symlinks included) to a regular
  file inside the folder.
* The viewer's routes, ``/site/`` and ``/site/posts/<slug>``: the viewer page.
  Post links in the static export are ``{site_url}/posts/<slug>``, and in local
  mode ``site_url`` is this mount, so internal links work. Anything else that
  isn't a file is a 404.

Every response carries ``Cache-Control: no-cache`` (a new post shows up on the
next reload) and a Content-Security-Policy. The CSP matters because this page
shares an origin with the worker API and, on Pro installs, the operator
console, while post HTML comes from an LLM working from web research. Scripts
may only load from this origin and inline handlers are blocked, so markup
injected into a post cannot run. The viewer also strips active content before
inserting a post, as a second layer.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.types import Receive, Scope, Send

from poindexter.services.local_object_store import UnsafeObjectKey
from poindexter.services.local_site import (
    LOCAL_SITE_MOUNT_PATH,
    is_local,
    local_object_store,
)
from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

VIEWER_ASSET_PREFIX = "_viewer/"
_VIEWER_ASSETS = frozenset({"viewer.js", "viewer.css"})
_BASE_PLACEHOLDER = "%%BASE%%"

# The viewer's own routes. Anything else that isn't a file in the folder is a
# 404, so a missing image never comes back as an HTML page.
_VIEWER_ROUTE_RE = re.compile(r"^(?:|index\.html|posts/?|posts/[^/]+/?)$")

# img/media allow http: and https: because a post's images can live on another
# origin (a stock-photo host, or the same worker reached by another hostname).
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https: http:",
        "media-src 'self' https: http:",
        "connect-src 'self'",
        "font-src 'self' data:",
        "object-src 'none'",
        "frame-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ],
)

_HEADERS = {
    "Cache-Control": "no-cache",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}

_MODE_OFF_MESSAGE = (
    "The local site is off: storage_provider is not 'local'. Turn it on with: "
    "poindexter settings set storage_provider local"
)


def _default_viewer_dir() -> Path:
    """``poindexter/local_site_viewer``, beside this package's ``utils/``."""
    return Path(__file__).resolve().parents[1] / "local_site_viewer"


def _route_path(scope: Scope) -> str:
    """The request path below the mount, without its leading slash.

    ``Mount`` passes the full path and extends ``root_path`` by the matched
    prefix, so the part this app routes on is the remainder.
    """
    path: str = scope["path"]
    root = scope.get("root_path", "") or ""
    if root and path.startswith(root):
        path = path[len(root) :]
    return path.lstrip("/")


def _contained_file(root: Path, candidate: Path) -> Path | None:
    """``candidate`` resolved, if it is a regular file inside ``root``.

    Resolving follows symlinks, so a link inside the folder that points outside
    it is refused like a ``..`` path.
    """
    try:
        real_root = os.path.realpath(root)
        real = os.path.realpath(candidate)
    except OSError:
        return None
    try:
        if os.path.commonpath([real_root, real]) != real_root:
            return None
    except ValueError:  # different drives on Windows
        return None
    return Path(real) if os.path.isfile(real) else None


def _not_found() -> Response:
    return PlainTextResponse("Not Found", status_code=404, headers=_HEADERS)


class LocalSiteApp:
    """ASGI app for ``/site``: the local folder plus the viewer."""

    def __init__(self, viewer_dir: Path | None = None) -> None:
        self._viewer_dir = viewer_dir or _default_viewer_dir()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Mount also routes websockets here; the site has none to offer.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1000})
            return
        response = await self._respond(scope)
        await response(scope, receive, send)

    async def _respond(self, scope: Scope) -> Response:
        if scope["method"] not in ("GET", "HEAD"):
            return PlainTextResponse(
                "Method Not Allowed",
                status_code=405,
                headers={"Allow": "GET, HEAD", **_HEADERS},
            )

        site_config = _site_config_for(scope)
        if not is_local(site_config):
            return PlainTextResponse(_MODE_OFF_MESSAGE, status_code=404, headers=_HEADERS)

        rel = _route_path(scope)
        if rel.startswith(VIEWER_ASSET_PREFIX):
            name = rel[len(VIEWER_ASSET_PREFIX) :]
            if name in _VIEWER_ASSETS:
                return FileResponse(self._viewer_dir / name, headers=_HEADERS)
            return _not_found()

        if rel and not rel.endswith("/"):
            store = local_object_store(site_config)
            try:
                candidate = store.path_for(rel)
            except UnsafeObjectKey:
                return _not_found()
            found = await asyncio.to_thread(_contained_file, store.root, candidate)
            if found is not None:
                return FileResponse(found, headers=_HEADERS)

        if _VIEWER_ROUTE_RE.match(rel):
            return self._viewer_page(scope)
        return _not_found()

    def _viewer_page(self, scope: Scope) -> Response:
        base = (scope.get("root_path", "") or "").rstrip("/") + "/"
        page = (self._viewer_dir / "index.html").read_text(encoding="utf-8")
        page = page.replace(_BASE_PLACEHOLDER, html.escape(base, quote=True))
        return HTMLResponse(page, headers=_HEADERS)


def _site_config_for(scope: Scope) -> Any:
    """The worker's live ``SiteConfig``, found the way route handlers find it."""
    from poindexter.utils.route_utils import get_site_config_dependency

    return get_site_config_dependency(Request(scope))


def mount_local_site(app: Any, *, viewer_dir: Path | None = None) -> bool:
    """Mount :class:`LocalSiteApp` at ``/site``.

    Always mounted when the viewer ships (it does, in every install): the app
    checks ``storage_provider`` per request. Call it after the API routers are
    registered so it can never shadow an ``/api`` path.

    Returns ``True`` when mounted, ``False`` when the viewer directory is
    missing (a broken install; logged).
    """
    viewer_dir = viewer_dir or _default_viewer_dir()
    if not (viewer_dir / "index.html").is_file():
        logger.warning(
            "[STARTUP] Local-site viewer missing at %s: %s/ not mounted",
            viewer_dir,
            LOCAL_SITE_MOUNT_PATH,
        )
        return False
    app.mount(
        LOCAL_SITE_MOUNT_PATH,
        LocalSiteApp(viewer_dir=viewer_dir),
        name="local_site",
    )
    logger.info(
        "[STARTUP] Local site mounted at %s/ (serves files when storage_provider=local)",
        LOCAL_SITE_MOUNT_PATH,
    )
    return True

"""docker_utils — tiny helpers for brain code running inside a container.

Keeping these out of health_probes / brain_daemon so they can be reused
by other brain modules (seed_loader, future phase-2 orchestration work)
without creating circular imports.
"""

from __future__ import annotations

import os
from pathlib import Path


def _detect_docker() -> bool:
    """True when running inside a Docker container.

    Uses two signals: the IN_DOCKER env var (set in docker-compose.local.yml)
    and /.dockerenv, which Docker creates on every container. Either alone
    is sufficient; checking both makes host-side pytest and tools that
    don't set the env var still behave correctly.
    """
    if os.getenv("IN_DOCKER", "").lower() in ("1", "true", "yes"):
        return True
    return Path("/.dockerenv").exists()


IN_DOCKER = _detect_docker()


def localize_url(url: str) -> str:
    """Rewrite host-side URLs so they work from inside a container.

    `app_settings` holds canonical URLs that work from the host (e.g.
    `http://localhost:3001` for Gitea). Inside a container, `localhost`
    loops back to the container itself, which isn't what's wanted —
    every port the host exposes is reachable at `host.docker.internal`
    instead, on the same port number.

    This function rewrites only the hostname; the port is preserved so
    the translation stays in sync with whatever docker-compose decides
    to expose.

    No-op when not running inside Docker.
    """
    if not url or not IN_DOCKER:
        return url
    return (
        url.replace("://localhost:", "://host.docker.internal:")
           .replace("://127.0.0.1:", "://host.docker.internal:")
    )

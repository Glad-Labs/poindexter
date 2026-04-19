"""docker_utils — tiny helpers for brain code running inside a container.

Keeping these out of health_probes / brain_daemon so they can be reused
by other brain modules (seed_loader, future phase-2 orchestration work)
without creating circular imports.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


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


async def resolve_url(
    pool_or_conn,
    *app_setting_keys: str,
    default: str = "",
    env_var: str | None = None,
) -> str:
    """Resolve a service URL with DB-first config + in-container translation.

    Precedence:
      1. If ``env_var`` is passed and that env var is non-empty, its value
         wins verbatim (no localize — env is assumed container-aware already).
      2. First non-empty value from the given ``app_setting_keys`` (tried in
         order), with ``localize_url()`` applied.
      3. ``default``, with ``localize_url()`` applied.

    Accepts either an ``asyncpg.Pool`` or an ``asyncpg.Connection``; both
    expose ``fetchval``.

    This is the single pattern that was duplicated across
    ``scripts/auto-embed.py``, ``brain/health_probes.py``, and
    ``brain/business_probes.py`` before 2026-04-18. Concentrating it here
    makes the "brain reports everything DOWN because localhost resolves
    to itself" class of bug impossible in new code — every caller just
    asks for the resolved URL and trusts it.
    """
    if env_var:
        env_val = os.getenv(env_var)
        if env_val:
            return env_val
    try:
        for key in app_setting_keys:
            val = await pool_or_conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
            if val:
                return localize_url(val)
    except Exception as e:
        logger.warning(
            "resolve_url: app_settings lookup failed for keys %s: %s — using default",
            app_setting_keys, e,
        )
    return localize_url(default)

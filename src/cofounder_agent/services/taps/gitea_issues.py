"""GiteaIssuesTap — ingest issues from the configured Gitea repo.

Replaces Phase 3 of ``scripts/auto-embed.py``. Talks to the Gitea HTTP
API, pages through all issues, yields one Document per issue.

## Config

Credentials + URL live in ``app_settings`` under the standard keys.
This Tap resolves them via :func:`brain.docker_utils.resolve_url` so
``localhost:3001`` in the DB works both from the host (native path)
and from inside a container (translated to ``host.docker.internal``).

- ``gitea_url`` (app_settings) — base URL; default ``http://localhost:3001``
- ``gitea_user`` (app_settings)
- ``gitea_password`` (app_settings, ``is_secret=true``)
- ``gitea_repo`` (app_settings) — ``owner/repo`` form; default
  ``gladlabs/glad-labs-codebase``

Per-install overrides in ``plugin.tap.gitea_issues`` JSON blob:
- ``page_size`` (default ``50``) — how many issues per API call
- ``max_pages`` (default ``100``) — hard safety cap on iteration
- ``state`` (default ``"all"``) — ``open`` / ``closed`` / ``all``
- ``body_limit`` (default ``3000``) — truncate issue bodies beyond this
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.tap import Document

logger = logging.getLogger(__name__)


async def _load_gitea_config(pool: Any) -> dict[str, str]:
    """Resolve Gitea creds + URL from app_settings with localize_url.

    ``brain.docker_utils`` and ``plugins.secrets`` imports are lazy so
    this Tap can load under packaging layouts that don't expose ``brain``
    on sys.path (e.g. entry_points discovery from outside the worker
    container). If the helpers aren't available we fall back to a direct
    SELECT without localize — acceptable because the only practical
    difference is the container/host URL rewrite, and installs running
    Gitea on localhost from the host path don't need it.
    """
    # Lazy imports — keep the module importable in minimal environments.
    try:
        from brain.docker_utils import resolve_url
    except ImportError:
        resolve_url = None  # type: ignore[assignment]
    try:
        from plugins.secrets import get_secret
    except ImportError:
        get_secret = None  # type: ignore[assignment]

    async with pool.acquire() as conn:
        if resolve_url is not None:
            url = await resolve_url(conn, "gitea_url", default="http://localhost:3001")
        else:
            raw = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'gitea_url'"
            )
            url = raw or "http://localhost:3001"

        user = await conn.fetchval("SELECT value FROM app_settings WHERE key = 'gitea_user'")

        pw = None
        if get_secret is not None:
            try:
                pw = await get_secret(conn, "gitea_password")
            except Exception:
                pw = None
        if not pw:
            pw = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'gitea_password'"
            )

        repo = await conn.fetchval("SELECT value FROM app_settings WHERE key = 'gitea_repo'")

    return {
        "url": url,
        "user": user or "",
        "password": pw or "",
        "repo": repo or "gladlabs/glad-labs-codebase",
    }


class GiteaIssuesTap:
    """One Document per Gitea issue."""

    name = "gitea_issues"
    interval_seconds = 3600

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        page_size = int(config.get("page_size", 50))
        max_pages = int(config.get("max_pages", 100))
        state = str(config.get("state", "all"))
        body_limit = int(config.get("body_limit", 3000))

        creds = await _load_gitea_config(pool)
        if not creds["password"]:
            logger.warning("GiteaIssuesTap: no gitea_password configured; skipping")
            return

        auth = (creds["user"], creds["password"])
        base = creds["url"].rstrip("/")
        repo = creds["repo"]

        all_issues: list[dict] = []
        page = 1
        async with httpx.AsyncClient(timeout=15) as http:
            while page <= max_pages:
                try:
                    resp = await http.get(
                        f"{base}/api/v1/repos/{repo}/issues",
                        params={"state": state, "limit": page_size, "page": page},
                        auth=auth,
                    )
                except Exception as e:
                    logger.warning("GiteaIssuesTap: HTTP error on page %d: %s", page, e)
                    break
                if resp.status_code != 200:
                    logger.warning(
                        "GiteaIssuesTap: %s returned %d", base, resp.status_code
                    )
                    break
                batch = resp.json()
                if not batch:
                    break
                all_issues.extend(batch)
                if len(batch) < page_size:
                    break
                page += 1

        logger.info("GiteaIssuesTap: %d issues from %s/%s", len(all_issues), base, repo)

        for issue in all_issues:
            num = issue["number"]
            title = issue.get("title", "")
            body = (issue.get("body") or "")[:body_limit]
            issue_state = issue.get("state", "")
            labels = ", ".join(lbl["name"] for lbl in issue.get("labels", []) or [])
            text = (
                f"Issue #{num}: {title}\n"
                f"State: {issue_state}\n"
                f"Labels: {labels}\n\n{body}"
            )
            yield Document(
                source_id=str(num),
                source_table="issues",
                text=text,
                metadata={
                    "title": title,
                    "state": issue_state,
                    "labels": labels,
                    "issue_number": num,
                },
                writer="auto-embed",
            )

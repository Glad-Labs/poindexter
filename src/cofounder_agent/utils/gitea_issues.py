"""Utility for creating deduplicated Gitea issues from background jobs.

Shared across services/jobs/* — any Job that wants to surface a finding
(broken links, stale approvals, backup failures, etc.) can call
``create_gitea_issue`` and get dedup-by-title-prefix for free.

Credentials come from site_config (app_settings), not env vars, per
Matt's DB-first policy.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from services.site_config import site_config

logger = logging.getLogger(__name__)


async def create_gitea_issue(title: str, body: str, *, timeout: float = 10.0) -> bool:
    """Create a deduplicated Gitea issue for tracking discovered problems.

    Dedup strategy: compare the title prefix (everything before the first
    colon, or the first 30 chars) against currently-open issues. If an open
    issue with the same prefix exists, skip — we don't want a dozen
    "links: N broken URLs" issues piling up between manual triage passes.

    Returns True iff a NEW issue was filed. Returns False if:
      - No Gitea password is configured (opt-out)
      - A matching open issue already exists (dedup hit)
      - The Gitea API call fails (logged at debug level — the finding
        itself still gets returned by the caller, so losing the issue
        filing is recoverable)
    """
    gitea_url = site_config.get("gitea_url", "http://localhost:3001")
    gitea_user = site_config.get("gitea_user", "gladlabs")
    gitea_pass = site_config.get("gitea_password", "")
    gitea_repo = site_config.get("gitea_repo", "gladlabs/glad-labs-codebase")

    if not gitea_pass:
        logger.debug("[GITEA] No password configured — skipping issue creation")
        return False

    try:
        auth = base64.b64encode(f"{gitea_user}:{gitea_pass}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            title_prefix = _prefix(title)
            search = await client.get(
                f"{gitea_url}/api/v1/repos/{gitea_repo}/issues",
                headers=headers,
                params={"state": "open", "limit": 10},
            )
            if search.status_code == 200:
                for issue in search.json():
                    if _prefix(issue.get("title", "")) == title_prefix:
                        logger.debug(
                            "[GITEA] Issue already exists: #%s %s",
                            issue.get("number"), title_prefix,
                        )
                        return False

            resp = await client.post(
                f"{gitea_url}/api/v1/repos/{gitea_repo}/issues",
                headers=headers,
                json={"title": title, "body": body},
            )
            if resp.status_code < 300:
                issue: Any = resp.json()
                logger.info(
                    "[GITEA] Created issue #%s: %s",
                    issue.get("number"), title[:50],
                )
                return True
            logger.warning(
                "[GITEA] Issue creation returned %s: %s",
                resp.status_code, resp.text[:200],
            )
            return False
    except Exception as e:
        # Don't fail the caller — issue filing is reporting, not critical.
        logger.debug("[GITEA] Issue creation failed: %s", e)
        return False


def _prefix(title: str) -> str:
    """Dedup key: everything before the first colon, or first 30 chars."""
    if ":" in title:
        return title.split(":", 1)[0].strip()
    return title[:30]

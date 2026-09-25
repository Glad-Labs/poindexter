"""GitHub REST errors for brain probes: the status, the rate limit, GitHub's words.

The PR staleness probe and the branch-drift canary both call the GitHub API
with the ``gh_token`` secret. On any non-200 they raise :class:`GitHubAPIError`.
That lets each probe's failure handler tell the two kinds of failure apart:

* a credential problem only the operator can fix: 401, a 403 that is not a
  rate limit, or a 404 for a private repo the token cannot see (GitHub answers
  404, not 403, to hide that the repo exists);
* a transient one that retrying fixes: 5xx, or a rate limit.

It also lets the handler quote GitHub's own message instead of an HTML error
page.

Stdlib only; the brain image ships ``poindexter/brain/`` alone.
"""

from __future__ import annotations

import json
from typing import Any

# How the operator replaces the token; quoted by every credential page.
TOKEN_FIX = "`poindexter settings set gh_token <token> --secret`"


def is_rate_limited(response: Any) -> bool:
    """True for GitHub's primary or secondary rate-limit answer.

    A 429 always is one. A 403 is one when ``x-ratelimit-remaining`` is 0
    (primary limit) or the body mentions a rate limit (secondary limit).
    Every other 403 is a permission problem.
    """
    status = getattr(response, "status_code", None)
    if status == 429:
        return True
    if status != 403:
        return False
    headers = getattr(response, "headers", None) or {}
    try:
        remaining = headers.get("x-ratelimit-remaining")
    except Exception:  # noqa: BLE001 — a header object we can't read is "no header"
        remaining = None
    if remaining is not None and str(remaining).strip() == "0":
        return True
    return "rate limit" in (getattr(response, "text", "") or "").lower()


def github_message(body: str) -> str:
    """The ``message`` field of a GitHub error body, else a compact excerpt."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])[:200]
    return " ".join((body or "").split())[:200] or "no body"


class GitHubAPIError(RuntimeError):
    """GitHub answered with a non-200; keeps what the operator page needs."""

    def __init__(
        self,
        endpoint: str,
        status_code: int,
        body: str,
        *,
        rate_limited: bool = False,
        ref: str = "",
    ) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        self.body = body
        self.rate_limited = rate_limited
        self.ref = ref
        super().__init__(f"GitHub /{endpoint} returned {status_code}: {body[:200]}")

    @classmethod
    def from_response(cls, endpoint: str, response: Any, *, ref: str = "") -> GitHubAPIError:
        """Build the error from an httpx-shaped response."""
        return cls(
            endpoint,
            int(response.status_code),
            getattr(response, "text", "") or "",
            rate_limited=is_rate_limited(response),
            ref=ref,
        )

    @property
    def transient(self) -> bool:
        """A failure a retry can fix: a rate limit or a GitHub-side 5xx."""
        return self.rate_limited or self.status_code >= 500

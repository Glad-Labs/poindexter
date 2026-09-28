"""Shared fakes for the newsletter signup-path tests.

``_``-prefixed so pytest never collects it; imported by
``test_newsletter_audience.py``, ``test_newsletter_signup_canary.py`` and the
job tests under ``tests/unit/services/jobs/``.
"""

from __future__ import annotations

#: A Resend segment id shaped like the real ones (UUID).
SEGMENT = "33333333-aaaa-4bbb-8ccc-dddddddddddd"


class FakeSiteConfig:
    """The slice of SiteConfig the signup path reads."""

    def __init__(self, values: dict[str, str] | None = None, api_key: str = "re_test"):
        self.values = {"resend_audience_id": SEGMENT, **(values or {})}
        self.api_key = api_key
        self.secret_reads: list[str] = []

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self.values.get(key)
        return int(raw) if raw not in (None, "") else default

    def get_float(self, key: str, default: float = 0.0) -> float:
        raw = self.values.get(key)
        return float(raw) if raw not in (None, "") else default

    async def get_secret(self, key: str, default: str = "") -> str:
        self.secret_reads.append(key)
        return self.api_key if key == "resend_api_key" else default

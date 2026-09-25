"""Unit tests — branch_drift_probe silent-failure surfacing.

Gap-site burn-down (best-effort-failure-visibility, batch 2).
``_is_deduped`` reads ``alert_dedup_state`` to decide whether to suppress
a repeat branch-drift alert. A swallowed read used to log at
``logger.debug`` and return ``False`` (not deduped) — so a broken read
silently bypassed dedup and let the alert re-fire, with no operator
signal to explain the re-pages. It now WARNs (the brain tree can't
``emit_finding``, so ``warning`` is the bar).

Mirrors the caplog assertion pattern in
``test_alert_dispatcher_silent_failures.py``.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "src" / "cofounder_agent" / "poindexter" / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from poindexter.brain import branch_drift_probe as bdp  # noqa: E402

_LOGGER = "poindexter.brain.branch_drift_probe"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDedupLookupFailureVisible:
    """A failed dedup lookup must WARN — it fails open (alert re-fires),
    so without a signal a broken read looks like a genuine repeat storm."""

    async def test_is_deduped_logs_warning_on_db_failure(self, caplog):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("dedup read exploded"))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = await bdp._is_deduped(
                pool,
                fingerprint="branch_drift_x",
                now_utc=datetime.now(timezone.utc),
                dedup_hours=6,
            )

        # Fail-open sentinel — the alert is allowed to fire.
        assert result is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "dedup lookup failure must be visible at WARNING"
        combined = " ".join(r.getMessage() for r in warnings)
        assert "dedup" in combined.lower()
        assert "dedup read exploded" in combined


# ---------------------------------------------------------------------------
# The canary's own failure page (brain/failure_episode.py). The failure is
# in audit_log either way; what must never be silent is that the operator
# was NOT told.
# ---------------------------------------------------------------------------

_EPISODE_LOGGER = "brain.failure_episode"


class _NotFound:
    status_code = 404
    text = '{"message":"Not Found"}'
    headers: dict[str, str] = {}

    def json(self):
        return {"message": "Not Found"}


class _GitHub404:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return _NotFound()


def _pool(*, execute_error: Exception | None = None) -> MagicMock:
    settings = {bdp.REPO_KEY: "Test-Org/test-repo", "gh_token": "test-token"}
    pool = MagicMock()

    async def _fetchval(query, *args):
        if "FROM app_settings" in query and "updated_at" not in query:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        if "FROM app_settings" in query and args[0] in settings:
            return {"value": settings[args[0]], "is_secret": False}
        return None

    async def _execute(query, *args):
        if execute_error is not None and "brain_knowledge" in query:
            raise execute_error
        return "OK"

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock(side_effect=_execute)
    return pool


async def _run_404(pool, notify_fn):
    bdp._reset_state()
    return await bdp.run_branch_drift_probe(
        pool,
        now_fn=lambda: datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc),
        notify_fn=notify_fn,
        http_client_factory=lambda: _GitHub404(),
        git_runner=lambda _git_dir: ("a" * 40, "main"),
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestFailurePageVisibility:
    async def test_a_page_that_cannot_be_sent_warns(self, caplog):
        def _down(**_kwargs):
            raise RuntimeError("notifier down")

        with caplog.at_level(logging.WARNING, logger=_EPISODE_LOGGER):
            summary = await _run_404(_pool(), _down)

        assert summary["paged"] is False
        text = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
        assert "NOT told" in text
        assert "notifier down" in text

    async def test_a_page_that_reached_no_channel_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger=_EPISODE_LOGGER):
            summary = await _run_404(
                _pool(), lambda **_: {"discord": "discord send failed: timeout"},
            )

        assert summary["paged"] is False
        assert "reached no channel" in caplog.text

    async def test_an_episode_that_cannot_be_saved_warns(self, caplog):
        """Without the row, the next pass cannot tell it already paged."""
        with caplog.at_level(logging.WARNING, logger=_EPISODE_LOGGER):
            summary = await _run_404(
                _pool(execute_error=RuntimeError("brain_knowledge write exploded")),
                lambda **_: {"discord": "discord"},
            )

        assert summary["paged"] is True
        assert "brain_knowledge write exploded" in caplog.text
        assert "may page again" in caplog.text

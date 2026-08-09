"""Tests for the ``schedule_post`` MCP tool — approve a task AND give it a
publish slot in one call.

The contract under test is the one documented in
``docs/architecture/publish-scheduling.md``:

* the request never carries ``auto_publish`` (the route 400s on the pair);
* success is ``scheduled_for`` in the response body, NOT HTTP 200 — a
  staging or slot failure commits the approve and still returns 200 with a
  null slot, which is the silent-failure bug #3121 fixed;
* an error body's ``message`` (from ``utils/exception_handlers``) must not
  be mistaken for the 200-path "approved but not scheduled" ``message``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import server  # noqa: E402

# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_post_posts_publish_at_without_auto_publish():
    """The route rejects ``auto_publish`` + ``publish_at`` together (400), so
    the key must be absent — not merely false-y by accident."""
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server,
            "_api",
            AsyncMock(
                return_value={
                    "status": "approved",
                    "scheduled_for": "2026-08-09T09:00:00+00:00",
                }
            ),
        ) as api,
    ):
        await server.schedule_post("abc1", "tomorrow 9am")

    api.assert_awaited_once_with(
        "POST",
        "/api/tasks/full/approve",
        {"approved": True, "publish_at": "tomorrow 9am"},
    )
    _method, _path, payload = api.await_args.args
    assert "auto_publish" not in payload


@pytest.mark.asyncio
async def test_schedule_post_resolves_short_task_id_prefix():
    """Operators paste 8-char prefixes from the dashboards / `tasks list`."""
    with (
        patch.object(
            server, "_resolve_task_id", AsyncMock(return_value="full-uuid"),
        ) as resolve,
        patch.object(
            server,
            "_api",
            AsyncMock(return_value={"scheduled_for": "2026-08-09T09:00:00+00:00"}),
        ) as api,
    ):
        await server.schedule_post("abc1", "2026-08-09T09:00:00+00:00")

    resolve.assert_awaited_once_with("abc1")
    assert api.await_args.args[1] == "/api/tasks/full-uuid/approve"


# ---------------------------------------------------------------------------
# Success — report the COMMITTED slot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_post_reports_committed_slot_not_requested_one():
    """``scheduled_for`` is what the server committed; the requested time is
    not echoed back on its own authority."""
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server,
            "_api",
            AsyncMock(
                return_value={
                    "status": "approved",
                    # Deliberately different from the requested spec.
                    "scheduled_for": "2026-08-09T13:00:00+00:00",
                }
            ),
        ),
    ):
        out = await server.schedule_post("abc1", "tomorrow 9am")

    assert "2026-08-09T13:00:00+00:00" in out
    assert "tomorrow 9am" not in out
    assert "Scheduled" in out


# ---------------------------------------------------------------------------
# HTTP 200 + no slot — the silent-failure case (#3121)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_post_surfaces_not_scheduled_message_on_200():
    """A staging/slot failure returns 200 with ``scheduled_for: null`` and a
    ``message``. The tool must relay it, never report success."""
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server,
            "_api",
            AsyncMock(
                return_value={
                    "status": "approved",
                    "scheduled_for": None,
                    "message": "Approved, but NOT scheduled — post staging failed",
                }
            ),
        ),
    ):
        out = await server.schedule_post("abc1", "tomorrow 9am")

    assert out == "Approved, but NOT scheduled — post staging failed"
    # The route's message is a complete sentence — no doubled lede.
    assert out.count("NOT scheduled") == 1
    assert not out.startswith("Scheduled")


@pytest.mark.asyncio
async def test_schedule_post_200_without_slot_or_message_still_warns():
    """Neither a slot nor a reason (e.g. staging succeeded but returned no
    post_id) must still not read as a scheduled post."""
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api", AsyncMock(return_value={"status": "approved"}),
        ),
    ):
        out = await server.schedule_post("abc1", "tomorrow 9am")

    assert "NOT scheduled" in out
    assert not out.startswith("Scheduled")


# ---------------------------------------------------------------------------
# Errors — surfaced verbatim, and never confused with the 200 message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_post_surfaces_unparseable_time_error():
    """A bad ``publish_at`` 400s with the task untouched; show the reason."""
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server,
            "_api",
            AsyncMock(
                return_value={
                    "error": "HTTP 400",
                    "error_code": "VALIDATION_ERROR",
                    "message": "Unrecognised time spec: 'next blursday'",
                    "request_id": "abc",
                }
            ),
        ),
    ):
        out = await server.schedule_post("abc1", "next blursday")

    assert out.startswith("Error:")
    assert "HTTP 400" in out
    assert "next blursday" in out
    # The error body's `message` shares a key with the 200-path "approved but
    # not scheduled" message — a rejected request must never read as one.
    assert "NOT scheduled" not in out


@pytest.mark.asyncio
async def test_schedule_post_surfaces_transport_error_without_detail():
    """``_api`` flattens oauth/network failures to a bare ``{"error": ...}``."""
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api", AsyncMock(return_value={"error": "oauth init failed: RuntimeError"}),
        ),
    ):
        out = await server.schedule_post("abc1", "tomorrow 9am")

    assert out == "Error: oauth init failed: RuntimeError"


# ---------------------------------------------------------------------------
# Registration — the tool is reachable over MCP, and stays off the read-only
# voice/mobile allowlist (it is a write tool).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_post_is_registered_as_an_mcp_tool():
    names = {t.name for t in await server.mcp.list_tools()}
    assert "schedule_post" in names


def test_schedule_post_not_in_read_only_voice_allowlist():
    import http_server  # noqa: PLC0415 — import here to keep module import cheap

    assert "schedule_post" not in http_server.DEFAULT_VOICE_MOBILE_ALLOWLIST

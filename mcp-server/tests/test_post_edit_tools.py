"""Tests for the draft-editing MCP tools (poindexter#523).

``edit_post_body`` / ``replace_post_image`` / ``regen_post_image`` wrap the
worker-API edit routes. These verify each tool resolves the task id and POSTs
the right payload; the service logic is covered in the backend test suite.
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


@pytest.mark.asyncio
async def test_edit_post_body_find_replace():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"detail": "edited", "warnings": []}),
        ) as api,
    ):
        out = await server.edit_post_body("abc1", find="[memory/x] ", replace="")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/edit-body", {"find": "[memory/x] ", "replace": ""},
    )
    assert "edited" in out


@pytest.mark.asyncio
async def test_edit_post_body_new_content_overwrites():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(server, "_api", AsyncMock(return_value={"detail": "edited"})) as api,
    ):
        await server.edit_post_body("abc1", new_content="brand new body")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/edit-body", {"new_content": "brand new body"},
    )


@pytest.mark.asyncio
async def test_edit_post_body_requires_input():
    """No find and no new_content → clear error, REST not called."""
    with patch.object(server, "_api", AsyncMock()) as api:
        out = await server.edit_post_body("abc1")
    assert "Error" in out
    api.assert_not_called()


@pytest.mark.asyncio
async def test_replace_post_image_calls_route():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"detail": "swapped", "new_url": "https://cdn/x.png"}),
        ) as api,
    ):
        out = await server.replace_post_image("abc1", "featured", "https://cdn/x.png")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/replace-image",
        {"which": "featured", "url": "https://cdn/x.png"},
    )
    assert "https://cdn/x.png" in out


@pytest.mark.asyncio
async def test_regen_post_image_calls_route():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"detail": "regenerated", "new_url": "u"}),
        ) as api,
    ):
        out = await server.regen_post_image("abc1", "inline:2", "a teal robot")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/regen-image",
        {"which": "inline:2", "prompt": "a teal robot"},
    )
    assert "regenerated" in out


@pytest.mark.asyncio
async def test_tool_surfaces_api_error():
    """A REST error (e.g. 400 from the route) surfaces, not swallowed."""
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"error": "HTTP 400: find string not present"}),
        ),
    ):
        out = await server.replace_post_image("abc1", "featured", "x")
    assert "Error" in out
    assert "find string not present" in out

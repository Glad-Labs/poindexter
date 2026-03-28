"""
Shared fixtures for route-level unit tests.

All fixtures wire up a minimal FastAPI app with:
- The router under test (no full main.py startup)
- Auth dependency overridden to return a test token
- DB dependency overridden to return an AsyncMock

This keeps tests fast and deterministic — no real DB or LLM calls.
"""

import os
from unittest.mock import AsyncMock

# Set OPERATOR_ID to match test fixtures before any route module imports it.
# This ensures ownership checks in routes (writing_style, workflows, etc.)
# match the mock data created by tests.
os.environ.setdefault("OPERATOR_ID", "test-user-id-123")

# ---------------------------------------------------------------------------
# Shared test-user stub (legacy — kept for test_auth_unified.py compatibility)
# ---------------------------------------------------------------------------

TEST_USER = {
    "id": "test-user-id-123",
    "email": "test@example.com",
    "username": "testuser",
    "auth_provider": "test",
    "is_active": True,
}


# ---------------------------------------------------------------------------
# DB mock builder
# ---------------------------------------------------------------------------


def make_mock_db() -> AsyncMock:
    """Return a fresh AsyncMock that looks like DatabaseService."""
    db = AsyncMock()
    # Default return values that match typical DB responses
    db.get_task = AsyncMock(return_value=None)
    db.get_tasks_by_ids = AsyncMock(return_value={})  # bulk fetch — default: empty dict
    db.add_task = AsyncMock(return_value="new-task-id-456")
    db.update_task = AsyncMock(return_value=True)
    db.update_task_status = AsyncMock(return_value=True)
    db.delete_task = AsyncMock(return_value=True)
    db.get_tasks_paginated = AsyncMock(return_value=([], 0))
    db.log_status_change = AsyncMock(return_value=None)
    db.create_post = AsyncMock(return_value={"id": "post-id-789"})
    return db

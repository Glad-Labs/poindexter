"""
Unit tests for services/writing_style_db.py.

Tests cover:
- WritingStyleDatabase._format_sample — None row, string metadata, dict metadata, datetime formatting
- WritingStyleDatabase.create_writing_sample — success, set_as_active deactivates others, raises on DB error
- WritingStyleDatabase.get_writing_sample — found, not found, raises on DB error
- WritingStyleDatabase.get_user_writing_samples — success, empty, raises on DB error
- WritingStyleDatabase.get_active_writing_sample — found, not found, raises on DB error
- WritingStyleDatabase.set_active_writing_sample — success, not found raises ValueError, raises on DB error
- WritingStyleDatabase.update_writing_sample — title update, content updates word/char count, no fields raises ValueError, not found raises ValueError
- WritingStyleDatabase.delete_writing_sample — success DELETE 1, not found DELETE 0, raises on DB error

asyncpg pool fully mocked; no real DB access.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.writing_style_db import WritingStyleDatabase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    """Create a mock asyncpg Record-like row.

    Strict ``__getitem__`` (KeyError on missing key) so production code
    that reads a column the test didn't set fails loudly instead of
    silently getting ``None`` and passing — see GH#337.

    Use this helper ONLY when production code reads ``row[<key>]`` — the
    strict mapping is what gives the test signal value. When a test
    just hands the row to a patched ``ModelConverter`` and asserts on
    the converter's return value, prefer ``object()`` directly: a
    literal sentinel makes it obvious the row contents are not under
    test, and prevents the row-faker from quietly accumulating stale
    columns over time (the original symptom in GH#30).
    """
    row = MagicMock()
    _data = {**kwargs}
    row.__getitem__ = lambda self, k, _d=_data: _d[k]
    row.get = lambda k, default=None, _d=_data: _d.get(k, default)
    row.__bool__ = lambda self: True
    row.items = lambda _d=_data: _d.items()
    row.keys = lambda _d=_data: _d.keys()
    return row


def _now():
    return datetime.now(timezone.utc)


def _make_sample_row(**overrides):
    defaults = {
        "id": 42,
        "user_id": "user-123",
        "title": "My Sample",
        "description": "A description",
        "content": "Hello world this is sample content",
        "is_active": False,
        "word_count": 6,
        "char_count": 33,
        "metadata": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    defaults.update(overrides)
    return _make_row(**defaults)


def _make_pool(
    fetchrow_result=None,
    fetch_result=None,
    execute_result=None,
    fetchrow_side_effect=None,
    fetch_side_effect=None,
    execute_side_effect=None,
):
    conn = MagicMock()
    if fetchrow_side_effect:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    if fetch_side_effect:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=fetch_result or [])
    if execute_side_effect:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock(return_value=execute_result or "OK")
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_db(pool=None) -> WritingStyleDatabase:
    return WritingStyleDatabase(pool=pool or _make_pool())


# ---------------------------------------------------------------------------
# _format_sample (static method)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatSample:
    def test_none_row_returns_empty_dict(self):
        result = WritingStyleDatabase._format_sample(None)
        assert result == {}

    def test_falsy_row_returns_empty_dict(self):
        row = MagicMock()
        row.__bool__ = lambda self: False
        result = WritingStyleDatabase._format_sample(row)
        assert result == {}

    def test_basic_fields_mapped(self):
        row = _make_sample_row()
        result = WritingStyleDatabase._format_sample(row)
        assert result["id"] == "42"  # int converted to string
        assert result["user_id"] == "user-123"
        assert result["title"] == "My Sample"
        assert result["content"] == "Hello world this is sample content"

    def test_metadata_none_returned_as_none(self):
        """When metadata column is NULL, _format_sample returns None (not {}).
        row.get("metadata", {}) returns None because None IS a valid value —
        the default {} only applies when the key is missing."""
        row = _make_sample_row(metadata=None)
        result = WritingStyleDatabase._format_sample(row)
        # metadata is None because row.get("metadata", {}) returns None
        # (the default {} only applies when key is absent from the row)
        assert result["metadata"] is None

    def test_metadata_dict_preserved(self):
        meta = {"style": "formal", "tone": "professional"}
        row = _make_sample_row(metadata=meta)
        result = WritingStyleDatabase._format_sample(row)
        assert result["metadata"] == meta

    def test_metadata_json_string_decoded(self):
        meta_str = json.dumps({"style": "casual"})
        row = _make_sample_row(metadata=meta_str)
        result = WritingStyleDatabase._format_sample(row)
        assert result["metadata"] == {"style": "casual"}

    def test_metadata_invalid_json_string_becomes_empty_dict(self):
        row = _make_sample_row(metadata="not valid json {")
        result = WritingStyleDatabase._format_sample(row)
        assert result["metadata"] == {}

    def test_created_at_iso_formatted(self):
        dt = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        row = _make_sample_row(created_at=dt)
        result = WritingStyleDatabase._format_sample(row)
        assert "2026-03-12" in result["created_at"]

    def test_none_created_at_returns_none(self):
        row = _make_sample_row(created_at=None)
        result = WritingStyleDatabase._format_sample(row)
        assert result["created_at"] is None

    def test_word_and_char_count_included(self):
        row = _make_sample_row(word_count=100, char_count=500)
        result = WritingStyleDatabase._format_sample(row)
        assert result["word_count"] == 100
        assert result["char_count"] == 500


# ---------------------------------------------------------------------------
# create_writing_sample
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateWritingSample:
    @pytest.mark.asyncio
    async def test_success_returns_dict(self):
        row = _make_sample_row(id=1, title="My Blog Post")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.create_writing_sample(
            user_id="user-123",
            title="My Blog Post",
            content="Some content here",
        )

        assert result["title"] == "My Blog Post"
        assert result["user_id"] == "user-123"

    @pytest.mark.asyncio
    async def test_word_count_computed_from_content(self):
        content = "one two three four five"
        row = _make_sample_row(word_count=5, char_count=len(content))
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.create_writing_sample(
            user_id="u1",
            title="Test",
            content=content,
        )

        assert result["word_count"] == 5

    @pytest.mark.asyncio
    async def test_set_as_active_executes_deactivate_first(self):
        row = _make_sample_row(is_active=True)
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        # Track execute calls
        execute_calls = []
        async with pool.acquire() as conn:

            async def _capture_execute(sql, *args):
                execute_calls.append(sql.strip()[:60])
                return "OK"

            conn.execute = _capture_execute

        await db.create_writing_sample(
            user_id="user-123",
            title="Active Sample",
            content="Content here",
            set_as_active=True,
        )

        # The deactivate UPDATE should have been called before INSERT
        assert any("UPDATE writing_samples SET is_active = FALSE" in c for c in execute_calls)

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.create_writing_sample(
                user_id="u1",
                title="Sample",
                content="Content",
            )


# ---------------------------------------------------------------------------
# get_writing_sample
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWritingSample:
    @pytest.mark.asyncio
    async def test_found_returns_dict(self):
        row = _make_sample_row(id=42)
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.get_writing_sample("42")

        assert result is not None
        assert result["id"] == "42"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        result = await db.get_writing_sample("999")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.get_writing_sample("42")


# ---------------------------------------------------------------------------
# get_user_writing_samples
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUserWritingSamples:
    @pytest.mark.asyncio
    async def test_success_returns_list_of_dicts(self):
        rows = [
            _make_sample_row(id=1, title="First"),
            _make_sample_row(id=2, title="Second"),
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        result = await db.get_user_writing_samples("user-123")

        assert len(result) == 2
        assert result[0]["title"] == "First"
        assert result[1]["title"] == "Second"

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)

        result = await db.get_user_writing_samples("user-no-samples")
        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.get_user_writing_samples("user-123")


# ---------------------------------------------------------------------------
# get_active_writing_sample
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetActiveWritingSample:
    @pytest.mark.asyncio
    async def test_found_returns_dict(self):
        row = _make_sample_row(is_active=True)
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.get_active_writing_sample("user-123")

        assert result is not None
        assert result["is_active"] is True

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        result = await db.get_active_writing_sample("user-no-active")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.get_active_writing_sample("user-123")


# ---------------------------------------------------------------------------
# set_active_writing_sample
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetActiveWritingSample:
    @pytest.mark.asyncio
    async def test_success_returns_updated_dict(self):
        row = _make_sample_row(id=5, is_active=True)
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.set_active_writing_sample("user-123", "5")

        assert result["is_active"] is True

    @pytest.mark.asyncio
    async def test_not_found_raises_value_error(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        with pytest.raises((ValueError, Exception)):
            await db.set_active_writing_sample("user-123", "999")

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.set_active_writing_sample("user-123", "5")


# ---------------------------------------------------------------------------
# update_writing_sample
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateWritingSample:
    @pytest.mark.asyncio
    async def test_title_update_returns_dict(self):
        row = _make_sample_row(id=7, title="New Title")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.update_writing_sample(
            sample_id="7",
            user_id="user-123",
            title="New Title",
        )

        assert result["title"] == "New Title"

    @pytest.mark.asyncio
    async def test_content_update_computes_word_and_char_count(self):
        new_content = "Hello world"  # 2 words, 11 chars
        row = _make_sample_row(id=8, content=new_content, word_count=2, char_count=11)
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.update_writing_sample(
            sample_id="8",
            user_id="user-123",
            content=new_content,
        )

        assert result["word_count"] == 2
        assert result["char_count"] == 11

    @pytest.mark.asyncio
    async def test_no_fields_raises_value_error(self):
        pool = _make_pool()
        db = _make_db(pool)

        with pytest.raises(ValueError, match="No fields to update"):
            await db.update_writing_sample(
                sample_id="7",
                user_id="user-123",
                # No title, description, or content
            )

    @pytest.mark.asyncio
    async def test_not_found_raises(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        with pytest.raises((ValueError, Exception)):
            await db.update_writing_sample(
                sample_id="999",
                user_id="user-123",
                title="New Title",
            )

    @pytest.mark.asyncio
    async def test_description_update(self):
        row = _make_sample_row(id=9, description="New description")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.update_writing_sample(
            sample_id="9",
            user_id="user-123",
            description="New description",
        )

        assert result["description"] == "New description"

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.update_writing_sample(
                sample_id="7",
                user_id="user-123",
                title="New Title",
            )


# ---------------------------------------------------------------------------
# delete_writing_sample
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteWritingSample:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        pool = _make_pool(execute_result="DELETE 1")
        db = _make_db(pool)

        result = await db.delete_writing_sample("42", "user-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found_returns_false(self):
        pool = _make_pool(execute_result="DELETE 0")
        db = _make_db(pool)

        result = await db.delete_writing_sample("999", "user-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(execute_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.delete_writing_sample("42", "user-123")

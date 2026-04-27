"""Unit tests for services.writing_style_context.

Targets ``build_writing_style_context``: the helper that loads recent
writing samples for voice matching during content generation. Bumps
the module from 16% to ~100% coverage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.writing_style_context import build_writing_style_context


@pytest.mark.unit
class TestBuildWritingStyleContext:
    """Coverage for the public helper."""

    async def test_returns_none_when_database_service_is_none(self):
        result = await build_writing_style_context(None)
        assert result is None

    async def test_returns_none_when_no_writing_style_attr(self):
        # database_service exists but has no `.writing_style`
        db = MagicMock(spec=[])  # spec=[] => attribute access raises AttributeError
        result = await build_writing_style_context(db)
        assert result is None

    async def test_returns_none_when_writing_style_is_falsy(self):
        # `getattr(database_service, "writing_style", None)` returns None.
        db = MagicMock()
        db.writing_style = None
        result = await build_writing_style_context(db)
        assert result is None

    async def test_returns_none_when_no_samples(self):
        db = MagicMock()
        db.writing_style.get_user_writing_samples = AsyncMock(return_value=[])
        result = await build_writing_style_context(db)
        assert result is None
        db.writing_style.get_user_writing_samples.assert_awaited_once_with(
            user_id="default", limit=3,
        )

    async def test_returns_none_when_samples_have_only_empty_content(self):
        db = MagicMock()
        # All samples present but every body is empty — every iteration
        # `continue`s, so excerpts stays empty and helper returns None.
        db.writing_style.get_user_writing_samples = AsyncMock(
            return_value=[
                {"title": "Empty 1", "content": ""},
                {"title": "Empty 2", "content": ""},
            ],
        )
        result = await build_writing_style_context(db)
        assert result is None

    async def test_joins_samples_with_titles_and_separator(self):
        db = MagicMock()
        db.writing_style.get_user_writing_samples = AsyncMock(
            return_value=[
                {"title": "First Post", "content": "Hello world."},
                {"title": "Second Post", "content": "Another piece."},
            ],
        )
        result = await build_writing_style_context(db, max_samples=2)
        assert result is not None
        # Should contain both samples separated by blank line.
        assert "### Sample: First Post\nHello world." in result
        assert "### Sample: Second Post\nAnother piece." in result
        assert "\n\n" in result  # joiner

    async def test_uses_default_title_when_missing(self):
        db = MagicMock()
        db.writing_style.get_user_writing_samples = AsyncMock(
            return_value=[{"content": "body without title"}],
        )
        result = await build_writing_style_context(db)
        assert result is not None
        assert "### Sample: Untitled" in result

    async def test_truncates_long_samples_to_max_words(self):
        long_words = ["word"] * 1000  # 1000 words
        long_content = " ".join(long_words)
        db = MagicMock()
        db.writing_style.get_user_writing_samples = AsyncMock(
            return_value=[{"title": "Long", "content": long_content}],
        )
        result = await build_writing_style_context(db, max_words_per_sample=10)
        assert result is not None
        # Truncated content is the first 10 "word"s plus an ellipsis.
        assert result.endswith("...")
        # The content portion (after the title line) should have exactly
        # 10 word tokens before the trailing "...".
        body = result.split("\n", 1)[1]
        # body looks like: "word word ... word..."
        # Strip the trailing "..." then split.
        before_ellipsis = body[: -3]
        assert before_ellipsis.split() == ["word"] * 10

    async def test_skips_empty_samples_but_keeps_others(self):
        db = MagicMock()
        db.writing_style.get_user_writing_samples = AsyncMock(
            return_value=[
                {"title": "Skip Me", "content": ""},  # empty body — skip
                {"title": "Keep Me", "content": "Real content."},
            ],
        )
        result = await build_writing_style_context(db)
        assert result is not None
        assert "Skip Me" not in result
        assert "Keep Me" in result

    async def test_respects_max_samples_param_in_truncation_loop(self):
        db = MagicMock()
        db.writing_style.get_user_writing_samples = AsyncMock(
            return_value=[
                {"title": "A", "content": "alpha"},
                {"title": "B", "content": "bravo"},
                {"title": "C", "content": "charlie"},
                {"title": "D", "content": "delta"},
            ],
        )
        # Even though the DB returned 4, max_samples=2 caps the loop.
        result = await build_writing_style_context(db, max_samples=2)
        assert result is not None
        assert "Sample: A" in result
        assert "Sample: B" in result
        # C/D are sliced out by `samples[:max_samples]`.
        assert "Sample: C" not in result
        assert "Sample: D" not in result

    async def test_returns_none_when_db_query_raises(self):
        db = MagicMock()
        db.writing_style.get_user_writing_samples = AsyncMock(
            side_effect=RuntimeError("DB blew up"),
        )
        # Helper swallows the exception, logs it, and returns None.
        result = await build_writing_style_context(db)
        assert result is None

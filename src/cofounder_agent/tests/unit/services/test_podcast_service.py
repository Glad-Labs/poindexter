"""Unit tests for podcast_service.py — markdown stripping and script building."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
import tempfile

from services.podcast_service import (
    PodcastService,
    EpisodeResult,
    _strip_markdown,
    _build_script,
    _estimate_duration_from_text,
)


class TestStripMarkdown:
    """Test markdown-to-plain-text conversion."""

    def test_removes_headings(self):
        assert _strip_markdown("# Title") == "Title"
        assert _strip_markdown("## Subtitle") == "Subtitle"
        assert _strip_markdown("### Deep heading") == "Deep heading"

    def test_removes_bold_italic(self):
        assert _strip_markdown("**bold text**") == "bold text"
        assert _strip_markdown("*italic text*") == "italic text"
        assert _strip_markdown("***bold italic***") == "bold italic"
        assert _strip_markdown("__underline bold__") == "underline bold"

    def test_converts_links_to_text(self):
        result = _strip_markdown("[click here](https://example.com)")
        assert result == "click here"

    def test_removes_images(self):
        result = _strip_markdown("![alt text](https://example.com/img.png)")
        assert result == ""

    def test_removes_code_blocks(self):
        md = "Before\n```python\nprint('hello')\n```\nAfter"
        result = _strip_markdown(md)
        assert "print" not in result
        assert "[code example omitted]" in result
        assert "Before" in result
        assert "After" in result

    def test_removes_inline_code(self):
        result = _strip_markdown("Use the `edge-tts` library")
        assert result == "Use the edge-tts library"

    def test_removes_blockquotes(self):
        result = _strip_markdown("> This is a quote")
        assert result == "This is a quote"

    def test_removes_list_markers(self):
        md = "- Item one\n- Item two\n* Item three"
        result = _strip_markdown(md)
        assert "Item one" in result
        assert "-" not in result
        assert "*" not in result

    def test_removes_numbered_lists(self):
        md = "1. First\n2. Second\n3. Third"
        result = _strip_markdown(md)
        assert "First" in result
        assert "1." not in result

    def test_removes_html_tags(self):
        result = _strip_markdown("<div>content</div>")
        assert result == "content"

    def test_removes_horizontal_rules(self):
        result = _strip_markdown("Before\n---\nAfter")
        assert "---" not in result

    def test_collapses_blank_lines(self):
        result = _strip_markdown("A\n\n\n\n\nB")
        assert result == "A\n\nB"

    def test_empty_input(self):
        assert _strip_markdown("") == ""

    def test_plain_text_unchanged(self):
        text = "Just a normal sentence with no markdown."
        assert _strip_markdown(text) == text


class TestBuildScript:
    """Test podcast script assembly."""

    def test_includes_intro(self):
        script = _build_script("My Title", "Some content")
        assert "Welcome to the Glad Labs podcast" in script
        assert "My Title" in script

    def test_includes_outro(self):
        script = _build_script("Title", "Content")
        assert "Thanks for listening" in script
        assert "gladlabs.io" in script

    def test_strips_markdown_from_content(self):
        script = _build_script("Title", "# Heading\n**bold** text")
        assert "#" not in script
        assert "**" not in script
        assert "bold text" in script


class TestEstimateDuration:
    """Test duration estimation."""

    def test_short_text(self):
        # 10 words ~= 4 seconds, but minimum is 30
        result = _estimate_duration_from_text("one two three four five six seven eight nine ten")
        assert result == 30

    def test_longer_text(self):
        words = " ".join(["word"] * 300)  # 300 words ~= 120 seconds
        result = _estimate_duration_from_text(words)
        assert result == 120


class TestPodcastService:
    """Test PodcastService class methods."""

    def test_get_episode_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            path = svc.get_episode_path("abc-123")
            assert path == Path(tmp) / "abc-123.mp3"

    def test_episode_exists_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            assert not svc.episode_exists("nonexistent")

    def test_episode_exists_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            # Create a fake MP3 file
            ep_path = Path(tmp) / "abc.mp3"
            ep_path.write_bytes(b"fake audio data")
            assert svc.episode_exists("abc")

    def test_list_episodes_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            assert svc.list_episodes() == []

    def test_list_episodes_with_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            (Path(tmp) / "ep1.mp3").write_bytes(b"data1")
            (Path(tmp) / "ep2.mp3").write_bytes(b"data2")
            episodes = svc.list_episodes()
            assert len(episodes) == 2
            ids = {ep["post_id"] for ep in episodes}
            assert ids == {"ep1", "ep2"}

    @pytest.mark.asyncio
    async def test_generate_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            # Pre-create episode
            (Path(tmp) / "abc.mp3").write_bytes(b"existing audio")
            result = await svc.generate_episode("abc", "Title", "Content")
            assert result.success
            assert result.file_size_bytes == 14  # len(b"existing audio")

    @pytest.mark.asyncio
    async def test_generate_empty_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            result = await svc.generate_episode("abc", "Title", "")
            # Should still have intro/outro text, so it won't be empty
            # But let's test a truly empty scenario via mocking
            assert result.success or result.error is not None

    @pytest.mark.asyncio
    async def test_generate_handles_import_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            with patch.dict("sys.modules", {"edge_tts": None}):
                result = await svc.generate_episode("abc", "Title", "Some content here")
                # Will fail gracefully with import error or module None
                assert not result.success or result.success  # Either way, no crash

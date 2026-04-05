"""Unit tests for podcast_service.py — markdown stripping and script building."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
import tempfile

from services.podcast_service import (
    PodcastService,
    EpisodeResult,
    _strip_markdown,
    _build_script_fallback as _build_script,
    _estimate_duration_from_text,
)


class TestStripMarkdown:
    """Test markdown-to-plain-text conversion."""

    def test_removes_headings(self):
        # Headings are stripped entirely for TTS (not natural in speech)
        assert _strip_markdown("# Title") == ""
        assert _strip_markdown("## Subtitle") == ""
        assert _strip_markdown("### Deep heading") == ""

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
        # Code blocks are removed entirely for TTS
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
        assert "gladlabs dot io" in script

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
            # Mock TTS to prevent real network call
            svc._generate_with_voice = AsyncMock(
                return_value=EpisodeResult(success=False, error="empty content")
            )
            result = await svc.generate_episode("abc", "Title", "")
            assert result.success or result.error is not None

    @pytest.mark.asyncio
    async def test_generate_handles_import_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            with patch.dict("sys.modules", {"edge_tts": None}):
                result = await svc.generate_episode("abc", "Title", "Some content here")
                assert result.success or result.error is not None


# ---------------------------------------------------------------------------
# generate_episode — with mocked edge_tts
# ---------------------------------------------------------------------------


class TestGenerateEpisode:
    """Test generate_episode with mocked edge_tts."""

    @pytest.fixture(autouse=True)
    def mock_llm_script(self):
        """Mock _build_script_with_llm to use fallback (no Ollama in tests)."""
        async def _fallback(title, content):
            return _build_script(title, content)

        with patch("services.podcast_service._build_script_with_llm", side_effect=_fallback):
            yield

    @pytest.mark.asyncio
    async def test_generate_episode_returns_mp3_path(self):
        """Successful generation returns an EpisodeResult with file_path."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))

            mock_communicate = MagicMock()
            # Make save() an async function that writes a fake MP3 file
            async def mock_save(path):
                Path(path).write_bytes(b"fake mp3 audio data here")

            mock_communicate.save = mock_save

            mock_edge_tts = MagicMock()
            mock_edge_tts.Communicate.return_value = mock_communicate

            with patch.dict("sys.modules", {"edge_tts": mock_edge_tts}):
                result = await svc.generate_episode(
                    "post-001", "My Great Post", "# Hello\n\nSome content."
                )

            assert result.success is True
            assert result.file_path is not None
            assert result.file_path.endswith("post-001.mp3")
            assert result.file_size_bytes > 0
            assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_generate_episode_idempotent_skips_existing(self):
        """If an episode already exists, generation is skipped (idempotent)."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            # Pre-create the episode file
            episode_path = Path(tmp) / "post-002.mp3"
            episode_path.write_bytes(b"already generated audio")

            result = await svc.generate_episode(
                "post-002", "Title", "Content body here"
            )

            assert result.success is True
            assert result.file_path == str(episode_path)
            assert result.file_size_bytes == len(b"already generated audio")
            # File content should be untouched
            assert episode_path.read_bytes() == b"already generated audio"

    @pytest.mark.asyncio
    async def test_generate_episode_force_regenerates(self):
        """With force=True, existing episode is regenerated."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            # Pre-create the episode file
            episode_path = Path(tmp) / "post-003.mp3"
            episode_path.write_bytes(b"old audio")

            mock_communicate = MagicMock()
            async def mock_save(path):
                Path(path).write_bytes(b"brand new audio data")

            mock_communicate.save = mock_save

            mock_edge_tts = MagicMock()
            mock_edge_tts.Communicate.return_value = mock_communicate

            with patch.dict("sys.modules", {"edge_tts": mock_edge_tts}):
                result = await svc.generate_episode(
                    "post-003", "Title", "Content", force=True
                )

            assert result.success is True
            assert episode_path.read_bytes() == b"brand new audio data"

    @pytest.mark.asyncio
    async def test_generate_episode_tries_fallback_voices(self):
        """If the primary voice fails, fallback voices are tried."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))

            call_count = 0

            class FakeCommunicate:
                def __init__(self, script, voice):
                    self.voice = voice

                async def save(self, path):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        raise Exception("Primary voice unavailable")
                    # Second call succeeds
                    Path(path).write_bytes(b"fallback audio")

            mock_edge_tts = MagicMock()
            mock_edge_tts.Communicate = FakeCommunicate

            with patch.dict("sys.modules", {"edge_tts": mock_edge_tts}):
                result = await svc.generate_episode(
                    "post-004", "Title", "Some content"
                )

            assert result.success is True
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_generate_episode_all_voices_fail(self):
        """If all voices fail, returns failure result."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))

            class FailCommunicate:
                def __init__(self, script, voice):
                    pass

                async def save(self, path):
                    raise Exception("Voice engine down")

            mock_edge_tts = MagicMock()
            mock_edge_tts.Communicate = FailCommunicate

            with patch.dict("sys.modules", {"edge_tts": mock_edge_tts}):
                result = await svc.generate_episode(
                    "post-005", "Title", "Some content"
                )

            assert result.success is False
            assert "All voices failed" in result.error


# ---------------------------------------------------------------------------
# list_episodes
# ---------------------------------------------------------------------------


class TestListEpisodes:
    """Test list_episodes returns the correct format."""

    def test_list_episodes_returns_correct_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            (Path(tmp) / "post-a.mp3").write_bytes(b"audio a")
            episodes = svc.list_episodes()
            assert len(episodes) == 1
            ep = episodes[0]
            assert ep["post_id"] == "post-a"
            assert ep["file_path"] == str(Path(tmp) / "post-a.mp3")
            assert ep["file_size_bytes"] == 7
            assert "created_at" in ep

    def test_list_episodes_sorted_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            (Path(tmp) / "b-episode.mp3").write_bytes(b"b")
            (Path(tmp) / "a-episode.mp3").write_bytes(b"a")
            episodes = svc.list_episodes()
            assert episodes[0]["post_id"] == "a-episode"
            assert episodes[1]["post_id"] == "b-episode"

    def test_list_episodes_ignores_non_mp3(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp))
            (Path(tmp) / "notes.txt").write_text("not audio")
            (Path(tmp) / "real.mp3").write_bytes(b"audio")
            episodes = svc.list_episodes()
            assert len(episodes) == 1
            assert episodes[0]["post_id"] == "real"

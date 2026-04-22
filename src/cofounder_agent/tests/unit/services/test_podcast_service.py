"""Unit tests for podcast_service.py — markdown stripping and script building.

Post-Phase-H (GH#95): PodcastService + module helpers take site_config
via DI. Tests build a MagicMock SiteConfig via ``_mock_sc()``.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.podcast_service import (
    EpisodeResult,
    PodcastService,
    _estimate_duration_from_text,
    _strip_markdown,
)
from services.podcast_service import (
    _build_script_fallback,
)


def _mock_sc(overrides: dict | None = None) -> MagicMock:
    """Return a MagicMock shaped like SiteConfig for podcast_service tests."""
    sc = MagicMock()
    values = overrides or {}
    sc.get.side_effect = lambda k, d="": values.get(k, d)
    return sc


def _build_script(title: str, content: str) -> str:
    """Test-friendly wrapper: build a script using a minimal mock site_config."""
    return _build_script_fallback(title, content, _mock_sc())


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
        assert "Welcome to" in script
        assert "My Title" in script

    def test_includes_outro(self):
        script = _build_script("Title", "Content")
        assert "Thanks for listening" in script
        assert "See you next time" in script

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
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            path = svc.get_episode_path("abc-123")
            assert path == Path(tmp) / "abc-123.mp3"

    def test_episode_exists_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            assert not svc.episode_exists("nonexistent")

    def test_episode_exists_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            # Create a fake MP3 file
            ep_path = Path(tmp) / "abc.mp3"
            ep_path.write_bytes(b"fake audio data")
            assert svc.episode_exists("abc")

    def test_list_episodes_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            assert svc.list_episodes() == []

    def test_list_episodes_with_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            (Path(tmp) / "ep1.mp3").write_bytes(b"data1")
            (Path(tmp) / "ep2.mp3").write_bytes(b"data2")
            episodes = svc.list_episodes()
            assert len(episodes) == 2
            ids = {ep["post_id"] for ep in episodes}
            assert ids == {"ep1", "ep2"}

    @pytest.mark.asyncio
    async def test_generate_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            # Pre-create episode
            (Path(tmp) / "abc.mp3").write_bytes(b"existing audio")
            result = await svc.generate_episode("abc", "Title", "Content")
            assert result.success
            assert result.file_size_bytes == 14  # len(b"existing audio")

    @pytest.mark.asyncio
    async def test_generate_empty_content(self):
        async def _mock_script(title, content, site_config):
            return _build_script(title, content)
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            svc._generate_with_voice = AsyncMock(
                return_value=EpisodeResult(success=False, error="empty content")
            )
            with patch("services.podcast_service._build_script_with_llm", side_effect=_mock_script):
                result = await svc.generate_episode("abc", "Title", "")
            assert result.success or result.error is not None

    @pytest.mark.asyncio
    async def test_generate_handles_import_error(self):
        async def _mock_script(title, content, site_config):
            return _build_script(title, content)
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            svc._generate_with_voice = AsyncMock(
                return_value=EpisodeResult(success=False, error="no edge_tts")
            )
            with patch("services.podcast_service._build_script_with_llm", side_effect=_mock_script):
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
        async def _fallback(title, content, site_config):
            return _build_script(title, content)

        with patch("services.podcast_service._build_script_with_llm", side_effect=_fallback):
            yield

    @pytest.mark.asyncio
    async def test_generate_episode_returns_mp3_path(self):
        """Successful generation returns an EpisodeResult with file_path."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())

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
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
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
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
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
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())

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
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())

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
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
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
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            (Path(tmp) / "b-episode.mp3").write_bytes(b"b")
            (Path(tmp) / "a-episode.mp3").write_bytes(b"a")
            episodes = svc.list_episodes()
            assert episodes[0]["post_id"] == "a-episode"
            assert episodes[1]["post_id"] == "b-episode"

    def test_list_episodes_ignores_non_mp3(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_mock_sc())
            (Path(tmp) / "notes.txt").write_text("not audio")
            (Path(tmp) / "real.mp3").write_bytes(b"audio")
            episodes = svc.list_episodes()
            assert len(episodes) == 1
            assert episodes[0]["post_id"] == "real"


# ===========================================================================
# _normalize_for_speech (DB-driven TTS replacements)
# ===========================================================================


class TestNormalizeForSpeech:
    def test_smart_quotes_converted_to_straight(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("\u201cHello\u201d and \u2018world\u2019", _mock_sc())
        assert "\u201c" not in result
        assert "\u201d" not in result
        assert "\u2018" not in result
        assert "\u2019" not in result

    def test_ellipsis_converted(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("wait\u2026 for it", _mock_sc())
        assert "\u2026" not in result
        assert "..." in result

    def test_double_spaces_collapsed(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("hello  world   foo", _mock_sc())
        assert "  " not in result

    def test_double_commas_collapsed(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("hello, , world", _mock_sc())
        assert ", ," not in result

    def test_db_pronunciation_override_applied(self):
        from services.podcast_service import _normalize_for_speech
        sc = _mock_sc({
            "tts_pronunciations": '{"GitHub": "git hub"}',
            "tts_acronym_replacements": "",
        })
        result = _normalize_for_speech("Visit GitHub today", sc)
        assert "git hub" in result.lower() or "git hub" in result

    def test_invalid_db_pronunciations_falls_back_to_defaults(self):
        from services.podcast_service import _normalize_for_speech
        sc = _mock_sc({
            "tts_pronunciations": "not valid json {",
            "tts_acronym_replacements": "",
        })
        # Should not raise — falls back to defaults
        result = _normalize_for_speech("Some text", sc)
        assert isinstance(result, str)

    def test_acronym_regex_applied(self):
        from services.podcast_service import _normalize_for_speech
        sc = _mock_sc({
            "tts_pronunciations": "",
            "tts_acronym_replacements": '{"NASA": "nassa"}',
        })
        result = _normalize_for_speech("Working with NASA", sc)
        assert "nassa" in result.lower()


class TestGetTtsReplacements:
    def test_returns_default_list_when_no_db_config(self):
        from services.podcast_service import _get_tts_replacements
        result = _get_tts_replacements(_mock_sc())
        assert isinstance(result, list)
        assert len(result) > 0
        # Each entry is a tuple
        for item in result[:3]:
            assert len(item) == 2

    def test_db_overrides_merge_with_defaults(self):
        from services.podcast_service import _get_tts_replacements
        sc = MagicMock()
        sc.get.return_value = '{"customword": "kustom werd"}'
        result = _get_tts_replacements(sc)
        # The custom DB key should be in the merged list
        as_dict = dict(result)
        assert as_dict.get("customword") == "kustom werd"

    def test_invalid_json_falls_back_to_defaults(self):
        from services.podcast_service import _get_tts_replacements
        sc = MagicMock()
        sc.get.return_value = "not json"
        result = _get_tts_replacements(sc)
        # Should still return a non-empty list (the defaults)
        assert isinstance(result, list)
        assert len(result) > 0


class TestGetAcronymRegex:
    def test_returns_default_list_when_no_db_config(self):
        from services.podcast_service import _get_acronym_regex
        result = _get_acronym_regex(_mock_sc())
        assert isinstance(result, list)
        assert len(result) > 0
        # Each entry is (compiled_pattern, replacement_string)
        for pattern, replacement in result[:3]:
            assert hasattr(pattern, "sub")  # compiled regex
            assert isinstance(replacement, str)

    def test_db_acronyms_compiled_to_regex(self):
        from services.podcast_service import _get_acronym_regex
        sc = MagicMock()
        sc.get.return_value = '{"AWS": "ay double-yoo ess"}'
        result = _get_acronym_regex(sc)
        # Should find at least one entry whose substitution is the AWS one
        replacements = [r for _, r in result]
        assert "ay double-yoo ess" in replacements

    def test_invalid_json_falls_back_to_defaults(self):
        from services.podcast_service import _get_acronym_regex
        sc = MagicMock()
        sc.get.return_value = "not json"
        result = _get_acronym_regex(sc)
        # Returns the default list (not crash, not empty)
        assert isinstance(result, list)
        assert len(result) > 0


# ===========================================================================
# generate_podcast_episode (fire-and-forget wrapper)
# ===========================================================================


class TestGeneratePodcastEpisodeWrapper:
    @pytest.mark.asyncio
    async def test_calls_service_generate_episode(self):
        from services.podcast_service import generate_podcast_episode

        with patch("services.podcast_service.PodcastService") as MockSvc:
            mock_instance = MagicMock()
            mock_result = MagicMock(success=True)
            mock_instance.generate_episode = AsyncMock(return_value=mock_result)
            MockSvc.return_value = mock_instance

            await generate_podcast_episode(
                "post-1", "Title", "Content body", site_config=_mock_sc(),
            )

            mock_instance.generate_episode.assert_awaited_once()
            args = mock_instance.generate_episode.await_args
            assert args.args[0] == "post-1"
            assert args.args[1] == "Title"

    @pytest.mark.asyncio
    async def test_logs_failure_without_raising(self):
        from services.podcast_service import generate_podcast_episode

        with patch("services.podcast_service.PodcastService") as MockSvc:
            mock_instance = MagicMock()
            mock_result = MagicMock(success=False, error="TTS down")
            mock_instance.generate_episode = AsyncMock(return_value=mock_result)
            MockSvc.return_value = mock_instance

            # Should not raise even though success is False
            await generate_podcast_episode(
                "post-1", "Title", "Content", site_config=_mock_sc(),
            )

    @pytest.mark.asyncio
    async def test_swallows_unexpected_exception(self):
        from services.podcast_service import generate_podcast_episode

        with patch("services.podcast_service.PodcastService") as MockSvc:
            mock_instance = MagicMock()
            mock_instance.generate_episode = AsyncMock(side_effect=RuntimeError("boom"))
            MockSvc.return_value = mock_instance

            # Fire-and-forget — must not propagate
            await generate_podcast_episode(
                "post-1", "Title", "Content", site_config=_mock_sc(),
            )

    @pytest.mark.asyncio
    async def test_pre_generated_script_passed_through(self):
        from services.podcast_service import generate_podcast_episode

        with patch("services.podcast_service.PodcastService") as MockSvc:
            mock_instance = MagicMock()
            mock_instance.generate_episode = AsyncMock(return_value=MagicMock(success=True))
            MockSvc.return_value = mock_instance

            await generate_podcast_episode(
                "post-1", "T", "C",
                site_config=_mock_sc(),
                pre_generated_script="my custom script",
            )

            kwargs = mock_instance.generate_episode.await_args.kwargs
            assert kwargs.get("pre_generated_script") == "my custom script"

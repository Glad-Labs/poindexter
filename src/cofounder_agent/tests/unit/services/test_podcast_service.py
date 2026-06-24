"""Unit tests for podcast_service.py — markdown stripping and script building."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.podcast_service import (
    VOICE_POOL,
    EpisodeResult,
    PodcastService,
    _estimate_duration_from_text,
    _resolve_voice_pool,
    _strip_markdown,
)
from services.podcast_service import (
    _build_script_fallback as _build_script,
)
from services.site_config import SiteConfig

# #272 Phase-2f: PodcastService + the free functions now require a
# site_config (the module-global fallback was deleted). Tests thread this
# shared empty SiteConfig — empty config exercises the same ``.get(key,
# default)`` defaults the old empty module global provided.
_TEST_SC = SiteConfig()

# SiteConfig with tts_pronunciations seeded — used by word-boundary tests that
# verify pronunciation entries are DB-driven (not hardcoded in _SPOKEN_REPLACEMENTS).
_TTS_SC = SiteConfig(initial_config={
    "tts_pronunciations": (
        '{"GB": "gigabyte", "MB": "megabyte", "TB": "terabyte",'
        ' "GHz": "gigahertz", "Mbps": "megabits per second",'
        ' "fps": "frames per second", "vs": "versus", "vs.": "versus"}'
    ),
    "tts_acronym_replacements": "",
})


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
        script = _build_script("My Title", "Some content", site_config=_TEST_SC)
        assert "Welcome to" in script
        assert "My Title" in script

    def test_includes_outro(self):
        script = _build_script("Title", "Content", site_config=_TEST_SC)
        assert "Thanks for listening" in script
        assert "See you next time" in script

    def test_strips_markdown_from_content(self):
        script = _build_script("Title", "# Heading\n**bold** text", site_config=_TEST_SC)
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
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            path = svc.get_episode_path("abc-123")
            assert path == Path(tmp) / "abc-123.mp3"

    def test_episode_exists_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            assert not svc.episode_exists("nonexistent")

    def test_episode_exists_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            # Create a fake MP3 file
            ep_path = Path(tmp) / "abc.mp3"
            ep_path.write_bytes(b"fake audio data")
            assert svc.episode_exists("abc")

    def test_list_episodes_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            assert svc.list_episodes() == []

    def test_list_episodes_with_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            (Path(tmp) / "ep1.mp3").write_bytes(b"data1")
            (Path(tmp) / "ep2.mp3").write_bytes(b"data2")
            episodes = svc.list_episodes()
            assert len(episodes) == 2
            ids = {ep["post_id"] for ep in episodes}
            assert ids == {"ep1", "ep2"}

    @pytest.mark.asyncio
    async def test_generate_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            # Pre-create episode
            (Path(tmp) / "abc.mp3").write_bytes(b"existing audio")
            result = await svc.generate_episode("abc", "Title", "Content")
            assert result.success
            assert result.file_size_bytes == 14  # len(b"existing audio")

    @pytest.mark.asyncio
    async def test_generate_empty_content(self):
        async def _mock_script(title, content, **kwargs):
            return _build_script(title, content, site_config=_TEST_SC)
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            svc._generate_with_voice = AsyncMock(
                return_value=EpisodeResult(success=False, error="empty content")
            )
            with patch("services.podcast_service._build_script_with_llm", side_effect=_mock_script):
                result = await svc.generate_episode("abc", "Title", "")
            assert result.success or result.error is not None

    @pytest.mark.asyncio
    async def test_generate_handles_import_error(self):
        async def _mock_script(title, content, **kwargs):
            return _build_script(title, content, site_config=_TEST_SC)
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
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
        async def _fallback(title, content, **kwargs):
            return _build_script(title, content, site_config=_TEST_SC)

        with patch("services.podcast_service._build_script_with_llm", side_effect=_fallback):
            yield

    @pytest.mark.asyncio
    async def test_generate_episode_returns_mp3_path(self):
        """Successful generation returns an EpisodeResult with file_path."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)

            async def mock_synthesize(text, *, site_config, output_path=None, voice=None):
                if output_path:
                    Path(output_path).write_bytes(b"fake mp3 audio data here")
                return b"fake mp3 audio data here"

            with patch("services.tts_service.synthesize_speech", side_effect=mock_synthesize):
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
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
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
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            # Pre-create the episode file
            episode_path = Path(tmp) / "post-003.mp3"
            episode_path.write_bytes(b"old audio")

            async def mock_synthesize(text, *, site_config, output_path=None, voice=None):
                if output_path:
                    Path(output_path).write_bytes(b"brand new audio data")
                return b"brand new audio data"

            with patch("services.tts_service.synthesize_speech", side_effect=mock_synthesize):
                result = await svc.generate_episode(
                    "post-003", "Title", "Content", force=True
                )

            assert result.success is True
            assert episode_path.read_bytes() == b"brand new audio data"

    @pytest.mark.asyncio
    async def test_generate_episode_tries_fallback_voices(self):
        """If the primary voice fails, fallback voices are tried."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)

            call_count = 0

            async def mock_synthesize(text, *, site_config, output_path=None, voice=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # First voice fails — return None (TTS unavailable / error)
                    return None
                # Second call succeeds
                if output_path:
                    Path(output_path).write_bytes(b"fallback audio")
                return b"fallback audio"

            with patch("services.tts_service.synthesize_speech", side_effect=mock_synthesize):
                result = await svc.generate_episode(
                    "post-004", "Title", "Some content"
                )

            assert result.success is True
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_generate_episode_all_voices_fail(self):
        """If all voices fail (TTS returns None), returns failure result."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)

            async def mock_synthesize_none(text, *, site_config, output_path=None, voice=None):
                # Always return None — simulates Speaches unavailable
                return None

            with patch("services.tts_service.synthesize_speech", side_effect=mock_synthesize_none):
                result = await svc.generate_episode(
                    "post-005", "Title", "Some content"
                )

            assert result.success is False
            assert result.error is not None and "All voices failed" in result.error


# ---------------------------------------------------------------------------
# list_episodes
# ---------------------------------------------------------------------------


class TestListEpisodes:
    """Test list_episodes returns the correct format."""

    def test_list_episodes_returns_correct_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
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
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
            (Path(tmp) / "b-episode.mp3").write_bytes(b"b")
            (Path(tmp) / "a-episode.mp3").write_bytes(b"a")
            episodes = svc.list_episodes()
            assert episodes[0]["post_id"] == "a-episode"
            assert episodes[1]["post_id"] == "b-episode"

    def test_list_episodes_ignores_non_mp3(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_TEST_SC)
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
        result = _normalize_for_speech("\u201cHello\u201d and \u2018world\u2019", site_config=_TEST_SC)
        assert "\u201c" not in result
        assert "\u201d" not in result
        assert "\u2018" not in result
        assert "\u2019" not in result

    def test_ellipsis_converted(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("wait\u2026 for it", site_config=_TEST_SC)
        assert "\u2026" not in result
        assert "..." in result

    def test_double_spaces_collapsed(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("hello  world   foo", site_config=_TEST_SC)
        assert "  " not in result

    def test_double_commas_collapsed(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("hello, , world", site_config=_TEST_SC)
        assert ", ," not in result

    def test_db_pronunciation_override_applied(self):
        from services.podcast_service import _normalize_for_speech
        sc = SiteConfig(initial_config={
            "tts_pronunciations": '{"GitHub": "git hub"}',
            "tts_acronym_replacements": "",
        })
        result = _normalize_for_speech("Visit GitHub today", site_config=sc)
        assert "git hub" in result.lower() or "git hub" in result

    def test_invalid_db_pronunciations_does_not_raise(self):
        from services.podcast_service import _normalize_for_speech
        sc = SiteConfig(initial_config={
            "tts_pronunciations": "not valid json {",
            "tts_acronym_replacements": "",
        })
        # Invalid JSON: structural transforms still apply, pronunciation table skipped
        result = _normalize_for_speech("Some text", site_config=sc)
        assert isinstance(result, str)

    def test_acronym_regex_applied(self):
        from services.podcast_service import _normalize_for_speech
        sc = SiteConfig(initial_config={
            "tts_pronunciations": "",
            "tts_acronym_replacements": '{"NASA": "nassa"}',
        })
        result = _normalize_for_speech("Working with NASA", site_config=sc)
        assert "nassa" in result.lower()


class TestGetTtsReplacements:
    def test_returns_default_list_when_no_db_config(self):
        from services.podcast_service import _get_tts_replacements
        sc = SiteConfig(initial_config={"tts_pronunciations": ""})
        result = _get_tts_replacements(site_config=sc)
        assert isinstance(result, list)
        assert len(result) > 0
        # Each entry is a tuple
        for item in result[:3]:
            assert len(item) == 2

    def test_db_overrides_merge_with_defaults(self):
        from services.podcast_service import _get_tts_replacements
        sc = SiteConfig(initial_config={
            "tts_pronunciations": '{"customword": "kustom werd"}',
        })
        result = _get_tts_replacements(site_config=sc)
        # The custom DB key should be in the merged list
        as_dict = dict(result)
        assert as_dict.get("customword") == "kustom werd"

    def test_invalid_json_falls_back_to_defaults(self):
        from services.podcast_service import _get_tts_replacements
        sc = SiteConfig(initial_config={"tts_pronunciations": "not json"})
        result = _get_tts_replacements(site_config=sc)
        # Should still return a non-empty list (the defaults)
        assert isinstance(result, list)
        assert len(result) > 0


class TestGetAcronymRegex:
    def test_returns_empty_when_no_db_config(self):
        from services.podcast_service import _get_acronym_regex
        sc = SiteConfig(initial_config={"tts_acronym_replacements": ""})
        result = _get_acronym_regex(site_config=sc)
        # No hardcoded fallback — empty DB key = no acronym expansion
        assert result == []

    def test_db_acronyms_compiled_to_regex(self):
        from services.podcast_service import _get_acronym_regex
        sc = SiteConfig(initial_config={
            "tts_acronym_replacements": '{"AWS": "ay double-yoo ess"}',
        })
        result = _get_acronym_regex(site_config=sc)
        # Should find at least one entry whose substitution is the AWS one
        replacements = [r for _, r in result]
        assert "ay double-yoo ess" in replacements

    def test_invalid_json_returns_empty(self):
        from services.podcast_service import _get_acronym_regex
        sc = SiteConfig(initial_config={"tts_acronym_replacements": "not json"})
        result = _get_acronym_regex(site_config=sc)
        # Invalid JSON: no fallback, no crash — returns empty
        assert result == []


# ===========================================================================
# Word-boundary safety + new computing-unit entries
# ===========================================================================


class TestNormalizeForSpeechWordBoundaries:
    """Pure-letter tokens must not fire inside longer words."""

    def test_gb_replaced_standalone(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("256 GB SSD", site_config=_TTS_SC)
        assert "gigabyte" in result.lower()

    def test_gb_does_not_fire_inside_rgb(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("RGB lighting", site_config=_TTS_SC)
        assert "gigabyte" not in result.lower()
        assert "rgb" in result.lower()

    def test_mb_replaced_standalone(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("The file is 512 MB", site_config=_TTS_SC)
        assert "megabyte" in result.lower()

    def test_tb_replaced_standalone(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("4 TB drive", site_config=_TTS_SC)
        assert "terabyte" in result.lower()

    def test_ghz_replaced(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("running at 3.5 GHz", site_config=_TTS_SC)
        assert "gigahertz" in result.lower()

    def test_mbps_replaced(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("1000 Mbps link", site_config=_TTS_SC)
        assert "megabits per second" in result.lower()

    def test_fps_replaced_standalone(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("running at 60 fps", site_config=_TTS_SC)
        assert "frames per second" in result.lower()

    def test_vs_does_not_corrupt_versus(self):
        # Regression: "vs" fired inside "versus" → "versuserus".
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("Team A versus Team B", site_config=_TTS_SC)
        assert "versuserus" not in result
        assert "versus" in result

    def test_db_override_respects_word_boundary(self):
        from services.podcast_service import _normalize_for_speech
        sc = SiteConfig(initial_config={
            "tts_pronunciations": '{"API": "A P I"}',
            "tts_acronym_replacements": "",
        })
        # "RAPID" contains "API" but word boundary should prevent a match
        result = _normalize_for_speech("RAPID API calls", site_config=sc)
        # "RAPID" must remain untouched
        assert "RAPID" in result or "rapid" in result.lower()


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

            await generate_podcast_episode("post-1", "Title", "Content body", site_config=_TEST_SC)

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
            await generate_podcast_episode("post-1", "Title", "Content", site_config=_TEST_SC)

    @pytest.mark.asyncio
    async def test_swallows_unexpected_exception(self):
        from services.podcast_service import generate_podcast_episode

        with patch("services.podcast_service.PodcastService") as MockSvc:
            mock_instance = MagicMock()
            mock_instance.generate_episode = AsyncMock(side_effect=RuntimeError("boom"))
            MockSvc.return_value = mock_instance

            # Fire-and-forget — must not propagate
            await generate_podcast_episode("post-1", "Title", "Content", site_config=_TEST_SC)

    @pytest.mark.asyncio
    async def test_pre_generated_script_passed_through(self):
        from services.podcast_service import generate_podcast_episode

        with patch("services.podcast_service.PodcastService") as MockSvc:
            mock_instance = MagicMock()
            mock_instance.generate_episode = AsyncMock(return_value=MagicMock(success=True))
            MockSvc.return_value = mock_instance

            await generate_podcast_episode("post-1", "T", "C", pre_generated_script="my custom script", site_config=_TEST_SC)

            kwargs = mock_instance.generate_episode.await_args.kwargs
            assert kwargs.get("pre_generated_script") == "my custom script"


# ---------------------------------------------------------------------------
# _unwrap_intro_outro + narration sibling
# Glad-Labs/poindexter#649 PR 2 — the body-only narration sibling MP3
# the video composer mixes in so videos don't open with "Welcome to ..."
# ---------------------------------------------------------------------------


class TestUnwrapIntroOutro:
    """``_unwrap_intro_outro`` must invert ``_wrap_with_intro_outro``."""

    def test_round_trip_returns_body_only(self, monkeypatch):
        """wrap then unwrap must equal the original body."""
        from services.podcast_service import (
            _unwrap_intro_outro,
            _wrap_with_intro_outro,
        )

        class _StubSC:
            @staticmethod
            def get(key, default=None):
                return {
                    "podcast_include_intro": "true",
                    "podcast_include_outro": "true",
                    "podcast_name": "Test Show",
                    "site_domain": "example.com",
                }.get(key, default)

        _sc = _StubSC()

        body = "Here is the post body content. It has multiple sentences."
        wrapped = _wrap_with_intro_outro(body, "My Title", site_config=_sc)  # type: ignore[arg-type]
        assert "Welcome to Test Show" in wrapped
        assert "Visit example dot com" in wrapped

        recovered = _unwrap_intro_outro(wrapped, "My Title", site_config=_sc)  # type: ignore[arg-type]
        assert recovered == body
        assert "Welcome to Test Show" not in recovered
        assert "Visit example dot com" not in recovered

    def test_unwrap_no_intro_when_disabled(self, monkeypatch):
        """When the wrapper didn't add an intro, unwrap leaves the
        leading content alone."""
        from services.podcast_service import _unwrap_intro_outro

        class _StubSC:
            @staticmethod
            def get(key, default=None):
                return {
                    "podcast_include_intro": "false",
                    "podcast_include_outro": "false",
                    "podcast_name": "Test Show",
                    "site_domain": "example.com",
                }.get(key, default)

        _sc = _StubSC()

        body = "Body only no wrappers at all."
        recovered = _unwrap_intro_outro(body, "Title", site_config=_sc)  # type: ignore[arg-type]
        assert recovered == body


class TestNarrationSibling:
    """``PodcastService._maybe_generate_narration_sibling`` writes a
    body-only MP3 next to the main episode for the video composer."""

    @pytest.mark.asyncio
    async def test_writes_narration_sibling_alongside_main_mp3(
        self, monkeypatch,
    ):
        """When enabled (default), the sibling MP3 lands at
        ``{post_id}-narration.mp3``, derived from the same script
        without the intro/outro wrappers."""
        from services.podcast_service import PodcastService

        class _StubSC:
            @staticmethod
            def get(key, default=None):
                return {
                    "podcast_include_intro": "true",
                    "podcast_include_outro": "true",
                    "podcast_name": "Test Show",
                    "site_domain": "example.com",
                    "podcast_video_narration_sibling_enabled": "true",
                }.get(key, default)

        _sc = _StubSC()

        captured_scripts: list[str] = []

        async def _mock_synthesize(text, *, site_config, output_path=None, voice=None):
            captured_scripts.append(text)
            if output_path:
                Path(output_path).write_bytes(b"x" * 2000)
            return b"x" * 2000

        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_sc)  # type: ignore[arg-type]

            wrapped_script = (
                "Welcome to Test Show. Today's episode: Post Title.\n\n"
                "This is the post body content.\n\n"
                "Thanks for listening to Test Show. "
                "Visit example dot com for more episodes, articles, "
                "and insights. See you next time."
            )

            with patch("services.tts_service.synthesize_speech", side_effect=_mock_synthesize):
                await svc._maybe_generate_narration_sibling(
                    post_id="abc",
                    script=wrapped_script,
                    title="Post Title",
                    voice="bf_emma",
                )

            sibling_path = Path(tmp) / "abc-narration.mp3"
            assert sibling_path.exists()
            assert sibling_path.stat().st_size > 1000

        assert len(captured_scripts) == 1
        sibling_script = captured_scripts[0]
        assert "Welcome to Test Show" not in sibling_script
        assert "Visit example dot com" not in sibling_script
        assert "post body content" in sibling_script

    @pytest.mark.asyncio
    async def test_disabled_setting_skips_sibling(self, monkeypatch):
        """When the toggle is off, no sibling MP3 is written."""
        from services.podcast_service import PodcastService

        class _StubSC:
            @staticmethod
            def get(key, default=None):
                return {
                    "podcast_include_intro": "true",
                    "podcast_include_outro": "true",
                    "podcast_name": "Test Show",
                    "site_domain": "example.com",
                    "podcast_video_narration_sibling_enabled": "false",
                }.get(key, default)

        _sc = _StubSC()

        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_sc)  # type: ignore[arg-type]
            await svc._maybe_generate_narration_sibling(
                post_id="abc",
                script="Welcome to Test Show...\n\nbody.\n\nThanks for listening...",
                title="Post Title",
                voice="en-US-AvaNeural",
            )
            assert not (Path(tmp) / "abc-narration.mp3").exists()

    @pytest.mark.asyncio
    async def test_failure_is_non_fatal(self, monkeypatch):
        """If TTS raises during the sibling pass, the call must
        not propagate — the main episode is already done."""
        from services.podcast_service import PodcastService

        class _StubSC:
            @staticmethod
            def get(key, default=None):
                return {
                    "podcast_include_intro": "true",
                    "podcast_include_outro": "true",
                    "podcast_name": "Test Show",
                    "site_domain": "example.com",
                    "podcast_video_narration_sibling_enabled": "true",
                }.get(key, default)

        _sc = _StubSC()

        async def _broken_synthesize(text, *, site_config, output_path=None, voice=None):
            raise RuntimeError("simulated Speaches TTS failure")

        with tempfile.TemporaryDirectory() as tmp:
            svc = PodcastService(output_dir=Path(tmp), site_config=_sc)  # type: ignore[arg-type]
            with patch("services.tts_service.synthesize_speech", side_effect=_broken_synthesize):
                # Must not raise — the sibling failure is best-effort.
                await svc._maybe_generate_narration_sibling(
                    post_id="abc",
                    script=(
                        "Welcome to Test Show. Today's episode: Title.\n\n"
                        "real body content here that is long enough.\n\n"
                        "Thanks for listening to Test Show. "
                        "Visit example dot com for more episodes, articles, "
                        "and insights. See you next time."
                    ),
                    title="Title",
                    voice="bf_emma",
                )


class TestResolveVoicePool:
    """DB-configurable voice-rotation pool (Plan 7, #689).

    Lifts the hardcoded ``VOICE_POOL`` to operator-tunable app_settings
    (``tts_voice_rotation_enabled`` / ``tts_voice_pool``). Behavior MUST be
    unchanged when unset — a disabled flag or an empty pool falls back to the
    module constant, so existing installs rotate exactly as before.
    """

    def test_disabled_falls_back_to_constant(self):
        # tts_voice_pool present but rotation disabled (default) → ignore it.
        sc = SiteConfig(initial_config={"tts_voice_pool": "voice-a,voice-b"})
        assert _resolve_voice_pool(sc) == list(VOICE_POOL)

    def test_enabled_empty_pool_falls_back_to_constant(self):
        sc = SiteConfig(
            initial_config={
                "tts_voice_rotation_enabled": "true",
                "tts_voice_pool": "",
            }
        )
        assert _resolve_voice_pool(sc) == list(VOICE_POOL)

    def test_enabled_with_pool_uses_db_values(self):
        # Comma-separated, whitespace-trimmed, blanks dropped.
        sc = SiteConfig(
            initial_config={
                "tts_voice_rotation_enabled": "true",
                "tts_voice_pool": "voice-a, voice-b ,, voice-c",
            }
        )
        assert _resolve_voice_pool(sc) == ["voice-a", "voice-b", "voice-c"]

    def test_none_site_config_falls_back_to_constant(self):
        assert _resolve_voice_pool(None) == list(VOICE_POOL)

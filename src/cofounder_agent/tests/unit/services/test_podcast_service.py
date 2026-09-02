"""Unit tests for podcast_service.py — markdown stripping and script building."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.tts_provider import TTSResult
from services.podcast_service import (
    VOICE_POOL,
    EpisodeResult,
    PodcastService,
    _estimate_duration_from_text,
    _resolve_voice_pool,
    _select_voice,
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


# ===========================================================================
# The file.ext rule must never eat a number.
#
# Regression: `[\w/\\]+\.\w{2,4}` treated the "1.65 " in "$1.65 trillion" as a
# filename (stem "1", ext "65") and deleted it. A published episode announced
# "Debt in AI Infrastructure: The $ Trillion Secret" and read its central
# claim as "hidden debt at around trillion dollars" — figure gone, silently.
# A filename is noise; a number is usually the point of the sentence.
# ===========================================================================


class TestFileExtensionRuleNeverEatsNumbers:
    @pytest.mark.parametrize("text", [
        # The exact strings from the 2026-07-27 episode.
        "Debt in AI Infrastructure: The $1.65 Trillion Secret",
        "puts this hidden debt at around 1.65 trillion dollars",
        "higher than the 1.35 trillion dollars officially listed",
        # Same shape, other units — 2-to-4-digit fractional parts were the trap.
        "that's about $3.60 per month per GPU",
        "Latency was 99.9th percentile",
        "a 1.65x improvement",
        "it grew 3.5 percent",
        "the U.S. economy shrank",
    ])
    def test_numbers_survive_normalization(self, text):
        from services.podcast_service import _normalize_for_speech
        assert _normalize_for_speech(text, site_config=_TEST_SC) == text

    @pytest.mark.parametrize("text,gone", [
        ("See config.yaml for details", "config.yaml"),
        ("Edit main.py then run it", "main.py"),
        ("the Node.js runtime", "Node.js"),
    ])
    def test_real_filenames_still_stripped(self, text, gone):
        """The rule must keep doing its job — a spoken "dot py" is noise."""
        from services.podcast_service import _normalize_for_speech
        assert gone not in _normalize_for_speech(text, site_config=_TEST_SC)


# ===========================================================================
# Model-identifier speech normalization — quant/config tails read awful
# token-by-token. gemma-4-31B-it-qat:latest should say "gemma four thirty-one
# B", not spell out the it-qat / :latest / -5090 config noise.
# ===========================================================================


class TestNormalizeModelNames:
    """family + version/size is spoken; quant/variant/tag/GPU noise is dropped."""

    FAMS = ("gemma", "glm", "qwen", "phi", "llama", "mistral", "deepseek")

    def test_strips_quant_and_tag_tail(self):
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("we use gemma-4-31B-it-qat:latest", families=self.FAMS)
            == "we use gemma 4 31B"
        )

    def test_strips_gpu_suffix_keeps_decimal_version(self):
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("the glm-4.7-5090 reviser", families=self.FAMS)
            == "the glm 4.7 reviser"
        )

    def test_colon_size_tag_kept(self):
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("runs gemma-4:31b locally", families=self.FAMS)
            == "runs gemma 4 31b locally"
        )

    def test_glued_ollama_version(self):
        from services.podcast_service import _normalize_model_names
        assert _normalize_model_names("qwen3:30b", families=self.FAMS) == "qwen 3 30b"

    def test_glued_family_version_no_tail(self):
        from services.podcast_service import _normalize_model_names
        assert _normalize_model_names("phi4", families=self.FAMS) == "phi 4"

    def test_instruct_variant_stripped(self):
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("mistral-7B-instruct", families=self.FAMS)
            == "mistral 7B"
        )

    def test_trailing_sentence_period_preserved(self):
        # A model at the end of a sentence must keep its period, not eat it.
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("It runs on gemma-4-31B.", families=self.FAMS)
            == "It runs on gemma 4 31B."
        )

    def test_prose_family_words_untouched(self):
        # No numeric version → not a model identifier → leave prose alone.
        from services.podcast_service import _normalize_model_names
        text = "The llama on the farm, the latest chat, and phi coefficients"
        assert _normalize_model_names(text, families=self.FAMS) == text

    def test_hyphenated_family_word_without_number_untouched(self):
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("a llama-shaped cookie", families=self.FAMS)
            == "a llama-shaped cookie"
        )

    def test_bare_integer_config_not_treated_as_model(self):
        # "phi-node-2" (compiler SSA term) has only a bare integer, no size/
        # decimal version — it must not be mistaken for a model identifier.
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("the phi-node-2 pass", families=self.FAMS)
            == "the phi-node-2 pass"
        )

    def test_family_substring_not_matched(self):
        # "Philadelphia" contains "phi" but must be left intact.
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("a trip to Philadelphia", families=self.FAMS)
            == "a trip to Philadelphia"
        )

    def test_multiple_models_one_sentence(self):
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names(
                "gemma-4-31B-it-qat:latest writes, glm-4.7 revises", families=self.FAMS
            )
            == "gemma 4 31B writes, glm 4.7 revises"
        )

    def test_empty_families_is_noop(self):
        from services.podcast_service import _normalize_model_names
        assert (
            _normalize_model_names("gemma-4-31B-it-qat:latest", families=())
            == "gemma-4-31B-it-qat:latest"
        )


class TestGetModelFamilies:
    def test_falls_back_to_default_when_unset(self):
        from services.podcast_service import (
            _DEFAULT_MODEL_FAMILIES,
            _get_model_families,
        )
        sc = SiteConfig(initial_config={"tts_model_name_families": ""})
        assert _get_model_families(site_config=sc) == _DEFAULT_MODEL_FAMILIES

    def test_reads_csv_from_db(self):
        from services.podcast_service import _get_model_families
        sc = SiteConfig(initial_config={"tts_model_name_families": "gemma, glm ,qwen"})
        assert _get_model_families(site_config=sc) == ("gemma", "glm", "qwen")

    def test_shipped_default_setting_has_core_families(self):
        from services.podcast_service import _get_model_families
        from services.settings_defaults import DEFAULTS

        sc = SiteConfig(initial_config={
            "tts_model_name_families": DEFAULTS["tts_model_name_families"],
        })
        fams = _get_model_families(site_config=sc)
        for core in ("gemma", "glm", "qwen", "phi"):
            assert core in fams


class TestNormalizeForSpeechModelNames:
    """End-to-end: the transform is wired into _normalize_for_speech and runs
    BEFORE the pronunciation map (so a split-off 'glm' still gets 'G L M')."""

    def test_quant_tail_stripped_end_to_end(self):
        from services.podcast_service import _normalize_for_speech
        sc = SiteConfig(initial_config={
            "tts_pronunciations": "",
            "tts_acronym_replacements": "",
        })
        result = _normalize_for_speech(
            "we run gemma-4-31B-it-qat:latest nightly", site_config=sc
        )
        assert "it-qat" not in result
        assert "qat" not in result
        assert "31B" in result

    def test_glm_family_pronounced_after_split(self):
        from services.podcast_service import _normalize_for_speech
        from services.settings_defaults import DEFAULTS

        sc = SiteConfig(initial_config={
            "tts_pronunciations": DEFAULTS["tts_pronunciations"],
            "tts_acronym_replacements": "",
        })
        result = _normalize_for_speech("the glm-4.7-5090 model", site_config=sc)
        assert "5090" not in result
        assert "G L M" in result  # pronunciation applied to the split-off family
        assert "4.7" in result


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
# CI / CI-CD pronunciation — shipped defaults, ordering, and the word-boundary
# fix that lets a short token like "CI" ship safely.
# ===========================================================================


def _default_pron_sc():
    """SiteConfig seeded with the real shipped tts_pronunciations default,
    so these tests pin the actual config operators receive."""
    from services.settings_defaults import DEFAULTS

    return SiteConfig(initial_config={
        "tts_pronunciations": DEFAULTS["tts_pronunciations"],
        "tts_acronym_replacements": "",
    })


class TestCiPronunciation:
    """``CI`` → "See Eye" must fire standalone, leave words alone, and not
    clobber the longer ``CI/CD`` form."""

    def test_ci_spoken_as_see_eye(self):
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("Our CI pipeline runs on push", site_config=_default_pron_sc())
        assert "Our See Eye pipeline" in result

    def test_ci_does_not_corrupt_words(self):
        # Regression guard: bare "CI" must not fire inside common words.
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech(
            "A social decision about efficiency and precision", site_config=_default_pron_sc()
        )
        assert "See Eye" not in result
        for word in ("social", "decision", "efficiency", "precision"):
            assert word in result

    def test_ci_cd_resolves_before_ci(self):
        # "CI/CD" must become "See Eye See Dee" — the slash form is consumed
        # first, leaving no stray bare "CI"/"CD".
        from services.podcast_service import _normalize_for_speech
        result = _normalize_for_speech("Our CI/CD pipeline ships nightly", site_config=_default_pron_sc())
        assert "See Eye See Dee" in result
        assert "CI/CD" not in result
        assert "CI CD" not in result


class TestRenderBoundaryWordSafety:
    """The TTS render boundary (``_generate_with_voice``) must apply the
    pronunciation map with the SAME word boundaries as generation — short
    tokens like CI/MB must not corrupt body words on re-render."""

    @pytest.mark.asyncio
    async def test_render_boundary_does_not_corrupt_words(self, tmp_path):
        from services.podcast_service import PodcastService
        from services.settings_defaults import DEFAULTS

        sc = SiteConfig(initial_config={
            "tts_pronunciations": DEFAULTS["tts_pronunciations"],
            "tts_acronym_replacements": "",
            "podcast_include_intro": "false",
            "podcast_include_outro": "false",
        })
        svc = PodcastService(output_dir=tmp_path, site_config=sc)
        captured: dict = {}

        async def fake_synth(text, *, site_config, output_path, voice):
            captured["text"] = text
            Path(output_path).write_bytes(b"audio-bytes")
            return b"audio-bytes"

        # "number"/"social" would corrupt under the old no-boundary pass
        # ("numegabyteer", "soSee Eyeal"); "CI" standalone must still convert.
        script = "Our CI run measured the number of social signals."
        with patch("services.tts_service.synthesize_speech", side_effect=fake_synth):
            result = await svc._generate_with_voice(script, "bf_emma", tmp_path / "r.mp3")

        assert result.success is True
        rendered = captured["text"]
        assert "number" in rendered
        assert "social" in rendered
        assert "See Eye run" in rendered  # standalone CI still converts


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


class TestSpokenDomain:
    """``_spoken_domain`` maps the TLD through ``tts_domain_tld_pronunciations``
    so the podcast outro says "gladlabs dot eye oh", not "gladlabs dot eoh"."""

    @staticmethod
    def _sc(domain="gladlabs.io", tld_map='{"io": "eye oh"}'):
        class _StubSC:
            @staticmethod
            def get(key, default=None):
                return {
                    "podcast_include_intro": "true",
                    "podcast_include_outro": "true",
                    "podcast_name": "Test Show",
                    "site_domain": domain,
                    "tts_domain_tld_pronunciations": tld_map,
                }.get(key, default)

        return _StubSC()

    def test_io_tld_spoken_as_eye_oh(self):
        from services.podcast_service import _spoken_domain

        assert (
            _spoken_domain("gladlabs.io", site_config=self._sc())  # type: ignore[arg-type]
            == "gladlabs dot eye oh"
        )

    def test_unmapped_tld_spoken_as_written(self):
        from services.podcast_service import _spoken_domain

        # "com" is not in the map → plain " dot " join (no regression).
        assert (
            _spoken_domain("example.com", site_config=self._sc())  # type: ignore[arg-type]
            == "example dot com"
        )

    def test_tld_match_is_case_insensitive(self):
        from services.podcast_service import _spoken_domain

        assert (
            _spoken_domain("GLADLABS.IO", site_config=self._sc())  # type: ignore[arg-type]
            == "GLADLABS dot eye oh"
        )

    def test_subdomain_only_tld_rewritten(self):
        from services.podcast_service import _spoken_domain

        assert (
            _spoken_domain("www.gladlabs.io", site_config=self._sc())  # type: ignore[arg-type]
            == "www dot gladlabs dot eye oh"
        )

    def test_empty_map_falls_back_to_dot_join(self):
        from services.podcast_service import _spoken_domain

        assert (
            _spoken_domain("gladlabs.io", site_config=self._sc(tld_map=""))  # type: ignore[arg-type]
            == "gladlabs dot io"
        )

    def test_invalid_json_map_falls_back_to_dot_join(self):
        from services.podcast_service import _spoken_domain

        assert (
            _spoken_domain("gladlabs.io", site_config=self._sc(tld_map="{not json"))  # type: ignore[arg-type]
            == "gladlabs dot io"
        )

    def test_no_dot_domain_unchanged(self):
        from services.podcast_service import _spoken_domain

        # The "our site" fallback has no TLD — leave it alone.
        assert (
            _spoken_domain("our site", site_config=self._sc())  # type: ignore[arg-type]
            == "our site"
        )

    def test_build_outro_uses_spoken_tld(self):
        from services.podcast_service import _build_outro

        outro = _build_outro(site_config=self._sc())  # type: ignore[arg-type]
        assert "Visit gladlabs dot eye oh for more episodes" in outro
        assert "dot io" not in outro


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
    """DB-configurable voice pool (Plan 7, #689).

    Lifts the hardcoded ``VOICE_POOL`` to operator-tunable app_settings
    (``tts_voice_rotation_enabled`` / ``tts_voice_pool``). This resolves the
    *pool*; the rotate-vs-pin decision is ``_select_voice``'s job (see
    ``TestSelectVoice``).
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


class TestSelectVoice:
    """``_select_voice`` honors ``tts_voice_rotation_enabled`` — rotation is OPT-IN.

    Regression for the flag-that-lied bug: the voice was hash-rotated for every
    episode regardless of the flag, so ``podcast_tts_voice`` was dead and the
    podcast (and the video narration that reuses it) rotated unconditionally.
    """

    def test_rotation_off_pins_the_fixed_voice_for_every_key(self):
        # Flag false → the single ``podcast_tts_voice`` for every rotation key.
        sc = SiteConfig(initial_config={
            "tts_voice_rotation_enabled": "false",
            "podcast_tts_voice": "bf_isabella",
        })
        picks = {_select_voice(sc, key) for key in ("post-1", "post-2", "post-3", "zzz")}
        assert picks == {"bf_isabella"}  # no rotation — one voice, always

    def test_rotation_is_off_by_default(self):
        # No flag at all → rotation is opt-in, so still one fixed voice.
        sc = SiteConfig(initial_config={"podcast_tts_voice": "am_michael"})
        picks = {_select_voice(sc, k) for k in ("a", "b", "c", "d", "e")}
        assert picks == {"am_michael"}

    def test_rotation_off_defaults_to_pool_head_when_voice_unset(self):
        sc = SiteConfig(initial_config={"tts_voice_rotation_enabled": "false"})
        assert _select_voice(sc, "anything") == VOICE_POOL[0]

    def test_rotation_on_actually_rotates_across_the_pool(self):
        sc = SiteConfig(initial_config={"tts_voice_rotation_enabled": "true"})
        picks = {_select_voice(sc, f"post-{i}") for i in range(50)}
        assert len(picks) > 1              # it really rotates
        assert picks <= set(VOICE_POOL)    # ...within the pool

    def test_rotation_on_is_deterministic_per_key(self):
        sc = SiteConfig(initial_config={"tts_voice_rotation_enabled": "true"})
        assert _select_voice(sc, "post-1") == _select_voice(sc, "post-1")


class TestGenerateWithVoiceEngineDispatch:
    """``_generate_with_voice`` routes on ``podcast_tts_engine`` (Phase 2 cutover).

    Default/empty stays on the existing Speaches path (regression guard);
    ``chatterbox`` delegates to ``ChatterboxTTSProvider`` with settings pulled
    from ``plugin.tts_provider.chatterbox.*``.
    """

    @pytest.mark.asyncio
    async def test_default_engine_still_uses_speaches_path(self, tmp_path):
        sc = SiteConfig(initial_config={})  # podcast_tts_engine unset

        async def _fake_synthesize_speech(text, *, site_config, output_path, voice):
            Path(output_path).write_bytes(b"KOKORO BYTES")
            return b"KOKORO BYTES"

        svc = PodcastService(output_dir=tmp_path, site_config=sc)
        with patch(
            "services.tts_service.synthesize_speech",
            new=AsyncMock(side_effect=_fake_synthesize_speech),
        ) as speaches_mock, patch(
            "services.tts_providers.chatterbox.ChatterboxTTSProvider.synthesize",
        ) as chatterbox_mock:
            out = tmp_path / "ep.mp3"
            result = await svc._generate_with_voice("hello world", "bf_emma", out)
        speaches_mock.assert_awaited_once()
        chatterbox_mock.assert_not_called()
        assert result.success
        assert out.read_bytes() == b"KOKORO BYTES"

    @pytest.mark.asyncio
    async def test_chatterbox_engine_delegates_to_chatterbox_provider(self, tmp_path):
        sc = SiteConfig(initial_config={
            "podcast_tts_engine": "chatterbox",
            "plugin.tts_provider.chatterbox.base_url": "http://chatterbox:8000/v1",
            "plugin.tts_provider.chatterbox.exaggeration": "0.6",
            "plugin.tts_provider.chatterbox.cfg_weight": "0.4",
            "plugin.tts_provider.chatterbox.audio_prompt_path": "/app/voices/podcast-voice.wav",
        })
        svc = PodcastService(output_dir=tmp_path, site_config=sc)
        out = tmp_path / "ep.mp3"

        async def _fake_synthesize(self, text, output_path, *, voice=None, config=None):
            output_path.write_bytes(b"CHATTERBOX BYTES")
            return TTSResult(
                audio_path=output_path, duration_seconds=42,
                voice=voice or "default", file_size_bytes=17,
                metadata={"engine": "chatterbox"},
            )

        with patch(
            "services.tts_providers.chatterbox.ChatterboxTTSProvider.synthesize",
            new=_fake_synthesize,
        ), patch(
            "services.tts_service.synthesize_speech", new=AsyncMock()
        ) as speaches_mock:
            result = await svc._generate_with_voice("hello world", "bf_emma", out)

        speaches_mock.assert_not_called()
        assert result.success
        assert result.file_path == str(out)
        assert result.duration_seconds == 42
        assert out.read_bytes() == b"CHATTERBOX BYTES"

    @pytest.mark.asyncio
    async def test_chatterbox_engine_forwards_remux_and_loudnorm_settings(
        self, tmp_path
    ):
        """The wiring-gap fix (audio-fidelity investigation): before this,
        podcast_tts_remux_bitrate / podcast_tts_loudnorm_* were read for the
        Speaches path but never forwarded into the Chatterbox provider's
        config — tuning them had zero effect on the engine that's actually
        live in production."""
        sc = SiteConfig(initial_config={
            "podcast_tts_engine": "chatterbox",
            "podcast_tts_remux_bitrate": "256k",
            "podcast_tts_loudnorm_i": "-14",
            "podcast_tts_loudnorm_tp": "-2.0",
            "podcast_tts_loudnorm_lra": "9",
            "podcast_tts_loudnorm_ar": "48000",
        })
        svc = PodcastService(output_dir=tmp_path, site_config=sc)
        out = tmp_path / "ep.mp3"
        captured = {}

        async def _fake_synthesize(self, text, output_path, *, voice=None, config=None):
            captured["config"] = config
            output_path.write_bytes(b"X")
            return TTSResult(
                audio_path=output_path, duration_seconds=1, voice=voice or "default",
                file_size_bytes=1, metadata={"engine": "chatterbox"},
            )

        with patch(
            "services.tts_providers.chatterbox.ChatterboxTTSProvider.synthesize",
            new=_fake_synthesize,
        ):
            await svc._generate_with_voice("hello", "bf_emma", out)

        cfg = captured["config"]
        assert cfg["remux_bitrate"] == "256k"
        assert cfg["loudnorm_i"] == "-14"
        assert cfg["loudnorm_tp"] == "-2.0"
        assert cfg["loudnorm_lra"] == "9"
        assert cfg["loudnorm_ar"] == "48000"

    @pytest.mark.asyncio
    async def test_chatterbox_engine_forwards_loudnorm_enabled_false(self, tmp_path):
        sc = SiteConfig(initial_config={
            "podcast_tts_engine": "chatterbox",
            "podcast_tts_loudnorm_enabled": "false",
        })
        svc = PodcastService(output_dir=tmp_path, site_config=sc)
        out = tmp_path / "ep.mp3"
        captured = {}

        async def _fake_synthesize(self, text, output_path, *, voice=None, config=None):
            captured["config"] = config
            output_path.write_bytes(b"X")
            return TTSResult(
                audio_path=output_path, duration_seconds=1, voice=voice or "default",
                file_size_bytes=1, metadata={"engine": "chatterbox"},
            )

        with patch(
            "services.tts_providers.chatterbox.ChatterboxTTSProvider.synthesize",
            new=_fake_synthesize,
        ):
            await svc._generate_with_voice("hello", "bf_emma", out)

        assert captured["config"]["loudnorm_enabled"] is False

    @pytest.mark.asyncio
    async def test_chatterbox_provider_failure_surfaces_as_error_result(self, tmp_path):
        """A raised provider error becomes EpisodeResult(success=False), never
        an unhandled exception — matches the Speaches path's failure contract."""
        sc = SiteConfig(initial_config={"podcast_tts_engine": "chatterbox"})
        svc = PodcastService(output_dir=tmp_path, site_config=sc)
        with patch(
            "services.tts_providers.chatterbox.ChatterboxTTSProvider.synthesize",
            new=AsyncMock(side_effect=RuntimeError("sidecar down")),
        ):
            result = await svc._generate_with_voice(
                "hello world", "bf_emma", tmp_path / "ep.mp3",
            )
        assert not result.success
        assert "sidecar down" in (result.error or "")


class TestScaffoldDumpGuard:
    """The LLM podcast script must never narrate a leaked prompt/plan.

    gemma-class models sometimes emit their prompt-echo + planning outline +
    self-QA checklist ahead of the narration; a clean script is pure prose, so
    ``_build_script_with_llm`` falls back to the deterministic regex script
    when the output opens with a bullet dump (root cause of a podcast that read
    its own checklist aloud, 2026-07-07).
    """

    # A representative slice of the real a5594ce1 leak: prompt-rule echo + an
    # outline pass + a self-check checklist, then the narration finally begins.
    _DUMP = (
        "Blog article about Speculative Decoding, Draft Model.\n"
        "Podcast script for a single narrator.\n\n"
        " * Text-to-speech ready, exactly what should be spoken.\n"
        " * Natural spoken English, no written/visual conventions.\n"
        " * No URLs, links, image references, or attribution lines.\n"
        " * No markdown formatting, asterisks, or brackets.\n"
        " * NO first person; the narrator presents facts.\n"
        " * *Intro:* Running models locally, the autoregressive pattern.\n"
        " * *Concept:* Draft model proposes, target model verifies.\n"
        " * *Quality/Safety:* Target model makes the decision.\n"
        " * Check: Any markdown? Removed.\n"
        " * Check: Acronyms spelled out? Yes.\n\n"
        "Every token a model produces requires a full forward pass through "
        "every layer of the network, and that is the bottleneck.\n"
    )

    def test_detects_leaked_scaffold_opening(self):
        from services.podcast_service import _looks_like_scaffold_dump
        assert _looks_like_scaffold_dump(self._DUMP) is True

    def test_clean_prose_not_flagged(self):
        from services.podcast_service import _looks_like_scaffold_dump
        clean = (
            "Every token a model produces requires a full forward pass through "
            "every layer of the network. The model cannot guess ahead; it "
            "computes one token, appends it, and starts over. This is the "
            "autoregressive pattern, and it is why a large model feels sluggish."
        )
        assert _looks_like_scaffold_dump(clean) is False

    def test_incidental_dash_not_flagged(self):
        """A single hyphen aside in otherwise-prose output is not a dump."""
        from services.podcast_service import _looks_like_scaffold_dump
        mostly_prose = (
            "Speculative decoding pairs two models instead of one.\n"
            "The draft proposes tokens and the target verifies them.\n"
            " - one incidental aside here\n"
            "The verification step is what keeps the output identical.\n"
            "That is the whole trick, and it is a clean engineering win.\n"
        )
        assert _looks_like_scaffold_dump(mostly_prose) is False

    def test_empty_not_flagged(self):
        from services.podcast_service import _looks_like_scaffold_dump
        assert _looks_like_scaffold_dump("") is False

    async def test_build_script_falls_back_on_dump(self):
        """A scaffold-dump LLM response must be discarded in favour of the
        deterministic fallback script — the scaffold never reaches TTS."""
        from services.podcast_service import _build_script_with_llm

        sc = SiteConfig(initial_config={
            "podcast_script_model": "ollama/gemma-4-31B-it-qat",
        })
        sc._pool = MagicMock()  # non-None → the LLM path is taken

        title = "Speculative Decoding for Local LLM Inference"
        content = (
            "# Speculative Decoding\n\n"
            "Speculative decoding pairs a small draft model with a large target "
            "model. The draft proposes several tokens and the target verifies "
            "them in one parallel pass, so the output is identical to standard "
            "decoding while latency drops. This is a rare pure engineering win."
        )

        dump_result = MagicMock()
        dump_result.text = self._DUMP
        mock_pm = MagicMock()
        mock_pm.get_prompt.return_value = "rewrite prompt"

        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            new=AsyncMock(return_value=dump_result),
        ), patch(
            "services.prompt_manager.get_prompt_manager", return_value=mock_pm,
        ), patch("utils.findings.emit_finding") as mock_finding:
            script = await _build_script_with_llm(title, content, site_config=sc)

        # Fell back to the exact deterministic helper the guard calls.
        assert script == _build_script(title, content, site_config=sc)
        # The scaffold never reaches TTS.
        assert "Text-to-speech ready" not in script
        assert "*Intro:*" not in script
        assert "Check:" not in script
        # The real article content IS spoken, and recovery is observable.
        assert "Speculative decoding pairs" in script
        assert mock_finding.called


class TestResolvePodcastThink:
    """think=False is threaded into the podcast script dispatch by default so the
    gemma-class model's reasoning channel can't leak its planning outline +
    self-QA checklist into the spoken narration (podcast_scaffold_dump root
    cause; mirrors writer #2163 / video director #2191)."""

    def test_default_disables_thinking(self):
        # Unset → default 'true' → disable the reasoning channel.
        from services.podcast_service import _resolve_podcast_think
        assert _resolve_podcast_think(SiteConfig()) is False

    def test_explicit_true_disables_thinking(self):
        from services.podcast_service import _resolve_podcast_think
        sc = SiteConfig(initial_config={"podcast_disable_thinking": "true"})
        assert _resolve_podcast_think(sc) is False

    def test_opt_out_leaves_backend_default(self):
        # Operator opt-out → None → leave the backend default (thinking on);
        # never pin think=True from here.
        from services.podcast_service import _resolve_podcast_think
        sc = SiteConfig(initial_config={"podcast_disable_thinking": "false"})
        assert _resolve_podcast_think(sc) is None

    def test_none_site_config_disables_thinking(self):
        from services.podcast_service import _resolve_podcast_think
        assert _resolve_podcast_think(None) is False


class TestBuildScriptThreadsThink:
    """_build_script_with_llm forwards the resolved think flag to the dispatcher —
    the source fix for podcast_scaffold_dump (the #2186 guard stays the net)."""

    _CLEAN = (
        "Every token a model produces requires a full forward pass through every "
        "layer of the network. The model cannot guess ahead; it computes one "
        "token, appends it, and starts over. That is the autoregressive pattern, "
        "and it is why a large model can feel sluggish even with headroom. "
        "Speculative decoding pairs a small draft model with a large target model "
        "to break that sequential bottleneck without changing the output."
    )

    async def _dispatch_kwargs(self, disable_value):
        """Run _build_script_with_llm with a clean (non-dump) LLM response and
        return the kwargs the dispatcher was called with."""
        from services.podcast_service import _build_script_with_llm

        cfg = {"podcast_script_model": "ollama/gemma-4-31B-it-qat"}
        if disable_value is not None:
            cfg["podcast_disable_thinking"] = disable_value
        sc = SiteConfig(initial_config=cfg)
        sc._pool = MagicMock()  # non-None → the LLM path is taken

        clean_result = MagicMock()
        clean_result.text = self._CLEAN
        mock_pm = MagicMock()
        mock_pm.get_prompt.return_value = "rewrite prompt"
        dispatch = AsyncMock(return_value=clean_result)

        with patch(
            "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
        ), patch(
            "services.prompt_manager.get_prompt_manager", return_value=mock_pm,
        ):
            await _build_script_with_llm("Title", "# Body\n\nprose body", site_config=sc)

        assert dispatch.await_count == 1
        return dispatch.await_args.kwargs

    async def test_default_forwards_think_false(self):
        kwargs = await self._dispatch_kwargs(None)
        assert kwargs.get("think") is False

    async def test_opt_out_omits_think_kwarg(self):
        # Operator opt-out resolves to None → no think kwarg, backend default kept.
        kwargs = await self._dispatch_kwargs("false")
        assert "think" not in kwargs


class TestAppendPodcastCta:
    """The per-medium review CTA (media.cta.podcast) must be appended to the
    manually-regenerated episode, matching the Stage-3 podcast.render path.
    Regression: PodcastService.generate_episode dropped it, so a regenerated
    episode lost the 'rate & review' ask the original render carried.
    """

    _CTA = (
        "If this was useful, follow the show and leave a quick rating or review "
        "on Spotify or Apple Podcasts — it genuinely helps us reach more people."
    )

    def test_appends_cta_when_set(self):
        from services.podcast_service import _append_podcast_cta
        sc = SiteConfig(initial_config={"media.cta.podcast": self._CTA})
        out = _append_podcast_cta(
            "Body text. Thanks for listening. See you next time.", site_config=sc,
        )
        assert out.endswith(self._CTA)
        # The pre-existing generic outro is preserved ahead of the CTA.
        assert "See you next time." in out

    def test_idempotent_no_double_append(self):
        from services.podcast_service import _append_podcast_cta
        sc = SiteConfig(initial_config={"media.cta.podcast": self._CTA})
        once = _append_podcast_cta("Body text.", site_config=sc)
        twice = _append_podcast_cta(once, site_config=sc)
        assert once == twice
        assert once.count(self._CTA) == 1

    def test_noop_when_cta_unset(self):
        from services.podcast_service import _append_podcast_cta
        sc = SiteConfig(initial_config={"media.cta.podcast": ""})
        script = "Body text. See you next time."
        assert _append_podcast_cta(script, site_config=sc) == script


class TestPronunciationsMalformedSurfaces:
    """A malformed tts_pronunciations map silently disabled the WHOLE table
    (one typo → every pronunciation skipped). It must SURFACE as a finding,
    not just a buried log line.
    """

    def test_invalid_pronunciations_emits_finding(self, monkeypatch):
        import utils.findings as findings
        from services.podcast_service import _get_tts_replacements

        calls: list[dict] = []
        monkeypatch.setattr(findings, "emit_finding", lambda **kw: calls.append(kw))

        sc = SiteConfig(initial_config={
            "tts_pronunciations": '{"QA-: "Q A", "iframe", "I frame"}',
            "tts_acronym_replacements": "",
        })
        result = _get_tts_replacements(site_config=sc)

        # Structural transforms still returned (fail-soft), and the breakage
        # surfaces exactly once (deduped) as a finding.
        assert isinstance(result, list) and len(result) > 0
        assert len(calls) == 1
        assert calls[0].get("dedup_key")


def test_default_tts_pronunciations_valid_and_has_model_names():
    """The seeded default must be valid JSON (a typo would disable the whole
    table for fresh installs) and cover the model families the podcasts name.
    """
    import json

    from services.settings_defaults import DEFAULTS

    parsed = json.loads(DEFAULTS["tts_pronunciations"])
    for key in ("GLM", "vLLM", "SDXL"):
        assert key in parsed, f"expected model-name pronunciation for {key!r}"


# ---------------------------------------------------------------------------
# 2026-08-01 normalizer split: scripts stay clean; phonetics live at the TTS
# boundary only.
# ---------------------------------------------------------------------------

from services.podcast_service import (  # noqa: E402
    _normalize_for_script,
    _normalize_for_speech,
)


def _pron_sc():
    import json

    from services.site_config import SiteConfig
    return SiteConfig(initial_config={
        "tts_pronunciations": json.dumps({
            "CI/CD": "See Eye See Dee",
            "GitHub": "git hub",
            "VRAM": "Vee RAM",
        }),
        "tts_acronym_replacements": json.dumps({"SOC": "security operations"}),
    })


class TestNormalizerSplit:
    def test_script_normalizer_keeps_written_forms(self):
        """The generation-side pass must NOT bake phonetics into the stored
        script — 'See Eye See Dee pipeline' / 'git hub Actions' were frozen
        into real scripts with zero audio benefit (the TTS boundary applies
        the map itself)."""
        text = "Our CI/CD pipeline runs on GitHub Actions with 24GB of VRAM. SOC matters."
        out = _normalize_for_script(text, site_config=_pron_sc())
        assert "CI/CD" in out
        assert "GitHub" in out
        assert "VRAM" in out
        assert "SOC" in out
        assert "See Eye" not in out

    def test_speech_normalizer_still_applies_phonetics(self):
        text = "Our CI/CD pipeline runs on GitHub."
        out = _normalize_for_speech(text, site_config=_pron_sc())
        assert "See Eye See Dee" in out
        assert "git hub" in out

    def test_speech_normalizer_idempotent_on_frozen_backlog(self):
        """Pre-split scripts already carry phonetic spellings; the TTS
        boundary re-applies the full pass — it must be a no-op on them."""
        frozen = "the See Eye See Dee pipeline on git hub with Vee RAM limits"
        once = _normalize_for_speech(frozen, site_config=_pron_sc())
        assert once == _normalize_for_speech(once, site_config=_pron_sc())

    def test_script_normalizer_strips_emoji(self):
        text = "Meet the problem 🕒 at Glad Labs 💻🚀 today 📈."
        out = _normalize_for_script(text, site_config=_pron_sc())
        for ch in "🕒💻🚀📈":
            assert ch not in out
        assert "Meet the problem" in out

    def test_script_normalizer_dashes_and_semicolons_to_commas(self):
        text = "five tech giants; Alphabet, Microsoft — the architects; of AI"
        out = _normalize_for_script(text, site_config=_pron_sc())
        assert ";" not in out
        assert "—" not in out
        assert "giants, Alphabet" in out

    def test_script_normalizer_preserves_money_figures(self):
        """The #2876 regression class: '$1.65 trillion' must survive every
        structural pass — a filename rule once ate the decimal and shipped
        '$ trillion' narration."""
        text = "hidden debt of $1.65 trillion versus $159 billion on-book and $400 wasted"
        out = _normalize_for_script(text, site_config=_pron_sc())
        assert "$1.65 trillion" in out
        assert "$159 billion" in out
        assert "$400" in out


# ---------------------------------------------------------------------------
# Digit-adjacent dash handling (_normalize_dashes).
#
# Every "spoken" expectation below is grounded in a 2026-08-24 TTS→STT
# round-trip against the live Chatterbox sidecar: the engine silently DROPPED
# the minus in "-5"/"-500" (meaning inverted) and MERGED digit ranges into one
# wrong number ("9-5 job" → "ninety-five job", "8-16 GB" → "816 GB"); ISO
# dates came out garbled.
#
# That probe read word-word compounds back verbatim and they were recorded as
# fine. They were not: a TTS→STT round-trip is BLIND to a pause, because
# whisper writes one back as nothing. An ffmpeg silencedetect pass over the
# audio (2026-09-01) showed the engine breathes at a compound hyphen —
# 0.10-0.86s of extra internal silence per real script sentence, roughly
# double the pause runs — so compounds now become spaces too. Measure the
# audio for timing questions and the transcript for word questions.
# ---------------------------------------------------------------------------


class TestNormalizeDashes:
    # -- the four confirmed live failures ----------------------------------
    @pytest.mark.parametrize("text,expected", [
        ("The temperature dropped to -5 degrees overnight.",
         "The temperature dropped to negative 5 degrees overnight."),
        ("We set the priority to -500 for the database.",
         "We set the priority to negative 500 for the database."),
        ("Most people work a 9-5 job.", "Most people work a 9 to 5 job."),
        ("The card needs 8-16 GB of VRAM.", "The card needs 8 to 16 GB of VRAM."),
    ])
    def test_confirmed_failure_modes(self, text, expected):
        from services.podcast_service import _normalize_dashes
        assert _normalize_dashes(text, site_config=_TEST_SC) == expected

    # -- range readings -----------------------------------------------------
    @pytest.mark.parametrize("text,expected", [
        ("expect 10-20% savings", "expect 10 to 20% savings"),
        ("ran from 2024-2026", "ran from 2024 to 2026"),
        ("the final score was 3-2", "the final score was 3 to 2"),
        ("open 9:00-17:30 daily", "open 9:00 to 17:30 daily"),
        ("a 1.5-2.0 ratio", "a 1.5 to 2.0 ratio"),
        ("costs $5-10 per month", "costs $5 to 10 per month"),
        # Spaced ASCII hyphen between digits is a range, not a pause.
        ("takes 5 - 10 minutes", "takes 5 to 10 minutes"),
        # Unspaced en dash is range typography.
        ("the 2024–2026 roadmap", "the 2024 to 2026 roadmap"),
    ])
    def test_ranges_become_to(self, text, expected):
        from services.podcast_service import _normalize_dashes
        assert _normalize_dashes(text, site_config=_TEST_SC) == expected

    # -- negative readings --------------------------------------------------
    @pytest.mark.parametrize("text,expected", [
        ("(-40 dB threshold)", "(negative 40 dB threshold)"),
        ("scored -0.1 on the gate", "scored negative 0.1 on the gate"),
        ("a swing of -$3 million", "a swing of negative $3 million"),
        # U+2212 real minus sign reads the same as ASCII.
        ("it hit −5 overnight", "it hit negative 5 overnight"),
    ])
    def test_negative_numbers(self, text, expected):
        from services.podcast_service import _normalize_dashes
        assert _normalize_dashes(text, site_config=_TEST_SC) == expected

    # -- ISO dates ----------------------------------------------------------
    def test_iso_date_spoken(self):
        from services.podcast_service import _normalize_dashes
        out = _normalize_dashes("It shipped on 2026-05-04, on schedule.", site_config=_TEST_SC)
        assert out == "It shipped on May 4, 2026, on schedule."

    def test_pseudo_date_falls_through_to_range(self):
        """A month of 99 is not a date — the generic range reading applies
        rather than a fabricated month name."""
        from services.podcast_service import _normalize_dashes
        out = _normalize_dashes("build 2026-99-01 failed", site_config=_TEST_SC)
        assert "May" not in out
        assert out == "build 2026 to 99 to 01 failed"

    # -- what must NOT change -----------------------------------------------
    @pytest.mark.parametrize("text", [
        # Word-word compounds USED to be listed here as untouched, on the
        # strength of a TTS→STT round-trip that read them back verbatim. That
        # probe could not see the actual failure — whisper writes a pause back
        # as nothing — and an ffmpeg silencedetect pass over the audio later
        # showed the engine does breathe at a compound hyphen. They now become
        # spaces; see the compound-hyphen tests below.
        # Letter-digit hyphens are spoken acceptably; leave them.
        "the COVID-19 era",
        "a top-10 list",
        "an RTX 5090-class GPU",
        # A dash after a word character is never a minus.
        "the pre-2026 baseline",
    ])
    def test_untouched_forms(self, text):
        from services.podcast_service import _normalize_dashes
        assert _normalize_dashes(text, site_config=_TEST_SC) == text

    def test_spaced_em_dash_between_numbers_stays_a_pause(self):
        """'in 2024 — 12 people came' is an aside, not a range: the dash pass
        leaves it, and the structural pass downstream turns it into a comma."""
        from services.podcast_service import _normalize_dashes
        text = "in 2024 — 12 people came"
        assert _normalize_dashes(text, site_config=_TEST_SC) == text
        full = _normalize_for_speech(text, site_config=_TEST_SC)
        assert " to " not in full
        assert "," in full

    # -- compound hyphens ---------------------------------------------------
    # These live in _space_compound_hyphens, NOT _normalize_dashes, because
    # they must run after every pass that matches on the written form.
    @pytest.mark.parametrize("text,expected", [
        ("a state-of-the-art setup", "a state of the art setup"),
        ("a self-hosted, low-cost stack", "a self hosted, low cost stack"),
        ("our decision-making process", "our decision making process"),
        # A space, never a deletion: joining these would say "resign".
        ("re-sign the contract", "re sign the contract"),
        ("e-mail and X-ray", "e mail and X ray"),
    ])
    def test_compound_hyphen_becomes_a_space(self, text, expected):
        from services.podcast_service import _space_compound_hyphens
        assert _space_compound_hyphens(text, site_config=_TEST_SC) == expected

    @pytest.mark.parametrize("text", [
        # Digit-adjacent hyphens belong to _normalize_dashes, not this rule.
        "the COVID-19 era",
        "a top-10 list",
        "an RTX 5090-class GPU",
        "the pre-2026 baseline",
    ])
    def test_compound_rule_never_touches_digit_adjacent_hyphens(self, text):
        from services.podcast_service import _space_compound_hyphens
        assert _space_compound_hyphens(text, site_config=_TEST_SC) == text

    def test_compound_and_digit_rules_compose(self):
        """Both fire in the full pass without eating each other's dashes."""
        out = _normalize_for_speech("a 9-5 job in a self-hosted rack", site_config=_TEST_SC)
        assert out == "a 9 to 5 job in a self hosted rack"

    def test_compound_rule_has_its_own_switch(self):
        sc = SiteConfig(initial_config={
            "tts_compound_hyphen_to_space_enabled": "false",
        })
        # Own switch off: compounds keep their hyphen, digit rules still run.
        assert _normalize_for_speech("a 9-5 state-of-the-art job", site_config=sc) == (
            "a 9 to 5 state-of-the-art job"
        )

    def test_master_switch_also_disables_the_compound_rule(self):
        from services.podcast_service import _space_compound_hyphens
        sc = SiteConfig(initial_config={"tts_dash_normalization_enabled": "false"})
        text = "a self-hosted rack"
        assert _space_compound_hyphens(text, site_config=sc) == text

    def test_compound_rule_is_idempotent(self):
        once = _normalize_for_speech("a state-of-the-art setup", site_config=_TEST_SC)
        assert _normalize_for_speech(once, site_config=_TEST_SC) == once

    def test_compound_rule_runs_after_the_url_and_filename_strip(self):
        r"""Ordering guard. Spacing compounds BEFORE the structural pass breaks
        `https?://\S+` and the filename rule, stranding half a token for the
        engine to read aloud."""
        assert _normalize_for_speech(
            "Visit https://my-site.com today", site_config=_TEST_SC
        ).strip() == "Visit today"
        # Pre-existing bug fixed alongside: the filename class omitted '-', so
        # "my-notes.md" matched only "notes.md" and left "my-" spoken.
        assert _normalize_for_speech(
            "read my-notes.md now", site_config=_TEST_SC
        ).strip() == "read now"
        assert _normalize_for_speech(
            "the file src/my-module/thing.py is here", site_config=_TEST_SC
        ).strip() == "the file is here"

    # -- config surface -----------------------------------------------------
    def test_disabled_flag_leaves_text_alone(self):
        from services.podcast_service import _normalize_dashes
        sc = SiteConfig(initial_config={"tts_dash_normalization_enabled": "false"})
        text = "a 9-5 job at -5 degrees on 2026-05-04"
        assert _normalize_dashes(text, site_config=sc) == text

    def test_custom_words(self):
        from services.podcast_service import _normalize_dashes
        sc = SiteConfig(initial_config={
            "tts_negative_number_word": "minus",
            "tts_number_range_word": "through",
        })
        out = _normalize_dashes("9-5 at -5 degrees", site_config=sc)
        assert out == "9 through 5 at minus 5 degrees"

    # -- placement in the chain --------------------------------------------
    def test_speech_pass_applies_dash_rules_end_to_end(self):
        """The full render-boundary pass must produce the spoken forms — the
        raw dash tokens were what the engine mangled."""
        out = _normalize_for_speech(
            "A 9-5 job at -5 degrees, shipped 2026-05-04.", site_config=_TEST_SC
        )
        assert "9 to 5" in out
        assert "negative 5 degrees" in out
        assert "May 4, 2026" in out

    def test_stored_script_keeps_written_forms(self):
        """Dash readings are speech opinions: the generation-side pass keeps
        the written forms in the stored script (2026-08-01 split)."""
        out = _normalize_for_script("a 9-5 job at -5 degrees", site_config=_TEST_SC)
        assert "9-5" in out
        assert "-5 degrees" in out

    def test_model_names_collapse_before_dash_rules(self):
        """gemma-4-31B is a model pin, not two ranges — the model-name pass
        runs first and removes its dashes before the range rule can fire."""
        out = _normalize_for_speech("we run gemma-4-31B locally", site_config=_TEST_SC)
        assert "gemma 4 31B" in out
        assert " to " not in out

    def test_idempotent_on_already_spoken_forms(self):
        from services.podcast_service import _normalize_dashes
        once = _normalize_dashes(
            "9-5 at -5 degrees on 2026-05-04", site_config=_TEST_SC
        )
        assert _normalize_dashes(once, site_config=_TEST_SC) == once

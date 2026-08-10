"""Duplicate-episode-title guard — the show intro names the episode ONCE.

``_build_intro`` announces "Welcome to {show}. Today's episode: {title}.", but
the script model is handed ``ARTICLE TITLE:`` and routinely opens with its own
greeting or a bare title echo, so the prepended intro said the name twice. The
strings below are the real openings of episodes rendered 2026-08-06..08.

Two boundaries are covered because a Stage-1 script persists for days before
Stage-3 renders it: the guard runs where the intro is prepended AND again at
render time, so the already-written backlog self-heals instead of stuttering
forever behind a fixed generator.
"""
from __future__ import annotations

import pytest

from services.podcast_service import (
    _wrap_with_intro_outro,
    dedupe_episode_title,
)
from services.site_config import SiteConfig

SHOW = "Glad Labs Podcast"


def _sc(**over) -> SiteConfig:
    cfg = {"podcast_name": SHOW, "site_domain": "gladlabs.io"}
    cfg.update({k: str(v) for k, v in over.items()})
    return SiteConfig(initial_config=cfg)


def _wrapped(title: str, body: str) -> str:
    return f"Welcome to {SHOW}. Today's episode: {title}.\n\n{body}"


@pytest.mark.unit
class TestRenderBoundaryRepair:
    """Already-wrapped Stage-1 artifacts — the title is recovered from the
    intro line itself, so the render boundary needs no title argument."""

    def test_strips_greeting_that_names_the_episode(self):
        # task 69c3fb56 — the episode Matt reported.
        out = dedupe_episode_title(
            _wrapped(
                "The tell in the pipeline",
                "Welcome to today's episode, titled The Tell in the "
                "Pipeline.\n\nIt started during a prompt tuning session.",
            ),
            site_config=_sc(),
        )
        assert out == _wrapped(
            "The tell in the pipeline",
            "It started during a prompt tuning session.",
        )

    def test_strips_bare_title_echo(self):
        # task 74941e39.
        out = dedupe_episode_title(
            _wrapped(
                "The Gap Nobody Names",
                "The Gap Nobody Names.\n\nImagine building a pipeline.",
            ),
            site_config=_sc(),
        )
        assert out == _wrapped("The Gap Nobody Names", "Imagine building a pipeline.")

    def test_echo_sharing_a_line_with_narration_keeps_the_narration(self):
        out = dedupe_episode_title(
            _wrapped(
                "The Gap Nobody Names",
                "The Gap Nobody Names. Imagine building a pipeline.",
            ),
            site_config=_sc(),
        )
        assert out == _wrapped("The Gap Nobody Names", "Imagine building a pipeline.")

    def test_clean_script_is_untouched(self):
        # task 1bdf0360 — the model opened on the story, as asked.
        script = _wrapped(
            "The framing that actually holds up",
            "You have seen the graph. Somebody posts their revenue chart.",
        )
        assert dedupe_episode_title(script, site_config=_sc()) == script

    def test_idempotent(self):
        script = _wrapped(
            "The Gap Nobody Names",
            "The Gap Nobody Names.\n\nImagine building a pipeline.",
        )
        once = dedupe_episode_title(script, site_config=_sc())
        assert dedupe_episode_title(once, site_config=_sc()) == once

    def test_greeting_then_bare_echo_both_go(self):
        out = dedupe_episode_title(
            _wrapped(
                "The Gap Nobody Names",
                "Welcome to today's episode, The Gap Nobody Names.\n\n"
                "The Gap Nobody Names.\n\nBody text.",
            ),
            site_config=_sc(),
        )
        assert out == _wrapped("The Gap Nobody Names", "Body text.")

    def test_no_canonical_intro_and_no_title_is_a_noop(self):
        # A body-only narration script (the video sibling) carries no intro to
        # recover a title from — nothing to compare against, so nothing moves.
        script = "The Gap Nobody Names.\n\nImagine building a pipeline."
        assert dedupe_episode_title(script, site_config=_sc()) == script

    def test_intro_from_a_different_show_name_is_left_alone(self):
        script = _wrapped("Some Title", "Some Title.\n\nBody.")
        assert dedupe_episode_title(script, site_config=_sc(
            podcast_name="A Different Show")) == script


@pytest.mark.unit
class TestOverStripGuards:
    def test_sentence_merely_opening_with_the_title_survives(self):
        body = "The Gap Nobody Names is the wall every team hits.\n\nMore text."
        assert dedupe_episode_title(
            body, spoken_title="The Gap Nobody Names", site_config=_sc(),
        ) == body

    def test_never_empties_the_script(self):
        assert dedupe_episode_title(
            "The Gap Nobody Names.",
            spoken_title="The Gap Nobody Names",
            site_config=_sc(),
        ) == "The Gap Nobody Names."

    def test_long_opening_paragraph_is_not_an_echo(self):
        body = (
            "The Gap Nobody Names. " + "Filler narration that runs on. " * 12
        )
        assert dedupe_episode_title(
            body, spoken_title="The Gap Nobody Names", site_config=_sc(),
        ) == body

    def test_empty_and_blank_scripts_round_trip(self):
        for raw in ("", "   ", "\n\n"):
            assert dedupe_episode_title(
                raw, spoken_title="X", site_config=_sc()) == raw

    def test_greeting_without_the_title_survives(self):
        # "welcome to" alone is not enough — the line must name THIS episode,
        # or a legitimate scene-setting opener gets eaten. The cost is that a
        # bare "Welcome to today's episode." (never seen in the corpus) is left
        # in; that is the deliberately safe side of the trade.
        body = "Welcome to the era of cheap inference.\n\nBody."
        assert dedupe_episode_title(
            body, spoken_title="The Gap Nobody Names", site_config=_sc(),
        ) == body


@pytest.mark.unit
class TestGenerationBoundary:
    """``_wrap_with_intro_outro`` dedupes before prepending, so the duplicate
    never gets baked into the persisted artifact in the first place."""

    @pytest.mark.parametrize(
        "body",
        [
            "The Gap Nobody Names.\n\nImagine building a pipeline.",
            "The Gap Nobody Names. Imagine building a pipeline.",
            "Welcome to today's episode, titled The Gap Nobody Names.\n\n"
            "Imagine building a pipeline.",
            "Imagine building a pipeline.",
        ],
    )
    def test_title_announced_exactly_once(self, body):
        out = _wrap_with_intro_outro(
            body, "The Gap Nobody Names", site_config=_sc(),
        )
        assert out.count("The Gap Nobody Names") == 1
        assert out.startswith(
            f"Welcome to {SHOW}. Today's episode: The Gap Nobody Names.")
        assert "Imagine building a pipeline." in out

    def test_intro_disabled_leaves_the_body_verbatim(self):
        body = "The Gap Nobody Names.\n\nImagine building a pipeline."
        out = _wrap_with_intro_outro(
            body, "The Gap Nobody Names",
            site_config=_sc(podcast_include_intro="false",
                            podcast_include_outro="false"),
        )
        assert out == body


@pytest.mark.unit
class TestConfigurablePhrases:
    def test_phrase_list_is_db_tunable(self):
        body = "Bienvenidos al episodio de hoy, The Gap Nobody Names.\n\nCuerpo."
        sc = _sc(podcast_intro_echo_phrases="bienvenidos al episodio")
        assert dedupe_episode_title(
            body, spoken_title="The Gap Nobody Names", site_config=sc,
        ) == "Cuerpo."

    def test_empty_phrase_list_still_catches_bare_echoes(self):
        body = "The Gap Nobody Names.\n\nBody."
        sc = _sc(podcast_intro_echo_phrases="")
        assert dedupe_episode_title(
            body, spoken_title="The Gap Nobody Names", site_config=sc,
        ) == "Body."

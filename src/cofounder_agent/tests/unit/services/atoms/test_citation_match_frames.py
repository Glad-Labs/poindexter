"""Attribution frames added 2026-09-14 — the shapes the writer actually used
for fabricated sources that week, plus the false-positive guards that keep
the frames from reading topic words as sources."""

from __future__ import annotations

import pytest

from poindexter.modules.content.atoms._citation_match import (
    find_attributions,
    find_unmatched_attributions,
    parse_corpus,
)

_CORPUS = parse_corpus(
    "- [The complete guide](https://dev.to/sreeraj/the-complete-guide-to-local-llm-inference): Jul 2026\n"
    "- [llama.cpp](https://github.com/ggerganov/llama.cpp): repo\n"
    "- [Why Zero-Click Content](https://www.linkedin.com/pulse/why-zero-click-content-c9aic/): Mar 12, 2026\n"
)


@pytest.mark.parametrize("text,expected", [
    # the four phantom sources from draft 38cba265 (QA 97)
    ("the VRLA Tech piece is right that this is the actual fork in the road.", ["VRLA Tech"]),
    ("The VRLA Tech comparison frames it as the tool you reach for.", ["VRLA Tech"]),
    ("it's the actual conclusion the AiCybr writeup lands on too.", ["AiCybr"]),
    ("a lot of memory headroom, according to the breakdown at Tutorials Point.", ["Tutorials Point"]),
    # classic shapes still work
    ("According to Hootsuite, give the audience the value up front.", ["Hootsuite"]),
    ("A 2026 report from Gartner puts the number at 40%.", ["Gartner"]),
    ("The 2026 Gartner report puts it at 40%.", ["Gartner"]),
    ("As noted by M. Huzaifa Rizwan, the trend continues.", ["M. Huzaifa Rizwan"]),
])
def test_fabricated_source_shapes_are_flagged(text, expected):
    assert find_unmatched_attributions(text, _CORPUS) == expected


@pytest.mark.parametrize("text", [
    # a real corpus source, unlinked: detected as an attribution (so the repair
    # pass can link it) but NOT flagged as fabricated
    "And per a recent LinkedIn analysis, AI-driven search has made this worse.",
    # already linked
    "as [the team at Chad Wyatt lays out](https://chad-wyatt.com/seo/), the CTR is dying.",
    # units and topic acronyms are not sources
    "We measured 120 tokens per GPU per second across the fleet.",
    "The AI report card for local models is mixed; the GPU benchmark says otherwise.",
    "The LLM guide we wrote covers quantization.",
    # "on <topic>" names a subject, not a source
    "I read an article on Kubernetes networking last week.",
    # first-person and rhetoric
    "Our analysis shows the gap is residency, not decode speed.",
])
def test_guards_do_not_flag(text):
    assert find_unmatched_attributions(text, _CORPUS) == []


def test_per_frame_surfaces_a_corpus_source_for_repair():
    subjects = [a.subject for a in find_attributions(
        "And per a recent LinkedIn analysis, AI-driven search has made this worse.", _CORPUS,
    )]
    assert subjects == ["LinkedIn"]


def test_sentence_final_period_is_not_part_of_the_subject():
    subjects = [a.subject for a in find_attributions("according to Hootsuite.", _CORPUS)]
    assert subjects == ["Hootsuite"]

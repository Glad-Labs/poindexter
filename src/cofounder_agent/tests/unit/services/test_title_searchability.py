"""services/title_searchability — the deterministic "does the title name a thing" gate.

Calibrated on the real August-2026 titles (zero clicks) versus the pages that
actually earned clicks in the trailing 60 days.
"""

from __future__ import annotations

import pytest

from services.title_searchability import (
    find_searchable_entities,
    has_searchable_entity,
    keyword_terms,
    render_entity_directive,
)

# Every page with a click, last 60 days (2026-09-07 GSC pull), with the
# primary keyword the pipeline would have handed the gate ("" = none needed).
_CLICKED = [
    ("The Memory Scaling Question: DDR5 6400 vs. 8000 on Ryzen 9", ""),
    ("CadQuery: Parametric 3D Design with Pure Python", ""),
    ("FastAPI Best Practices You Are Probably Ignoring", ""),
    ("From Data Silos to Smart Answers: Building a Local RAG Pipeline with Ollama", ""),
    ("The 32GB Threshold: How the RTX 5090 Redefines Local LLM Development", ""),
    ("Choosing a quantization format for local LLM inference: GGUF Q4_K_M vs AWQ", ""),
    ("Chatterbox swallows minus signs", ""),
    ("Postgres vacuum debt, measured", ""),
    # Title Case hides plain proper nouns; the article's keyword admits them.
    ("Mechanical Keyboard Switches Explained: Linear vs Tactile vs Clicky", "mechanical keyboard switches"),
    ("Writing a Python Interpreter in Python: From Bytecode to Execution", "python interpreter"),
    ("Why Solo Developers Should Embrace Docker Containers", "docker containers"),
]

# August-2026 cohort titles: 0 clicks, ~3 impressions each.
_UNSEARCHABLE = [
    "The Gap Nobody Names",
    "The Stuck Task",
    "The five days nobody was watching",
    "The Echoing Title Bug",
    "Why 'Make More Money' Isn't a Goal -- It's an Antigoal Waiting to Be Defined",
    "Reading Time Is Now a Ranking Signal",
    "When the Logs Go Silent",
]


@pytest.mark.parametrize(("title", "keyword"), _CLICKED)
def test_every_clicked_title_passes(title, keyword):
    report = has_searchable_entity(title, primary_keyword=keyword)
    assert report.ok, (title, report)


def test_title_case_plain_capitals_are_not_evidence():
    # Every word capitalised by convention: "Stuck"/"Task" prove nothing.
    r = find_searchable_entities("The Stuck Task")
    assert not r.ok
    # ...but the same words in sentence case ARE a name signal.
    assert find_searchable_entities("Prefect stuck task").ok
    assert find_searchable_entities("Chatterbox swallows minus signs").ok


def test_sentence_case_gerund_or_short_opener_is_grammar_not_a_name():
    assert not find_searchable_entities("Building a better pipeline").ok
    assert not find_searchable_entities("Why nobody was watching").ok


@pytest.mark.parametrize("title", _UNSEARCHABLE)
def test_every_august_zero_click_title_fails_without_keywords(title):
    report = find_searchable_entities(title)
    assert not report.ok, (title, report.entities)


def test_digit_tokens_count():
    r = find_searchable_entities("A 4-bit model just beat its full-precision original")
    assert r.ok and "4-bit" in r.entities and "digit" in r.reasons


def test_internal_capitals_and_all_caps_count_even_sentence_initial():
    assert find_searchable_entities("FastAPI is not your problem").ok
    assert find_searchable_entities("RAG without a reranker").ok
    assert find_searchable_entities("LangGraph checkpoints, explained").ok


def test_stopword_openers_never_count_even_in_sentence_case():
    assert not find_searchable_entities("Why nobody was watching").ok
    assert not find_searchable_entities("The gap: nobody names it").ok


def test_stopwords_capitalised_by_title_case_do_not_count():
    assert not find_searchable_entities("The Thing That Nobody Wants To Say").ok


def test_keyword_terms_admit_lowercase_technical_words():
    kw = keyword_terms(primary_keyword="local llm quantization", tags=["GGUF"], topic="")
    assert "quantization" in kw and "gguf" in kw
    assert "local" in kw  # 5 letters, not a stopword — a real term
    r = find_searchable_entities("a field guide to quantization", keyword_terms=kw)
    assert r.ok and r.reasons == ("keyword",)


def test_keyword_terms_drop_stopwords_and_short_fragments():
    kw = keyword_terms(primary_keyword="the way to go", topic="an old thing")
    assert kw == ()


def test_topic_is_only_a_last_resort_keyword_source():
    # With a keyword present, the directive-shaped topic contributes nothing…
    kw = keyword_terms(primary_keyword="prefect", topic="expand coverage of stuck flows")
    assert kw == ("prefect",)
    # …so a topic word cannot launder an unsearchable title through rule 3.
    assert not has_searchable_entity(
        "The Stuck Task", primary_keyword="prefect", tags=["prefect"], topic="prefect stuck flow reclaim",
    ).ok
    # With no keyword and no tags, the topic is all we have.
    assert has_searchable_entity("The stuck task", topic="prefect stuck flow reclaim").ok


def test_has_searchable_entity_wrapper_matches_find():
    r = has_searchable_entity(
        "The Stuck Task", primary_keyword="prefect stuck flow", tags=[], topic="",
    )
    # "stuck" is in the keyword set → passes via rule 3, with the reason recorded.
    assert r.ok and "keyword" in r.reasons


def test_empty_title_fails_with_reason():
    r = find_searchable_entities("")
    assert not r.ok and r.reasons == ("empty title",)


def test_report_as_dict_is_json_shaped():
    d = find_searchable_entities("RTX 5090 local LLM").as_dict()
    assert set(d) == {"ok", "entities", "reasons", "keyword_terms"}
    assert d["ok"] is True and "5090" in d["entities"]


def test_directive_names_article_terms_and_the_rejected_title():
    text = render_entity_directive(
        primary_keyword="rtx 5090 local llm",
        tags=["vram"],
        topic="ignored when terms exist",
        rejected_title="The Gap Nobody Names",
        heading_terms=["Ollama", "32GB"],
    )
    assert "SEARCHABILITY" in text
    assert "'The Gap Nobody Names'" in text
    assert "rtx 5090 local llm" in text and "vram" in text and "Ollama" in text
    assert "ignored when terms exist" not in text


def test_directive_falls_back_to_topic_when_nothing_else_is_known():
    text = render_entity_directive(topic="prefect stuck flows")
    assert "prefect stuck flows" in text

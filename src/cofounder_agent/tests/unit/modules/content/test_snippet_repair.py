"""Search snippet repair (2026-10-28): what counts as broken, what a fix must pass."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.modules.content import snippet_repair as sr

CUT = ("Stop relying on benchmarks alone to choose your inference engine. Learn why "
       "llama.cpp excels at local flexibility while vLLM and SGLang are built for")


def test_a_long_description_without_closing_punctuation_is_cut():
    assert sr.description_is_cut(CUT)
    assert not sr.description_is_cut(CUT + " serving many users.")
    assert not sr.description_is_cut("Short and unpunctuated")


def test_the_dev_diary_placeholder_is_broken():
    assert sr.description_is_placeholder("_What we shipped on 2026-08-01_")
    assert not sr.description_is_placeholder("What we shipped and why it mattered.")


@pytest.mark.parametrize(("seo", "full", "cut"), [
    ("Llama.cpp vs vLLM vs SGLang: Choosing Between Local",
     "Llama.cpp vs vLLM vs SGLang: Choosing Between Local and Server Inference", True),
    ("Mechanical Keyboard Switches Explained: Linear vs Tactile",
     "Mechanical Keyboard Switches Explained: Linear vs Tactile vs Clicky for Programming", True),
    # Stops at a natural break: a whole title on its own.
    ("Why Your Favorite Indie Game Stopped Getting Updates",
     "Why Your Favorite Indie Game Stopped Getting Updates: The Live-Service Trap", False),
    ("Claude Is Not Your Architect. Stop", "Claude Is Not Your Architect. Stop.", False),
    ("Same Title", "Same Title", False),
    ("Different", "Not a prefix at all", False),
])
def test_seo_title_is_cut_only_mid_phrase(seo, full, cut):
    assert sr.seo_title_is_cut(seo, full) is cut


def test_a_leaked_batch_label_is_stripped():
    title = "Why Your Favorite Indie Game Stopped Getting Updates: The Live-Service Trap (2026-05-11 17:48 batch C #5)"
    assert sr.strip_batch_label(title).endswith("The Live-Service Trap")
    assert sr.diagnose({"title": title, "seo_title": "", "seo_description": "Fine."}) == ["title"]


@pytest.mark.parametrize(("text", "ok"), [
    ("Compare vLLM, SGLang and llama.cpp to pick the right engine for local or multi-user serving.", True),
    ("* The surprising diversity of revenue streams beyond typical SaaS subscriptions.", False),
    ("## Overview of the approach taken in this article and why it works.", False),
    (CUT, False),
    ("Too short.", False),
])
def test_a_replacement_description_must_be_complete_prose(text, ok):
    assert sr.usable_description(text) is ok


@pytest.mark.parametrize(("text", "ok"), [
    ("vLLM vs SGLang vs llama.cpp: Choosing the Right Engine", True),
    ("What You'll Learn", False),
    ("Short", False),
    ("A" * 61, False),
])
def test_a_replacement_title_must_be_a_real_title(text, ok):
    assert sr.usable_title(text) is ok


@pytest.mark.asyncio
async def test_plan_repair_changes_only_broken_fields_and_reports_weak_output():
    post = {"id": "p1", "slug": "s", "title": "Llama.cpp vs vLLM vs SGLang: Choosing Between Local and Server",
            "seo_title": "Llama.cpp vs vLLM vs SGLang: Choosing Between Local",
            "seo_description": CUT, "content": "body"}
    generated = {"seo_title": "What You'll Learn",
                 "seo_description": "Compare vLLM, SGLang and llama.cpp to pick the right engine for your setup."}
    with patch("poindexter.modules.content.atoms.seo_generate_all_metadata.run",
               AsyncMock(return_value=generated)):
        rep = await sr.plan_repair(post, site_config=MagicMock(), pool=None)
    assert set(rep.changes) == {"seo_description"}
    assert rep.changes["seo_description"][1].startswith("Compare vLLM")
    assert rep.unfixed and rep.unfixed[0].startswith("seo_title")


@pytest.mark.asyncio
async def test_a_post_with_nothing_broken_makes_no_llm_call():
    post = {"id": "p1", "slug": "s", "title": "Fine Title", "seo_title": "Fine Title",
            "seo_description": "A complete description.", "content": "body"}
    with patch("poindexter.modules.content.atoms.seo_generate_all_metadata.run", AsyncMock()) as run:
        rep = await sr.plan_repair(post, site_config=MagicMock(), pool=None)
    run.assert_not_awaited()
    assert rep.changes == {} and rep.unfixed == []

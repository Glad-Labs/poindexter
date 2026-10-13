"""A Short's own title + description lead (services/short_package.py).

The accepted fixtures are real replies from the production director model
(gemma-4-31B) for the ten Shorts live on 2026-10-13; the rejected ones are the
shapes the checks exist for, several taken from the titles those Shorts were
actually published under.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.services import short_package as sp

_GPT_OSS_LONG = "More Than a Year After gpt-oss, OpenAI Has No Successor. Build As If None Is Coming"
_GPT_OSS_SOURCE = (
    "OpenAI released gpt-oss in August 2025. 350 days later there is no successor, "
    "while Kimi and Qwen ship. Three hundred fifty days have passed since OpenAI's last "
    "open-weights language model release."
)


class _SC:
    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


# ---------------------------------------------------------------------------
# title checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    "OpenAI gpt-oss: No new models in 350 days",
    "Title: OpenAI gpt-oss: No new models in 350 days",
    '"OpenAI gpt-oss: No new models in 350 days."',
    "**OpenAI gpt-oss: No new models in 350 days** #Shorts",
    "OpenAI gpt-oss: No new models in 350 days\n\nThis title leads with the subject.",
])
def test_a_measured_title_survives_its_wrapping(raw):
    title, reason = sp.clean_short_title(raw, long_title=_GPT_OSS_LONG, source_text=_GPT_OSS_SOURCE)
    assert (title, reason) == ("OpenAI gpt-oss: No new models in 350 days", "")


@pytest.mark.parametrize("raw, why", [
    ("", "empty reply"),
    ("Here is a title for the Short", "instruction echo"),
    # Over budget is REJECTED, never cut: a cut title is the defect this replaces.
    ("DeepSeek and Huawei's open-source tools for Ascend chips signify a shift in how AI software gets built",
     "characters; the limit is 70"),
    ("OpenAI gpt-oss: No new models in 400 days", "number '400' is not in the post"),
    ("RTX 5090", "under three words"),
    ("Welcome to the era of local LLMs", "describes the video"),
    # A run-up is stripped, and what is left must still name something.
    ("In this video, we explain things", "names nothing a viewer would search for"),
    ("Why it matters more than you think", "names nothing a viewer would search for"),
    ("OpenAI Has No Successor After gpt-oss", "repeats the full video's title"),
])
def test_a_title_that_cannot_be_published_says_why(raw, why):
    title, reason = sp.clean_short_title(raw, long_title=_GPT_OSS_LONG, source_text=_GPT_OSS_SOURCE)
    assert title == "" and why in reason, reason


def test_a_run_up_is_stripped_when_a_whole_title_remains():
    title, _ = sp.clean_short_title(
        "In today's AI race, OpenAI gpt-oss has no successor after 350 days",
        long_title="Something else entirely", source_text=_GPT_OSS_SOURCE,
    )
    assert title == "OpenAI gpt-oss has no successor after 350 days"


def test_a_lowercase_subject_counts_when_it_is_the_posts_keyword():
    title, reason = sp.clean_short_title(
        "quantization makes a big model fit on one card", long_title="t", source_text="",
        keywords=["quantization"],
    )
    assert title and not reason


def test_the_title_limit_is_the_setting_not_a_constant():
    raw = "OpenAI gpt-oss: No new models in 350 days"
    assert sp.clean_short_title(raw, long_title="x", source_text=_GPT_OSS_SOURCE, max_chars=30)[0] == ""


# ---------------------------------------------------------------------------
# description checks
# ---------------------------------------------------------------------------

_LEAD = (
    "OpenAI hasn't released a new open-weights model since gpt-oss 350 days ago. "
    "This gap allows competitors like Kimi and Qwen to capture mindshare."
)


def test_a_measured_lead_passes():
    text, reason = sp.clean_short_description(_LEAD, title="t", source_text=_GPT_OSS_SOURCE)
    assert (text, reason) == (_LEAD, "")


@pytest.mark.parametrize("raw, why", [
    ("", "empty reply"),
    ("Read more at https://gladlabs.io/posts/x for details.", "carries a link"),
    ("OpenAI has no successor to gpt-oss. #AI #OpenAI", "carries a hashtag"),
    ("In this video, we look at why OpenAI stopped shipping open models.", "describing the video"),
    ("Discover how OpenAI stopped shipping open models.", "describing the video"),
    ("Interestingly, OpenAI stopped shipping open models.", "run-up"),
    ("OpenAI has shipped nothing open in 500 days.", "number '500'"),
    ("x " * 200, "the limit is 300"),
])
def test_a_lead_that_cannot_be_published_says_why(raw, why):
    text, reason = sp.clean_short_description(raw, title="t", source_text=_GPT_OSS_SOURCE)
    assert text == "" and why in reason, reason


def test_a_lead_that_only_repeats_the_title_is_sent_back():
    text, reason = sp.clean_short_description(
        "OpenAI gpt-oss: No new models in 350 days.",
        title="OpenAI gpt-oss: No new models in 350 days", source_text=_GPT_OSS_SOURCE,
    )
    assert text == "" and "repeats the title" in reason


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


class _Completion:
    def __init__(self, text):
        self.text = text


def _ctx_kwargs(**over):
    kw = dict(
        script=_GPT_OSS_SOURCE, long_title=_GPT_OSS_LONG, summary="OpenAI has no gpt-oss successor.",
        pool=object(), task_id="t1",
    )
    kw.update(over)
    return kw


@pytest.mark.asyncio
async def test_the_package_is_written_title_first_and_checked():
    replies = iter([
        _Completion("OpenAI gpt-oss: No new models in 400 days"),  # invented number
        _Completion("OpenAI gpt-oss: No new models in 350 days"),
        _Completion(_LEAD),
    ])
    dispatch = AsyncMock(side_effect=lambda *a, **k: next(replies))
    sc = _SC(video_director_model="ollama/gemma")
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        pkg = await sp.generate_short_package(site_config=sc, **_ctx_kwargs())
    assert pkg.title == "OpenAI gpt-oss: No new models in 350 days" and pkg.title_note == "retry"
    assert pkg.description == _LEAD and pkg.description_note == ""
    assert pkg.model == "ollama/gemma" and pkg.composed_at
    fix = dispatch.await_args_list[1].args[1][-1]["content"]  # the SKILL's video.short_title_fix
    assert fix.startswith("FIX:") and "'400'" in fix
    # The description is written about the title that passed, not the rejected one.
    assert "No new models in 350 days" in dispatch.await_args_list[2].args[1][0]["content"]
    assert {c.kwargs["phase"] for c in dispatch.await_args_list} == {
        "youtube_short_title", "youtube_short_description",
    }


@pytest.mark.asyncio
async def test_twice_rejected_leaves_the_field_empty_for_the_narration_fallback():
    dispatch = AsyncMock(return_value=_Completion("Why it matters more than you think"))
    sc = _SC(video_director_model="ollama/gemma")
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        pkg = await sp.generate_short_package(site_config=sc, **_ctx_kwargs())
    assert pkg.title == "" and "rejected twice" in pkg.title_note


@pytest.mark.asyncio
async def test_the_package_model_wins_and_the_call_takes_its_settings():
    dispatch = AsyncMock(return_value=_Completion("OpenAI gpt-oss: No new models in 350 days"))
    sc = _SC(
        video_director_model="ollama/gemma", youtube_short_package_model="ollama/small",
        youtube_short_package_temperature="0.2", youtube_short_package_max_tokens="99",
        youtube_short_package_timeout_seconds="33",
    )
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        await sp.generate_short_package(site_config=sc, **_ctx_kwargs())
    call = dispatch.await_args_list[0]
    assert call.args[2] == "ollama/small"
    assert (call.kwargs["temperature"], call.kwargs["max_tokens"], call.kwargs["timeout_s"]) == (0.2, 99, 33.0)
    assert call.kwargs["task_id"] == "t1"


@pytest.mark.asyncio
@pytest.mark.parametrize("sc, script, why", [
    (_SC(youtube_short_package_enabled="false", video_director_model="m"), "s", "is off"),
    (_SC(), "s", "video_director_model"),
    (_SC(video_director_model="m"), "  ", "no narration"),
])
async def test_no_call_when_it_cannot_or_should_not_run(sc, script, why):
    dispatch = AsyncMock()
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        pkg = await sp.generate_short_package(site_config=sc, **_ctx_kwargs(script=script))
    dispatch.assert_not_awaited()
    assert pkg.title == "" and why in pkg.title_note


@pytest.mark.asyncio
async def test_a_failed_call_is_a_note_not_an_exception():
    dispatch = AsyncMock(side_effect=TimeoutError("slow"))
    with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
        pkg = await sp.generate_short_package(site_config=_SC(video_director_model="m"), **_ctx_kwargs())
    assert pkg.title == "" and "short title call failed" in pkg.title_note


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_the_package_round_trips_through_row_metadata():
    pkg = sp.ShortPackage(title="t", title_note="retry", description="d", model="m", composed_at="z")
    assert sp.ShortPackage.from_metadata(json.loads(json.dumps(pkg.as_metadata()))) == pkg
    assert sp.ShortPackage.from_metadata(None) is None
    assert sp.ShortPackage.from_metadata({"title": "t"}).description == ""


@pytest.mark.asyncio
async def test_load_reads_the_shorts_own_row_and_accepts_jsonb_as_text():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=json.dumps({"title": "T", "description": "D"}))
    pkg = await sp.load_short_package(pool, "t1")
    assert (pkg.title, pkg.description) == ("T", "D")
    sql = pool.fetchval.await_args.args[0]
    assert "type = 'video_short'" in sql and "youtube_package" in sql


@pytest.mark.asyncio
async def test_store_reports_whether_a_short_row_took_it():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    assert await sp.store_short_package(pool, "t1", sp.ShortPackage(title="T")) is True
    pool.execute = AsyncMock(return_value="UPDATE 0")
    assert await sp.store_short_package(pool, "t1", sp.ShortPackage(title="T")) is False


def test_the_prompts_exist_in_the_pack_and_carry_no_quotable_example():
    """A verbatim example in a published-output prompt gets copied onto
    unrelated videos (4 in 10, services/short_hook.py) — so none here."""
    from poindexter.services.prompt_manager import get_prompt_manager

    pm = get_prompt_manager()
    title = pm.get_prompt("video.short_title", long_title="L", summary="S", script="C", max_words=10)
    fix = pm.get_prompt("video.short_title_fix", reason="R", max_chars=70, max_words=10)
    desc = pm.get_prompt("video.short_description", title="T", summary="S", script="C")
    desc_fix = pm.get_prompt("video.short_description_fix", reason="R", max_chars=300)
    assert "FULL VIDEO TITLE: L" in title and "10 words" in title
    assert fix.startswith("FIX: R") and desc_fix.startswith("FIX: R")
    assert "SHORT TITLE: T" in desc
    for text in (title, desc):
        assert "example" not in text.lower() and "e.g." not in text.lower()


# ---------------------------------------------------------------------------
# backfill (Shorts rendered before packages existed)
# ---------------------------------------------------------------------------


def _short_row(task_id, video_id, package=None):
    return {
        "task_id": task_id, "video_id": video_id, "post_id": f"post-{task_id}",
        "slug": f"slug-{task_id}", "title": f"Title {task_id}", "youtube_package": package,
    }


def _bf_pool(rows, store_result="UPDATE 1"):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.execute = AsyncMock(return_value=store_result)
    return pool


@pytest.mark.asyncio
async def test_backfill_writes_the_missing_and_reports_the_stored_and_sends_nothing():
    stored = json.dumps({"title": "Stored title here", "description": "d"})
    pool = _bf_pool([_short_row("a", "SA"), _short_row("b", "SB", stored)])
    fresh = sp.ShortPackage(title="New title for a", description="lead")
    with patch.object(sp, "compose_short_package", AsyncMock(return_value=fresh)) as compose:
        out = await sp.backfill_short_packages(pool, None)
    assert [o.action for o in out] == ["composed — review, then sync-metadata --apply", "stored"]
    assert compose.await_count == 1 and out[1].package.title == "Stored title here"
    stored_json = json.loads(pool.execute.await_args.args[2])
    assert stored_json["title"] == "New title for a"


@pytest.mark.asyncio
async def test_backfill_recompose_rerolls_a_stored_package():
    pool = _bf_pool([_short_row("b", "SB", {"title": "Old", "description": "d"})])
    with patch.object(sp, "compose_short_package", AsyncMock(return_value=sp.ShortPackage(title="New"))):
        out = await sp.backfill_short_packages(pool, None, recompose=True)
    assert out[0].package.title == "New"


@pytest.mark.asyncio
async def test_a_hand_written_title_keeps_the_stored_description_and_skips_the_model():
    pool = _bf_pool([_short_row("b", "SB", {"title": "Old", "description": "Kept lead."})])
    with patch.object(sp, "compose_short_package", AsyncMock()) as compose:
        out = await sp.backfill_short_packages(pool, None, selector="SB", title="My own title")
    compose.assert_not_awaited()
    p = out[0].package
    assert (p.title, p.title_note, p.description) == ("My own title", "set by operator", "Kept lead.")
    assert pool.fetch.await_args.args[1] is True  # a selection also reaches pending Shorts


@pytest.mark.asyncio
async def test_hand_written_text_must_land_on_exactly_one_short():
    pool = _bf_pool([_short_row("a", "SA"), _short_row("b", "SB")])
    with pytest.raises(ValueError, match="exactly one Short"):
        await sp.backfill_short_packages(pool, None, title="T")


@pytest.mark.asyncio
async def test_a_package_no_row_took_is_a_failure():
    pool = _bf_pool([_short_row("a", "SA")], store_result="UPDATE 0")
    with patch.object(sp, "compose_short_package", AsyncMock(return_value=sp.ShortPackage(title="T"))):
        out = await sp.backfill_short_packages(pool, None)
    assert out[0].failed and out[0].action == "store failed"

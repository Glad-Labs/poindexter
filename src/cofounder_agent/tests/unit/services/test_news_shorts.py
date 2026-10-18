"""services.news_shorts — a YouTube Short from one news story, with no post."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import news_shorts as ns
from poindexter.services.topic_scope import ScopeResult

SOURCE = (
    "A shipment of Nvidia RTX 5090 graphics cards worth $470,000 went missing at "
    "Guarulhos International Airport in Sao Paulo. The distributor Oderco says 2 "
    "pallets of Zotac cards arrived and then vanished, and it has had no answer."
)
CORPUS = [SOURCE]
GOOD = (
    "A shipment of Nvidia RTX 5090 cards worth $470,000 has vanished at a Brazilian "
    "airport. The distributor Oderco says 2 pallets of Zotac cards landed at Guarulhos "
    "International Airport in Sao Paulo and then disappeared. These are the hardest "
    "cards to buy for anyone building a local AI workstation, so every missing pallet "
    "matters. The company says it has had no official answer, and nobody knows yet "
    "whether the cards will be found."
)


class _SC:
    """A SiteConfig stand-in: ``get`` / ``get_int`` over a dict."""

    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_int(self, key, default=0):
        return int(self.values.get(key, default))


def _story(**kw) -> ns.NewsStory:
    base = {"pool_id": "p1", "source": "rss", "title": "RTX 5090 shipment goes missing",
            "url": "https://example.com/story", "summary": "Two pallets vanished.", "score": 2.0}
    base.update(kw)
    return ns.NewsStory(**base)


# ---------------------------------------------------------------------------
# clean_script
# ---------------------------------------------------------------------------


def test_a_grounded_spoken_script_passes():
    text, reason = ns.clean_script(GOOD, corpus=CORPUS, min_words=40, max_words=120)
    assert reason == ""
    assert text == GOOD


def test_a_label_and_wrapping_quotes_are_stripped():
    text, reason = ns.clean_script(f'Narration: "{GOOD}"', corpus=CORPUS, min_words=40, max_words=120)
    assert reason == ""
    assert text.startswith("A shipment")


@pytest.mark.parametrize(
    ("raw", "fault"),
    [
        ("", "empty"),
        (GOOD + " Read more at https://example.com.", "link"),
        ("**Breaking:** " + GOOD, "markdown"),
        (GOOD + " 🚀", "emoji"),
        ("A shipment of cards vanished at an airport.", "words"),
        (GOOD[:-1] + " and", "mid-sentence"),
        (GOOD.replace("$470,000", "$520,000"), "not in the source: 520000"),
        (GOOD.replace("2 pallets", "9 pallets"), "not in the source: 9"),
        (GOOD + " The theft happened in 2025.", "not in the source: 2025"),
        ("Ever wondered where graphics cards go? " + GOOD, "first sentence"),
    ],
)
def test_a_script_that_cannot_be_narrated_names_its_fault(raw, fault):
    text, reason = ns.clean_script(raw, corpus=CORPUS, min_words=40, max_words=120)
    assert text == ""
    assert fault in reason


def test_a_spelled_number_in_the_source_supports_its_digits():
    corpus = [SOURCE.replace("2 pallets", "two pallets")]
    text, reason = ns.clean_script(GOOD, corpus=corpus, min_words=40, max_words=120)
    assert reason == ""


def test_numbers_fused_to_a_name_are_not_counts():
    assert ns._number_tokens("phi4 and H100 and RTX 5090 at 3.50 and 1,200.") == {"5090", "3.5", "1200"}


def test_word_bounds_follow_the_short_lane_settings():
    sc = _SC(video_short_target_seconds="45", media_narration_words_per_second="2.1",
             news_shorts_script_min_words="55")
    assert ns.word_bounds(sc) == (55, 94, 108)


def test_word_bounds_never_put_the_floor_above_the_target():
    sc = _SC(video_short_target_seconds="20", media_narration_words_per_second="2",
             news_shorts_script_min_words="200")
    minimum, target, maximum = ns.word_bounds(sc)
    assert minimum == target == 40
    assert maximum == 46


# ---------------------------------------------------------------------------
# NewsStory
# ---------------------------------------------------------------------------


def test_a_story_round_trips_through_task_metadata():
    story = _story()
    assert ns.NewsStory.from_metadata(json.dumps(story.as_metadata())) == story


@pytest.mark.parametrize("raw", [None, "not json", {"title": "x"}, {"url": "u"}, []])
def test_a_story_without_a_title_and_url_is_none(raw):
    assert ns.NewsStory.from_metadata(raw) is None


# ---------------------------------------------------------------------------
# pick_story
# ---------------------------------------------------------------------------


def _pool_with(rows):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    return pool


def _row(i, **kw):
    base = {"pool_id": f"p{i}", "source": "hackernews", "title": f"Story {i}",
            "url": f"https://example.com/{i}", "summary": "", "score": 3.0 - i}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_the_best_in_scope_story_is_picked():
    pool = _pool_with([_row(0), _row(1), _row(2)])
    niche = SimpleNamespace(slug="starter-blog", has_topic_scope=True, topic_subject="AI hardware")
    with patch("poindexter.services.niche_service.NicheService") as svc, \
         patch("poindexter.services.topic_scope.check_scope",
               AsyncMock(return_value=ScopeResult(in_scope={"n1", "n2"}, out_of_scope={"n0"}))):
        svc.return_value.get_by_slug = AsyncMock(return_value=niche)
        result = await ns.pick_story(pool, _SC())
    assert result.story is not None and result.story.pool_id == "p1"
    assert (result.considered, result.in_scope) == (3, 2)


@pytest.mark.asyncio
async def test_an_unjudged_story_is_not_picked():
    """Fails closed, unlike the batch sweep: a wrong pick costs a GPU render."""
    pool = _pool_with([_row(0)])
    niche = SimpleNamespace(slug="starter-blog", has_topic_scope=True, topic_subject="AI hardware")
    with patch("poindexter.services.niche_service.NicheService") as svc, \
         patch("poindexter.services.topic_scope.check_scope",
               AsyncMock(return_value=ScopeResult(unjudged={"n0"}, errors=["timeout"]))):
        svc.return_value.get_by_slug = AsyncMock(return_value=niche)
        result = await ns.pick_story(pool, _SC())
    assert result.story is None
    assert "1 unjudged" in result.note
    assert result.errors == ["timeout"]


@pytest.mark.asyncio
async def test_a_niche_without_a_subject_picks_nothing():
    pool = _pool_with([_row(0)])
    niche = SimpleNamespace(slug="starter-blog", has_topic_scope=False, topic_subject=None)
    with patch("poindexter.services.niche_service.NicheService") as svc:
        svc.return_value.get_by_slug = AsyncMock(return_value=niche)
        result = await ns.pick_story(pool, _SC())
    assert result.story is None
    assert "no topic_subject" in result.note


@pytest.mark.asyncio
async def test_an_empty_pool_picks_nothing_without_a_scope_call():
    with patch("poindexter.services.topic_scope.check_scope", AsyncMock()) as scope:
        result = await ns.pick_story(_pool_with([]), _SC())
    assert result.story is None and result.considered == 0
    scope.assert_not_called()


@pytest.mark.asyncio
async def test_candidates_are_read_with_the_configured_window():
    pool = _pool_with([])
    sc = _SC(news_shorts_niche="other", news_shorts_sources="rss, hackernews",
             news_shorts_max_age_hours="12", news_shorts_candidate_limit="5")
    await ns.candidate_stories(pool, sc)
    _sql, *args = pool.fetch.call_args.args
    assert args == ["other", ["rss", "hackernews"], 12, "news_short", 5]


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_queues_a_news_short_task_carrying_the_story():
    with patch("poindexter.services.tasks_db.TasksDatabase") as tdb:
        tdb.return_value.add_task = AsyncMock(return_value="t-1")
        task_id = await ns.enqueue_news_short(MagicMock(), _story(), niche="starter-blog")
    assert task_id == "t-1"
    data = tdb.return_value.add_task.call_args.args[0]
    assert data["template_slug"] == data["task_type"] == "news_short"
    assert data["status"] == "pending"
    assert data["niche_slug"] == "starter-blog"
    assert data["task_metadata"]["news_short"]["url"] == "https://example.com/story"


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_refuses_a_page_too_short_to_write_from():
    with patch("poindexter.services.web_research.WebResearcher") as wr:
        wr.return_value.fetch_page_text = AsyncMock(return_value="Just a headline.")
        with pytest.raises(ValueError, match="news_shorts_min_source_chars"):
            await ns.research(MagicMock(), _SC(news_shorts_min_source_chars="800"), _story())


@pytest.mark.asyncio
async def test_research_returns_the_page_related_post_and_subject():
    page = "x" * 900
    niche = SimpleNamespace(topic_subject="AI and computer hardware")
    with patch("poindexter.services.web_research.WebResearcher") as wr, \
         patch.object(ns, "related_post", AsyncMock(return_value={"slug": "s"})) as rel, \
         patch("poindexter.services.niche_service.NicheService") as svc:
        wr.return_value.fetch_page_text = AsyncMock(return_value=page)
        svc.return_value.get_by_slug = AsyncMock(return_value=niche)
        found = await ns.research(MagicMock(), _SC(), _story(), niche="starter-blog")
    assert found == {"source_text": page, "related_post": {"slug": "s"},
                     "subject": "AI and computer hardware"}
    assert rel.call_args.args[2] == "RTX 5090 shipment goes missing", "searched by headline"
    assert wr.return_value.fetch_page_text.call_args.kwargs["max_chars"] == 6000


# ---------------------------------------------------------------------------
# related_post
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("cosine", "linked"), [(0.649, True), (0.61, False)])
async def test_a_related_post_is_linked_only_above_the_floor(cosine, linked):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"slug": "data-center-crunch", "title": "T", "cosine": cosine})
    with patch("poindexter.services.topic_ranking.embed_text", AsyncMock(return_value=[0.1, 0.2])):
        got = await ns.related_post(pool, _SC(), "Data center bans delay three projects")
    if linked:
        assert got == {"slug": "data-center-crunch", "title": "T", "similarity": "0.649"}
        assert pool.fetchrow.call_args.args[1] == "[0.100000,0.200000]"
    else:
        assert got is None


@pytest.mark.asyncio
async def test_a_failed_related_search_links_nothing():
    with patch("poindexter.services.topic_ranking.embed_text", AsyncMock(side_effect=RuntimeError("down"))):
        assert await ns.related_post(MagicMock(), _SC(), "headline") is None


@pytest.mark.asyncio
async def test_a_zero_floor_turns_the_link_off():
    with patch("poindexter.services.topic_ranking.embed_text", AsyncMock()) as embed:
        assert await ns.related_post(
            MagicMock(), _SC(news_shorts_related_post_min_similarity="0"), "headline",
        ) is None
    embed.assert_not_called()


# ---------------------------------------------------------------------------
# write_script
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_script_checks_the_reply_against_the_source():
    sc = _SC(video_director_model="director", news_shorts_script_min_words="40")
    with patch("poindexter.services.checked_llm_line.generate_checked_line",
               AsyncMock(return_value=(GOOD, ""))) as gen, \
         patch("poindexter.services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt = MagicMock(return_value="prompt")
        script, note = await ns.write_script(
            pool=MagicMock(), site_config=sc, story=_story(), source_text=SOURCE, subject="AI hardware",
        )
    assert (script, note) == (GOOD, "")
    kwargs = gen.call_args.kwargs
    assert kwargs["model"] == "director"
    assert kwargs["check"](GOOD) == (GOOD, "")
    assert kwargs["check"](GOOD.replace("2 pallets", "9 pallets"))[1].startswith("these numbers")
    assert kwargs["check"](GOOD.replace("$470,000", "$470,000.00")) == (GOOD.replace("$470,000", "$470,000.00"), "")
    prompt_kwargs = pm.return_value.get_prompt.call_args_list[0].kwargs
    assert prompt_kwargs["subject"] == "AI hardware"
    assert prompt_kwargs["source"] == SOURCE


@pytest.mark.asyncio
async def test_write_script_without_a_model_writes_nothing():
    script, note = await ns.write_script(
        pool=MagicMock(), site_config=_SC(), story=_story(), source_text=SOURCE,
    )
    assert script == ""
    assert "video_director_model" in note


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


def test_standalone_metadata_carries_the_source_and_the_related_slug():
    meta = ns.standalone_metadata(_story(), {"slug": "related", "title": "R"})
    assert meta["standalone"] is True
    assert meta["news_source_url"] == "https://example.com/story"
    assert meta["related_post_slug"] == "related"
    assert meta["news_short"]["pool_id"] == "p1"


def test_standalone_metadata_without_a_related_post_has_no_slug():
    assert "related_post_slug" not in ns.standalone_metadata(_story(), None)


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.fetch = AsyncMock(return_value=rows)
        self.execute = AsyncMock(return_value="UPDATE 1")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_mark_standalone_titles_the_short_and_stores_its_script():
    conn = _Conn([{"id": "a1"}])
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)
    ids = await ns.mark_standalone(pool, "t1", _story(), related=None, script="the script")
    assert ids == ["a1"]
    sql, task_id, meta, title, description = conn.fetch.call_args.args
    assert "post_id IS NULL" in sql and "'video_short'" in sql
    assert task_id == "t1"
    assert json.loads(meta)["standalone"] is True
    assert title == "RTX 5090 shipment goes missing"
    assert description == "Source: https://example.com/story"
    assert conn.execute.call_args.args[1:] == ("t1", "the script")

"""Unit tests for Reddit draft generation: warnings, deterministic link-append,
and the generate_reddit_draft orchestration (stubbed LLM + fake pool)."""
from __future__ import annotations

import pytest

import services.community_drafts as cd
from services.community_drafts import (
    SubredditProfile,
    compute_warnings,
    maybe_append_blog_link,
)
from services.site_config import SiteConfig


def test_compute_warnings_strict_flair_karma():
    p = SubredditProfile(subreddit="X", self_promo="strict", flair="Discussion",
                         min_karma=100, cadence_cap_days=7)
    w = compute_warnings(p)
    assert any("strict" in x for x in w)
    assert any("Discussion" in x for x in w)
    assert any("100" in x for x in w)
    assert any("7" in x for x in w)


def test_compute_warnings_ok_promo_minimal():
    assert compute_warnings(SubredditProfile(subreddit="X", self_promo="ok")) == []


def test_link_omitted_for_text_posts():
    sc = SiteConfig(initial_config={"site_url": "https://example.test"})
    out = maybe_append_blog_link("body", post_type="text", slug="my-post", site_config=sc)
    assert out == "body"


def test_link_appended_for_link_posts():
    sc = SiteConfig(initial_config={"site_url": "https://example.test"})
    out = maybe_append_blog_link("body", post_type="either", slug="my-post", site_config=sc)
    assert "https://example.test/posts/my-post" in out


def test_link_never_broken_when_base_unset():
    sc = SiteConfig(initial_config={"site_url": ""})
    out = maybe_append_blog_link("body", post_type="link", slug="my-post", site_config=sc)
    assert out == "body"      # no base configured → never emit a broken link


# --- generate_reddit_draft orchestration (stub LLM + prompt + fake pool) ---
class _GenPool:
    def __init__(self, profile_row, post_row, ctype_rows):
        self._profile_row = profile_row
        self._post_row = post_row
        self._ctype_rows = ctype_rows
        self.created = {}

    async def fetchrow(self, sql, *args):
        if "subreddit_profiles" in sql:
            return self._profile_row
        if "FROM posts" in sql:
            return self._post_row
        if "community_post_drafts" in sql:      # get_draft after insert
            return {**self.created, "id": 1, "status": "draft", "posted_url": None}
        return None

    async def fetch(self, sql, *args):
        return self._ctype_rows

    async def fetchval(self, sql, *args):       # create_draft RETURNING id
        # args: target, title, body, post_type, source_post_id, warnings, model
        self.created = dict(
            target=args[0], title=args[1], body=args[2], post_type=args[3],
            source_post_id=args[4], warnings=list(args[5]), model=args[6],
        )
        return 1


def _profile_row(**over):
    base = dict(subreddit="LocalLLaMA", enabled=True, content_types=["ai-ml"],
                post_type="text", self_promo="strict", flair="Discussion",
                min_karma=None, min_account_age_days=None, rules_summary="No memes.",
                tone_notes="Technical.", cadence_cap_days=None)
    base.update(over)
    return base


async def test_generate_reddit_draft_stores_body_and_warnings(monkeypatch):
    monkeypatch.setattr(cd, "_resolve_reddit_prompt", lambda **k: "PROMPT")

    async def _fake_llm(prompt, **kw):
        return "  Native value post body.  "
    monkeypatch.setattr(cd, "ollama_chat_text", _fake_llm)

    pool = _GenPool(
        profile_row=_profile_row(),
        post_row={"title": "RTX 5090 inference", "content": "the source", "slug": "rtx-5090"},
        ctype_rows=[{"content_type": "ai-ml"}],
    )
    sc = SiteConfig(initial_config={"site_url": "https://example.test",
                                    "pipeline_writer_model": "gemma-4-31B"})
    draft = await cd.generate_reddit_draft(pool, post_id="p1", subreddit="LocalLLaMA", site_config=sc)

    assert draft.target == "reddit:LocalLLaMA"
    assert pool.created["body"] == "Native value post body."   # stripped, no link (text)
    assert "set flair: Discussion" in pool.created["warnings"]
    assert pool.created["model"] == "gemma-4-31B"
    assert pool.created["source_post_id"] == "p1"


async def test_generate_reddit_draft_appends_link_for_either(monkeypatch):
    monkeypatch.setattr(cd, "_resolve_reddit_prompt", lambda **k: "PROMPT")

    async def _fake_llm(prompt, **kw):
        return "Body."
    monkeypatch.setattr(cd, "ollama_chat_text", _fake_llm)

    pool = _GenPool(
        profile_row=_profile_row(post_type="either"),
        post_row={"title": "T", "content": "src", "slug": "rtx-5090"},
        ctype_rows=[{"content_type": "ai-ml"}],
    )
    sc = SiteConfig(initial_config={"site_url": "https://example.test",
                                    "pipeline_writer_model": "gemma-4-31B"})
    await cd.generate_reddit_draft(pool, post_id="p1", subreddit="LocalLLaMA", site_config=sc)
    assert "https://example.test/posts/rtx-5090" in pool.created["body"]


async def test_generate_reddit_draft_missing_profile_raises():
    pool = _GenPool(profile_row=None, post_row=None, ctype_rows=[])
    sc = SiteConfig(initial_config={"pipeline_writer_model": "gemma"})
    with pytest.raises(KeyError):
        await cd.generate_reddit_draft(pool, post_id="p1", subreddit="nope", site_config=sc)

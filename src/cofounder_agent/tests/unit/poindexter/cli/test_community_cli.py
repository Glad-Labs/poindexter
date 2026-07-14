"""Unit tests for the `poindexter community` CLI (mocked pool + services)."""
from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

import poindexter.cli.community as com
from services.community_drafts import CommunityDraft


class _NoopPool:
    async def close(self):
        pass


@pytest.fixture
def runner(monkeypatch):
    async def _fake_connect():
        return _NoopPool()
    monkeypatch.setattr(com, "_connect", _fake_connect)

    async def _fake_sc(pool):
        return object()
    monkeypatch.setattr(com, "_make_site_config", _fake_sc)

    # By default resolve a post arg to itself (echo the prefix) so draft tests
    # exercise the command flow without a DB; not-found/ambiguous tests override.
    async def _fake_resolve(pool, **kw):
        return kw.get("prefix")
    monkeypatch.setattr(com, "resolve_uuid_prefix", _fake_resolve)
    return CliRunner()


def test_profiles_list_empty(runner, monkeypatch):
    async def _list(pool, *, enabled_only=False):
        return []
    monkeypatch.setattr(com, "list_profiles", _list)
    res = runner.invoke(com.community_group, ["profiles", "list"])
    assert res.exit_code == 0
    assert "No subreddit profiles" in res.output


def test_profiles_add_reports_created(runner, monkeypatch):
    async def _add(pool, profile):
        return True
    monkeypatch.setattr(com, "add_profile", _add)
    res = runner.invoke(com.community_group, [
        "profiles", "add", "LocalLLaMA", "--content-types", "ai-ml",
        "--post-type", "text", "--self-promo", "strict",
    ])
    assert res.exit_code == 0
    assert "LocalLLaMA" in res.output


def test_profiles_add_existing_warns(runner, monkeypatch):
    async def _add(pool, profile):
        return False
    monkeypatch.setattr(com, "add_profile", _add)
    res = runner.invoke(com.community_group, ["profiles", "add", "LocalLLaMA"])
    assert res.exit_code != 0
    assert "exists" in res.output.lower()


def test_draft_reddit_requires_subreddit_or_suggests(runner, monkeypatch):
    async def _suggest(pool, post_id):
        return ["LocalLLaMA", "selfhosted"]
    monkeypatch.setattr(com, "suggest_subreddits_for_post", _suggest)
    res = runner.invoke(com.community_group, ["draft", "reddit", "some-post"])
    assert res.exit_code != 0
    # no --subreddit → lists suggestions and stops (never auto-fans-out)
    assert "LocalLLaMA" in res.output and "selfhosted" in res.output


def test_draft_reddit_generates_with_subreddit(runner, monkeypatch):
    async def _gen(pool, *, post_id, subreddit, site_config):
        return CommunityDraft(id=7, target=f"reddit:{subreddit}", title="T", body="B",
                              post_type="text", source_post_id=post_id,
                              warnings=["set flair: Discussion"], status="draft",
                              posted_url=None, model="gemma")
    monkeypatch.setattr(com, "generate_reddit_draft", _gen)
    res = runner.invoke(com.community_group,
                        ["draft", "reddit", "some-post", "--subreddit", "LocalLLaMA"])
    assert res.exit_code == 0
    assert "#7" in res.output and "set flair: Discussion" in res.output


def test_draft_reddit_resolves_post_prefix(runner, monkeypatch):
    # An 8-char id prefix is expanded to the full UUID (same as other commands)
    # and passed on to generation — no more "slug or id" fiction.
    seen = {}

    async def _resolve(pool, **kw):
        return "full-uuid-1234"

    async def _gen(pool, *, post_id, subreddit, site_config):
        seen["post_id"] = post_id
        return CommunityDraft(id=3, target=f"reddit:{subreddit}", title="T", body="B",
                              post_type="text", source_post_id=post_id, warnings=[],
                              status="draft", posted_url=None, model="gemma")
    monkeypatch.setattr(com, "resolve_uuid_prefix", _resolve)
    monkeypatch.setattr(com, "generate_reddit_draft", _gen)
    res = runner.invoke(com.community_group,
                        ["draft", "reddit", "a1b2c3d4", "--subreddit", "LocalLLaMA"])
    assert res.exit_code == 0
    assert seen["post_id"] == "full-uuid-1234"   # resolved, not the raw prefix


def test_draft_reddit_unknown_post_errors(runner, monkeypatch):
    async def _resolve_none(pool, **kw):
        return None
    monkeypatch.setattr(com, "resolve_uuid_prefix", _resolve_none)
    res = runner.invoke(com.community_group,
                        ["draft", "reddit", "nope", "--subreddit", "LocalLLaMA"])
    assert res.exit_code != 0
    assert "no post matching" in res.output


def test_drafts_mark_posted(runner, monkeypatch):
    async def _mp(pool, draft_id, *, url):
        return True
    monkeypatch.setattr(com, "mark_posted", _mp)
    res = runner.invoke(com.community_group,
                        ["drafts", "mark-posted", "7", "--url", "https://reddit.com/x"])
    assert res.exit_code == 0 and "7" in res.output


def test_profiles_import_csv(runner, monkeypatch, tmp_path):
    from services.subreddit_import import ImportReport, ImportRowResult

    async def _imp(pool, path, *, force=False):
        return ImportReport(rows=[ImportRowResult("LocalLLaMA", "created")])
    monkeypatch.setattr(com, "import_csv", _imp)
    f = tmp_path / "s.csv"
    f.write_text("subreddit\nLocalLLaMA", encoding="utf-8")
    res = runner.invoke(com.community_group, ["profiles", "import-csv", str(f)])
    assert res.exit_code == 0 and "created" in res.output


async def test_make_site_config_fails_loud_on_load_error(monkeypatch):
    """A settings-load failure surfaces its real cause (draft generation needs
    settings — there is no pool-only fallback to swallow it for)."""
    class _BadSiteConfig:
        def __init__(self, *a, **k):
            pass

        async def load(self, pool):
            raise RuntimeError("db boom")

    # _make_site_config imports SiteConfig from services.site_config at call time.
    monkeypatch.setattr("services.site_config.SiteConfig", _BadSiteConfig)
    with pytest.raises(click.ClickException, match="failed to load settings"):
        await com._make_site_config(object())

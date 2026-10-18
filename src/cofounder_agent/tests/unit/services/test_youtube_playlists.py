"""youtube_playlists: keyword rules place each upload in its topic playlists.

Pins the matching rules (short keywords are whole words, the order and the
cap hold), that a malformed setting is loud, that the hook never raises, that
the dry run sends nothing, and that the adapter neither duplicates a video in
a playlist nor trips over the few seconds a new playlist 404s.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import youtube_playlists as yp
from poindexter.services.site_config import SiteConfig

_RULES = json.dumps([
    {"title": "Local AI", "description": "d1", "keywords": ["vram", "undervolt", "local llm"]},
    {"title": "News", "description": "d2", "keywords": ["openai", "deepseek", "asml"]},
    {"title": "Building", "description": "d3", "keywords": ["rag", "ci", "pipeline"]},
])


def _sc(**kw):
    return SiteConfig(initial_config={"youtube_playlists": _RULES, **kw})


def _pool(rows=None):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.execute = AsyncMock()
    pool = MagicMock()

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    pool.acquire = lambda: _Acq()
    return pool, conn


class TestRules:
    def test_parses_the_seeded_setting(self):
        from poindexter.services.settings_defaults import DEFAULTS

        titles = [r.title for r in yp.parse_rules(DEFAULTS["youtube_playlists"])]
        assert titles == [
            "Local AI on Your Own GPU", "AI Models and Industry News",
            "Building AI Systems", "Content and Distribution",
        ]

    @pytest.mark.parametrize("raw", ["{", '{"title": "x"}', '[{"title": "", "keywords": ["a"]}]', '[{"title": "x", "keywords": []}]', "[1]"])
    def test_a_malformed_setting_is_loud(self, raw):
        with pytest.raises(yp.PlaylistConfigError):
            yp.parse_rules(raw)

    def test_empty_means_no_playlists(self):
        assert yp.parse_rules("") == []

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Single-GPU VRAM Budgeting", ["Local AI"]),
            ("Undervolting your GPU", ["Local AI"]),  # long keyword matches a word start
            ("CI pipelines and schema drift", ["Building"]),
            ("A circuit diagram", []),  # 'ci' is short: whole words only
            ("DeepSeek and the RAG pipeline", ["News", "Building"]),
            ("Nothing relevant here", []),
        ],
    )
    def test_matching(self, text, expected):
        rules = yp.parse_rules(_RULES)
        assert [r.title for r in yp.match_playlists(text, rules, limit=3)] == expected

    def test_the_cap_keeps_rule_order(self):
        rules = yp.parse_rules(_RULES)
        hits = yp.match_playlists("vram openai rag", rules, limit=2)
        assert [r.title for r in hits] == ["Local AI", "News"]


class TestAfterUpload:
    async def test_matched_playlists_go_to_the_adapter_and_are_stamped(self):
        pool, conn = _pool()
        adapter = MagicMock()
        adapter.add_to_playlists = AsyncMock(return_value=(True, "ok", {"News": "added"}))
        with patch(
            "poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter,
        ), patch.object(yp, "emit_finding") as finding, patch.object(
            yp, "sync_shelves", AsyncMock(return_value=(True, "ok", {})),
        ):
            status = await yp.playlists_after_upload(
                pool, _sc(), asset_id="00000000-0000-0000-0000-000000000001",
                video_id="VID9", text="OpenAI ships nothing",
            )
        assert status == "ok"
        kw = adapter.add_to_playlists.await_args.kwargs
        assert kw["playlists"] == [("News", "d2")] and kw["privacy"] == "public"
        payload = json.loads(conn.execute.await_args.args[3])
        assert payload["status"] == "ok" and payload["playlists"] == {"News": "added"}
        finding.assert_not_called()

    async def test_no_match_calls_nothing(self):
        pool, _conn = _pool()
        with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter") as cls:
            status = await yp.playlists_after_upload(pool, _sc(), asset_id="a", video_id="V", text="cats")
        assert status == "no playlist matched"
        cls.assert_not_called()

    async def test_a_broken_setting_never_raises_and_is_surfaced(self):
        pool, _conn = _pool()
        with patch.object(yp, "emit_finding") as finding:
            status = await yp.playlists_after_upload(
                pool, SiteConfig(initial_config={"youtube_playlists": "{"}),
                asset_id="00000000-0000-0000-0000-000000000001", video_id="V", text="x", subject="p1",
            )
        assert status.startswith("failed:")
        assert finding.call_args.kwargs["kind"] == "youtube_playlist_failed"
        assert "playlists --apply" in finding.call_args.kwargs["body"]

    async def test_off_switch(self):
        pool, conn = _pool()
        status = await yp.playlists_after_upload(
            pool, _sc(youtube_playlists_enabled="false"), asset_id="a", video_id="V", text="openai",
        )
        assert status.startswith("skipped")
        conn.execute.assert_not_called()


def _row(**kw):
    base = {
        "id": "00000000-0000-0000-0000-000000000001", "type": "video", "video_id": "VID9",
        "playlist_status": None, "title": "OpenAI ships", "post_title": "OpenAI ships", "seo_keywords": "",
    }
    base.update(kw)
    return base


class TestBackfill:
    async def test_dry_run_sends_nothing(self):
        pool, conn = _pool([_row()])
        with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter") as cls:
            [out] = await yp.backfill_playlists(pool, _sc())
        assert out.playlists == ["News"] and out.action.startswith("would add")
        cls.assert_not_called()
        conn.execute.assert_not_called()

    async def test_apply_adds_and_stamps(self):
        pool, conn = _pool([_row()])
        adapter = MagicMock()
        adapter.add_to_playlists = AsyncMock(return_value=(True, "ok", {"News": "added"}))
        with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter):
            [out] = await yp.backfill_playlists(pool, _sc(), apply=True)
        assert out.action == "News: added" and not out.failed
        assert adapter.add_to_playlists.await_args.kwargs["force"] is True
        assert json.loads(conn.execute.await_args.args[3])["status"] == "ok"

    async def test_done_videos_are_skipped_and_unmatched_reported(self):
        pool, _conn = _pool([_row(playlist_status="ok"), _row(video_id="V2", title="cats", post_title="cats")])
        a, b = await yp.backfill_playlists(pool, _sc(), apply=True)
        assert a.action.startswith("skipped")
        assert b.action == "no playlist matched" and b.playlists == []

    async def test_a_broken_setting_raises_before_touching_videos(self):
        pool, conn = _pool([_row()])
        with pytest.raises(yp.PlaylistConfigError):
            await yp.backfill_playlists(pool, SiteConfig(initial_config={"youtube_playlists": "{"}))
        conn.fetch.assert_not_called()


class TestAdapterPlaylists:
    def _youtube(self, *, existing=None, member=False):
        yt = MagicMock()
        yt.playlists.return_value.list.return_value.execute.return_value = {
            "items": [{"id": pid, "snippet": {"title": t}} for t, pid in (existing or {}).items()],
        }
        yt.playlists.return_value.insert.return_value.execute.return_value = {"id": "NEWPL"}
        yt.playlistItems.return_value.list.return_value.execute.return_value = {"items": [{"id": "x"}] if member else []}
        return yt

    def _run(self, yt, playlists):
        """Fake googleapiclient (CI does not install the youtube extra)."""
        import sys
        import types

        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        pkg = types.ModuleType("googleapiclient")
        discovery = types.ModuleType("googleapiclient.discovery")
        discovery.build = MagicMock(return_value=yt)  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"googleapiclient": pkg, "googleapiclient.discovery": discovery}), patch(
            "poindexter.services.publish_adapters.youtube.time.sleep",
        ) as sleep:
            out = YouTubePublishAdapter._add_to_playlists_blocking(
                credentials=object(), video_id="V", playlists=playlists, privacy="public",
            )
        return out, sleep

    def test_an_existing_playlist_and_membership_are_reused(self):
        yt = self._youtube(existing={"News": "PL1"}, member=True)
        out, _ = self._run(yt, [("News", "d")])
        assert out == {"News": "already in"}
        yt.playlists.return_value.insert.assert_not_called()
        yt.playlistItems.return_value.insert.assert_not_called()

    def test_a_missing_playlist_is_created_and_skips_the_membership_check(self):
        yt = self._youtube()
        out, _ = self._run(yt, [("News", "d")])
        assert out == {"News": "added"}
        body = yt.playlists.return_value.insert.call_args.kwargs["body"]
        assert body["snippet"]["title"] == "News" and body["status"]["privacyStatus"] == "public"
        yt.playlistItems.return_value.list.assert_not_called()
        item = yt.playlistItems.return_value.insert.call_args.kwargs["body"]["snippet"]
        assert item["playlistId"] == "NEWPL" and item["resourceId"]["videoId"] == "V"

    def test_a_new_playlists_brief_404_is_retried(self):
        yt = self._youtube()
        calls = {"n": 0}

        def _execute():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("<HttpError 404 ... playlistNotFound>")
            return {}

        yt.playlistItems.return_value.insert.return_value.execute.side_effect = _execute
        out, sleep = self._run(yt, [("News", "d")])
        assert out == {"News": "added"} and calls["n"] == 3
        assert sleep.call_count == 2

    def test_a_404_on_an_old_playlist_is_not_retried(self):
        yt = self._youtube(existing={"News": "PL1"})
        yt.playlistItems.return_value.insert.return_value.execute.side_effect = RuntimeError("HttpError 404")
        with pytest.raises(RuntimeError):
            self._run(yt, [("News", "d")])

    async def test_a_missing_scope_names_the_fix(self):
        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        adapter = YouTubePublishAdapter(site_config=SiteConfig(initial_config={}))
        with patch.object(adapter, "_check_gating", AsyncMock(return_value=(True, None, {}))), patch.object(
            adapter, "_build_credentials", return_value=object(),
        ), patch.object(
            YouTubePublishAdapter, "_add_to_playlists_blocking",
            side_effect=RuntimeError("<HttpError 403 insufficientPermissions: Request had insufficient authentication scopes>"),
        ):
            ok, detail, outcome = await adapter.add_to_playlists(video_id="V", playlists=[("News", "d")])
        assert ok is False and "setup --with-update" in detail and outcome == {}

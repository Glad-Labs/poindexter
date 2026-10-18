"""youtube_captions: transcribe the uploaded video, keep the SRT, attach it once.

A caption track is what YouTube search reads; the burned-in captions are only
pixels. These tests pin the contract that keeps it safe to run on every
upload: an SRT beside the video is reused, a failure never raises and is
stamped and surfaced, the dry run sends nothing, and the adapter never stacks
a second copy of our track.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from poindexter.services import youtube_captions as yc
from poindexter.services.site_config import SiteConfig

_SRT = "1\n00:00:00,000 --> 00:00:02,000\nHello there\n\n2\n00:00:02,500 --> 00:00:04,000\nFollow for more\n"


def _sc(**kw):
    return SiteConfig(initial_config=kw)


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


class TestBasics:
    def test_sidecar_path(self):
        assert yc.sidecar_srt_path("/v/abc_short.mp4") == "/v/abc_short.srt"

    def test_count_cues(self):
        assert yc.count_cues(_SRT) == 2
        assert yc.count_cues("") == 0

    def test_settings_defaults(self):
        sc = _sc()
        assert yc.captions_enabled(sc) is True
        assert yc.caption_language(sc) == "en"
        assert yc.caption_track_name(sc) == "English"
        assert yc.captions_enabled(_sc(youtube_captions_enabled="false")) is False

    def test_the_seeded_settings_match_the_code_defaults(self):
        from poindexter.services.settings_defaults import DEFAULTS

        assert DEFAULTS["youtube_captions_enabled"] == "true"
        assert DEFAULTS["youtube_caption_language"] == "en"
        assert DEFAULTS["youtube_caption_track_name"] == "English"
        assert DEFAULTS["findings.youtube_caption_failed.delivery"] == "discord"


class TestEnsureSrt:
    async def test_an_existing_srt_is_reused_without_transcribing(self, tmp_path):
        video = tmp_path / "v.mp4"
        (tmp_path / "v.srt").write_text(_SRT)
        with patch("poindexter.services.caption_providers.get_caption_provider") as gp:
            path, why = await yc.ensure_srt(str(video), _sc())
        assert path == str(tmp_path / "v.srt") and why == ""
        gp.assert_not_called()

    async def test_a_missing_video_is_reported(self, tmp_path):
        path, why = await yc.ensure_srt(str(tmp_path / "gone.mp4"), _sc())
        assert path == "" and "missing" in why

    async def test_transcribes_the_audio_and_keeps_the_srt(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"mp4")
        provider = MagicMock()
        provider.transcribe = AsyncMock(return_value=SimpleNamespace(success=True, srt_text=_SRT, error=""))
        with patch.object(yc, "_extract_audio", AsyncMock()) as extract, patch(
            "poindexter.services.caption_providers.get_caption_provider", return_value=provider,
        ):
            path, why = await yc.ensure_srt(str(video), _sc(), task_id="t1")
        assert why == "" and (tmp_path / "v.srt").read_text() == _SRT
        assert extract.await_args.args[0] == str(video)
        assert provider.transcribe.await_args.kwargs["granularity"] == "segment"

    async def test_an_empty_transcript_is_a_failure(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"mp4")
        provider = MagicMock()
        provider.transcribe = AsyncMock(return_value=SimpleNamespace(success=False, srt_text="", error="503"))
        with patch.object(yc, "_extract_audio", AsyncMock()), patch(
            "poindexter.services.caption_providers.get_caption_provider", return_value=provider,
        ):
            path, why = await yc.ensure_srt(str(video), _sc())
        assert path == "" and "503" in why
        assert not (tmp_path / "v.srt").exists()


class TestAfterUpload:
    async def test_off_switch(self):
        pool, conn = _pool()
        status = await yc.caption_after_upload(
            pool, _sc(youtube_captions_enabled="false"), asset_id="a", video_path="/v.mp4", video_id="V",
        )
        assert status.startswith("skipped")
        conn.execute.assert_not_called()

    async def test_success_is_stamped_and_raises_no_finding(self, tmp_path):
        pool, conn = _pool()
        adapter = MagicMock()
        adapter.set_captions = AsyncMock(return_value=(True, "set"))
        with patch.object(yc, "ensure_srt", AsyncMock(return_value=("/v.srt", ""))), patch(
            "poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter,
        ), patch.object(yc, "emit_finding") as finding:
            status = await yc.caption_after_upload(
                pool, _sc(), asset_id="00000000-0000-0000-0000-000000000001",
                video_path="/v.mp4", video_id="VID9",
            )
        assert status == "set"
        kw = adapter.set_captions.await_args.kwargs
        assert kw == {"video_id": "VID9", "srt_path": "/v.srt", "language": "en", "name": "English"}
        sql, _aid, key, payload = conn.execute.await_args.args
        assert key == "youtube_captions" and json.loads(payload)["status"] == "set"
        finding.assert_not_called()

    async def test_a_failure_never_raises_and_is_surfaced(self):
        pool, conn = _pool()
        with patch.object(yc, "ensure_srt", AsyncMock(side_effect=RuntimeError("boom"))), patch.object(
            yc, "emit_finding",
        ) as finding:
            status = await yc.caption_after_upload(
                pool, _sc(), asset_id="00000000-0000-0000-0000-000000000001",
                video_path="/v.mp4", video_id="VID9", subject="p1", shorts=True,
            )
        assert status.startswith("failed:") and "boom" in status
        assert json.loads(conn.execute.await_args.args[3])["status"].startswith("failed")
        kw = finding.call_args.kwargs
        assert kw["kind"] == "youtube_caption_failed"
        assert kw["dedup_key"] == "youtube_caption_failed:p1:short"
        assert "captions --apply" in kw["body"]


def _row(**kw):
    base = {
        "id": "00000000-0000-0000-0000-000000000001", "type": "video_short",
        "storage_path": "/v/abc_short.mp4", "url": "", "task_id": "t1",
        "video_id": "VID9", "caption_status": None, "title": "A Short",
    }
    base.update(kw)
    return base


class TestBackfill:
    async def test_dry_run_transcribes_and_sends_nothing(self, tmp_path):
        srt = tmp_path / "abc_short.srt"
        srt.write_text(_SRT)
        pool, conn = _pool([_row()])
        adapter = MagicMock()
        adapter.set_captions = AsyncMock()
        with patch.object(yc, "ensure_srt", AsyncMock(return_value=(str(srt), ""))), patch(
            "poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter,
        ):
            [out] = await yc.backfill_captions(pool, _sc())
        assert out.action.startswith("transcribed") and out.cues == 2
        adapter.set_captions.assert_not_called()
        conn.execute.assert_not_called()

    async def test_apply_uploads_the_stored_file_and_stamps(self, tmp_path):
        srt = tmp_path / "abc_short.srt"
        srt.write_text(_SRT)
        pool, conn = _pool([_row()])
        adapter = MagicMock()
        adapter.set_captions = AsyncMock(return_value=(True, "set"))
        with patch.object(yc, "ensure_srt", AsyncMock(return_value=(str(srt), ""))), patch(
            "poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter,
        ):
            [out] = await yc.backfill_captions(pool, _sc(), apply=True)
        assert out.action == "set" and not out.failed
        assert adapter.set_captions.await_args.kwargs["srt_path"] == str(srt)
        assert adapter.set_captions.await_args.kwargs["force"] is True
        assert json.loads(conn.execute.await_args.args[3])["status"] == "set"

    async def test_an_already_captioned_video_is_skipped(self):
        pool, _conn = _pool([_row(caption_status="set")])
        with patch.object(yc, "ensure_srt", AsyncMock()) as ensure:
            [out] = await yc.backfill_captions(pool, _sc())
        assert out.action.startswith("skipped")
        ensure.assert_not_called()

    async def test_a_gone_file_is_fetched_from_its_url(self, tmp_path):
        pool, _conn = _pool([_row(storage_path=str(tmp_path / "gone.mp4"), url="https://cdn/x.mp4")])
        seen = {}

        async def _ensure(source, sc, *, task_id, srt_path):
            seen["source"], seen["srt"] = source, srt_path
            return "", "test stop"

        with patch.object(yc, "_download", AsyncMock()) as dl, patch.object(yc, "ensure_srt", _ensure):
            [out] = await yc.backfill_captions(pool, _sc())
        assert dl.await_args.args[0] == "https://cdn/x.mp4"
        assert seen["source"] != str(tmp_path / "gone.mp4")
        assert seen["srt"] == str(tmp_path / "gone.srt")  # the SRT lands beside the row's path
        assert out.failed and "test stop" in out.action

    async def test_limit(self):
        pool, _conn = _pool([_row(video_id="A", caption_status="set"), _row(video_id="B", caption_status="set")])
        outs = await yc.backfill_captions(pool, _sc(), limit=1)
        assert [o.video_id for o in outs] == ["A"]


class TestAdapterCaptions:
    """The adapter checks YouTube before inserting, so a re-run never stacks a duplicate."""

    def _youtube(self, items):
        yt = MagicMock()
        yt.captions.return_value.list.return_value.execute.return_value = {"items": items}
        yt.captions.return_value.insert.return_value.execute.return_value = {"id": "NEW"}
        return yt

    def _run(self, yt):
        """Fake googleapiclient (CI does not install the youtube extra)."""
        import sys
        import types

        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        pkg = types.ModuleType("googleapiclient")
        discovery = types.ModuleType("googleapiclient.discovery")
        discovery.build = MagicMock(return_value=yt)  # type: ignore[attr-defined]
        http = types.ModuleType("googleapiclient.http")
        http.MediaFileUpload = MagicMock()  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {
            "googleapiclient": pkg, "googleapiclient.discovery": discovery, "googleapiclient.http": http,
        }):
            return YouTubePublishAdapter._set_captions_blocking(
                credentials=object(), video_id="V", srt_path="/v.srt", language="en", name="English",
            )

    def test_an_existing_track_of_ours_is_reused(self):
        yt = self._youtube([{"id": "OURS", "snippet": {"language": "en", "name": "English", "trackKind": "standard"}}])
        assert self._run(yt) == {"id": "OURS", "existing": True}
        yt.captions.return_value.insert.assert_not_called()

    def test_youtubes_own_speech_track_does_not_count(self):
        yt = self._youtube([{"id": "AUTO", "snippet": {"language": "en", "name": "", "trackKind": "asr"}}])
        assert self._run(yt) == {"id": "NEW", "existing": False}
        body = yt.captions.return_value.insert.call_args.kwargs["body"]
        assert body == {"snippet": {"videoId": "V", "language": "en", "name": "English", "isDraft": False}}

    async def test_a_missing_scope_names_the_fix(self, tmp_path):
        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        srt = tmp_path / "v.srt"
        srt.write_text(_SRT)
        adapter = YouTubePublishAdapter(site_config=_sc())
        with patch.object(adapter, "_check_gating", AsyncMock(return_value=(True, None, {}))), patch.object(
            adapter, "_build_credentials", return_value=object(),
        ), patch.object(
            YouTubePublishAdapter, "_set_captions_blocking",
            side_effect=RuntimeError("<HttpError 403 ... insufficientPermissions: Request had insufficient authentication scopes>"),
        ):
            ok, detail = await adapter.set_captions(video_id="V", srt_path=str(srt), language="en", name="English")
        assert ok is False and "setup --with-update" in detail

    async def test_a_missing_file_is_refused_before_calling_google(self):
        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        ok, detail = await YouTubePublishAdapter(site_config=_sc()).set_captions(
            video_id="V", srt_path="/nope.srt", language="en", name="English",
        )
        assert ok is False and "missing" in detail

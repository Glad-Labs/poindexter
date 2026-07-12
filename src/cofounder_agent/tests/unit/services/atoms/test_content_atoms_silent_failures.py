"""Unit tests — modules/content atom best-effort-failure visibility.

Gap-site burn-down (batch 3). Seven best-effort swallows across the content
atoms used to log at ``logger.debug`` (invisible at the prod log level) and
return a fallback — so a source that came up empty because it *failed* was
indistinguishable from one that was legitimately empty. They now
``emit_finding`` a non-paging ``info`` finding (visible on the Findings
dashboard, deduped per site, never routed — ``info`` sits below the
findings router's severity floor) while still returning the fallback
(non-blocking).

Each test forces the failure path and asserts (a) exactly one finding of
the expected kind + dedup_key fires, and (b) the fallback sentinel is
still returned.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest

import modules.content.atoms._image_helpers as image_helpers
import modules.content.atoms.approval_gate as approval_gate
import modules.content.atoms.content_inject_affiliate_links as affiliate
import modules.content.atoms.narrate_bundle as narrate_bundle
import modules.content.atoms.qa_audio as qa_audio

pytestmark = pytest.mark.unit


class _BrokenPool:
    """A pool whose ``acquire`` raises synchronously — drives the
    ``async with pool.acquire()`` helpers into their except branch."""

    def acquire(self):
        raise RuntimeError("pool acquire boom")


def _capture(monkeypatch, module) -> list[dict]:
    """Patch ``module.emit_finding`` with a capture list and return it."""
    calls: list[dict] = []
    monkeypatch.setattr(module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


# --- qa.audio: ffmpeg measurement failures ---------------------------------


@pytest.mark.asyncio
async def test_detect_silences_emits_finding_on_ffmpeg_failure(monkeypatch):
    calls = _capture(monkeypatch, qa_audio)
    # ffmpeg "present" so we pass the shutil.which guard and reach _run_argv.
    monkeypatch.setattr(qa_audio.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        qa_audio, "_run_argv", AsyncMock(side_effect=RuntimeError("ffmpeg boom"))
    )

    result = await qa_audio._detect_silences("/x.wav", max_silence_s=2.0)

    assert result is None  # sentinel preserved — check recorded "unavailable"
    assert len(calls) == 1
    assert calls[0]["kind"] == "audio_qa_measurement_failed"
    assert calls[0]["dedup_key"] == "audio_qa_measurement_failed:silencedetect"
    assert calls[0].get("severity", "info") == "info"  # non-paging


@pytest.mark.asyncio
async def test_measure_volume_emits_finding_on_ffmpeg_failure(monkeypatch):
    calls = _capture(monkeypatch, qa_audio)
    monkeypatch.setattr(qa_audio.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        qa_audio, "_run_argv", AsyncMock(side_effect=RuntimeError("ffmpeg boom"))
    )

    result = await qa_audio._measure_volume("/x.wav")

    assert result is None
    assert len(calls) == 1
    assert calls[0]["dedup_key"] == "audio_qa_measurement_failed:volumedetect"


# --- narrate_bundle: dev-diary bundle read failure -------------------------


@pytest.mark.asyncio
async def test_load_bundle_from_db_emits_finding_on_read_failure(monkeypatch):
    calls = _capture(monkeypatch, narrate_bundle)

    result = await narrate_bundle._load_bundle_from_db(_BrokenPool(), "task-1")

    assert result == {}  # narration proceeds without the bundle
    assert len(calls) == 1
    assert calls[0]["kind"] == "dev_diary_bundle_read_failed"


# --- approval_gate: gate-decision + pending-regen reads --------------------


@pytest.mark.asyncio
async def test_gate_decision_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch, approval_gate)

    result = await approval_gate._gate_decision(
        _BrokenPool(), "task-1", "preview_approval"
    )

    assert result is None  # fail-safe — task re-pauses for review
    assert len(calls) == 1
    assert calls[0]["kind"] == "approval_gate_read_failed"
    assert calls[0]["dedup_key"] == "approval_gate_read_failed:gate_decision"


@pytest.mark.asyncio
async def test_pending_regen_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch, approval_gate)

    result = await approval_gate._pending_regen(_BrokenPool(), "task-1")

    assert result is None  # normal pause, no regen
    assert len(calls) == 1
    assert calls[0]["dedup_key"] == "approval_gate_read_failed:pending_regen"


# --- content.inject_affiliate_links: list_active failure -------------------


@pytest.mark.asyncio
async def test_affiliate_list_active_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch, affiliate)
    # list_active is imported at call time from the source module, so patching
    # the source attribute intercepts it regardless of the local binding.
    monkeypatch.setattr(
        "modules.content.affiliate_links.list_active",
        AsyncMock(side_effect=RuntimeError("list_active boom")),
    )

    class _Cfg:
        def get_bool(self, k, d=False):
            return True if k == "affiliate_injection_enabled" else d

        def get(self, k, d=""):
            return d

        def get_int(self, k, d=0):
            return d

    class _DB:  # mimics state["database_service"].pool
        pool = object()  # non-None; list_active is patched to raise anyway

    state = {
        "content": "We use Mercury for banking.",
        "site_config": _Cfg(),
        "database_service": _DB(),
    }
    result = await affiliate.run(state)

    assert result == {}  # content passes through unmodified
    assert len(calls) == 1
    assert calls[0]["kind"] == "affiliate_links_read_failed"


# --- _image_helpers: media_asset_recorder import guard ---------------------


@pytest.mark.asyncio
async def test_record_inline_image_asset_emits_finding_on_import_failure(monkeypatch):
    calls = _capture(monkeypatch, image_helpers)
    # A ``None`` entry in sys.modules makes ``from services.media_asset_recorder
    # import record_media_asset`` raise ImportError — simulating a broken /
    # missing module without uninstalling anything. monkeypatch restores it.
    monkeypatch.setitem(sys.modules, "services.media_asset_recorder", None)

    result = await image_helpers._record_inline_image_asset(
        site_config=object(),
        post_id="p1",  # non-None so we pass the early guard and reach the import
        public_url="/media/img.png",
        provider_plugin="sdxl",
        width=1024,
        height=576,
        mime_type="image/png",
        metadata={},
    )

    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "media_asset_recorder_unavailable"

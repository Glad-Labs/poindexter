"""Unit tests for ``services/settings_read_sink.py``.

The read sink is a process-level buffer that ``SettingsService.get`` records
into (Glad-Labs/poindexter#756). ``SiteConfig`` owns its own instance read-set,
but ``SettingsService`` is constructed ad-hoc in several places with only a
pool, so there is no single instance for the flush job to drain — its reads
land in this shared buffer instead, and ``FlushSettingsReadTelemetryJob`` unions
both. Write-then-drain, the same shape as a metrics counter, behind a
two-function seam (``record_read`` / ``drain_read_keys``).
"""

from __future__ import annotations

import pytest

from services import settings_read_sink


@pytest.mark.unit
class TestSettingsReadSink:
    def setup_method(self):
        # Start every test from an empty buffer — the sink is module-global,
        # so a prior test's reads must not bleed into this one.
        settings_read_sink.drain_read_keys()

    def test_record_then_drain_returns_key(self):
        settings_read_sink.record_read("pipeline_critic_model")
        assert settings_read_sink.drain_read_keys() == ["pipeline_critic_model"]

    def test_drain_clears_the_buffer(self):
        settings_read_sink.record_read("qa_temperature")
        settings_read_sink.drain_read_keys()
        # Second drain sees nothing — the first drain cleared it, so the flush
        # job never re-stamps a key that hasn't been read since.
        assert settings_read_sink.drain_read_keys() == []

    def test_records_are_deduped(self):
        settings_read_sink.record_read("qa_critic_weight")
        settings_read_sink.record_read("qa_critic_weight")
        assert settings_read_sink.drain_read_keys() == ["qa_critic_weight"]

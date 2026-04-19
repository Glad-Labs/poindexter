"""Verify the sample plugins conform to their Protocols + are discoverable.

Samples prove the plugin framework works end-to-end for real plugin code,
not just mocks. Each sample:

- Is importable
- Has the correct shape for its Protocol (checked structurally)
- Appears in ``get_core_samples()`` output
"""

from __future__ import annotations

import pytest

from plugins import (
    Job,
    JobResult,
    Probe,
    ProbeResult,
    Tap,
    get_core_samples,
)


class TestHelloTap:
    """Sample Tap: plugins.samples.hello_tap.HelloTap"""

    def test_conforms_to_tap_protocol(self):
        from plugins.samples.hello_tap import HelloTap
        assert isinstance(HelloTap(), Tap)

    def test_has_required_attributes(self):
        from plugins.samples.hello_tap import HelloTap
        tap = HelloTap()
        assert tap.name == "hello"
        assert isinstance(tap.interval_seconds, int)

    @pytest.mark.asyncio
    async def test_extract_yields_document(self):
        from plugins.samples.hello_tap import HelloTap
        tap = HelloTap()

        docs = []
        async for doc in tap.extract(pool=None, config={"greeting": "hi"}):
            docs.append(doc)

        assert len(docs) == 1
        assert docs[0].source_id.startswith("samples/")
        assert docs[0].source_table == "samples"
        assert "hi" in docs[0].text
        assert docs[0].writer == "poindexter-samples"


class TestDatabaseProbe:
    """Sample Probe: plugins.samples.database_probe.DatabaseProbe"""

    def test_conforms_to_probe_protocol(self):
        from plugins.samples.database_probe import DatabaseProbe
        assert isinstance(DatabaseProbe(), Probe)

    def test_has_required_attributes(self):
        from plugins.samples.database_probe import DatabaseProbe
        probe = DatabaseProbe()
        assert probe.name == "database"
        assert probe.category == "infrastructure"
        assert isinstance(probe.interval_seconds, int)


class TestNoopJob:
    """Sample Job: plugins.samples.noop_job.NoopJob"""

    def test_conforms_to_job_protocol(self):
        from plugins.samples.noop_job import NoopJob
        assert isinstance(NoopJob(), Job)

    def test_has_required_attributes(self):
        from plugins.samples.noop_job import NoopJob
        job = NoopJob()
        assert job.name == "noop"
        assert job.schedule == "every 1 hour"
        assert job.idempotent is True

    @pytest.mark.asyncio
    async def test_run_returns_ok_result(self):
        from plugins.samples.noop_job import NoopJob
        result = await NoopJob().run(pool=None, config={})

        assert isinstance(result, JobResult)
        assert result.ok is True
        assert result.changes_made == 0


class TestCoreSamplesDiscovery:
    """``get_core_samples()`` returns every imperatively-loaded core plugin.

    The ``_SAMPLES`` table in ``plugins.registry`` grows as each phase
    lands, so these tests assert the minimum contract — sample plugins
    are present — instead of pinning exact counts that would flap every
    phase.
    """

    def test_sample_plugins_present(self):
        samples = get_core_samples()

        tap_names = {t.name for t in samples["taps"]}
        probe_names = {p.name for p in samples["probes"]}
        job_names = {j.name for j in samples["jobs"]}

        # Samples ship in every boot — their absence means registry is broken.
        assert "hello" in tap_names
        assert "database" in probe_names
        assert "noop" in job_names

    def test_unmigrated_plugin_types_empty(self):
        samples = get_core_samples()

        # Specializations + packs haven't been migrated yet.
        assert samples["reviewers"] == []
        assert samples["adapters"] == []
        assert samples["providers"] == []
        assert samples["packs"] == []
        # llm_providers DID migrate in Phase J — should be populated.
        assert len(samples["llm_providers"]) >= 1
        # stages: Phase E migration in progress. At least verify_task exists.
        stage_names = {s.name for s in samples["stages"]}
        assert "verify_task" in stage_names

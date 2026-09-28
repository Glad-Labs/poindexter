"""Tests for scripts/ci/settings_read_flush_lint.py.

Read telemetry (poindexter#756) is buffered per process, so every process that
loads a ``SiteConfig`` has to flush it, or every key only that process reads
looks unused to ``ProbeZeroReaderSettingsJob``. The Prefect flow runs, the CLI
and the auto-embed sidecar each missed that, silently, for months. The lint
makes every construction site flush or carry a reasoned ``ALLOWLIST`` entry.

``scan_source`` and ``evaluate`` are pure functions of source text, so most
tests below drive them with plain strings. Two tests pin the live tree: it is
clean, and the detector still finds the construction sites it exists for (a
detector that stopped matching would report clean forever).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit._nonempty import nonempty


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "settings_read_flush_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/settings_read_flush_lint.py")


_REPO = _find_repo_root(Path(__file__))
_LINT_PATH = _REPO / "scripts" / "ci" / "settings_read_flush_lint.py"


def _load():
    name = "settings_read_flush_lint_under_test"
    spec = importlib.util.spec_from_file_location(name, _LINT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Registered before exec: @dataclass resolves the module's (postponed)
    # annotations through sys.modules[cls.__module__].
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


lint = _load()

_SVC = "src/cofounder_agent/poindexter/services/example.py"
_CLI = "src/cofounder_agent/poindexter/cli/example.py"


def _sites(source: str, path: str = _SVC):
    report = lint.scan_source(source, path)
    return (
        sorted((s.scope, s.kind) for s in report.covered),
        sorted((s.scope, s.kind) for s in report.uncovered),
    )


@pytest.mark.unit
class TestFindsConstructionSites:
    def test_siteconfig_with_a_pool_is_a_site(self):
        _, uncovered = _sites(
            "async def run(pool):\n"
            "    sc = SiteConfig(pool=pool)\n"
            "    await sc.load(pool)\n"
        )
        assert uncovered == [("run", "SiteConfig(pool=...)")]

    def test_siteconfig_loaded_after_construction_is_a_site(self):
        _, uncovered = _sites(
            "async def run(pool):\n"
            "    cfg = SiteConfig()\n"
            "    await cfg.reload(pool)\n"
        )
        assert uncovered == [("run", "SiteConfig() then .load()")]

    def test_an_unloaded_siteconfig_is_not_a_site(self):
        # env/default-only instances (fallbacks, stubs) never read the DB
        assert _sites(
            "def fallback():\n"
            "    sc = SiteConfig()\n"
            "    return sc.get('x')\n"
            "def stub():\n"
            "    return SiteConfig(initial_config={})\n"
        ) == ([], [])

    def test_a_load_on_a_different_name_does_not_count(self):
        assert _sites(
            "async def run(pool):\n"
            "    sc = SiteConfig()\n"
            "    await PluginConfig.load(pool, 'tap', 'x')\n"
        ) == ([], [])

    def test_module_level_instance_loaded_in_a_function(self):
        # main.py's shape: `_site_cfg = SiteConfig()` at import, loaded in lifespan
        _, uncovered = _sites(
            "_site_cfg = SiteConfig()\n"
            "async def lifespan(app):\n"
            "    await _site_cfg.load(pool)\n"
        )
        assert uncovered == [("<module>", "SiteConfig() then .load()")]

    def test_attribute_instance_loaded_in_another_method(self):
        _, uncovered = _sites(
            "class Agent:\n"
            "    def __init__(self):\n"
            "        self._sc = SiteConfig()\n"
            "    async def start(self, pool):\n"
            "        await self._sc.load(pool)\n"
        )
        assert uncovered == [("Agent.__init__", "SiteConfig() then .load()")]

    def test_builder_calls_are_sites(self):
        _, uncovered = _sites(
            "async def a(pool):\n"
            "    c = await build_container(pool)\n"
            "async def b(pool):\n"
            "    sc, c = await di_wiring.build_and_wire_subprocess_with_container(pool)\n"
        )
        assert uncovered == [
            ("a", "build_container()"),
            ("b", "build_and_wire_subprocess_with_container()"),
        ]

    def test_constructions_inside_a_builder_definition_are_skipped(self):
        report = lint.scan_source(
            "async def build_container(pool, *, site_config=None):\n"
            "    site_config = SiteConfig(pool=pool)\n",
            "src/cofounder_agent/poindexter/services/bootstrap.py",
        )
        assert report.uncovered == [] and report.covered == []
        assert report.builder_defs_seen == {"build_container"}

    def test_the_same_function_name_elsewhere_is_not_skipped(self):
        # only the real builder definitions are exempt, not a namesake
        _, uncovered = _sites(
            "async def build_container(pool):\n"
            "    return SiteConfig(pool=pool)\n"
        )
        assert uncovered == [("build_container", "SiteConfig(pool=...)")]


@pytest.mark.unit
class TestCoverage:
    def test_a_direct_flush_covers_the_site(self):
        covered, uncovered = _sites(
            "async def run(pool):\n"
            "    sc = SiteConfig(pool=pool)\n"
            "    try:\n"
            "        await work(sc)\n"
            "    finally:\n"
            "        await flush_read_telemetry(pool, sc)\n"
        )
        assert covered == [("run", "SiteConfig(pool=...)")] and uncovered == []

    def test_a_flush_through_a_module_local_helper_covers_the_site(self):
        covered, _ = _sites(
            "async def _stamp(pool, sc):\n"
            "    await settings_read_telemetry.flush_read_telemetry(pool, sc)\n"
            "async def _stamp_twice(pool, sc):\n"
            "    await _stamp(pool, sc)\n"
            "async def run(pool):\n"
            "    sc = SiteConfig(pool=pool)\n"
            "    await _stamp_twice(pool, sc)\n"
        )
        assert covered == [("run", "SiteConfig(pool=...)")]

    def test_a_builder_is_covered_when_every_caller_flushes(self):
        # content_generation's shape: _wire_... builds, the flow flushes
        covered, uncovered = _sites(
            "async def _wire(db):\n"
            "    sc, _ = await build_and_wire_subprocess_with_container(db.pool)\n"
            "    return sc\n"
            "async def flow(db):\n"
            "    sc = await _wire(db)\n"
            "    try:\n"
            "        return await run(sc)\n"
            "    finally:\n"
            "        await flush_read_telemetry(db.pool, sc)\n"
        )
        assert covered == [("_wire", "build_and_wire_subprocess_with_container()")]
        assert uncovered == []

    def test_a_builder_is_uncovered_when_one_caller_does_not_flush(self):
        _, uncovered = _sites(
            "async def _make(pool):\n"
            "    sc = SiteConfig(pool=pool)\n"
            "    return sc\n"
            "async def good(pool):\n"
            "    sc = await _make(pool)\n"
            "    await flush_read_telemetry(pool, sc)\n"
            "async def bad(pool):\n"
            "    return (await _make(pool)).get('k')\n"
        )
        assert uncovered == [("_make", "SiteConfig(pool=...)")]

    def test_a_builder_nobody_in_the_module_calls_is_uncovered(self):
        _, uncovered = _sites("async def _make(pool):\n    return SiteConfig(pool=pool)\n")
        assert uncovered == [("_make", "SiteConfig(pool=...)")]

    def test_names_resolve_lexically_not_by_simple_name(self):
        """A flushing `_go` in one command must not cover another command
        whose own nested `_go` doesn't flush (CLI modules reuse the name)."""
        _, uncovered = _sites(
            "def cmd_a():\n"
            "    async def _go(pool):\n"
            "        sc = SiteConfig(pool=pool)\n"
            "        await flush_read_telemetry(pool, sc)\n"
            "    return run_service(_go)\n"
            "def cmd_b():\n"
            "    async def _go(pool):\n"
            "        return 1\n"
            "    async def _impl(pool):\n"
            "        sc = SiteConfig(pool=pool)\n"
            "        return await _go(pool)\n"
            "    return run_service(_impl)\n"
        )
        assert uncovered == [("cmd_b._impl", "SiteConfig(pool=...)")]

    def test_self_method_calls_resolve_to_the_class(self):
        covered, _ = _sites(
            "class Runner:\n"
            "    async def _flush(self, pool, sc):\n"
            "        await flush_read_telemetry(pool, sc)\n"
            "    async def run(self, pool):\n"
            "        sc = SiteConfig(pool=pool)\n"
            "        await self._flush(pool, sc)\n"
        )
        assert covered == [("Runner.run", "SiteConfig(pool=...)")]

    def test_a_flush_in_a_nested_function_does_not_cover_its_parent(self):
        # the nested def may never run; it is its own scope
        _, uncovered = _sites(
            "async def run(pool):\n"
            "    sc = SiteConfig(pool=pool)\n"
            "    async def later():\n"
            "        await flush_read_telemetry(pool, sc)\n"
            "    return sc\n"
        )
        assert uncovered == [("run", "SiteConfig(pool=...)")]


@pytest.mark.unit
class TestCliSiteConfigPlacement:
    def test_flagged_outside_the_cli(self):
        report = lint.scan_source("sc = cli_site_config(pool)\n", _SVC)
        assert report.misplaced_cli_ctor == [1]

    def test_fine_inside_the_cli(self):
        report = lint.scan_source("sc = cli_site_config(pool)\n", _CLI)
        assert report.misplaced_cli_ctor == []


@pytest.mark.unit
class TestEvaluate:
    _UNFLUSHED = "async def run(pool):\n    return SiteConfig(pool=pool)\n"

    def test_an_unflushed_site_fails_with_the_remedy(self):
        result = lint.evaluate({_SVC: self._UNFLUSHED}, allowlist={}, builder_defs=frozenset())
        assert len(result.failures) == 1
        assert f"{_SVC}:2" in result.failures[0]
        assert "flush_read_telemetry" in result.failures[0]

    def test_an_allowlisted_site_passes(self):
        result = lint.evaluate(
            {_SVC: self._UNFLUSHED},
            allowlist={f"{_SVC}::run": "long-lived process"},
            builder_defs=frozenset(),
        )
        assert result.failures == []
        assert result.allowlisted == 1

    def test_a_stale_allowlist_entry_fails(self):
        flushed = (
            "async def run(pool):\n"
            "    sc = SiteConfig(pool=pool)\n"
            "    await flush_read_telemetry(pool, sc)\n"
        )
        result = lint.evaluate(
            {_SVC: flushed},
            allowlist={f"{_SVC}::run": "no longer true"},
            builder_defs=frozenset(),
        )
        assert result.covered == 1
        assert len(result.failures) == 1
        assert "matches no unflushed SiteConfig" in result.failures[0]

    def test_a_stale_builder_def_fails(self):
        result = lint.evaluate(
            {_SVC: "x = 1\n"},
            allowlist={},
            builder_defs=frozenset({(_SVC, "build_container")}),
        )
        assert any("BUILDER_DEFS" in f for f in result.failures)

    def test_a_misplaced_cli_ctor_fails(self):
        result = lint.evaluate(
            {_SVC: "sc = cli_site_config(pool)\n"}, allowlist={}, builder_defs=frozenset()
        )
        assert any("outside poindexter/cli/" in f for f in result.failures)


@pytest.mark.unit
class TestTheLiveTree:
    def test_every_allowlist_entry_carries_a_reason(self):
        for key, reason in nonempty(lint.ALLOWLIST.items(), "ALLOWLIST"):
            assert "::" in key, key
            assert len(reason.split()) >= 8, (key, reason)

    def test_the_tree_is_clean(self):
        proc = subprocess.run(
            [sys.executable, str(_LINT_PATH)], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, proc.stderr
        assert "clean" in proc.stdout

    def test_the_detector_still_finds_the_sites_it_exists_for(self):
        """The flushed sites this lint was written around must stay visible.
        If a refactor blinds the detector, these go missing and the lint would
        report clean over nothing."""
        wanted = {
            ("src/cofounder_agent/poindexter/services/flows/content_generation.py",
             "_wire_subprocess_site_config"),
            ("src/cofounder_agent/poindexter/services/taps/runner.py", "run_all"),
            ("src/cofounder_agent/poindexter/cli/_lifecycle.py", "container_for_cli"),
            ("src/cofounder_agent/scripts/regen_media_scripts.py", "_main"),
        }
        found = set()
        for rel, _fn in wanted:
            report = lint.scan_source((_REPO / rel).read_text(encoding="utf-8"), rel)
            found |= {(s.path, s.scope) for s in report.covered}
        assert wanted <= found

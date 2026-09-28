"""Unit tests for services.taps.memory.MemoryFilesTap.

Uses ``tmp_path`` to simulate a Claude projects + OpenClaw memory
directory structure. Verifies:

- Multi-scope discovery (every ``<scope>/memory/`` directory is scanned,
  whatever the scope naming convention — Windows ``C--*`` or Linux
  ``-home-*``; regression test for the 2026-07-20 Pop!_OS migration where
  a hardcoded ``C--*`` glob silently matched zero scopes for 17 days)
- Scope-aware source_ids (same-named file in two scopes produces
  distinct source_ids — regression test for the 2026-04-18 collision bug)
- Chunking (files >MAX_CHARS yield multiple Documents with
  chunk_index metadata)
- Writer labels (claude-code / shared-context / openclaw)
- Dedup-relevant fields (content_hash stability via metadata)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest import mock

import pytest

from poindexter.plugins import Tap
from poindexter.services.site_config import SiteConfig
from poindexter.services.taps.memory import MemoryFilesTap, _build_source_id, _discover_memory_dirs
from tests.unit._nonempty import anonempty

_TAP_LOGGER = "poindexter.services.taps.memory"


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path_factory) -> Path:
    """An empty home directory, so no test here can read the developer's real one.

    The three sources have built-in defaults under ``Path.home()``. Before this
    fixture a test that skipped only some of them ingested whatever the machine
    running it happened to have (the operator's own checkout, in one case).
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    return fake_home


class TestDiscoverMemoryDirs:
    def test_finds_every_claude_scope(self, tmp_path: Path):
        projects = tmp_path / "projects"
        (projects / "C--users-alice" / "memory").mkdir(parents=True)
        (projects / "C--WINDOWS-system32" / "memory").mkdir(parents=True)
        (projects / "c--Users-alice-website" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(claude_projects_dir=str(projects))

        scopes = [scope for _, origin, scope in dirs if origin == "claude-code"]
        assert "C--users-alice" in scopes
        assert "C--WINDOWS-system32" in scopes
        # Discovery keys off "has a memory/ subdir", not a name pattern, so
        # casing no longer changes the outcome on any platform.
        assert "c--Users-alice-website" in scopes

    def test_finds_linux_scopes(self, tmp_path: Path):
        """Linux scope dirs are discovered — the Pop!_OS migration regression.

        The old ``projects_root.glob("C--*")`` encoded Windows scope naming.
        After the migration re-keyed scopes to the Linux checkout path it
        matched nothing, and the tap ingested zero files for 17 days while
        still reporting a healthy run.
        """
        projects = tmp_path / "projects"
        (projects / "-home-alice-project" / "memory").mkdir(parents=True)
        (projects / "-home-alice-other-project" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(
            claude_projects_dir=str(projects),
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
        )

        scopes = [scope for _, origin, scope in dirs if origin == "claude-code"]
        assert "-home-alice-project" in scopes
        assert "-home-alice-other-project" in scopes

    def test_finds_windows_and_linux_scopes_together(self, tmp_path: Path):
        """Back-compat: a host carrying both naming conventions gets both."""
        projects = tmp_path / "projects"
        (projects / "C--Users-alice-project" / "memory").mkdir(parents=True)
        (projects / "-home-alice-project" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(
            claude_projects_dir=str(projects),
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
        )

        scopes = {scope for _, _, scope in dirs}
        assert scopes == {"C--Users-alice-project", "-home-alice-project"}

    def test_skips_scopes_without_memory_subdir(self, tmp_path: Path):
        projects = tmp_path / "projects"
        projects.mkdir()
        (projects / "C--no-memory-here").mkdir()
        (projects / "C--has-memory" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(
            claude_projects_dir=str(projects),
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
        )
        scopes = [s for _, _, s in dirs]
        assert "C--has-memory" in scopes
        assert "C--no-memory-here" not in scopes

    def test_honors_openclaw_override(self, tmp_path: Path):
        projects = tmp_path / "empty"
        projects.mkdir()
        openclaw = tmp_path / "openclaw-memory"
        openclaw.mkdir()

        dirs = _discover_memory_dirs(
            claude_projects_dir=str(projects),
            openclaw_memory_dir=str(openclaw),
            shared_context_dir="__skip__",
        )
        origins = [origin for _, origin, _ in dirs]
        assert "openclaw" in origins

    def test_scope_allowlist_filters_other_scopes(self, tmp_path: Path):
        """Only allowlisted claude-code scopes are scanned (junction dedup).

        Guards the C--Users-alice ⇄ C--Users-alice-myproject Junction:
        the second scope is a reparse point to the first, so without the
        allowlist the host would embed the same files under two scopes.
        """
        projects = tmp_path / "projects"
        (projects / "C--Users-alice" / "memory").mkdir(parents=True)
        (projects / "C--Users-alice-myproject" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(
            claude_projects_dir=str(projects),
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
            scope_allowlist="C--Users-alice",
        )
        scopes = [s for _, _, s in dirs]
        assert "C--Users-alice" in scopes
        assert "C--Users-alice-myproject" not in scopes

    def test_scope_allowlist_is_case_insensitive(self, tmp_path: Path):
        """Docker bind mounts can lowercase Windows dir names — match on lower()."""
        projects = tmp_path / "projects"
        (projects / "C--Users-alice" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(
            claude_projects_dir=str(projects),
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
            scope_allowlist="c--users-alice",  # different casing than on disk
        )
        scopes = [s for _, _, s in dirs]
        assert "C--Users-alice" in scopes

    def test_empty_allowlist_keeps_all_scopes(self, tmp_path: Path):
        """Back-compat: empty allowlist means ingest every scope."""
        projects = tmp_path / "projects"
        (projects / "C--Users-alice" / "memory").mkdir(parents=True)
        (projects / "C--Users-alice-myproject" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(
            claude_projects_dir=str(projects),
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
            scope_allowlist="",
        )
        scopes = {s for _, _, s in dirs}
        assert "C--Users-alice" in scopes
        assert "C--Users-alice-myproject" in scopes


class TestSourceRootResolution:
    """Where each source's directory comes from, and what "unset" means.

    Precedence is the tap's own config, then the app-level setting of the same
    name (read from ``config["_site_config"]``), then the built-in default.
    """

    # -- shared-context: no operator-specific default -----------------------

    def test_shared_context_has_no_default_source(self, monkeypatch):
        """Unset, the source is off, and no location is even looked at.

        It used to default to a folder under the home directory named for one
        operator's checkout. Every other install carried that layout in its
        code, and any machine that did have the folder ingested it into the RAG
        corpus unasked. Asserting that nothing is probed pins "no default"
        without naming a path, so it holds against any replacement default too.
        """
        probed: list[Path] = []
        real_is_dir = Path.is_dir

        def _record(self: Path, *args, **kwargs) -> bool:
            probed.append(self)
            return real_is_dir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "is_dir", _record)

        dirs = _discover_memory_dirs(claude_projects_dir="__skip__", openclaw_memory_dir="__skip__")

        assert dirs == []
        assert probed == []

    def test_shared_context_dir_argument_is_ingested(self, tmp_path: Path):
        notes = tmp_path / "notes"
        notes.mkdir()

        dirs = _discover_memory_dirs(
            claude_projects_dir="__skip__",
            openclaw_memory_dir="__skip__",
            shared_context_dir=str(notes),
        )

        assert dirs == [(notes, "shared-context", "")]

    def test_shared_context_dir_setting_is_ingested(self, tmp_path: Path):
        notes = tmp_path / "notes"
        notes.mkdir()

        dirs = _discover_memory_dirs(
            claude_projects_dir="__skip__",
            openclaw_memory_dir="__skip__",
            site_config=SiteConfig(initial_config={"shared_context_dir": str(notes)}),
        )

        assert dirs == [(notes, "shared-context", "")]

    def test_seeded_empty_setting_leaves_the_source_off(self):
        """The seeded value is ``''``: it must read as "unset", not as a path."""
        dirs = _discover_memory_dirs(
            claude_projects_dir="__skip__",
            openclaw_memory_dir="__skip__",
            site_config=SiteConfig(initial_config={"shared_context_dir": ""}),
        )

        assert dirs == []

    # -- precedence and the skip sentinel, for all three sources ------------

    def test_tap_config_beats_the_setting(self, tmp_path: Path):
        from_tap = tmp_path / "from-tap"
        from_tap.mkdir()
        from_setting = tmp_path / "from-setting"
        from_setting.mkdir()

        dirs = _discover_memory_dirs(
            claude_projects_dir="__skip__",
            openclaw_memory_dir="__skip__",
            shared_context_dir=str(from_tap),
            site_config=SiteConfig(initial_config={"shared_context_dir": str(from_setting)}),
        )

        assert dirs == [(from_tap, "shared-context", "")]

    @pytest.mark.parametrize(
        ("key", "origin"),
        [("openclaw_memory_dir", "openclaw"), ("shared_context_dir", "shared-context")],
    )
    def test_the_setting_is_used_when_the_tap_config_names_nothing(
        self, key: str, origin: str, tmp_path: Path
    ):
        target = tmp_path / "target"
        target.mkdir()
        skips = {"claude_projects_dir": "__skip__", "openclaw_memory_dir": "__skip__",
                 "shared_context_dir": "__skip__"}
        skips.pop(key)

        dirs = _discover_memory_dirs(
            **skips, site_config=SiteConfig(initial_config={key: str(target)})
        )

        assert dirs == [(target, origin, "")]

    def test_claude_projects_dir_setting_is_used(self, tmp_path: Path):
        projects = tmp_path / "projects"
        (projects / "-home-alice-project" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(
            openclaw_memory_dir="__skip__",
            site_config=SiteConfig(initial_config={"claude_projects_dir": str(projects)}),
        )

        assert [(o, s) for _, o, s in dirs] == [("claude-code", "-home-alice-project")]

    @pytest.mark.parametrize("key", ["claude_projects_dir", "openclaw_memory_dir", "shared_context_dir"])
    def test_skip_sentinel_in_the_setting_turns_the_source_off_quietly(
        self, key: str, caplog
    ):
        """``__skip__`` works from either surface, and is not a "missing directory"."""
        with caplog.at_level(logging.WARNING, logger=_TAP_LOGGER):
            dirs = _discover_memory_dirs(
                site_config=SiteConfig(initial_config={key: "__skip__"}),
            )

        assert [o for _, o, _ in dirs if o != "claude-code"] == []
        assert "__skip__" not in caplog.text

    def test_every_setting_is_read_even_when_the_tap_config_pins_the_source(self):
        """A pinned source still reads its setting, so the key never looks orphaned.

        The zero-reader probe lists a seeded key nothing has read in 30 days. An
        install whose ``plugin.tap.memory`` row pins a source with ``__skip__``
        would otherwise never touch that source's setting, so a live key would
        be reported as dead.
        """
        site_config = SiteConfig()

        _discover_memory_dirs(
            claude_projects_dir="__skip__",
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
            site_config=site_config,
        )

        assert set(site_config.drain_read_keys()) == {
            "claude_projects_dir",
            "openclaw_memory_dir",
            "shared_context_dir",
        }

    # -- openclaw keeps its standard-location auto-detect -------------------

    def test_openclaw_is_still_auto_detected_at_its_standard_location(self, home: Path):
        memory = home / ".openclaw" / "workspace" / "memory"
        memory.mkdir(parents=True)

        dirs = _discover_memory_dirs(claude_projects_dir="__skip__")

        assert dirs == [(memory, "openclaw", "")]

    def test_claude_projects_dir_still_defaults_to_the_home_projects_tree(self, home: Path):
        (home / ".claude" / "projects" / "-home-alice-project" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs()

        assert [(o, s) for _, o, s in dirs] == [("claude-code", "-home-alice-project")]


class TestConfiguredButUnusableDirectoryWarns:
    """A path the operator set that is not a directory must not read as "no files".

    Before this, a mistyped ``shared_context_dir`` (or a container that never
    mounted the tree) ingested nothing and logged nothing.
    """

    _KEYS = ["claude_projects_dir", "openclaw_memory_dir", "shared_context_dir"]

    @pytest.mark.parametrize("key", _KEYS)
    def test_a_missing_directory_from_the_tap_config_warns(self, key: str, tmp_path: Path, caplog):
        missing = tmp_path / "typo"
        args = {"claude_projects_dir": "__skip__", "openclaw_memory_dir": "__skip__",
                "shared_context_dir": "__skip__", key: str(missing)}

        with caplog.at_level(logging.WARNING, logger=_TAP_LOGGER):
            dirs = _discover_memory_dirs(**args)

        assert dirs == []
        assert key in caplog.text
        assert str(missing) in caplog.text

    @pytest.mark.parametrize("key", _KEYS)
    def test_a_missing_directory_from_the_setting_warns(self, key: str, tmp_path: Path, caplog):
        missing = tmp_path / "typo"
        skips = {"claude_projects_dir": "__skip__", "openclaw_memory_dir": "__skip__",
                 "shared_context_dir": "__skip__"}
        skips.pop(key)

        with caplog.at_level(logging.WARNING, logger=_TAP_LOGGER):
            dirs = _discover_memory_dirs(
                **skips, site_config=SiteConfig(initial_config={key: str(missing)})
            )

        assert dirs == []
        assert key in caplog.text
        assert str(missing) in caplog.text

    def test_a_file_where_a_directory_is_expected_warns(self, tmp_path: Path, caplog):
        not_a_dir = tmp_path / "notes.md"
        not_a_dir.write_text("hello", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger=_TAP_LOGGER):
            dirs = _discover_memory_dirs(
                claude_projects_dir="__skip__",
                openclaw_memory_dir="__skip__",
                shared_context_dir=str(not_a_dir),
            )

        assert dirs == []
        assert "shared_context_dir" in caplog.text

    def test_absent_defaults_stay_quiet(self, caplog):
        """Most installs have no OpenClaw and no Claude Code: that is not a fault."""
        with caplog.at_level(logging.WARNING, logger=_TAP_LOGGER):
            dirs = _discover_memory_dirs()

        assert dirs == []
        assert caplog.text == ""

    def test_a_usable_configured_directory_does_not_warn(self, tmp_path: Path, caplog):
        notes = tmp_path / "notes"
        notes.mkdir()

        with caplog.at_level(logging.WARNING, logger=_TAP_LOGGER):
            _discover_memory_dirs(
                claude_projects_dir="__skip__",
                openclaw_memory_dir="__skip__",
                shared_context_dir=str(notes),
            )

        assert caplog.text == ""


class TestDiscoveryFailsLoud:
    """A tap that ingests nothing must say so — never return a quiet []."""

    def test_allowlist_matching_no_scope_warns(self, tmp_path: Path, caplog):
        """The exact shape of the 2026-07-20 outage: a stale allowlist.

        ``memory_scope_allowlist='C--Users-<you>'`` survived the Pop!_OS
        migration and filtered out every Linux scope. An empty result is
        indistinguishable from "no memory files exist" unless it warns.
        """
        projects = tmp_path / "projects"
        (projects / "-home-alice-project" / "memory").mkdir(parents=True)

        with caplog.at_level(logging.WARNING, logger="poindexter.services.taps.memory"):
            dirs = _discover_memory_dirs(
                claude_projects_dir=str(projects),
                openclaw_memory_dir="__skip__",
                shared_context_dir="__skip__",
                scope_allowlist="C--Users-alice",
            )

        assert dirs == []
        assert "matched none of the 1 scope(s)" in caplog.text
        # The operator needs the real scope names to fix the setting.
        assert "-home-alice-project" in caplog.text

    def test_no_scopes_at_all_warns(self, tmp_path: Path, caplog):
        projects = tmp_path / "projects"
        projects.mkdir()

        with caplog.at_level(logging.WARNING, logger="poindexter.services.taps.memory"):
            dirs = _discover_memory_dirs(
                claude_projects_dir=str(projects),
                openclaw_memory_dir="__skip__",
                shared_context_dir="__skip__",
            )

        assert dirs == []
        assert "no project scopes with a memory/ subdirectory" in caplog.text


class TestDiscoveryPermissions:
    """EACCES on one scope must not abort discovery of the others.

    ``Path.is_dir()`` propagates EACCES (it only ignores ENOENT/ENOTDIR/
    EBADF/ELOOP). Claude Code writes session scope dirs mode 0700, so a
    container running under a different uid raises on the first one.
    """

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root bypasses directory permission bits"
    )
    def test_unreadable_scope_is_skipped_not_fatal(self, tmp_path: Path, caplog):
        projects = tmp_path / "projects"
        (projects / "-home-alice-readable" / "memory").mkdir(parents=True)
        locked = projects / "-home-alice-locked"
        (locked / "memory").mkdir(parents=True)
        locked.chmod(0o000)

        try:
            with caplog.at_level(logging.WARNING, logger="poindexter.services.taps.memory"):
                dirs = _discover_memory_dirs(
                    claude_projects_dir=str(projects),
                    openclaw_memory_dir="__skip__",
                    shared_context_dir="__skip__",
                )

            scopes = [s for _, _, s in dirs]
            assert scopes == ["-home-alice-readable"]
            assert "permission denied" in caplog.text.lower()
        finally:
            locked.chmod(0o700)  # let tmp_path cleanup succeed

    def test_unlistable_projects_root_returns_empty(self, tmp_path: Path, caplog):
        """iterdir() raising must degrade to [], not propagate."""
        projects = tmp_path / "projects"
        projects.mkdir()

        def _boom(self):
            raise PermissionError(13, "Permission denied")

        with caplog.at_level(logging.WARNING, logger="poindexter.services.taps.memory"):
            with mock.patch.object(Path, "iterdir", _boom):
                dirs = _discover_memory_dirs(
                    claude_projects_dir=str(projects),
                    openclaw_memory_dir="__skip__",
                    shared_context_dir="__skip__",
                )

        assert dirs == []
        assert "cannot list project scopes" in caplog.text


class TestBuildSourceId:
    def test_claude_code_includes_scope(self):
        assert _build_source_id("claude-code", "C--WINDOWS-system32", "MEMORY.md") == (
            "claude-code/C--WINDOWS-system32/MEMORY.md"
        )

    def test_scope_prevents_collision(self):
        """Same filename, different scope → distinct source_ids.

        Regression guard for the 2026-04-18 collision bug.
        """
        a = _build_source_id("claude-code", "C--users-alice", "MEMORY.md")
        b = _build_source_id("claude-code", "C--WINDOWS-system32", "MEMORY.md")
        assert a != b

    def test_shared_context_no_scope(self):
        assert _build_source_id("shared-context", "", "feedback/matt.md") == (
            "shared-context/feedback/matt.md"
        )

    def test_openclaw_no_scope(self):
        assert _build_source_id("openclaw", "", "2026-04-19.md") == "openclaw/2026-04-19.md"


class TestMemoryFilesTapConformance:
    def test_satisfies_tap_protocol(self):
        assert isinstance(MemoryFilesTap(), Tap)

    def test_has_required_attributes(self):
        tap = MemoryFilesTap()
        assert tap.name == "memory"
        assert tap.interval_seconds == 3600


class TestMemoryFilesTapExtract:
    @pytest.fixture
    def populated_projects(self, tmp_path: Path):
        """Build a small faux Claude projects tree with 3 scopes + 5 files."""
        projects = tmp_path / "projects"
        (projects / "C--users-alice" / "memory").mkdir(parents=True)
        (projects / "C--users-alice" / "memory" / "MEMORY.md").write_text(
            "- user stuff\n", encoding="utf-8"
        )
        (projects / "C--users-alice" / "memory" / "project_vision.md").write_text(
            "# Vision\nBuild great things.\n", encoding="utf-8"
        )

        (projects / "C--WINDOWS-system32" / "memory").mkdir(parents=True)
        (projects / "C--WINDOWS-system32" / "memory" / "MEMORY.md").write_text(
            "- windows stuff\n", encoding="utf-8"
        )
        (projects / "C--WINDOWS-system32" / "memory" / "feedback_rule.md").write_text(
            "Don't do X.\n", encoding="utf-8"
        )

        # Scope with empty memory dir — should not yield anything.
        (projects / "C--empty-scope" / "memory").mkdir(parents=True)

        # Empty openclaw.
        openclaw = tmp_path / "openclaw-memory"
        openclaw.mkdir()

        return projects, openclaw

    @pytest.mark.asyncio
    async def test_yields_document_per_file(self, populated_projects):
        projects, openclaw = populated_projects
        tap = MemoryFilesTap()

        docs = []
        async for doc in anonempty(
            tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects),
                    "openclaw_memory_dir": str(openclaw),
                    "shared_context_dir": "__skip__",
                },
            ),
            "tap.extract",
        ):
            docs.append(doc)

        assert len(docs) == 4  # 2 files in each of 2 scopes; empty scope yields nothing

    @pytest.mark.asyncio
    async def test_source_ids_include_scope(self, populated_projects):
        projects, openclaw = populated_projects
        tap = MemoryFilesTap()

        source_ids = set()
        async for doc in anonempty(
            tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects),
                    "openclaw_memory_dir": str(openclaw),
                    "shared_context_dir": "__skip__",
                },
            ),
            "tap.extract",
        ):
            source_ids.add(doc.source_id)

        assert "claude-code/C--users-alice/MEMORY.md" in source_ids
        assert "claude-code/C--WINDOWS-system32/MEMORY.md" in source_ids
        # Two MEMORY.md files produced two distinct IDs — collision bug guard.
        memory_md_ids = [sid for sid in source_ids if sid.endswith("MEMORY.md")]
        assert len(memory_md_ids) == 2

    @pytest.mark.asyncio
    async def test_writer_set_to_origin(self, populated_projects):
        projects, openclaw = populated_projects
        tap = MemoryFilesTap()

        async for doc in anonempty(
            tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects),
                    "openclaw_memory_dir": str(openclaw),
                    "shared_context_dir": "__skip__",
                },
            ),
            "tap.extract",
        ):
            if doc.source_id.startswith("claude-code/"):
                assert doc.writer == "claude-code"

    @pytest.mark.asyncio
    async def test_metadata_includes_type_and_chars(self, populated_projects):
        projects, openclaw = populated_projects
        tap = MemoryFilesTap()

        async for doc in anonempty(
            tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects),
                    "openclaw_memory_dir": str(openclaw),
                    "shared_context_dir": "__skip__",
                },
            ),
            "tap.extract",
        ):
            assert "type" in doc.metadata
            assert "chars" in doc.metadata
            assert "filename" in doc.metadata
            assert "origin_path" in doc.metadata

    @pytest.mark.asyncio
    async def test_yields_one_document_per_file_regardless_of_size(self, tmp_path: Path):
        """Taps yield one Document per file — chunking happens in the runner.

        This keeps every Tap's contract simple and lets the chunking policy
        change in one place (services/taps/_chunking.py + the runner).
        """
        projects = tmp_path / "projects"
        (projects / "C--test" / "memory").mkdir(parents=True)

        big_content = (
            "# Section A\n" + ("a" * 4000) + "\n"
            "# Section B\n" + ("b" * 4000) + "\n"
        )
        (projects / "C--test" / "memory" / "big.md").write_text(big_content, encoding="utf-8")

        openclaw = tmp_path / "openclaw"
        openclaw.mkdir()

        tap = MemoryFilesTap()
        docs = []
        async for doc in anonempty(
            tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects),
                    "openclaw_memory_dir": str(openclaw),
                    "shared_context_dir": "__skip__",
                },
            ),
            "tap.extract",
        ):
            docs.append(doc)

        # Single document per file; full content preserved.
        assert len(docs) == 1
        assert docs[0].source_id == "claude-code/C--test/big.md"
        assert docs[0].text == big_content
        assert docs[0].metadata["chars"] == len(big_content)

    @pytest.mark.asyncio
    async def test_empty_files_skipped(self, tmp_path: Path):
        projects = tmp_path / "projects"
        (projects / "C--test" / "memory").mkdir(parents=True)
        (projects / "C--test" / "memory" / "empty.md").write_text("", encoding="utf-8")
        (projects / "C--test" / "memory" / "whitespace.md").write_text("   \n\n", encoding="utf-8")
        (projects / "C--test" / "memory" / "real.md").write_text("real content", encoding="utf-8")
        openclaw = tmp_path / "openclaw"
        openclaw.mkdir()

        tap = MemoryFilesTap()
        docs = []
        async for doc in anonempty(
            tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects),
                    "openclaw_memory_dir": str(openclaw),
                    "shared_context_dir": "__skip__",
                },
            ),
            "tap.extract",
        ):
            docs.append(doc)

        source_ids = {d.source_id for d in docs}
        assert "claude-code/C--test/real.md" in source_ids
        assert not any("empty" in sid for sid in source_ids)
        assert not any("whitespace" in sid for sid in source_ids)

    @pytest.mark.asyncio
    async def test_extract_reads_settings_from_the_site_config_key(self, tmp_path: Path):
        """The tap resolves a setting through ``config["_site_config"]``."""
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "handoff.md").write_text("# Handoff\nship it\n", encoding="utf-8")

        docs = []
        async for doc in anonempty(
            MemoryFilesTap().extract(
                pool=None,
                config={
                    "claude_projects_dir": "__skip__",
                    "openclaw_memory_dir": "__skip__",
                    "_site_config": SiteConfig(initial_config={"shared_context_dir": str(notes)}),
                },
            ),
            "tap.extract",
        ):
            docs.append(doc)

        assert [d.source_id for d in docs] == ["shared-context/handoff.md"]
        assert docs[0].writer == "shared-context"

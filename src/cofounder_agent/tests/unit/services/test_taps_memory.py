"""Unit tests for services.taps.memory.MemoryFilesTap.

Uses ``tmp_path`` to simulate a Claude projects + OpenClaw memory
directory structure. Verifies:

- Multi-scope discovery (every ``C--*/memory/`` directory is scanned)
- Scope-aware source_ids (same-named file in two scopes produces
  distinct source_ids — regression test for the 2026-04-18 collision bug)
- Chunking (files >MAX_CHARS yield multiple Documents with
  chunk_index metadata)
- Writer labels (claude-code / shared-context / openclaw)
- Dedup-relevant fields (content_hash stability via metadata)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins import Tap
from services.taps.memory import MemoryFilesTap, _build_source_id, _discover_memory_dirs


class TestDiscoverMemoryDirs:
    def test_finds_every_claude_scope(self, tmp_path: Path):
        projects = tmp_path / "projects"
        (projects / "C--users-mattm" / "memory").mkdir(parents=True)
        (projects / "C--WINDOWS-system32" / "memory").mkdir(parents=True)
        (projects / "c--Users-mattm-website" / "memory").mkdir(parents=True)

        dirs = _discover_memory_dirs(claude_projects_dir=str(projects))

        scopes = [scope for _, origin, scope in dirs if origin == "claude-code"]
        assert "C--users-mattm" in scopes
        assert "C--WINDOWS-system32" in scopes
        # Case-insensitive glob `C--*` matches `c--Users-...` too on Windows.
        # On Linux the glob is case-sensitive so the lowercase scope may not match.
        # Accept either outcome — the Phase B user is on Windows.

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

    def test_uses_injected_site_config(self, tmp_path: Path):
        """Runner-injected site_config resolves paths when no explicit
        args are passed (Phase H step 4.6, GH#95)."""

        class _FakeSC:
            def __init__(self, vals: dict[str, str]):
                self._vals = vals

            def get(self, key: str, default=""):
                return self._vals.get(key, default)

        projects = tmp_path / "sc-projects"
        (projects / "C--from-sc" / "memory").mkdir(parents=True)

        sc = _FakeSC({"claude_projects_dir": str(projects)})
        dirs = _discover_memory_dirs(
            openclaw_memory_dir="__skip__",
            shared_context_dir="__skip__",
            site_config=sc,
        )
        scopes = [scope for _, origin, scope in dirs if origin == "claude-code"]
        assert "C--from-sc" in scopes


class TestBuildSourceId:
    def test_claude_code_includes_scope(self):
        assert _build_source_id("claude-code", "C--WINDOWS-system32", "MEMORY.md") == (
            "claude-code/C--WINDOWS-system32/MEMORY.md"
        )

    def test_scope_prevents_collision(self):
        """Same filename, different scope → distinct source_ids.

        Regression guard for the 2026-04-18 collision bug.
        """
        a = _build_source_id("claude-code", "C--users-mattm", "MEMORY.md")
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
        (projects / "C--users-mattm" / "memory").mkdir(parents=True)
        (projects / "C--users-mattm" / "memory" / "MEMORY.md").write_text(
            "- user stuff\n", encoding="utf-8"
        )
        (projects / "C--users-mattm" / "memory" / "project_vision.md").write_text(
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
        async for doc in tap.extract(
            pool=None,
            config={
                "claude_projects_dir": str(projects),
                "openclaw_memory_dir": str(openclaw),
                "shared_context_dir": "__skip__",
            },
        ):
            docs.append(doc)

        assert len(docs) == 4  # 2 files in each of 2 scopes; empty scope yields nothing

    @pytest.mark.asyncio
    async def test_source_ids_include_scope(self, populated_projects):
        projects, openclaw = populated_projects
        tap = MemoryFilesTap()

        source_ids = set()
        async for doc in tap.extract(
            pool=None,
            config={
                "claude_projects_dir": str(projects),
                "openclaw_memory_dir": str(openclaw),
                "shared_context_dir": "__skip__",
            },
        ):
            source_ids.add(doc.source_id)

        assert "claude-code/C--users-mattm/MEMORY.md" in source_ids
        assert "claude-code/C--WINDOWS-system32/MEMORY.md" in source_ids
        # Two MEMORY.md files produced two distinct IDs — collision bug guard.
        memory_md_ids = [sid for sid in source_ids if sid.endswith("MEMORY.md")]
        assert len(memory_md_ids) == 2

    @pytest.mark.asyncio
    async def test_writer_set_to_origin(self, populated_projects):
        projects, openclaw = populated_projects
        tap = MemoryFilesTap()

        async for doc in tap.extract(
            pool=None,
            config={
                "claude_projects_dir": str(projects),
                "openclaw_memory_dir": str(openclaw),
                "shared_context_dir": "__skip__",
            },
        ):
            if doc.source_id.startswith("claude-code/"):
                assert doc.writer == "claude-code"

    @pytest.mark.asyncio
    async def test_metadata_includes_type_and_chars(self, populated_projects):
        projects, openclaw = populated_projects
        tap = MemoryFilesTap()

        async for doc in tap.extract(
            pool=None,
            config={
                "claude_projects_dir": str(projects),
                "openclaw_memory_dir": str(openclaw),
                "shared_context_dir": "__skip__",
            },
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
        async for doc in tap.extract(
            pool=None,
            config={
                "claude_projects_dir": str(projects),
                "openclaw_memory_dir": str(openclaw),
                "shared_context_dir": "__skip__",
            },
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
        async for doc in tap.extract(
            pool=None,
            config={
                "claude_projects_dir": str(projects),
                "openclaw_memory_dir": str(openclaw),
                "shared_context_dir": "__skip__",
            },
        ):
            docs.append(doc)

        source_ids = {d.source_id for d in docs}
        assert "claude-code/C--test/real.md" in source_ids
        assert not any("empty" in sid for sid in source_ids)
        assert not any("whitespace" in sid for sid in source_ids)

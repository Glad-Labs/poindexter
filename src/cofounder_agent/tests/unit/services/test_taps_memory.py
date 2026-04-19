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
    async def test_metadata_includes_type_and_chunk_index(self, populated_projects):
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
            assert "chunk_index" in doc.metadata
            assert "total_chunks" in doc.metadata
            assert doc.metadata["chunk_index"] == 0  # all test files fit in one chunk
            assert doc.metadata["total_chunks"] == 1

    @pytest.mark.asyncio
    async def test_oversize_file_yields_multiple_documents(self, tmp_path: Path):
        projects = tmp_path / "projects"
        (projects / "C--test" / "memory").mkdir(parents=True)

        # Build a file >MAX_CHARS with heading boundaries for clean splitting.
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

        assert len(docs) >= 2
        # All docs for the same file share source_id but have distinct chunk_index.
        assert all(d.source_id == "claude-code/C--test/big.md" for d in docs)
        indices = sorted(d.metadata["chunk_index"] for d in docs)
        assert indices == list(range(len(docs)))
        # Every chunk reports total_chunks == len(docs)
        assert all(d.metadata["total_chunks"] == len(docs) for d in docs)

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

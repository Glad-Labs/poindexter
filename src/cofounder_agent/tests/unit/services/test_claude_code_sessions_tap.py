"""Unit tests for ``services/taps/claude_code_sessions.py``.

Covers:

- JSONL parsing (well-formed + malformed lines)
- Noise filtering (system reminders, loop sentinels, file-history-snapshot)
- Assistant block handling (text / thinking / tool_use / tool_result)
- Secret scrubbing (OpenAI / Anthropic / GitHub / JWT / AWS / enc:v1:)
- Session discovery across scope directories
- Config overrides (max_sessions_per_run, session_age_max_days, custom scrub)

No real filesystem outside ``tmp_path``. No real DB.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from services.taps.claude_code_sessions import (
    ClaudeCodeSessionsTap,
    _compile_scrub_patterns,
    _extract_assistant_text,
    _extract_user_text,
    _render_session,
    _resolve_projects_dir,
    _scrub,
    _split_session,
    _strip_user_noise,
    _summarize_tool_input,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _jsonl_line(obj: dict[str, Any]) -> str:
    return json.dumps(obj) + "\n"


def _make_session_file(
    dir_path: Path, session_uuid: str, events: list[dict[str, Any]]
) -> Path:
    """Write a JSONL session file with the given events."""
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{session_uuid}.jsonl"
    path.write_text(
        "".join(_jsonl_line(e) for e in events), encoding="utf-8"
    )
    return path


@pytest.fixture
def projects_tree(tmp_path: Path) -> Path:
    """Build a minimal .claude/projects tree with two scopes."""
    root = tmp_path / "projects"
    root.mkdir()
    (root / "C--scope-a").mkdir()
    (root / "C--scope-b").mkdir()
    return root


# ---------------------------------------------------------------------------
# Scrub patterns
# ---------------------------------------------------------------------------


class TestScrub:
    def test_openai_key_redacted(self):
        patterns = _compile_scrub_patterns([])
        out = _scrub("My key is sk-abc123def456ghi789jkl012mno345pqr678", patterns)
        assert "sk-abc123" not in out
        assert "[REDACTED:sk]" in out

    def test_anthropic_key_redacted(self):
        patterns = _compile_scrub_patterns([])
        out = _scrub("key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz", patterns)
        assert "sk-ant-api03" not in out
        assert "[REDACTED:sk-ant]" in out

    def test_github_pat_redacted(self):
        patterns = _compile_scrub_patterns([])
        out = _scrub("token: ghp_abcdefghijklmnopqrstuvwxyz01234567890", patterns)
        assert "ghp_abc" not in out
        assert "[REDACTED:ghp]" in out

    def test_aws_key_redacted(self):
        patterns = _compile_scrub_patterns([])
        out = _scrub("access=AKIAIOSFODNN7EXAMPLE", patterns)
        assert "AKIA" not in out
        assert "[REDACTED:aws]" in out

    def test_jwt_redacted(self):
        patterns = _compile_scrub_patterns([])
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        out = _scrub(f"auth: Bearer {jwt}", patterns)
        assert "eyJhbGciOi" not in out
        assert "[REDACTED:jwt]" in out

    def test_enc_v1_ciphertext_redacted(self):
        """Our own encrypted-secret ciphertext prefix must not leak into
        embeddings — otherwise encrypted rows from app_settings end up
        semi-searchable and bloat the vector index."""
        patterns = _compile_scrub_patterns([])
        out = _scrub(
            "value: enc:v1:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH+/==",
            patterns,
        )
        assert "enc:v1:" not in out
        assert "[REDACTED:enc]" in out

    def test_custom_pattern(self):
        patterns = _compile_scrub_patterns([r"secret-\d{5}"])
        out = _scrub("value=secret-12345 rest", patterns)
        assert "secret-12345" not in out
        assert "[REDACTED:custom]" in out

    def test_innocent_text_untouched(self):
        patterns = _compile_scrub_patterns([])
        out = _scrub("ordinary sentence with api and keys as words", patterns)
        assert out == "ordinary sentence with api and keys as words"


# ---------------------------------------------------------------------------
# User text extraction
# ---------------------------------------------------------------------------


class TestStripUserNoise:
    def test_system_reminder_removed(self):
        text = "real user text\n<system-reminder>noise</system-reminder>"
        assert _strip_user_noise(text) == "real user text"

    def test_multiline_system_reminder(self):
        text = (
            "start\n<system-reminder>\nline1\nline2\n</system-reminder>\nend"
        )
        assert _strip_user_noise(text) == "start\n\nend"

    def test_command_name_tag(self):
        text = "<command-name>hello</command-name>running command"
        assert _strip_user_noise(text) == "running command"

    def test_autonomous_loop_sentinel(self):
        assert _strip_user_noise("<<autonomous-loop-dynamic>>") == ""
        assert _strip_user_noise("<<autonomous-loop>>") == ""


class TestExtractUserText:
    def test_simple_string_content(self):
        event = {"type": "user", "message": {"content": "hello"}}
        assert _extract_user_text(event) == "hello"

    def test_pure_noise_returns_empty(self):
        event = {
            "type": "user",
            "message": {"content": "<system-reminder>x</system-reminder>"},
        }
        assert _extract_user_text(event) == ""

    def test_array_content_text_block(self):
        event = {
            "type": "user",
            "message": {"content": [{"type": "text", "text": "multi-block"}]},
        }
        assert _extract_user_text(event) == "multi-block"

    def test_tool_result_block_included(self):
        event = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "result data here"}
                ]
            },
        }
        assert "[tool result]:" in _extract_user_text(event)
        assert "result data here" in _extract_user_text(event)

    def test_missing_message_returns_empty(self):
        assert _extract_user_text({"type": "user"}) == ""


# ---------------------------------------------------------------------------
# Assistant text extraction
# ---------------------------------------------------------------------------


class TestExtractAssistantText:
    def test_text_block_verbatim(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Reply text"}]},
        }
        assert _extract_assistant_text(event, 2000) == "Reply text"

    def test_thinking_block_dropped(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "internal", "signature": "abc"},
                    {"type": "text", "text": "visible"},
                ]
            },
        }
        out = _extract_assistant_text(event, 2000)
        assert "internal" not in out
        assert out == "visible"

    def test_tool_use_summarized(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "ls /tmp", "description": "list"},
                    }
                ]
            },
        }
        out = _extract_assistant_text(event, 2000)
        assert out.startswith("[tool: Bash(")
        assert "command=ls /tmp" in out

    def test_tool_result_truncated(self):
        big_output = "x" * 10000
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_result", "content": big_output}
                ]
            },
        }
        out = _extract_assistant_text(event, max_tool_result_chars=500)
        assert "[tool result]:" in out
        # Content part only (header not counted)
        assert len(out) < 600

    def test_empty_returns_empty(self):
        event = {"type": "assistant", "message": {"content": []}}
        assert _extract_assistant_text(event, 2000) == ""


class TestSummarizeToolInput:
    def test_empty(self):
        assert _summarize_tool_input(None) == ""
        assert _summarize_tool_input({}) == ""

    def test_short_values(self):
        out = _summarize_tool_input({"a": "1", "b": "2"})
        assert out == "a=1, b=2"

    def test_truncates_long_value(self):
        long_str = "y" * 200
        out = _summarize_tool_input({"cmd": long_str})
        assert len(out) < 120

    def test_only_first_three_keys(self):
        inp = {f"k{i}": f"v{i}" for i in range(10)}
        out = _summarize_tool_input(inp)
        assert "k0=v0" in out
        assert "k2=v2" in out
        assert "k5=v5" not in out


# ---------------------------------------------------------------------------
# Session rendering
# ---------------------------------------------------------------------------


class TestRenderSession:
    def test_renders_user_assistant_pairs(self, tmp_path: Path):
        path = _make_session_file(
            tmp_path,
            "session-1",
            [
                {"type": "file-history-snapshot", "messageId": "x"},
                {"type": "user", "message": {"content": "hello"}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "hi there"}]
                    },
                },
            ],
        )
        out = _render_session(path, max_tool_chars=2000)
        assert "USER: hello" in out
        assert "ASSISTANT: hi there" in out
        # file-history-snapshot dropped
        assert "snapshot" not in out.lower()

    def test_empty_session_returns_empty(self, tmp_path: Path):
        path = _make_session_file(tmp_path, "empty", [])
        assert _render_session(path, max_tool_chars=2000) == ""

    def test_malformed_json_lines_skipped(self, tmp_path: Path):
        tmp_path.mkdir(exist_ok=True)
        path = tmp_path / "s.jsonl"
        path.write_text(
            _jsonl_line({"type": "user", "message": {"content": "valid"}})
            + "{not-json\n"
            + _jsonl_line({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}),
            encoding="utf-8",
        )
        out = _render_session(path, max_tool_chars=2000)
        assert "USER: valid" in out
        assert "ASSISTANT: ok" in out

    def test_noise_only_session_returns_empty(self, tmp_path: Path):
        """Session with nothing but system reminders + snapshots yields
        no meaningful text — caller should skip emitting a Document."""
        path = _make_session_file(
            tmp_path,
            "noise",
            [
                {"type": "file-history-snapshot", "messageId": "x"},
                {
                    "type": "user",
                    "message": {
                        "content": "<system-reminder>x</system-reminder>"
                    },
                },
                {"type": "system", "content": "internal"},
            ],
        )
        assert _render_session(path, max_tool_chars=2000) == ""


# ---------------------------------------------------------------------------
# Projects-dir resolution
# ---------------------------------------------------------------------------


class _FakeSiteConfig:
    """Dict-backed stand-in for services.site_config.site_config.

    Mirrors the ``.get(key, default)`` shape the Tap relies on. Used in
    tests so the injected site_config path can be exercised without
    depending on the real module-level singleton (Phase H step 4.6,
    GH#95).
    """

    def __init__(self, values: dict[str, Any] | None = None):
        self._values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


class TestResolveProjectsDir:
    def test_config_override_wins(self, tmp_path: Path):
        custom = tmp_path / "custom"
        custom.mkdir()
        result = _resolve_projects_dir({"claude_projects_dir": str(custom)})
        assert result == custom

    def test_injected_site_config_used(self, tmp_path: Path):
        """When the runner seeds ``_site_config`` into the config dict, the
        Tap should read ``claude_projects_dir`` from it instead of importing
        the module singleton."""
        custom = tmp_path / "from-sc"
        custom.mkdir()
        sc = _FakeSiteConfig({"claude_projects_dir": str(custom)})
        result = _resolve_projects_dir({"_site_config": sc})
        assert result == custom

    def test_config_override_beats_injected_site_config(self, tmp_path: Path):
        override = tmp_path / "override"
        override.mkdir()
        sc = _FakeSiteConfig({"claude_projects_dir": str(tmp_path / "ignored")})
        result = _resolve_projects_dir(
            {"claude_projects_dir": str(override), "_site_config": sc}
        )
        assert result == override

    def test_default_home_path(self):
        """With no overrides and no injected site_config, falls back to the
        legacy singleton or the home-dir default."""
        result = _resolve_projects_dir({})
        # We don't assert the exact value — home-dependent — just the shape.
        assert result.name == "projects"
        assert result.parent.name == ".claude"


# ---------------------------------------------------------------------------
# Tap.extract integration (filesystem + Document yields)
# ---------------------------------------------------------------------------


class TestClaudeCodeSessionsTapExtract:
    @pytest.mark.asyncio
    async def test_yields_document_per_session(self, projects_tree: Path):
        _make_session_file(
            projects_tree / "C--scope-a",
            "sess-1",
            [
                {"type": "user", "message": {"content": "q1"}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "a1"}]}},
            ],
        )
        _make_session_file(
            projects_tree / "C--scope-b",
            "sess-2",
            [
                {"type": "user", "message": {"content": "q2"}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "a2"}]}},
            ],
        )

        tap = ClaudeCodeSessionsTap()
        docs = []
        async for doc in tap.extract(pool=None, config={"claude_projects_dir": str(projects_tree)}):
            docs.append(doc)

        assert len(docs) == 2
        source_ids = sorted(d.source_id for d in docs)
        assert source_ids == [
            "claude-code-sessions/C--scope-a/sess-1",
            "claude-code-sessions/C--scope-b/sess-2",
        ]
        assert all(d.source_table == "claude_sessions" for d in docs)
        assert all(d.writer == "claude-code-sessions" for d in docs)
        assert all("USER:" in d.text and "ASSISTANT:" in d.text for d in docs)

    @pytest.mark.asyncio
    async def test_empty_sessions_skipped(self, projects_tree: Path):
        _make_session_file(projects_tree / "C--scope-a", "empty", [])
        tap = ClaudeCodeSessionsTap()
        docs = [d async for d in tap.extract(
            pool=None, config={"claude_projects_dir": str(projects_tree)}
        )]
        assert docs == []

    @pytest.mark.asyncio
    async def test_max_sessions_per_run_cap(self, projects_tree: Path):
        for i in range(5):
            _make_session_file(
                projects_tree / "C--scope-a",
                f"sess-{i}",
                [{"type": "user", "message": {"content": f"q{i}"}}],
            )
        tap = ClaudeCodeSessionsTap()
        docs = [
            d async for d in tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects_tree),
                    "max_sessions_per_run": 2,
                },
            )
        ]
        assert len(docs) == 2

    @pytest.mark.asyncio
    async def test_session_age_max_days_skips_old(self, projects_tree: Path):
        old_path = _make_session_file(
            projects_tree / "C--scope-a",
            "old",
            [{"type": "user", "message": {"content": "ancient"}}],
        )
        # Backdate the file mtime by 30 days
        old_time = time.time() - 30 * 86400
        os.utime(old_path, (old_time, old_time))

        _make_session_file(
            projects_tree / "C--scope-a",
            "recent",
            [{"type": "user", "message": {"content": "fresh"}}],
        )

        tap = ClaudeCodeSessionsTap()
        docs = [
            d async for d in tap.extract(
                pool=None,
                config={
                    "claude_projects_dir": str(projects_tree),
                    "session_age_max_days": 7,
                },
            )
        ]
        source_ids = [d.source_id for d in docs]
        assert source_ids == ["claude-code-sessions/C--scope-a/recent"]

    @pytest.mark.asyncio
    async def test_secret_scrub_applied(self, projects_tree: Path):
        _make_session_file(
            projects_tree / "C--scope-a",
            "secret-sess",
            [
                {
                    "type": "user",
                    "message": {
                        "content": "key=sk-abc123def456ghi789jkl012mno345pqr"
                    },
                },
            ],
        )
        tap = ClaudeCodeSessionsTap()
        docs = [
            d async for d in tap.extract(
                pool=None,
                config={"claude_projects_dir": str(projects_tree)},
            )
        ]
        assert len(docs) == 1
        assert "sk-abc" not in docs[0].text
        assert "[REDACTED:sk]" in docs[0].text

    @pytest.mark.asyncio
    async def test_nonexistent_projects_dir_yields_nothing(self, tmp_path: Path):
        tap = ClaudeCodeSessionsTap()
        docs = [
            d async for d in tap.extract(
                pool=None,
                config={"claude_projects_dir": str(tmp_path / "does-not-exist")},
            )
        ]
        assert docs == []


# ---------------------------------------------------------------------------
# Tap contract
# ---------------------------------------------------------------------------


class TestTapContract:
    def test_has_required_attrs(self):
        tap = ClaudeCodeSessionsTap()
        assert tap.name == "claude_code_sessions"
        assert isinstance(tap.interval_seconds, int)
        assert tap.interval_seconds > 0

    def test_extract_is_async_generator(self):
        import inspect
        assert inspect.isasyncgenfunction(ClaudeCodeSessionsTap.extract)


# ---------------------------------------------------------------------------
# Session-level chunking (keeps Documents inside nomic-embed-text's context)
# ---------------------------------------------------------------------------


class TestSplitSession:
    def test_short_session_one_chunk(self):
        text = "USER: hi\n\nASSISTANT: hello"
        assert _split_session(text) == [text]

    def test_long_session_splits_on_turn_boundary(self):
        # 4 turns, each ~1000 chars → should split at turn boundaries.
        turns = [f"USER: {'q'*1000}", f"ASSISTANT: {'a'*1000}"] * 2
        text = "\n\n".join(turns)
        chunks = _split_session(text, max_chars=2500)
        assert len(chunks) >= 2
        # Every chunk should start with USER: or ASSISTANT: (boundary preserved).
        for c in chunks:
            assert c.startswith("USER:") or c.startswith("ASSISTANT:")

    def test_oversized_single_turn_hard_sliced(self):
        # One massive turn that exceeds max_chars all by itself.
        text = "USER: " + ("x" * 10000)
        chunks = _split_session(text, max_chars=3000)
        # Must emit something even when no turn boundary helps.
        assert len(chunks) >= 3
        assert all(len(c) <= 3000 for c in chunks)

    def test_yields_non_empty_chunks_only(self):
        text = "USER: hi\n\n\n\nASSISTANT: hello"
        chunks = _split_session(text, max_chars=100)
        assert all(c.strip() for c in chunks)


class TestExtractMultipartDocuments:
    @pytest.mark.asyncio
    async def test_large_session_yields_multi_part_docs(self, projects_tree: Path):
        # Build a session big enough to force splitting.
        big_events = []
        for i in range(20):
            big_events.append({
                "type": "user",
                "message": {"content": f"question {i} " + ("q" * 400)},
            })
            big_events.append({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": f"answer {i} " + ("a" * 400)}
                    ]
                },
            })
        _make_session_file(projects_tree / "C--scope-a", "big", big_events)

        tap = ClaudeCodeSessionsTap()
        docs = [d async for d in tap.extract(
            pool=None, config={"claude_projects_dir": str(projects_tree)},
        )]
        # Many parts expected
        assert len(docs) > 1
        # Part suffix present on each
        assert all("/part-" in d.source_id for d in docs)
        # Metadata carries part numbers
        assert all(d.metadata["part"] >= 1 for d in docs)
        assert len({d.metadata["total_parts"] for d in docs}) == 1
        # Chunks stay within the nomic-safe bound
        assert all(len(d.text) <= 3500 for d in docs)

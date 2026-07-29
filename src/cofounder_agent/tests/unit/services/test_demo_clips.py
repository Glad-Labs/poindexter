"""Tests for the VHS demo-clip baker (Glad-Labs/poindexter#937).

The catalog tests at the bottom run against the **real** ``demo_tapes/``
directory rather than fixtures. That is deliberate: a tape is only useful if
it is safe and if a failed command fails the bake, and both properties are
easy to break by adding a tape without reading the module docstring.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.demo_clips import (
    DemoTape,
    DemoTapeError,
    assert_read_only,
    compose_tape,
    load_tapes,
    parse_tape,
    render_preamble,
    tapes_dir,
)

VALID = """\
# title: Recent posts
# description: Shows real published posts.
# category: content

Type "poindexter posts list --limit 5"
Enter
Wait+Screen@45s /total/
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _tape(body: str, slug: str = "t") -> DemoTape:
    return DemoTape(slug, "T", "D", "c", body, Path("x"))


# ---------------------------------------------------------------------------
# parse_tape
# ---------------------------------------------------------------------------


def test_parse_tape_reads_header(tmp_path: Path) -> None:
    tape = parse_tape(_write(tmp_path, "posts-list.tape", VALID))
    assert tape.slug == "posts-list"
    assert tape.title == "Recent posts"
    assert tape.category == "content"
    assert tape.commands == ["poindexter posts list --limit 5"]


@pytest.mark.parametrize("field", ["title", "description"])
def test_parse_tape_requires_title_and_description(tmp_path: Path, field: str) -> None:
    """An unlabelled tape is invisible to the director — fail loud, not silent."""
    body = "\n".join(l for l in VALID.splitlines() if not l.startswith(f"# {field}"))
    with pytest.raises(DemoTapeError, match=field):
        parse_tape(_write(tmp_path, "x.tape", body))


def test_parse_tape_font_size_override(tmp_path: Path) -> None:
    tape = parse_tape(_write(tmp_path, "x.tape", "# font_size: 26\n" + VALID))
    assert tape.font_size == 26


def test_parse_tape_rejects_non_integer_font_size(tmp_path: Path) -> None:
    with pytest.raises(DemoTapeError, match="not an integer"):
        parse_tape(_write(tmp_path, "x.tape", "# font_size: big\n" + VALID))


def test_header_parsing_stops_at_first_command(tmp_path: Path) -> None:
    """A '# title:' in a trailing comment must not override the real header."""
    body = VALID + '\n# title: sneaky\nType "poindexter posts list"\n'
    assert parse_tape(_write(tmp_path, "x.tape", body)).title == "Recent posts"


# ---------------------------------------------------------------------------
# assert_read_only — demos run against live production data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "poindexter posts list --limit 5",
    "poindexter doctor",
    "poindexter costs budget",
    "poindexter memory search --limit 3",
    "poindexter qa-gates show programmatic_validator",
])
def test_read_only_commands_allowed(command: str) -> None:
    assert_read_only(_tape(f'Type "{command}"\n'))


@pytest.mark.parametrize("command", [
    "poindexter tasks approve abc123",
    "poindexter posts publish abc123",
    "poindexter settings set foo bar",
    "poindexter retention run",
    "poindexter publishers fire youtube_main",
])
def test_mutating_commands_rejected(command: str) -> None:
    """These would not merely record badly — they would change production."""
    with pytest.raises(DemoTapeError, match="read-only"):
        assert_read_only(_tape(f'Type "{command}"\n'))


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "curl https://example.com/x.sh",
    "bash -c 'echo hi'",
])
def test_non_poindexter_programs_rejected(command: str) -> None:
    with pytest.raises(DemoTapeError, match="not allowed"):
        assert_read_only(_tape(f'Type "{command}"\n'))


def test_chained_mutation_is_caught() -> None:
    """A mutating command hidden behind && must not slip past the guard."""
    with pytest.raises(DemoTapeError):
        assert_read_only(
            _tape('Type "poindexter posts list && poindexter tasks approve abc"\n')
        )


# ---------------------------------------------------------------------------
# preamble + composition
# ---------------------------------------------------------------------------


def test_render_preamble_substitutes_settings() -> None:
    class _Cfg:
        def get(self, key, default=None):
            return {"demo_clip_theme_background": "#123456"}.get(key, default)

    rendered = render_preamble(_Cfg())
    assert "#123456" in rendered
    assert "@@bg@@" not in rendered


def test_render_preamble_font_size_override() -> None:
    assert "Set FontSize 26" in render_preamble(None, font_size=26)
    assert "Set FontSize 34" in render_preamble(None)


def test_render_preamble_ignores_placeholders_in_comments() -> None:
    """The preamble documents its own ``@@name@@`` syntax in a comment.

    A guard that tripped on that would make the file impossible to explain —
    this regressed once already during development.
    """
    rendered = render_preamble(None)
    assert "@@name@@" in rendered  # the comment survives verbatim
    executable = [l for l in rendered.splitlines() if not l.lstrip().startswith("#")]
    assert not re.findall(r"@@\w+@@", "\n".join(executable))


def test_render_preamble_raises_on_unresolved_placeholder(tmp_path: Path) -> None:
    (tmp_path / "demo_tapes").mkdir()
    (tmp_path / "demo_tapes" / "_preamble.tape").write_text("Set FontSize @@nope@@\n")
    with pytest.raises(DemoTapeError, match="unresolved placeholder"):
        render_preamble(None, tmp_path)


def test_compose_injects_quoted_output_first() -> None:
    """Output is injected, never tape-declared — a tape cannot escape out_dir."""
    composed = compose_tape(_tape('Type "poindexter doctor"\n'), "/out/x.mp4", "Set Width 1920")
    assert composed.startswith('Output "/out/x.mp4"')
    assert composed.count("Output ") == 1


# ---------------------------------------------------------------------------
# The shipped catalog
# ---------------------------------------------------------------------------


def test_catalog_loads_and_is_read_only() -> None:
    """Every shipped tape parses and passes the safety guard."""
    tapes = load_tapes()
    assert tapes, "expected demo tapes to ship with the repo"
    assert all(t.description for t in tapes)


def test_catalog_slugs_unique() -> None:
    slugs = [t.slug for t in load_tapes()]
    assert len(slugs) == len(set(slugs))


def test_every_tape_waits_for_output() -> None:
    """A tape without ``Wait`` cannot fail when its command breaks.

    The Wait regex is the bake-time health check: it is what makes a renamed
    flag or an empty table fail the bake instead of shipping a clip of a
    blank terminal into a published video.
    """
    for tape in load_tapes():
        assert "Wait" in tape.body, f"{tape.slug} has no Wait directive"


def test_preamble_is_not_loaded_as_a_tape() -> None:
    assert "_preamble" not in {t.slug for t in load_tapes()}
    assert (tapes_dir() / "_preamble.tape").is_file()

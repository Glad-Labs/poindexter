"""Bake VHS tapes into demo clips for the video shot list.

Real footage of the ``poindexter`` CLI, recorded with `VHS
<https://github.com/charmbracelet/vhs>`_, as an alternative to synthetic
stills and generic stock video (Glad-Labs/poindexter#937).

Why tapes are repo files, not DB rows
-------------------------------------

Every other declarative surface in this codebase (``external_taps``,
``publishing_adapters``, ``qa_gates``) is a DB table an operator edits at
runtime. Demo tapes deliberately are **not**: a tape is a shell script, so a
DB-editable tape would turn any ``app_settings`` write — or any SQL-injection
reaching that table — into arbitrary code execution inside the worker.
Tapes ship in ``demo_tapes/`` and change through code review. The DB's role
(a later PR) is the bake *ledger*: where the clip landed, how long it is,
whether the last bake failed.

Why bake ahead of time
----------------------

Recording at video-render time would make every render depend on a live
database, a warm API, and a working terminal emulator — three new ways for a
render to fail late, after the expensive LLM work is already paid for. Baking
on a schedule instead means a broken demo fails in the bake (alertable, out
of band) while the renderer only ever consumes a finished MP4.

The director never authors commands
-----------------------------------

An LLM asked to write CLI invocations will produce plausible flags that do
not exist, and the clip would faithfully record the error message. The
director picks a ``slug`` from this catalog; the commands are written by
hand and reviewed. :func:`assert_read_only` is the second line of defence.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Where tapes live, relative to the package root (``src/cofounder_agent``).
TAPES_DIRNAME = "demo_tapes"
PREAMBLE_NAME = "_preamble.tape"

# Metadata header lines: ``# <key>: <value>`` before the first VHS command.
_META_RE = re.compile(
    r"^#\s*(title|description|category|slug|font_size)\s*:\s*(.+?)\s*$", re.I,
)

# Terminal columns available at a given font size, for the default 1920px
# width and 60px padding. Measured, not derived: JetBrains Mono's advance
# width is ~0.6em, so usable_px / (0.6 * font_size).
#
# This matters because a table wider than the terminal WRAPS MID-WORD, which
# both looks broken on camera and silently breaks a ``Wait`` regex — the
# `publishers list` header wrapped as "FAI" / "L", so /FAIL/ could never
# match even though the word was on screen. Wide-table tapes set
# ``# font_size:`` to fit.
COLUMNS_AT_FONT_SIZE: dict[int, int] = {22: 136, 26: 115, 30: 100, 34: 88, 38: 78}

# ``Type "..."`` lines are the only place a tape can run anything.
_TYPE_RE = re.compile(r'^\s*Type\s+"(.*)"\s*$')

# Read-only leaf verbs. An ALLOWLIST, not a denylist: a new mutating
# subcommand added upstream is then refused by default rather than silently
# permitted because nobody remembered to ban it.
# Only LEAF verbs belong here. A group name (e.g. ``niche``, which has its
# own mutating subcommands) would greenlight everything under it, because the
# positional check below only inspects the first two words.
READ_ONLY_VERBS: frozenset[str] = frozenset({
    "list", "show", "get", "status", "search", "pending", "budget",
    "operational", "doctor", "logs", "list-paused", "qa", "--help", "-h",
})

# Commands a tape may invoke at all. ``poindexter`` is the point; the shell
# builtins are what the preamble itself needs.
_ALLOWED_PROGRAMS: frozenset[str] = frozenset({"poindexter", "clear", "cd", "export", "alias"})


class DemoTapeError(RuntimeError):
    """A tape is malformed, unsafe, or failed to bake."""


@dataclass(frozen=True)
class DemoTape:
    """One catalog entry: a recordable CLI demonstration.

    ``description`` is what the video director reads when choosing a shot, so
    it should say what the clip *shows a viewer*, not what the command does.
    """

    slug: str
    title: str
    description: str
    category: str
    body: str
    path: Path
    # Per-tape font override for wide output. ``None`` uses the setting.
    font_size: int | None = None

    @property
    def commands(self) -> list[str]:
        """The shell command strings this tape types on camera."""
        return [m.group(1) for line in self.body.splitlines() if (m := _TYPE_RE.match(line))]


@dataclass
class BakeResult:
    """Outcome of baking one tape."""

    slug: str
    success: bool
    clip_path: str | None = None
    duration_s: float = 0.0
    error: str = ""


def parse_tape(path: Path) -> DemoTape:
    """Parse a ``.tape`` file into a :class:`DemoTape`.

    Fails loud on a missing title/description rather than substituting a
    placeholder — an unlabelled tape is invisible to the director, which
    would look like the catalog silently shrinking (``feedback_no_silent_defaults``).
    """
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        if m := _META_RE.match(stripped):
            meta[m.group(1).lower()] = m.group(2)

    slug = meta.get("slug") or path.stem
    missing = [k for k in ("title", "description") if not meta.get(k)]
    if missing:
        raise DemoTapeError(
            f"{path.name}: missing required header field(s) {missing}; "
            f"add '# title: ...' / '# description: ...' above the first command",
        )

    raw_font = meta.get("font_size")
    try:
        font_size = int(raw_font) if raw_font else None
    except ValueError as exc:
        raise DemoTapeError(f"{path.name}: font_size {raw_font!r} is not an integer") from exc

    return DemoTape(
        slug=slug,
        title=meta["title"],
        description=meta["description"],
        category=meta.get("category", "general"),
        body=text,
        path=path,
        font_size=font_size,
    )


def assert_read_only(tape: DemoTape) -> None:
    """Reject a tape that would mutate production state.

    Demos run against the live database against real data, so a tape that
    typed ``poindexter tasks approve`` would not merely record badly — it
    would publish something. Checked at load time so a bad tape fails in CI
    and in ``demos list``, not first on camera.
    """
    for command in tape.commands:
        # A tape may chain setup with && (the preamble does). Check each part.
        for part in re.split(r"&&|\|\||;|\|", command):
            tokens = part.split()
            if not tokens:
                continue
            program = tokens[0]
            if program not in _ALLOWED_PROGRAMS:
                raise DemoTapeError(
                    f"{tape.slug}: command {program!r} is not allowed in a demo tape "
                    f"(allowed: {sorted(_ALLOWED_PROGRAMS)})",
                )
            if program != "poindexter":
                continue
            # The verb is POSITIONAL — one of the first two bare words after
            # ``poindexter`` (`logs`, or `posts list`). Everything after that
            # is an argument.
            #
            # Scanning every token for "does any of them look read-only"
            # is unsafe: `poindexter settings set logs value` would pass on
            # the strength of an ARGUMENT named `logs`. Only the command path
            # decides whether a command mutates.
            verbs = [t for t in tokens[1:] if not t.startswith("-")]
            if not any(v in READ_ONLY_VERBS for v in verbs[:2]):
                raise DemoTapeError(
                    f"{tape.slug}: {part.strip()!r} has no read-only verb "
                    f"(allowed: {sorted(READ_ONLY_VERBS)}). Demo tapes run against "
                    f"live production data and must never mutate it.",
                )


def assert_commands_exist(tape: DemoTape) -> None:
    """Reject a tape naming a CLI command that does not exist.

    ``assert_read_only`` checks that a verb is *allowlisted*, not that it is
    *real* — so a typo, or a tape landing in the same PR as the command it
    demonstrates, passes validation and fails at bake time with a clip of a
    click usage error. That is the same plausible-but-nonexistent failure the
    read-only guard exists to prevent, one level up.

    Walks the real click tree, so it cannot drift from the CLI. Skipped
    silently if the CLI is not importable (the baker may run in a trimmed
    environment); a missing check is better than a false rejection.
    """
    try:
        from poindexter.cli.app import main as cli_root
    except Exception:  # silent-ok: absent CLI means we simply cannot check
        return

    import click

    for command in tape.commands:
        for part in re.split(r"&&|\|\||;|\|", command):
            tokens = [t for t in part.split() if not t.startswith("-")]
            if not tokens or tokens[0] != "poindexter":
                continue
            node: Any = cli_root
            for token in tokens[1:]:
                if not isinstance(node, click.Group):
                    break  # reached a leaf; the rest are arguments
                child = node.get_command(None, token)  # type: ignore[arg-type]
                if child is None:
                    raise DemoTapeError(
                        f"{tape.slug}: no such CLI command {' '.join(tokens[:2])!r} "
                        f"(token {token!r}). If the command ships in this same "
                        f"change, the tape can only bake once it is deployed.",
                    )
                node = child


def tapes_dir(package_root: Path | None = None) -> Path:
    """Absolute path to ``demo_tapes/``."""
    root = package_root or Path(__file__).resolve().parent.parent
    return root / TAPES_DIRNAME


def load_tapes(package_root: Path | None = None) -> list[DemoTape]:
    """Load and validate every tape in the catalog, sorted by slug.

    Underscore-prefixed files are fragments (``_preamble.tape``), not tapes.
    """
    directory = tapes_dir(package_root)
    if not directory.is_dir():
        return []
    out: list[DemoTape] = []
    for path in sorted(directory.glob("*.tape")):
        if path.name.startswith("_"):
            continue
        tape = parse_tape(path)
        assert_read_only(tape)
        assert_commands_exist(tape)
        out.append(tape)
    return out


def _setting(site_config, key: str, default: str) -> str:
    if site_config is None:
        return default
    value = site_config.get(key, default)
    return default if value in (None, "") else str(value)


def render_preamble(
    site_config,
    package_root: Path | None = None,
    *,
    font_size: int | None = None,
) -> str:
    """Render ``_preamble.tape``'s ``@@name@@`` placeholders from settings.

    ``font_size`` overrides the setting for one tape — wide tables need a
    smaller face to avoid wrapping (see :data:`COLUMNS_AT_FONT_SIZE`).
    """
    template = (tapes_dir(package_root) / PREAMBLE_NAME).read_text(encoding="utf-8")
    values = {
        "font_family": _setting(site_config, "demo_clip_font_family", "JetBrains Mono"),
        "font_size": (
            str(font_size)
            if font_size
            else _setting(site_config, "demo_clip_font_size", "34")
        ),
        "width": _setting(site_config, "demo_clip_width", "1920"),
        "height": _setting(site_config, "demo_clip_height", "1080"),
        "padding": _setting(site_config, "demo_clip_padding", "60"),
        "framerate": _setting(site_config, "demo_clip_framerate", "30"),
        "typing_speed": _setting(site_config, "demo_clip_typing_speed", "45ms"),
        "bg": _setting(site_config, "demo_clip_theme_background", "#18181B"),
        "fg": _setting(site_config, "demo_clip_theme_foreground", "#E4E4E7"),
        "accent": _setting(site_config, "demo_clip_theme_accent", "#22D3EE"),
        "accent_bright": _setting(site_config, "demo_clip_theme_accent_bright", "#67E8F9"),
        "active": _setting(site_config, "demo_clip_theme_active", "#FBBF24"),
        "attention": _setting(site_config, "demo_clip_theme_attention", "#C084FC"),
        "failure": _setting(site_config, "demo_clip_theme_failure", "#F87171"),
        "dim": _setting(site_config, "demo_clip_theme_dim", "#8B8B93"),
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"@@{key}@@", value)
    # Scan only executable lines: the preamble's own comments document the
    # ``@@name@@`` syntax, and a guard that trips on its own documentation
    # would make the file impossible to explain.
    executable = "\n".join(
        line for line in rendered.splitlines() if not line.lstrip().startswith("#")
    )
    leftover = re.findall(r"@@(\w+)@@", executable)
    if leftover:
        # A renamed placeholder would otherwise reach vhs verbatim and fail
        # with a parser error that says nothing about the real cause.
        raise DemoTapeError(
            f"{PREAMBLE_NAME}: unresolved placeholder(s) {sorted(set(leftover))}",
        )
    return rendered


def compose_tape(tape: DemoTape, output_path: str, preamble: str) -> str:
    """Build the full VHS script: injected Output + preamble + tape body.

    ``Output`` is injected here rather than declared per-tape so a tape
    cannot write outside the bake directory. VHS requires the path quoted
    when absolute.
    """
    return f'Output "{output_path}"\n\n{preamble}\n{tape.body}\n'


async def probe_duration_s(path: str, *, ffprobe: str = "ffprobe") -> float:
    """Return a media file's duration in seconds, or ``0.0`` if unreadable."""
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


async def bake_tape(
    tape: DemoTape,
    *,
    out_dir: Path,
    site_config=None,
    package_root: Path | None = None,
    vhs_binary: str = "vhs",
    timeout_s: int = 300,
) -> BakeResult:
    """Record one tape to ``out_dir/<slug>.mp4``.

    A bake that produces no file, or a zero-duration file, is a failure: VHS
    exits 0 in some parse-error paths, so the artefact — not the exit code —
    is the evidence that it worked.
    """
    if shutil.which(vhs_binary) is None:
        return BakeResult(tape.slug, False, error=f"{vhs_binary!r} not on PATH")

    out_dir.mkdir(parents=True, exist_ok=True)
    clip_path = out_dir / f"{tape.slug}.mp4"
    script_path = out_dir / f"{tape.slug}.composed.tape"
    script_path.write_text(
        compose_tape(
            tape,
            str(clip_path),
            render_preamble(site_config, package_root, font_size=tape.font_size),
        ),
        encoding="utf-8",
    )

    proc = await asyncio.create_subprocess_exec(
        vhs_binary, str(script_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return BakeResult(tape.slug, False, error=f"vhs timed out after {timeout_s}s")

    tail = (stdout or b"").decode(errors="replace").strip().splitlines()[-4:]
    if not clip_path.is_file():
        return BakeResult(tape.slug, False, error=f"no output file; vhs said: {' | '.join(tail)}")

    duration = await probe_duration_s(str(clip_path))
    if duration <= 0:
        return BakeResult(
            tape.slug, False, clip_path=str(clip_path),
            error=f"clip has zero duration; vhs said: {' | '.join(tail)}",
        )

    logger.info("[DEMO_CLIP] baked %s -> %s (%.2fs)", tape.slug, clip_path, duration)
    return BakeResult(tape.slug, True, clip_path=str(clip_path), duration_s=duration)

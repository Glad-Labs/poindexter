#!/usr/bin/env python3
"""Every unit-test file must be run by a CI pytest step that can fail.

``.github/workflows/unit-tests.yml`` runs pytest once PER DIRECTORY
(``$PYTEST tests/unit/services/``, ``$PYTEST tests/unit/cli/``, ...) rather
than once over the tree, so singleton state cannot leak between directories.
The cost is that the list is kept by hand, and a directory nobody adds to it
never runs in CI. Its tests cannot fail a PR: a real regression there is red
on every local run and green on every CI run.

That already happened twice. poindexter#641 found five directories (modules,
poindexter, prompts, schemas, scripts) missing from the list in May 2026 and
added them by hand. On 2026-09-25 three more were missing:
``tests/unit/seo`` (10 files), ``tests/unit/infrastructure`` (3) and
``tests/unit/console`` (1). A real failure was hiding in them: #4011 added a
rule to ``pipeline_architect._validate_spec`` that also rejected the seeded
``seo_refresh`` graph, and ``test_seo_refresh_spec`` was the only test that
noticed. This lint turns the hand-kept list into a checked one.

The rule: every ``test_*.py`` under ``src/cofounder_agent/tests/unit`` (the
``python_files`` pattern in ``pyproject.toml``) must sit at or under a path
that a pytest command in the workflow names, resolved against that step's
``working-directory``. A file counts as NOT run when the only command naming
it excludes it (``--ignore`` / ``--ignore-glob`` / ``--deselect``), when the
only step that runs it is ``continue-on-error`` or ``if: false`` (on the step
or on its job), or when that step's script discards pytest's exit status.

**Discarded exit status.** A step fails only when its script exits non-zero,
so ``$PYTEST tests/unit/x/ || true`` runs every test and gates none of them.
The script is judged the way GitHub runs it on Linux: no ``shell:`` is
``bash -e {0}`` (errexit, NO pipefail), and ``shell: bash`` is
``bash --noprofile --norc -eo pipefail {0}``. Discarded: anything after
``||`` that does not exit non-zero (``|| true``, ``|| :``, ``|| echo``), a
pipe without pipefail (``| tee log`` under the default shell), ``&``,
``set +e`` with commands after it, a ``&&`` list with commands after it, and
a ``code=$?`` capture that no later ``exit`` re-raises. Kept: the capture the
modules step uses (``|| code=$?`` ... ``exit "${code:-0}"``), ``|| exit 1``,
``|| { echo "::error::..."; exit 1; }``, and pytest as the script's last
command. Any other ``shell:`` (``sh`` included) is not read, so its pytest
commands gate nothing. The tests hold this model to a real bash.

**Stdlib only, no PyYAML.** This lint also runs in ``lint-main``, which
installs no dependencies. The workflow is read by a small scanner that
understands only what this rule needs: each job's steps and their ``run``,
``working-directory``, ``shell``, ``continue-on-error`` and ``if`` keys,
including block scalars (``run: |``), plus the job's ``if`` and
``continue-on-error`` and ``defaults.run.shell`` on the job and workflow. A
shape it does not understand fails closed: a pytest command it cannot read
covers nothing, so the files are reported rather than passed. The same holds
for the shell reading above: a way to hand pytest's status on that it does
not recognise (``|| (echo; exit 1)``, ``if ! $PYTEST``) is reported, not
trusted.

Run: ``python scripts/ci/unit_test_dirs_lint.py``
Exit 0 = every test file runs in a gating step. Exit 1 = some do not, or the
lint found no test files or no pytest commands to check.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
import shlex
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import ScanFloorError, require_dir, require_scanned  # noqa: E402

LINT = "unit_test_dirs_lint"
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_REL = Path(".github") / "workflows" / "unit-tests.yml"
TESTS_ROOT_REL = Path("src") / "cofounder_agent" / "tests" / "unit"
# ``python_files`` in src/cofounder_agent/pyproject.toml.
TEST_FILE_GLOB = "test_*.py"

# The working directory every pytest step uses today, and the one a fix
# snippet should use.
_SNIPPET_WORKDIR = "src/cofounder_agent"


# ---------------------------------------------------------------------------
# Workflow scanner (a YAML subset: block mappings, block sequences, scalars)
# ---------------------------------------------------------------------------


class Step(NamedTuple):
    job: str
    name: str
    line: int  # 1-based line of the step's ``-``
    run: str
    working_directory: str  # "" = the repository root
    gating: bool  # False when continue-on-error or if: false (step or job)
    # The step's ``shell``, else its job's then the workflow's
    # ``defaults.run.shell``; "" = unspecified (GitHub's ``bash -e {0}``).
    shell: str = ""


_KEY_RE = re.compile(r"(?P<key>[A-Za-z0-9_-]+):(?:[ \t]+(?P<value>.*?))?[ \t]*$")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][0-9+-]*\s*(?:#.*)?$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_skippable(line: str) -> bool:
    """A blank line, or a whole-line comment outside a block scalar."""
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


_DQ_ESCAPE_RE = re.compile(r"\\(x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)")
_DQ_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
    "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
    "N": "\x85", "_": "\xa0", "L": " ", "P": " ",
}


def _unescape_double_quoted(inner: str) -> str:
    """YAML double-quoted escapes (``\\n``, ``\\"``, ``\\U0001F511`` ...)."""

    def _one(match: re.Match[str]) -> str:
        esc = match.group(1)
        if len(esc) > 1:  # \xNN / \uNNNN / \UNNNNNNNN
            return chr(int(esc[1:], 16))
        return _DQ_ESCAPES.get(esc, "\\" + esc)

    return _DQ_ESCAPE_RE.sub(_one, inner)


def _plain_value(raw: str) -> str:
    """One scalar without its quotes or trailing comment."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = 1
        while end < len(raw):
            if raw[end] == quote:
                if quote == "'" and raw[end + 1 : end + 2] == "'":
                    end += 2
                    continue
                break
            if quote == '"' and raw[end] == "\\":
                end += 1
            end += 1
        inner = raw[1:end]
        return inner.replace("''", "'") if quote == "'" else _unescape_double_quoted(inner)
    return re.split(r"\s#", raw, maxsplit=1)[0].strip()


def _block_scalar(indicator: str, content: list[str]) -> str:
    """The text of a ``|`` or ``>`` block scalar, its indentation removed."""
    first = next((ln for ln in content if ln.strip()), None)
    if first is None:
        return ""
    base = _indent(first)
    text = [ln[base:] if _indent(ln) >= base else ln.lstrip() for ln in content]
    text = [ln if ln.strip() else "" for ln in text]
    if indicator.startswith(">"):
        folded: list[str] = []
        para: list[str] = []
        for ln in text:
            if ln:
                para.append(ln.strip())
                continue
            if para:
                folded.append(" ".join(para))
                para = []
            folded.append("")
        if para:
            folded.append(" ".join(para))
        text = folded
    return "\n".join(text).strip("\n")


def _iter_keys(lines: list[str], start: int, end: int, col: int) -> Iterator[tuple[str, str | None, int, int]]:
    """Yield ``(key, value, first, last)`` for each ``key:`` at column ``col``.

    ``value`` is the scalar (a block scalar's joined text), or ``None`` for a
    nested mapping or sequence, which spans ``lines[first:last]``.
    """
    i = start
    while i < end:
        line = lines[i]
        match = None if _is_skippable(line) or _indent(line) != col else _KEY_RE.match(line[col:])
        if match is None:
            i += 1
            continue
        key, raw = match.group("key"), (match.group("value") or "")
        j = i + 1
        if _BLOCK_SCALAR_RE.match(raw):
            # Content is every line indented past the key, comment-looking
            # lines included (they are shell, not YAML, in there).
            while j < end and (not lines[j].strip() or _indent(lines[j]) > col):
                j += 1
            yield key, _block_scalar(raw, lines[i + 1 : j]), i + 1, j
        elif raw and not raw.startswith("#"):
            value = raw
            while j < end and not _is_skippable(lines[j]) and _indent(lines[j]) > col:
                value += " " + lines[j].strip()  # multi-line plain scalar
                j += 1
            yield key, _plain_value(value), i + 1, j
        else:
            # A nested block. A sequence may sit at the key's own column.
            while j < end and (
                _is_skippable(lines[j])
                or _indent(lines[j]) > col
                or (_indent(lines[j]) == col and lines[j][col:].startswith("-"))
            ):
                j += 1
            yield key, None, i + 1, j
        i = j


def _first_real(lines: list[str], start: int, end: int) -> int | None:
    return next((i for i in range(start, end) if not _is_skippable(lines[i])), None)


def _nested_scalar(lines: list[str], block: tuple[str | None, int, int] | None, *path: str) -> str | None:
    """The scalar at ``path`` inside a nested mapping block, as
    ``(value, first, last)`` from :func:`_iter_keys` (``defaults`` → ``run``
    → ``shell``). ``None`` when any key on the way is absent."""
    for key in path:
        if block is None or block[0] is not None:
            return None
        _value, first, last = block
        line = _first_real(lines, first, last)
        if line is None:
            return None
        block = next(
            ((v, a, b) for k, v, a, b in _iter_keys(lines, first, last, _indent(lines[line])) if k == key), None
        )
    return None if block is None else block[0]


def _is_off(value: str | None) -> bool:
    """``false`` / ``${{ false }}`` — a literal the workflow can never flip."""
    if value is None:
        return False
    bare = value.strip()
    if bare.startswith("${{") and bare.endswith("}}"):
        bare = bare[3:-2].strip()
    return bare.lower() == "false"


def _is_on(value: str | None) -> bool:
    """Anything but absent or ``false`` may be true, so it may not gate."""
    return value is not None and value.strip().lower() != "false"


def scan_steps(text: str) -> list[Step]:
    """Every step of every job, with the keys this lint reads."""
    lines = text.splitlines()
    top = next((i for i, ln in enumerate(lines) if re.match(r"^jobs:\s*(?:#.*)?$", ln)), None)
    if top is None:
        return []
    end = next(
        (i for i in range(top + 1, len(lines)) if not _is_skippable(lines[i]) and _indent(lines[i]) == 0),
        len(lines),
    )
    first = _first_real(lines, top + 1, end)
    if first is None:
        return []
    top_keys = {k: (v, a, b) for k, v, a, b in _iter_keys(lines, 0, len(lines), 0)}
    workflow_shell = _nested_scalar(lines, top_keys.get("defaults"), "run", "shell") or ""

    steps: list[Step] = []
    for job, _value, j_first, j_last in _iter_keys(lines, first, end, _indent(lines[first])):
        key_line = _first_real(lines, j_first, j_last)
        if key_line is None:
            continue
        job_keys = {k: (v, a, b) for k, v, a, b in _iter_keys(lines, j_first, j_last, _indent(lines[key_line]))}
        # A job that is `if: false` never runs, so none of its steps can fail.
        job_gating = not _is_on(job_keys.get("continue-on-error", (None, 0, 0))[0]) and not _is_off(
            job_keys.get("if", (None, 0, 0))[0]
        )
        job_shell = _nested_scalar(lines, job_keys.get("defaults"), "run", "shell") or workflow_shell
        if "steps" not in job_keys:
            continue
        _, s_first, s_last = job_keys["steps"]
        item = _first_real(lines, s_first, s_last)
        if item is None or not lines[item].lstrip().startswith("-"):
            continue
        item_indent = _indent(lines[item])
        starts = [
            i
            for i in range(item, s_last)
            if not _is_skippable(lines[i]) and _indent(lines[i]) == item_indent and lines[i].lstrip().startswith("-")
        ]
        for n, s in enumerate(starts):
            stop = starts[n + 1] if n + 1 < len(starts) else s_last
            after_dash = lines[s][item_indent + 1 :]
            if after_dash.strip():
                key_col = item_indent + 1 + _indent(after_dash)
                body = [" " * key_col + after_dash.strip(), *lines[s + 1 : stop]]
            else:  # a bare `-` with the mapping on the lines below
                nxt = _first_real(lines, s + 1, stop)
                if nxt is None:
                    continue
                key_col = _indent(lines[nxt])
                body = lines[s + 1 : stop]
            keys = {k: v for k, v, _a, _b in _iter_keys(body, 0, len(body), key_col)}
            run = keys.get("run") or ""
            name = keys.get("name") or keys.get("uses") or (run.splitlines() or [""])[0]
            steps.append(
                Step(
                    job=job,
                    name=name.strip(),
                    line=s + 1,
                    run=run,
                    working_directory=(keys.get("working-directory") or "").strip(),
                    gating=job_gating and not _is_on(keys.get("continue-on-error")) and not _is_off(keys.get("if")),
                    shell=(keys.get("shell") or job_shell).strip(),  # nosec B604 - a workflow shell name, not subprocess
                )
            )
    return steps


# ---------------------------------------------------------------------------
# Shell: find the pytest commands in a step's `run`
# ---------------------------------------------------------------------------

# `<<WORD` / `<<'WORD'` / `<<-WORD`, but not a `<<<` here-string.
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"(?:^|/)python(?:3(?:\.\d+)?)?$")


def shell_commands(script: str) -> list[list[str]]:
    """Split a ``run`` script into simple commands, as unexpanded tokens.

    Joins backslash continuations, drops heredoc bodies and comments, and
    splits on ``;`` ``&&`` ``||`` ``|`` ``&``. ``$PYTEST`` stays ``$PYTEST``.
    Parentheses are NOT split on: a pytest inside ``$( ... )`` has its exit
    status swallowed, so it is left unrecognised rather than counted.
    """
    return [command for command, _op in shell_commands_with_ops(script)]


def shell_commands_with_ops(script: str) -> list[tuple[list[str], str]]:
    """:func:`shell_commands`, each paired with the operator that ends it:
    ``;`` ``&&`` ``||`` ``|`` ``&``, or ``""`` at the end of a line.

    An ``&`` inside a redirection (``2>&1``, ``>&2``, ``&>log``) stays in its
    word rather than reading as ``&``; ``|&`` is a pipe.
    """
    commands: list[tuple[list[str], str]] = []
    for line in _logical_lines(script):
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        try:
            words = list(lexer)
        except ValueError:  # unbalanced quotes: unreadable, so it covers nothing
            continue
        current: list[str] = []
        glue = ""  # "fd": append the next word (2>& + 1); "&": prefix it (& + >log)
        for k, word in enumerate(words):
            if word and set(word) <= set(";&|"):
                if word == "&" and current and current[-1][-1:] in ("<", ">"):
                    current[-1] += word
                    glue = "fd"
                    continue
                if word == "&" and words[k + 1 : k + 2] and words[k + 1].startswith(">"):
                    glue = "&"
                    continue
                if current:
                    commands.append((current, "|" if word == "|&" else word))
                current, glue = [], ""
            elif glue == "fd" and current:
                current[-1] += word
                glue = ""
            else:
                current.append("&" + word if glue == "&" else word)
                glue = ""
        if current:
            commands.append((current, ""))
    return commands


def _logical_lines(script: str) -> list[str]:
    """``script``'s lines with backslash continuations joined and heredoc
    bodies dropped."""
    logical: list[str] = []
    pending = ""
    heredoc_end: str | None = None
    for raw in script.splitlines():
        if heredoc_end is not None:
            if raw.strip() == heredoc_end:
                heredoc_end = None
            continue
        line = pending + raw
        if line.endswith("\\"):
            pending = line[:-1] + " "
            continue
        pending = ""
        match = _HEREDOC_RE.search(line)
        if match:
            heredoc_end = match.group(2)
        logical.append(line)
    if pending:
        logical.append(pending)
    return logical


def pytest_args(command: list[str]) -> list[str] | None:
    """The arguments of a pytest invocation, or ``None`` if it is not one."""
    tokens = list(command)
    while tokens and _ASSIGNMENT_RE.match(tokens[0]):
        tokens.pop(0)
    if tokens[:2] in (["poetry", "run"], ["uv", "run"]):
        tokens = tokens[2:]
    if not tokens:
        return None
    head = tokens[0]
    # $PYTEST is this workflow's own seam (`python -m pytest`, or
    # `--collect-only` on the public mirror); see its env block.
    if head in ("$PYTEST", "${PYTEST}", "pytest") or head.endswith("/pytest"):
        return tokens[1:]
    if _PYTHON_RE.search(head) and tokens[1:3] == ["-m", "pytest"]:
        return tokens[3:]
    return None


# ---------------------------------------------------------------------------
# Shell: does a failing pytest fail its step?
# ---------------------------------------------------------------------------

# (errexit, pipefail) for each `shell:` this lint reads, as GitHub runs it on
# Linux. Unspecified is NOT `bash`: it runs `bash -e {0}`, with no pipefail.
# `sh` (dash on Ubuntu) is deliberately unread: no pipefail, and `cmd &> f`
# backgrounds `cmd` there, so it would need a second model.
_SHELLS: dict[str, tuple[bool, bool]] = {
    "": (True, False),  # bash -e {0}
    "bash": (True, True),  # bash --noprofile --norc -eo pipefail {0}
}
_CAPTURE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\$\?$")


def _after_set(command: list[str], errexit: bool, pipefail: bool) -> tuple[bool, bool]:
    """Apply one ``set`` builtin: ``-e`` / ``+e`` / ``-euo pipefail`` / ``+o errexit``."""
    args = iter(command[1:])
    for arg in args:
        if arg in ("-", "--") or arg[:1] not in ("-", "+"):
            break
        on = arg[0] == "-"
        if "e" in arg[1:]:
            errexit = on
        if "o" in arg[1:]:
            name = next(args, "")
            if name == "errexit":
                errexit = on
            elif name == "pipefail":
                pipefail = on
    return errexit, pipefail


def _exits_with(command: list[str], name: str) -> bool:
    """``exit "$name"`` / ``exit "${name:-0}"``."""
    ref = re.compile(r"\$\{?" + re.escape(name) + r"(?![A-Za-z0-9_])")
    return command[:1] == ["exit"] and any(ref.search(arg) for arg in command[1:])


def _exits_non_zero(command: list[str]) -> bool:
    """``exit 1`` (any literal status that is not 0 mod 256)."""
    return command[:1] == ["exit"] and command[1:2] != [] and command[1].isdigit() and int(command[1]) % 256 != 0


def _hands_status_on(command: list[str], later: list[tuple[list[str], str]]) -> bool:
    """Does ``command``, run straight after a failing pytest, fail the script?

    ``exit`` / ``exit $?`` / ``exit <non-zero>`` / ``false`` do, and so does a
    ``code=$?`` capture that a later ``exit "$code"`` re-raises (the modules
    step's exit-5 handling), and a ``{ ...; }`` group that opens with one of
    those or exits non-zero (``|| { echo "::error::..."; exit 1; }``).
    Anything else is taken to discard the status.
    """
    if not command:
        return False
    if command[0] == "{":
        inner = [command[1:]]
        for later_command, _op in later:
            if later_command[-1:] == ["}"]:
                inner.append(later_command[:-1])
                break
            inner.append(later_command)
        else:
            return False  # an unterminated group is not read
        inner = [c for c in inner if c]
        # Only the group's first command still sees pytest's `$?`.
        return bool(inner) and (_hands_status_on(inner[0], later) or any(_exits_non_zero(c) for c in inner[1:]))
    if command == ["false"]:
        return True
    if command[0] == "exit":
        return command[1:] in ([], ["$?"]) or _exits_non_zero(command)
    capture = _CAPTURE_RE.match(command[0]) if len(command) == 1 else None
    return capture is not None and any(_exits_with(c, capture.group(1)) for c, _op in later)


def discarded_status(commands: list[tuple[list[str], str]], index: int, shell: str = "") -> str:
    """Why a failing ``commands[index]`` cannot fail its step, or ``""`` when
    it does.

    Not a shell. It follows the status through what comes after the command
    (a pipe, a ``&&`` list, ``||``, ``&``, the next line) under the step's
    errexit / pipefail, and accepts only the shapes that are known to hand the
    status on (see :func:`_hands_status_on`); anything else is reported.
    """
    if shell not in _SHELLS:
        return f"`shell: {shell}` is not a shell this lint reads"
    errexit, pipefail = _SHELLS[shell]
    for command, _op in commands[:index]:
        if command[:1] == ["set"]:
            errexit, pipefail = _after_set(command, errexit, pipefail)

    end = index
    if commands[end][1] == "|" and not pipefail:
        return "piped without pipefail, so the step sees the pipe's last command"
    while commands[end][1] == "|" and end + 1 < len(commands):
        end += 1
    in_list = False  # a failure inside `a && b` skips to the list's end
    while commands[end][1] == "&&" and end + 1 < len(commands):
        in_list = True
        end += 1
        while commands[end][1] == "|" and end + 1 < len(commands):
            end += 1
    op, rest = commands[end][1], commands[end + 1 :]

    if op == "&":
        return "run in the background (&), so nothing waits for its status"
    if op == "||":
        if rest and _hands_status_on(rest[0][0], rest[1:]):
            return ""
        handler = " ".join(rest[0][0]) + (" ..." if rest[0][1] else "") if rest else ""
        return f"`|| {handler}` runs in its place, and the step sees that command's status"
    if not rest or (errexit and not in_list):
        return ""  # the script's last command, or errexit stops the script there
    if _hands_status_on(rest[0][0], rest[1:]):
        return ""
    if in_list:
        return "a failing `&&` list does not stop the script, and a later command sets the step's status"
    return "`set +e` lets the script run past it, and a later command sets the step's status"


# pytest options whose value is the NEXT argument. An option missing from this
# list is read as a flag, which only matters if its value happens to name a
# path under tests/unit (it would read as a target) -- which is why every
# option that takes a path is listed.
_VALUE_OPTIONS = frozenset(
    {
        "-c", "-k", "-m", "-n", "-o", "-p", "-r", "-W",
        "--basetemp", "--capture", "--color", "--confcutdir", "--cov",
        "--cov-config", "--cov-fail-under", "--cov-report", "--deselect",
        "--dist", "--durations", "--ignore", "--ignore-glob", "--import-mode",
        "--junit-xml", "--junitxml", "--log-cli-level", "--log-file",
        "--log-level", "--max-worker-restart", "--maxfail", "--numprocesses",
        "--override-ini", "--pythonwarnings", "--reruns", "--rootdir",
        "--tb", "--timeout",
    }
)
_GLOB_CHARS = frozenset("*?[")


class Invocation(NamedTuple):
    step: Step
    targets: tuple[Path, ...]  # absolute; each at or under the tests root
    ignores: tuple[Path, ...]  # absolute
    ignore_globs: tuple[str, ...]  # absolute patterns, as pytest applies them
    discarded: str = ""  # why the step never sees pytest's exit status ("" = it does)

    @property
    def gating(self) -> bool:
        """A failure here fails the job."""
        return self.step.gating and not self.discarded


def _norm(path: Path) -> Path:
    return Path(os.path.normpath(path))


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve(token: str, cwd: Path) -> list[Path]:
    if _GLOB_CHARS & set(token):
        return [_norm(cwd / hit) for hit in sorted(glob.glob(token, root_dir=cwd))]
    return [_norm(cwd / token)]


def find_invocations(text: str, repo_root: Path, tests_root: Path) -> list[Invocation]:
    """Every pytest command in the workflow that names a path under ``tests_root``."""
    found: list[Invocation] = []
    for step in scan_steps(text):
        cwd = _norm(repo_root / step.working_directory)
        commands = shell_commands_with_ops(step.run)
        for index, (command, _op) in enumerate(commands):
            args = pytest_args(command)
            if args is None:
                continue
            targets: list[Path] = []
            ignores: list[Path] = []
            ignore_globs: list[str] = []
            i = 0
            while i < len(args):
                token = args[i]
                i += 1
                if token.startswith("-"):
                    option, has_value, value = token.partition("=")
                    if option in _VALUE_OPTIONS and not has_value:
                        value = args[i] if i < len(args) else ""
                        i += 1
                    if option == "--ignore-glob" and value:
                        ignore_globs.append(str(_norm(cwd / value)))
                    elif option in ("--ignore", "--deselect") and value and "::" not in value:
                        ignores.append(_norm(cwd / value))
                    continue
                # An unexpanded variable ($COV) or one test's node id: neither
                # names files that run in full.
                if "$" in token or "::" in token:
                    continue
                targets.extend(p for p in _resolve(token, cwd) if _within(p, tests_root))
            if targets:
                found.append(
                    Invocation(
                        step, tuple(targets), tuple(ignores), tuple(ignore_globs),
                        discarded_status(commands, index, step.shell),
                    )
                )
    return found


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def discover_test_files(tests_root: Path) -> list[Path]:
    return sorted(
        p for p in tests_root.rglob(TEST_FILE_GLOB) if p.is_file() and "__pycache__" not in p.parts
    )


def _excluded(path: Path, inv: Invocation) -> bool:
    return any(_within(path, ig) for ig in inv.ignores) or any(
        fnmatch.fnmatch(str(path), pattern) for pattern in inv.ignore_globs
    )


def _group(path: Path, tests_root: Path) -> str:
    """``tests/unit/<dir>/`` for a file in a subdirectory, the root-level glob otherwise."""
    rel = path.relative_to(tests_root)
    base = tests_root.relative_to(tests_root.parents[1]).as_posix()  # tests/unit
    if len(rel.parts) == 1:
        return f"{base}/{TEST_FILE_GLOB}"
    return f"{base}/{rel.parts[0]}/"


def uncovered(test_files: list[Path], invocations: list[Invocation], tests_root: Path) -> dict[tuple[str, str], list[Path]]:
    """``{(group, reason): [files]}`` for every test file no gating step runs."""
    problems: dict[tuple[str, str], list[Path]] = {}
    for path in test_files:
        naming = [inv for inv in invocations if any(_within(path, t) for t in inv.targets)]
        if any(inv.gating and not _excluded(path, inv) for inv in naming):
            continue
        if not naming:
            reason = "no pytest step names it"
        elif any(inv.gating for inv in naming):
            names = sorted({inv.step.name for inv in naming if inv.gating})
            reason = f"excluded (--ignore/--deselect) by: {', '.join(names)}"
        elif any(inv.step.gating for inv in naming):
            names = sorted({f"{inv.step.name}: {inv.discarded}" for inv in naming if inv.step.gating})
            reason = f"run, but the step discards pytest's exit status ({'; '.join(names)})"
        else:
            names = sorted({inv.step.name for inv in naming})
            reason = (
                "only run by a step that cannot fail the job "
                f"(continue-on-error or if: false, on the step or its job): {', '.join(names)}"
            )
        problems.setdefault((_group(path, tests_root), reason), []).append(path)
    return problems


def _snippet(group: str) -> str:
    label = group.rstrip("/").rsplit("/", 1)[-1]
    return (
        f"      - name: Unit tests — {label}\n"
        "        if: steps.changes.outputs.needs_tests == 'true'\n"
        f"        working-directory: {_SNIPPET_WORKDIR}\n"
        f"        run: $PYTEST {group} -q --tb=short -p no:cacheprovider $COV"
    )


def main(repo_root: Path = REPO_ROOT) -> int:
    workflow = repo_root / WORKFLOW_REL
    tests_root = repo_root / TESTS_ROOT_REL

    require_dir(tests_root, lint=LINT)
    if not workflow.is_file():
        raise ScanFloorError(
            f"{LINT}: workflow not found: {workflow}\n"
            "  The CI pytest steps live there. If the workflow moved, update "
            "WORKFLOW_REL; do not let this lint report clean without reading it."
        )
    test_files = discover_test_files(tests_root)
    require_scanned(len(test_files), lint=LINT, what=f"{TEST_FILE_GLOB} files", roots=(tests_root,))
    invocations = find_invocations(workflow.read_text(encoding="utf-8"), repo_root, tests_root)
    require_scanned(
        len(invocations), lint=LINT, what=f"pytest commands naming a path under {TESTS_ROOT_REL.as_posix()}",
        roots=(workflow,),
    )

    problems = uncovered(test_files, invocations, tests_root)
    if not problems:
        steps = len({inv.step.line for inv in invocations if inv.gating})
        print(
            f"[unit-test-dirs] OK — {len(test_files)} test file(s) under {TESTS_ROOT_REL.as_posix()}, "
            f"every one run by one of {steps} gating pytest step(s) in {WORKFLOW_REL.as_posix()}."
        )
        return 0

    total = sum(len(files) for files in problems.values())
    print(
        f"[unit-test-dirs] FAIL — {total} test file(s) under {TESTS_ROOT_REL.as_posix()} run in no CI step that "
        f"can fail, so they gate nothing ({WORKFLOW_REL.as_posix()}):\n"
    )
    width = max(len(group) for group, _ in problems)
    for (group, reason), files in sorted(problems.items()):
        print(f"  {group:<{width}}  {len(files):>3} file(s) — {reason}")
    print(
        "\n  CI runs pytest once per directory, so a directory missing from that\n"
        "  list never runs: a regression in it is red locally and green in CI.\n"
        "  tests/unit/seo hid a real failure this way (2026-09-25)."
    )
    missing = sorted(group for group, reason in problems if reason == "no pytest step names it")
    if missing:
        print("\n  Fix: add a step to the test-backend job for each, e.g.\n")
        print("\n\n".join(_snippet(group) for group in missing))
    if any("discards pytest's exit status" in reason for _group_, reason in problems):
        print(
            "\n  Fix for a discarded status: let pytest's exit status end the step. Drop the\n"
            "  `|| ...` / `set +e` / `&`, give a pipe pipefail (`set -o pipefail` or\n"
            "  `shell: bash`), or capture it and re-raise it as the modules step does:\n"
            '  `$PYTEST ... || code=$?` then `exit "${code:-0}"`.'
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())

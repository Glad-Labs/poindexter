#!/usr/bin/env python3
"""CI lint: no tracked file carries a literal database password.

Thirty tracked files used to fall back to a hardcoded local DSN,
``postgresql://poindexter:<the old default password>@localhost:5433/...``, or
to that password as a shell ``${LOCAL_POSTGRES_PASSWORD:-<literal>}`` default.
26 of them shipped in the public mirror. When this was found (2026-10-03) the
operator's database still used that literal as its real password, so the
public repo held a working credential. The password has since been rotated,
and every fallback now resolves through
``poindexter.brain.bootstrap.require_database_url()``, which fails loud.

A literal fallback is wrong even after the rotation. ``poindexter setup``
generates ``local_postgres_password`` per install, so a hardcoded password can
only fail to authenticate (a confusing error, far from its cause) or succeed
against a database somebody set up with it. This lint keeps a new one out.

What it flags
-------------
In every tracked text file (lockfiles excepted):

* ``postgres://user:<password>@`` / ``postgresql://...`` / ``postgresql+driver://...``
  whose password is not a placeholder;
* ``${<NAME containing PASS>:-<default>}`` (or ``-``) shell defaults whose
  default is not a placeholder.

What it allows
--------------
Anywhere: an empty password; template syntax (``<password>``, ``${VAR}``,
``{password}``, ``%(password)s``); redaction marks (``***``, ``[Filtered]``,
``[REDACTED]``, ``...``); the generic words below; values of one or two
characters; and ``postgres``, the password every CI workflow gives its
throwaway ``postgres`` service container.

In test files only (``tests/`` dirs, ``test_*``, ``conftest.py``, JS
``*.test.*`` / ``*.spec.*``): ``test`` plus a small vocabulary of obvious
fixture fakes, and anything starting with ``fake``. Scrubber and URL-quoting
tests need a password-shaped value to assert on, and these are recognisably
not credentials. Prefer ``FAKEPW`` in a new test over growing the list.

The report masks every password it prints: CI logs on the public mirror are
public, so a lint that echoed the match would republish the leak it caught.

A DSN split across two adjacent string literals (``"postgresql://u:pw"`` on
one line, ``"@host/db"`` on the next) is read as one: two of the original
thirty sites were written that way. A password mentioned on its own, outside
any DSN or shell default, is out of reach of a pattern lint.

Run:
    python scripts/ci/dsn_credential_literal_lint.py

Exit 0 = clean, exit 1 = at least one literal (or nothing was scanned).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

LINT = "dsn_credential_literal_lint"
REPO_ROOT = Path(__file__).resolve().parents[2]

# user:password@ inside a postgres URL. The password stops at whitespace, a
# quote or a backtick, so a DSN inside a string literal or Markdown code span
# is read correctly.
DSN_RE = re.compile(
    r"\bpostgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://"
    r"(?P<user>[^:/@\s\"'`<>]+):(?P<password>[^@\s\"'`]*)@"
)
# The first half of a DSN split across adjacent string literals: the literal
# closes right after the password, and the next line opens with "@...".
DSN_SPLIT_HEAD_RE = re.compile(
    r"\bpostgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://"
    r"(?P<user>[^:/@\s\"'`<>]+):(?P<password>[^@\s\"'`]*)[\"']\s*$"
)
DSN_SPLIT_TAIL_RE = re.compile(r"""^\s*[rRfFbBuU]{0,2}["']@""")
# ${LOCAL_POSTGRES_PASSWORD:-<literal>} and ${PGPASS-<literal>}. `:?` (required)
# and `:+` are not defaults and are fine.
SHELL_DEFAULT_RE = re.compile(
    r"\$\{(?P<var>[A-Za-z0-9_]*PASS[A-Za-z0-9_]*):?-(?P<password>[^}]*)\}"
)

# Template / redaction shapes that are never a credential.
_PLACEHOLDER_SHAPES = (
    re.compile(r"<[^<>]*>"),  # <password>, <pw>
    re.compile(r"\$\{?[A-Za-z_][^}]*\}?"),  # ${POSTGRES_PASSWORD}, $PGPASS, nested ${A:-${B
    re.compile(r"\{\{?[^{}]*\}?\}"),  # {password} (f-string), {{ secret }}
    re.compile(r"%\([A-Za-z_]+\)s"),  # %(password)s
    re.compile(r"\*+"),  # ***
    re.compile(r"\[[A-Za-z ]+\]"),  # [Filtered], [REDACTED]
    re.compile(r"\.{2,}|…"),  # ..., …
)
_PLACEHOLDER_WORDS = frozenset(
    {
        "password",
        "pass",
        "passwd",
        "pwd",
        "your-password",
        "your_password",
        "yourpassword",
        "changeme",
        "redacted",
        # The official image's convention, and what every CI workflow sets on
        # its throwaway service container (POSTGRES_PASSWORD: postgres).
        "postgres",
    }
)
_TEST_FIXTURE_WORDS = frozenset(
    {
        "test",
        "secret",
        "s3cr3t",
        "supersecret",
        "topsecret",
        "hunter2",
        "swordfish",
        "nothing",
        "else",
        "old",
        "stub",
        "boot",
        "env",
        # URL-quoting tests: p@ss, p@ss/word and s3cr@t, percent-encoded.
        "p%40ss",
        "p%40ss%2fword",
        "s3cr%40t",
    }
)
_LOCKFILES = frozenset({"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock"})


def is_test_path(path: str) -> bool:
    """True for test sources, which may use the fixture vocabulary."""
    p = PurePosixPath(path)
    name = p.name
    return (
        "tests" in p.parts
        or "__tests__" in p.parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or ".test." in name
        or ".spec." in name
    )


def is_placeholder(password: str, *, in_test: bool) -> bool:
    """True when ``password`` is a stand-in rather than a credential."""
    if len(password) <= 2:
        return True
    if any(shape.fullmatch(password) for shape in _PLACEHOLDER_SHAPES):
        return True
    lowered = password.lower()
    if lowered in _PLACEHOLDER_WORDS:
        return True
    if in_test and (lowered in _TEST_FIXTURE_WORDS or lowered.startswith("fake")):
        return True
    return False


def _mask(password: str) -> str:
    return f"<{len(password)} chars>"


def scan_text(path: str, text: str) -> list[str]:
    """Return one report line per literal credential found in ``text``."""
    in_test = is_test_path(path)
    problems: list[str] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        dsns = list(DSN_RE.finditer(line))
        split = DSN_SPLIT_HEAD_RE.search(line)
        if split and lineno < len(lines) and DSN_SPLIT_TAIL_RE.match(lines[lineno]):
            dsns.append(split)
        for m in dsns:
            pw = m.group("password")
            if not is_placeholder(pw, in_test=in_test):
                problems.append(
                    f"{path}:{lineno}: DSN for user {m.group('user')!r} carries a "
                    f"literal password {_mask(pw)}"
                )
        for m in SHELL_DEFAULT_RE.finditer(line):
            pw = m.group("password")
            if not is_placeholder(pw, in_test=in_test):
                problems.append(
                    f"{path}:{lineno}: ${{{m.group('var')}:-...}} defaults to a "
                    f"literal password {_mask(pw)}"
                )
    return problems


def tracked_files(root: Path) -> list[str]:
    """Repo-relative paths git tracks under ``root``."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            f"{LINT}: could not list tracked files with git in {root}: {exc}. "
            "This lint scans what git tracks; run it inside the checkout."
        ) from exc
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def scan(root: Path) -> tuple[list[str], int]:
    """Return (problems, text files scanned)."""
    problems: list[str] = []
    scanned = 0
    for rel in tracked_files(root):
        name = PurePosixPath(rel).name
        if name.endswith(".lock") or name in _LOCKFILES:
            continue
        try:
            raw = (root / rel).read_bytes()
        except OSError:
            continue  # deleted in the working tree, or a dangling symlink
        if b"\0" in raw[:8192]:
            continue  # binary
        scanned += 1
        problems.extend(scan_text(rel, raw.decode("utf-8", "replace")))
    return problems, scanned


def main() -> int:
    problems, scanned = scan(REPO_ROOT)
    require_scanned(scanned, lint=LINT, what="tracked text files", roots=(REPO_ROOT,))
    if problems:
        print(f"{LINT}: {len(problems)} literal database credential(s) in tracked files:\n")
        for line in problems:
            print(f"  {line}")
        print(
            "\nResolve the DSN at runtime instead: "
            "poindexter.brain.bootstrap.require_database_url() (env, then "
            "bootstrap.toml, else notify + exit), or require the env var and "
            "exit with a clear message. In docs, write <password>. In a test, "
            "read the DSN from env and skip when unset, or use a FAKEPW value."
        )
        return 1
    print(f"{LINT}: clean ({scanned} tracked text files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

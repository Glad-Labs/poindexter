#!/usr/bin/env python3
"""CI lint: no NEW mypy errors (the mypy ratchet).

Until this lint, no workflow ran mypy. ``npm run type:check`` existed and
nothing enforced it, so type errors landed silently. A full run over the
backend reported 74 errors in 33 files (checked 907 source files) when this
was written. That is too many to fix before gating, and gating on zero would
mean never gating at all.

So mypy joins the bandit and semgrep ratchets (``bandit_lint.py``,
``semgrep_lint.py``) under the same doctrine: the existing errors are
grandfathered in ``mypy_baseline.json``, CI blocks only a **net-new** error,
nothing files an issue, and the baseline only ever shrinks.

Baseline shape: per file, per error CODE, never per line
---------------------------------------------------------
``{relpath: {code: count}}``. No line numbers, so an edit above an old error
doesn't churn the baseline. Keyed per code, so a new ``[arg-type]`` can't
ride in behind a fixed ``[assignment]`` in the same file. A per-file total
would wave that swap straight through.

Escape hatch: mypy's own ``# type: ignore[<code>]``
------------------------------------------------------
No custom marker. A false positive gets suppressed at its line, scoped to its
one code, with a comment saying why: ``# type: ignore[arg-type]  # <why>``.
Never use a bare ``# type: ignore``, which hides every code on the line,
including the next real one.

The baseline only shrinks
-------------------------
``--update-baseline`` only lowers counts and drops entries. It refuses when
the tree has an error the baseline doesn't allow, because re-baselining a new
error is exactly what this gate exists to stop. ``--allow-growth`` is for the
cases where growth is legitimate: a file move (its errors reappear under a new
key), or a mypy or dependency bump that changes what mypy reports. Say which
one in the commit message.

The baseline ships in the public mirror, so it must never name a file the
mirror strips. The shrink-only update can't add one. With ``--allow-growth``,
a new error in a mirror-stripped file must be fixed or ``type: ignore``d,
never baselined.

Run it inside the backend's environment
---------------------------------------
mypy only type-checks faithfully when the backend's third-party packages are
installed. Measured on the same tree, the full backend env (``poetry install
--no-root --extras "pipeline qa rag"``) reports the 74 errors, identical line
for line to a developer venv. mypy alone reports 203. 130 of those are ``Class
cannot subclass "BaseModel" (has type "Any")`` (every missing library becomes
``Any``), and it misses 2 of the 74 real errors, both at a library boundary.
So CI installs the lock into an isolated venv and runs this lint with that
venv's interpreter. mypy runs as ``sys.executable -m mypy``, so the
interpreter you launch this with is the environment it checks.

A baseline is only meaningful against the mypy and the dependency set that
produced it. A dependabot bump of a typed library can change the result.
That's a real signal: the PR that changes the types is the one that shows it.

A check that did not complete has not passed
--------------------------------------------
- mypy exit 2 (a blocking error such as a syntax error or a duplicate module,
  a crash, or a usage error) is a failure, whatever else was printed.
- A non-zero exit with no parseable error lines is a failure.
- Every ``error:`` line must parse, with an error code. The parsed count must
  equal mypy's own ``Found N errors in M files`` summary. A change in mypy's
  output format then fails loud instead of undercounting, which would let new
  errors through silently.
- A missing source root, or fewer than ``MIN_FILES_CHECKED`` files checked,
  fails via ``lib_scan_floor``.

Config: the repo-root ``pyproject.toml`` ``[tool.mypy]`` is the only one,
the same config ``npm run type:check`` uses. The package's
``src/cofounder_agent/pyproject.toml`` carries none, so a bare ``mypy`` run
from that directory finds the root config too.

Run (from the repo root, with the backend env's interpreter):
    python scripts/ci/mypy_lint.py                                  # check
    python scripts/ci/mypy_lint.py --update-baseline                # lock in fixes
    python scripts/ci/mypy_lint.py --update-baseline --allow-growth # file move / tool bump

Exit 0 = no new errors. Exit 1 = a new error, a refused update, or a scan that
did not complete.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import subprocess  # nosec B404 - invoking our own pinned mypy, no user input
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import ScanFloorError, require_dir, require_scanned  # noqa: E402

LINT = "mypy_lint"
REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "mypy_baseline.json"
# The one mypy config. `npm run type:check` passes the same file.
CONFIG_PATH = REPO_ROOT / "pyproject.toml"
# mypy runs from here, as `type:check` does. The root config's `mypy_path`
# names this directory, so the package base and the working directory agree
# and no module is found under two names.
SOURCE_ROOT = REPO_ROOT / "src" / "cofounder_agent"

# mypy checked 907 source files when this was written. A collapse below about
# half means the config's `files` or `exclude` changed, or the source tree
# moved, and "no new errors" out of that run means nothing.
MIN_FILES_CHECKED = 450

# A cold run takes ~30 s. The timeout only stops a hung mypy from silently
# eating the whole CI job.
MYPY_TIMEOUT_SECONDS = 900

# Pin every flag that changes the shape of an output line, so a config edit
# (`pretty = true`, `show_column_numbers = true`, ...) can't break the parser.
# Each overrides the config file.
OUTPUT_SHAPE_FLAGS = (
    "--show-error-codes",
    "--no-pretty",
    "--no-color-output",
    "--error-summary",
    "--hide-error-context",
    "--hide-column-numbers",
    "--hide-error-end",
    "--hide-error-code-links",
    "--hide-absolute-path",
)

# `path:line: severity: message  [code]`. The column groups are optional, in
# case a column flag ever reaches the output anyway.
_LOCATED_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::\d+){0,3}: (?P<severity>error|note|warning): (?P<rest>.*)$"
)
# The error code is the trailing `[code]`, separated by two spaces.
_ERROR_CODE = re.compile(r"^(?P<message>.*?)  \[(?P<code>[a-z][a-z0-9-]*)\]$")
_FOUND_SUMMARY = re.compile(
    r"^Found (?P<errors>\d+) errors? in (?P<files>\d+) files? "
    r"\(checked (?P<checked>\d+) source files?\)$"
)
_SUCCESS_SUMMARY = re.compile(r"^Success: no issues found in (?P<checked>\d+) source files?$")
_SEVERITIES = ("error", "note", "warning")


def _is_crash_line(line: str) -> bool:
    # mypy reports a crash as `path:line: error: INTERNAL ERROR -- ...` and a
    # traceback. Matched per line, so an ordinary message that merely quotes
    # those words can't trip it.
    return ": error: INTERNAL ERROR" in line or line.startswith("Traceback (most recent call last)")


def _first_severity(line: str) -> str | None:
    """The severity a line opens with, for a line with no ``path:line:``."""
    if line.startswith(tuple(f"{sev}: " for sev in _SEVERITIES)):
        return line.split(":", 1)[0]
    hits = [(line.find(f": {sev}: "), sev) for sev in _SEVERITIES]
    hits = [(pos, sev) for pos, sev in hits if pos >= 0]
    return min(hits)[1] if hits else None


class MypyRunError(RuntimeError):
    """mypy did not produce a result this lint can trust."""


@dataclass(frozen=True)
class MypyError:
    path: str  # repo-relative, forward slashes
    line: int
    code: str
    message: str
    raw: str


@dataclass(frozen=True)
class MypyReport:
    errors: tuple[MypyError, ...]
    checked: int  # source files mypy says it checked
    summary: str


def normalize_path(raw: str, *, cwd: Path = SOURCE_ROOT, repo_root: Path = REPO_ROOT) -> str:
    """Repo-relative and forward-slashed, so baseline keys are portable.

    mypy prints paths relative to its working directory (``SOURCE_ROOT``).
    The baseline records them from the repo root, like the bandit and semgrep
    baselines, so a key names one file however the lint was launched.
    """
    path = Path(raw.replace("\\", "/"))
    if not path.is_absolute():
        path = cwd / path
    path = Path(os.path.normpath(path))
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _excerpt(text: str, limit: int = 1500) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "...\n" + text[-limit:]


def parse_report(
    returncode: int,
    stdout: str,
    stderr: str = "",
    *,
    cwd: Path = SOURCE_ROOT,
    repo_root: Path = REPO_ROOT,
) -> MypyReport:
    """Turn one mypy run into errors plus the file count, or raise.

    Raises ``MypyRunError`` whenever the run can't be trusted: a crash, a
    blocking error, an unparseable ``error:`` line, a missing summary, or a
    parsed count that disagrees with mypy's own summary.
    """
    out = stdout or ""
    err = stderr or ""
    context = (
        f"\n  rc={returncode}\n  stdout (tail):\n{_excerpt(out)}\n  stderr (tail):\n{_excerpt(err)}"
    )

    if any(_is_crash_line(line) for line in (out + "\n" + err).splitlines()):
        raise MypyRunError("mypy crashed, so the check did not complete." + context)
    if returncode not in (0, 1):
        raise MypyRunError(
            f"mypy exited {returncode}. Exit 2 means a blocking error (a syntax "
            "error, a module found twice, an unreadable file), a crash, or a "
            "usage error. mypy stopped before type-checking the tree." + context
        )

    errors: list[MypyError] = []
    unparsed: list[str] = []
    summary: re.Match[str] | None = None
    for line in out.splitlines():
        found = _FOUND_SUMMARY.match(line) or _SUCCESS_SUMMARY.match(line)
        if found:
            summary = found
            continue
        located = _LOCATED_LINE.match(line)
        if located is None:
            if _first_severity(line) == "error":
                unparsed.append(line)  # e.g. `path: error: ...` with no line number
            continue
        if located["severity"] != "error":
            continue
        coded = _ERROR_CODE.match(located["rest"])
        if coded is None:
            unparsed.append(line)
            continue
        errors.append(
            MypyError(
                path=normalize_path(located["path"], cwd=cwd, repo_root=repo_root),
                line=int(located["line"]),
                code=coded["code"],
                message=coded["message"],
                raw=line,
            )
        )

    if unparsed:
        shown = "\n".join(f"    {line}" for line in unparsed[:10])
        raise MypyRunError(
            f"{len(unparsed)} `error:` line(s) have no `path:line:` location or "
            "no `[code]`, so they can't be keyed into the baseline. Either mypy's "
            "output format changed or an error code is hidden "
            f"(`hide_error_codes`):\n{shown}" + context
        )
    if summary is None:
        raise MypyRunError(
            "mypy printed no summary line (`Found N errors ...` / `Success: ...`), "
            "so it did not complete a check. Is mypy installed in "
            f"{sys.executable}?" + context
        )

    checked = int(summary["checked"])
    if returncode == 0:
        if errors or summary.re is not _SUCCESS_SUMMARY:
            raise MypyRunError("mypy exited 0 but did not report a clean run." + context)
        return MypyReport(errors=(), checked=checked, summary=summary.group(0))

    if summary.re is not _FOUND_SUMMARY or not errors:
        raise MypyRunError(
            "mypy exited 1 but printed no error this lint could parse. A "
            "non-zero exit with nothing parseable is a failure, never a clean "
            "result." + context
        )
    expected_errors = int(summary["errors"])
    expected_files = int(summary["files"])
    files_seen = len({e.path for e in errors})
    if len(errors) != expected_errors or files_seen != expected_files:
        raise MypyRunError(
            f"parsed {len(errors)} error(s) in {files_seen} file(s), but mypy's "
            f"own summary says {expected_errors} in {expected_files}. The parser "
            "and mypy disagree, so the counts can't be trusted." + context
        )
    return MypyReport(errors=tuple(errors), checked=checked, summary=summary.group(0))


def run_mypy(
    *,
    python: str = sys.executable,
    cwd: Path = SOURCE_ROOT,
    config: Path = CONFIG_PATH,
) -> subprocess.CompletedProcess[str]:
    """One full mypy run, configured by ``config``'s ``files`` / ``exclude``."""
    try:
        return subprocess.run(  # nosec B603 - fixed argv, no shell, no user input
            [python, "-m", "mypy", f"--config-file={config}", *OUTPUT_SHAPE_FLAGS],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=MYPY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MypyRunError(f"mypy did not finish within {MYPY_TIMEOUT_SECONDS} s.") from exc


def scan(
    *,
    python: str = sys.executable,
    source_root: Path = SOURCE_ROOT,
    config: Path = CONFIG_PATH,
    repo_root: Path = REPO_ROOT,
) -> MypyReport:
    """Run mypy over the tree and return a validated report, or fail loud."""
    require_dir(source_root, lint=LINT)
    if not config.is_file():
        raise MypyRunError(f"mypy config not found: {config}")
    if python == sys.executable and importlib.util.find_spec("mypy") is None:
        raise MypyRunError(
            f"mypy is not installed in {sys.executable}. Run this lint with the "
            "backend env's interpreter (the one `poetry install` populated), not "
            "a bare Python. Without the backend's packages mypy's result doesn't "
            "match the baseline."
        )
    proc = run_mypy(python=python, cwd=source_root, config=config)
    return parse_report(
        proc.returncode, proc.stdout, proc.stderr, cwd=source_root, repo_root=repo_root
    )


def check_floor(
    report: MypyReport,
    *,
    min_files: int = MIN_FILES_CHECKED,
    source_root: Path = SOURCE_ROOT,
) -> int:
    """Fail when mypy checked nothing, or far fewer files than the tree holds."""
    require_scanned(report.checked, lint=LINT, what="source files", roots=(source_root,))
    if report.checked < min_files:
        raise ScanFloorError(
            f"{LINT}: mypy checked only {report.checked} source file(s), expected "
            f"at least {min_files}. Refusing to report clean off a run that "
            "barely happened. Check the `files` / `exclude` settings in the root "
            f"pyproject.toml [tool.mypy], or whether the tree moved.\n"
            f"  looked in:\n    {source_root}"
        )
    return report.checked


def counts_from_errors(errors: Iterable[MypyError]) -> dict[str, dict[str, int]]:
    """``relpath -> {code: count}``, sorted for a stable baseline diff."""
    counts: dict[str, dict[str, int]] = {}
    for error in errors:
        per_file = counts.setdefault(error.path, {})
        per_file[error.code] = per_file.get(error.code, 0) + 1
    return {rel: dict(sorted(codes.items())) for rel, codes in sorted(counts.items())}


def find_regressions(
    counts: Mapping[str, Mapping[str, int]],
    baseline: Mapping[str, Mapping[str, int]],
) -> list[tuple[str, str, int, int]]:
    """``(relpath, code, found, allowed)`` for each code over its baseline."""
    out: list[tuple[str, str, int, int]] = []
    for rel, codes in sorted(counts.items()):
        allowed_codes = baseline.get(rel, {})
        for code, found in sorted(codes.items()):
            allowed = allowed_codes.get(code, 0)
            if found > allowed:
                out.append((rel, code, found, allowed))
    return out


def find_stale(
    counts: Mapping[str, Mapping[str, int]],
    baseline: Mapping[str, Mapping[str, int]],
) -> list[tuple[str, str, int, int]]:
    """``(relpath, code, found, allowed)`` for each entry the tree has shrunk below.

    Clean, not a failure: the ratchet only shrinks. Listed so the win gets
    locked in with ``--update-baseline`` before the slack lets a new error of
    the same code in the same file ride in.
    """
    out: list[tuple[str, str, int, int]] = []
    for rel, codes in sorted(baseline.items()):
        found_codes = counts.get(rel, {})
        for code, allowed in sorted(codes.items()):
            found = found_codes.get(code, 0)
            if found < allowed:
                out.append((rel, code, found, allowed))
    return out


def validate_baseline(data: object, *, source: str = "baseline") -> dict[str, dict[str, int]]:
    """The baseline's shape, checked, so a hand edit fails loud and clear."""
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected an object of relpath -> {{code: count}}")
    for rel, codes in data.items():
        if not isinstance(rel, str) or "\\" in rel or Path(rel).is_absolute():
            raise ValueError(f"{source}: {rel!r} must be a repo-relative forward-slash path")
        if not isinstance(codes, dict) or not codes:
            raise ValueError(f"{source}: {rel} must map to a non-empty {{code: count}} object")
        for code, count in codes.items():
            if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", code):
                raise ValueError(f"{source}: {rel}: {code!r} is not a mypy error code")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise ValueError(f"{source}: {rel}: [{code}] must be a positive integer")
    return data


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, int]]:
    # A missing baseline allows ZERO errors, so it fails loud instead of
    # permitting everything.
    if not path.exists():
        return {}
    return validate_baseline(json.loads(path.read_text(encoding="utf-8")), source=path.name)


def write_baseline(counts: Mapping[str, Mapping[str, int]], path: Path = BASELINE_PATH) -> None:
    path.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _total(counts: Mapping[str, Mapping[str, int]]) -> int:
    return sum(sum(codes.values()) for codes in counts.values())


def _mypy_version() -> str:
    # Printed with every result: a baseline is only comparable to a run of the
    # same mypy, so a local-vs-CI disagreement should be diagnosable from the
    # two logs alone.
    try:
        return importlib.metadata.version("mypy")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _print_regressions(
    regressions: list[tuple[str, str, int, int]], errors: Iterable[MypyError]
) -> None:
    by_key: dict[tuple[str, str], list[MypyError]] = {}
    for error in errors:
        by_key.setdefault((error.path, error.code), []).append(error)
    print("NEW MYPY ERROR (not in baseline):")
    for rel, code, found, allowed in regressions:
        print(f"  {rel}: [{code}] = {found} error(s), baseline allows {allowed}")
        # The baseline counts, it doesn't record lines, so name every current
        # error of this code in this file. At least one of them is new.
        for error in by_key.get((rel, code), []):
            print(f"      {rel}:{error.line}: {error.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mypy ratchet lint.")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite mypy_baseline.json from the current tree (shrink-only).",
    )
    parser.add_argument(
        "--allow-growth",
        action="store_true",
        help=(
            "With --update-baseline: also record NEW errors. Only for a file move "
            "or a mypy/dependency bump; say which in the commit message."
        ),
    )
    args = parser.parse_args(argv)
    if args.allow_growth and not args.update_baseline:
        parser.error("--allow-growth only applies with --update-baseline")

    try:
        report = scan()
    except MypyRunError as exc:
        print(f"{LINT}: FAILED, no trustworthy result. {exc}", file=sys.stderr)
        return 1
    checked = check_floor(report)

    counts = counts_from_errors(report.errors)
    baseline_path = BASELINE_PATH  # read at call time, so a test can point it elsewhere
    try:
        baseline = load_baseline(baseline_path)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError too
        print(f"{LINT}: FAILED, {baseline_path.name} is malformed: {exc}", file=sys.stderr)
        return 1
    regressions = find_regressions(counts, baseline)

    if args.update_baseline:
        if regressions and not args.allow_growth:
            _print_regressions(regressions, report.errors)
            print(
                "\n--update-baseline only shrinks the baseline, and these errors are "
                "not in it. Fix them, or suppress a false positive at its line with "
                "`# type: ignore[<code>]  # <why>`. If the growth is legitimate (a "
                "file move, a mypy or dependency bump), re-run with --allow-growth "
                "and say which in the commit message."
            )
            return 1
        write_baseline(counts, baseline_path)
        grew = sum(found - allowed for _, _, found, allowed in regressions)
        shrank = sum(allowed - found for _, _, found, allowed in find_stale(counts, baseline))
        print(
            f"{LINT}: baseline written: {_total(counts)} error(s) across "
            f"{len(counts)} file(s), {checked} source files checked, mypy "
            f"{_mypy_version()} (shrank by {shrank}, grew by {grew})."
        )
        return 0

    if regressions:
        _print_regressions(regressions, report.errors)
        print(
            "\nFix the new error. If it's a false positive, suppress it at its line "
            "with `# type: ignore[<code>]  # <why>`, scoped to the one code, never "
            "a bare `# type: ignore`. Don't re-baseline to make this pass: "
            "--update-baseline only shrinks."
        )
        return 1

    # FOUND and BASELINED are printed separately on purpose. A tree below its
    # baseline is clean but not yet locked in, and one merged number would hide
    # that.
    stale = find_stale(counts, baseline)
    tail = "" if not stale else "  <- re-baseline to lock the win in"
    print(
        f"{LINT}: clean, no new errors ({_total(counts)} found / {_total(baseline)} "
        f"baselined across {checked} source files checked, mypy {_mypy_version()}; "
        f"ratchet only shrinks).{tail}"
    )
    for rel, code, found, allowed in stale:
        print(f"  below baseline: {rel}: [{code}] {found} found, baseline allows {allowed}")
    if stale:
        print(f"  lock it in: python scripts/ci/{Path(__file__).name} --update-baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())

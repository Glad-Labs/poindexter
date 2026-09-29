"""Every place the code spells the R2 bucket's host agrees with the public site's.

The public site names its bucket once, as ``DEFAULT_STATIC_URL`` in
``web/public-site/lib/static-url.js``. The backend and the operator tools cannot
import that file, and some cannot read the ``storage_public_url`` setting
either: the DR re-import runs when the database is gone, and the operator overlay
is what seeds the setting in the first place. So a few places still spell the
host. Left unwatched those copies rot. A bucket move updates the site, and the
copy nobody remembers keeps pointing at the old bucket until the day it matters,
which for the DR tool is the worst day.

This derives its expectation instead of listing the copies. It reads the site's
default, finds every R2 public-bucket host (``pub-<32 hex>.r2.dev``) in code, and
requires each to be that host. A new copy is covered the moment it is written,
and a bucket move fails here until every copy has moved with it.

Where a copy can be removed it has been: the brain's R2 probe reads
``storage_public_url`` and carries no host, and
``test_r2_connectivity_probe.py`` holds it to that. What remains is a literal
because it has to be one.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.unit.conftest import find_repo_root

SITE_MODULE = "web/public-site/lib/static-url.js"
DR_TOOL = "scripts/dr-reimport-posts-from-r2.py"

_SITE_DEFAULT = re.compile(r"""DEFAULT_STATIC_URL\s*=\s*['"]([^'"]+)['"]""")
_DR_DEFAULT = re.compile(r"""_DEFAULT_R2_URL\s*=\s*['"]([^'"]+)['"]""")
_R2_PUBLIC_HOST = re.compile(r"pub-[0-9a-f]{32}\.r2\.dev")

_SCAN_ROOTS = ("src", "scripts", "web")
# Fixtures may name any host they like, and the rest is not source.
_SKIP_DIRS = frozenset(
    {"node_modules", ".next", "__pycache__", ".git", ".venv", "logs", "coverage", "tests", "__tests__"}
)
_CODE_SUFFIXES = frozenset(
    {".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".json", ".toml", ".yml", ".yaml", ".sql"}
)
_MAX_BYTES = 1_000_000  # vendored, minified bundles

# What the scan must have looked at, so a renamed root cannot leave it vacuous.
_MUST_SCAN = (
    SITE_MODULE,
    DR_TOOL,
    "src/cofounder_agent/poindexter/brain/health_probes.py",
)
_SCAN_FLOOR = 400  # the tree has ~1,270 such files


def _repo_root() -> Path:
    try:
        return find_repo_root(Path(__file__))
    except RuntimeError:
        pytest.skip("no repo root here (a container image carries only the backend)")


def _site_default(root: Path) -> str:
    site = root / SITE_MODULE
    if not site.is_file():
        pytest.skip(
            "the public site is not in this tree (the public mirror strips it), "
            "so there is no default to compare against"
        )
    found = _SITE_DEFAULT.search(site.read_text(encoding="utf-8"))
    assert found, f"no DEFAULT_STATIC_URL in {SITE_MODULE}; this guard's parser needs updating"
    return found.group(1)


def _scan(root: Path) -> tuple[list[str], list[tuple[str, int, str]]]:
    """(files looked at, every (file, line, host) spelling of an R2 public host)."""
    scanned: list[str] = []
    spellings: list[tuple[str, int, str]] = []
    for top in _SCAN_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix not in _CODE_SUFFIXES or path.stat().st_size > _MAX_BYTES:
                    continue
                rel = path.relative_to(root).as_posix()
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                scanned.append(rel)
                for number, line in enumerate(text.splitlines(), start=1):
                    for host in _R2_PUBLIC_HOST.findall(line):
                        spellings.append((rel, number, host))
    return scanned, spellings


@pytest.mark.unit
def test_every_spelling_of_the_bucket_host_is_the_sites():
    root = _repo_root()
    expected = urlsplit(_site_default(root)).hostname
    scanned, spellings = _scan(root)

    # A guard that scanned nothing has not passed.
    assert len(scanned) > _SCAN_FLOOR, (
        f"only {len(scanned)} files scanned under {_SCAN_ROOTS}; the roots moved or are empty"
    )
    missing = [path for path in _MUST_SCAN if path not in scanned]
    assert not missing, f"the scan never looked at {missing}"

    stale = [f"{rel}:{n}: {host}" for rel, n, host in spellings if host != expected]
    assert not stale, (
        f"the site's DEFAULT_STATIC_URL ({SITE_MODULE}) names {expected}, but these name another "
        "bucket. A bucket move has to update every one of them:\n  " + "\n  ".join(stale)
    )


@pytest.mark.unit
def test_the_dr_tools_default_is_the_sites_default():
    """The DR tool cannot read ``storage_public_url``: it runs when the database
    is gone. Its default is a literal, and a stale one would restore from the OLD
    bucket, so it must be exactly the site's, path and all."""
    root = _repo_root()
    site_default = _site_default(root)
    tool = root / DR_TOOL
    if not tool.is_file():
        pytest.skip(f"{DR_TOOL} is not in this tree")
    found = _DR_DEFAULT.search(tool.read_text(encoding="utf-8"))
    assert found, f"no _DEFAULT_R2_URL in {DR_TOOL}; this guard's parser needs updating"
    assert found.group(1) == site_default, (
        f"{DR_TOOL} would restore from {found.group(1)}, but the site reads {site_default}"
    )


# The scanner is the thing that keeps the two tests above honest, so it gets a
# positive control of its own on a synthetic tree.
_CURRENT = "pub-" + "a" * 32 + ".r2.dev"
_OLD = "pub-" + "b" * 32 + ".r2.dev"


@pytest.mark.unit
def test_the_scan_finds_a_stale_spelling_and_skips_fixtures(tmp_path):
    def write(rel: str, text: str) -> None:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write("src/pkg/tool.py", f'HOST = "https://{_OLD}"\n')
    write("scripts/run.sh", f"curl https://{_CURRENT}/static/x\n")
    write("web/site/app.js", "export const nothing = 1;\n")
    # Skipped: a test fixture, a dependency, and a file that is not code.
    write("src/pkg/tests/test_tool.py", f'HOST = "https://{_OLD}"\n')
    write("web/site/node_modules/dep/index.js", f'const h = "{_OLD}";\n')
    write("src/pkg/notes.md", f"{_OLD}\n")

    scanned, spellings = _scan(tmp_path)

    assert sorted(scanned) == ["scripts/run.sh", "src/pkg/tool.py", "web/site/app.js"]
    assert sorted(spellings) == [
        ("scripts/run.sh", 1, _CURRENT),
        ("src/pkg/tool.py", 1, _OLD),
    ]

"""Contract tests for ``scripts/ci/grafana_dashboard_host_lint.py``.

The lint exists because a dashboard link pinned to ``http://localhost:<port>``
resolves only for a browser on the Docker host -- dead from a phone -- and the
defect has already been fixed twice and regressed once (PR #3114 repointed
Mission Control and missed the identical links on Pipeline and QA Rails).

These tests pin BOTH halves of the contract: it must catch loopback, and it
must NOT catch legitimate external links. The second half matters as much as
the first -- a gate that cries wolf on github.com gets ignored and then
deleted, which is how the bandit sweep produced 91 false positives.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_LINT = _REPO_ROOT / "scripts" / "ci" / "grafana_dashboard_host_lint.py"
_LIB = _REPO_ROOT / "scripts" / "ci" / "lib_scan_floor.py"


def _run_in_tree(tmp_path: Path, dashboards: dict[str, dict]) -> subprocess.CompletedProcess:
    """Run the lint against a synthetic repo holding only these dashboards."""
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n", encoding="utf-8")
    ci = tmp_path / "scripts" / "ci"
    ci.mkdir(parents=True)
    ci.joinpath(_LINT.name).write_text(_LINT.read_text(encoding="utf-8"), encoding="utf-8")
    ci.joinpath(_LIB.name).write_text(_LIB.read_text(encoding="utf-8"), encoding="utf-8")
    d = tmp_path / "infrastructure" / "grafana" / "dashboards"
    d.mkdir(parents=True)
    for name, doc in dashboards.items():
        d.joinpath(name).write_text(json.dumps(doc), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(ci / _LINT.name)],
        cwd=tmp_path, capture_output=True, text=True,
    )


def _board(url: str) -> dict:
    return {
        "uid": "b", "title": "b", "panels": [],
        "links": [{"type": "link", "title": "svc", "url": url}],
    }


def test_the_real_dashboards_pass() -> None:
    """The shipped boards must be clean -- this lint lands at zero, not on a baseline."""
    r = subprocess.run(
        [sys.executable, str(_LINT)], cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"lint failed on the real dashboards:\n{r.stdout}\n{r.stderr}"
    assert "no hardcoded loopback link hosts" in r.stdout


@pytest.mark.parametrize("url", [
    "http://localhost:4200",
    "http://127.0.0.1:3010",
    "http://0.0.0.0:8080",
    "https://localhost:18443/some/path",
])
def test_loopback_link_is_rejected(tmp_path: Path, url: str) -> None:
    r = _run_in_tree(tmp_path, {"b.json": _board(url)})
    assert r.returncode == 1, f"expected failure for {url}, got:\n{r.stdout}"
    assert "__POINDEXTER_SERVICE_HOST__" in r.stdout, "message must name the remedy"


def test_grafana_own_port_gets_the_relative_url_remedy(tmp_path: Path) -> None:
    """:3000 is Grafana itself -- the fix is a relative URL, not the placeholder."""
    r = _run_in_tree(tmp_path, {"b.json": _board("http://localhost:3000/d/mission-control")})
    assert r.returncode == 1
    assert "relative" in r.stdout.lower(), (
        "Grafana's own origin must be steered to a relative URL, not to the "
        f"render placeholder. Got:\n{r.stdout}"
    )


def test_placeholder_link_passes(tmp_path: Path) -> None:
    r = _run_in_tree(tmp_path, {"b.json": _board("http://__POINDEXTER_SERVICE_HOST__:4200")})
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("url", [
    "https://github.com/Glad-Labs/poindexter/issues/461",
    "https://grafana.com/docs",
    "https://vercel.com/dashboard",
])
def test_external_links_are_not_flagged(tmp_path: Path, url: str) -> None:
    """The narrow scope IS the contract -- see the module docstring."""
    r = _run_in_tree(tmp_path, {"b.json": _board(url)})
    assert r.returncode == 0, (
        f"{url} was flagged. This lint covers loopback only; operator-host "
        # Names the guard in prose, not by path: the public-mirror leak
        # guard is stripped from the mirror, and a shipping test that
        # spells its path trips the stripped-script reference check.
        f"leaks belong to the public-mirror leak guard.\n{r.stdout}"
    )


def test_loopback_inside_a_text_panel_is_caught(tmp_path: Path) -> None:
    """Markdown link tables are how most of these shipped -- not just `links`."""
    doc = {
        "uid": "b", "title": "b", "links": [],
        "panels": [{
            "type": "text", "id": 1,
            "options": {"mode": "markdown",
                        "content": "| Prefect | [x](http://localhost:4200) |"},
        }],
    }
    r = _run_in_tree(tmp_path, {"b.json": doc})
    assert r.returncode == 1, f"text-panel link was missed:\n{r.stdout}"


def test_empty_tree_fails_rather_than_reporting_clean(tmp_path: Path) -> None:
    """A lint that scanned nothing has not passed (scripts/ci/lib_scan_floor)."""
    r = _run_in_tree(tmp_path, {})
    assert r.returncode != 0, (
        "lint reported success with zero dashboards scanned -- the exact "
        f"silently-disarmed failure the scan floor exists to prevent:\n{r.stdout}"
    )

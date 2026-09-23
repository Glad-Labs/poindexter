"""The Grafana dashboard render must stay wired end to end.

Dashboards ship with a ``__POINDEXTER_SERVICE_HOST__`` placeholder because
Grafana does NOT interpolate env vars inside dashboard JSON (``${__env.X}``
comes back literal on grafana-oss 13.0.1), so the only way a link host can be
configuration is to rewrite the file before Grafana reads it. That render runs
in the grafana service's entrypoint.

Every link on every board depends on four things staying true together. Any one
of them silently reverting leaves either an unrendered ``__POINDEXTER_...__``
literal in every URL, or Grafana reading an empty directory. Neither fails
loudly on its own, which is why they are pinned here rather than left to
review.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_COMPOSE_FILES = ("docker-compose.local.yml", "docker-compose.consumer.yml")
_PLACEHOLDER = "__POINDEXTER_SERVICE_HOST__"
_RENDER_DIR = "/var/lib/grafana/dashboards"
_SRC_DIR = "/etc/grafana/dashboards-src"


def _grafana_service(name: str) -> dict:
    doc = yaml.safe_load((_REPO_ROOT / name).read_text(encoding="utf-8"))
    return doc["services"]["grafana"]


@pytest.mark.parametrize("compose", _COMPOSE_FILES)
def test_entrypoint_renders_the_placeholder(compose: str) -> None:
    svc = _grafana_service(compose)
    ep = svc.get("entrypoint")
    assert ep, f"{compose}: grafana has no entrypoint -- the render is gone"
    script = ep[-1] if isinstance(ep, list) else str(ep)
    assert _PLACEHOLDER in script, (
        f"{compose}: entrypoint no longer substitutes {_PLACEHOLDER}. Every "
        "cross-service dashboard link would ship the literal placeholder."
    )
    assert "POINDEXTER_SERVICE_HOST:-localhost" in script, (
        f"{compose}: the localhost fallback is gone. A fresh install with no "
        "app_settings row must still get working on-host links."
    )
    assert script.rstrip().endswith("exec /run.sh"), (
        f"{compose}: entrypoint must hand off to Grafana's own /run.sh, or the "
        "container renders dashboards and then exits."
    )


@pytest.mark.parametrize("compose", _COMPOSE_FILES)
def test_source_is_mounted_read_only_and_render_target_is_writable(compose: str) -> None:
    svc = _grafana_service(compose)
    mounts = [m for m in svc["volumes"] if isinstance(m, str)]
    src = [m for m in mounts if m.endswith(f"{_SRC_DIR}:ro")]
    assert src, (
        f"{compose}: dashboards are not mounted read-only at {_SRC_DIR}. "
        f"Mounting them at {_RENDER_DIR} instead would make the render write "
        "back into the repo checkout."
    )
    assert any(m.startswith("grafana-data:") for m in mounts), (
        f"{compose}: grafana-data volume missing -- {_RENDER_DIR} lives inside "
        "it precisely because a fresh named volume is root-owned and Grafana "
        "runs as uid 472 (first render dies on 'Permission denied')."
    )


def test_provisioning_provider_reads_the_rendered_dir() -> None:
    cfg = _REPO_ROOT / "infrastructure/grafana/provisioning/dashboards/default.yml"
    doc = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    paths = [p["options"]["path"] for p in doc["providers"]]
    assert _RENDER_DIR in paths, (
        f"provider path is {paths}, not {_RENDER_DIR}. Pointing it back at the "
        "source mount serves unrendered placeholders; pointing it anywhere "
        "else serves nothing at all."
    )


def test_every_dashboard_placeholder_carries_a_port() -> None:
    """The placeholder is a HOST, so it must always be followed by ``:<port>``.

    It legitimately appears in two positions -- inside the URL, and inside the
    markdown link LABEL, so the service table renders the operator's real host
    instead of a literal placeholder. What must never happen is a bare
    placeholder in prose: sed would splice a hostname into a sentence, which
    reads as corruption rather than as a broken link.
    """
    root = _REPO_ROOT / "infrastructure/grafana/dashboards"
    boards = sorted(root.glob("*.json"))
    assert boards, f"no dashboards under {root} -- test would vacuously pass"

    bare = re.compile(re.escape(_PLACEHOLDER) + r"(?!:\d)")
    seen = 0
    for f in boards:
        body = f.read_text(encoding="utf-8")
        seen += body.count(_PLACEHOLDER)
        hit = bare.search(body)
        assert hit is None, (
            f"{f.name}: {_PLACEHOLDER} is not followed by a :<port>:\n"
            f"  ...{body[max(0, hit.start() - 70):hit.start() + 70]}..."
        )
    assert seen, (
        "no placeholders found in any dashboard -- either they were all "
        "hardcoded back to a literal host, or this test stopped matching."
    )

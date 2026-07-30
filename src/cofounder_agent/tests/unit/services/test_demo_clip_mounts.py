"""The demo-clip directory must be a SHARED host mount, not container-local.

Two containers touch it: ``worker`` runs ``poindexter media demos bake``, and
``prefect-worker`` runs the media_pipeline render that consumes the clips. If
the path is not bind-mounted from the host on both, the bake writes into
container-local storage the renderer cannot see and every ``cli_demo`` shot
silently falls back to a branded card — a working-looking video with none of
the footage it was supposed to contain.

This is the exact shape of Glad-Labs/poindexter#906, where the appuser media
mounts were added to ``worker`` but not ``prefect-worker`` and would have
stranded every Linux media render. Nothing at runtime catches it; the render
succeeds, it is just wrong. Hence a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE_FILES = ("docker-compose.local.yml", "docker-compose.consumer.yml")

# Services that read or write the demo-clip directory.
REQUIRED_SERVICES = ("worker", "prefect-worker")

# Must match ``demo_clip_dir`` in settings_defaults.py.
CONTAINER_PATH = "/home/appuser/.poindexter/demo-clips"


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docker-compose.local.yml").is_file():
            return parent
    pytest.skip("compose files not reachable from the test location")


def _volumes(compose: dict, service: str) -> list[str]:
    return [str(v) for v in (compose["services"].get(service) or {}).get("volumes", [])]


@pytest.mark.parametrize("filename", COMPOSE_FILES)
@pytest.mark.parametrize("service", REQUIRED_SERVICES)
def test_demo_clip_dir_is_mounted(filename: str, service: str) -> None:
    """Both bake and render containers must see the same host directory."""
    compose = yaml.safe_load((_repo_root() / filename).read_text(encoding="utf-8"))
    if service not in compose["services"]:
        pytest.skip(f"{filename} has no {service} service")

    mounts = [v for v in _volumes(compose, service) if CONTAINER_PATH in v]
    assert mounts, (
        f"{filename}: service {service!r} has no bind mount for {CONTAINER_PATH}. "
        f"Without it the demo-clip bake and the video render do not share a "
        f"filesystem, and every cli_demo shot silently degrades to a card."
    )
    host_side = mounts[0].split(":")[0]
    assert host_side and not host_side.startswith("/home/appuser"), (
        f"{filename}: {service!r} maps {CONTAINER_PATH} from {host_side!r}; "
        f"it must come from the HOST, not a container-internal path"
    )


def test_container_path_matches_the_setting_default() -> None:
    """A drifted default would mount one directory and read another."""
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["demo_clip_dir"] == CONTAINER_PATH


# ---------------------------------------------------------------------------
# The bake target and the render source must be the SAME directory
# ---------------------------------------------------------------------------


def test_bake_default_and_render_default_agree() -> None:
    """`demos bake` must write where `_resolve_demo_clip` reads.

    These drifted once: the CLI defaulted to /tmp/poindexter-demo-clips while
    the renderer looked in ``demo_clip_dir``, so a bake with no ``--out``
    landed somewhere nothing would ever look and every cli_demo shot carded.
    Nothing failed — the bake reported success and the video reported success.
    """
    from services.demo_clips import clip_dir
    from services.settings_defaults import DEFAULTS

    assert str(clip_dir(None)) == DEFAULTS["demo_clip_dir"]
    assert str(clip_dir(None)) == CONTAINER_PATH


def test_zygote_failure_is_diagnosed_not_dumped() -> None:
    """Chromium's sandbox abort must produce an actionable message.

    Its stack dump is multi-KB and the real cause is nowhere near the end, so
    a naive tail yields `trp: 0000... | [end of stack trace]` — which is what
    the first real bake reported, and it says nothing.
    """
    from services.demo_clips import _diagnose_bake_failure

    blob = "content::ZygoteHostImpl::Init()\ntrp: 0000 msk: 0000\n[end of stack trace]"
    msg = _diagnose_bake_failure(blob, blob.splitlines()[-2:])
    assert "seccomp=unconfined" in msg
    assert "trp:" not in msg


def test_unrecognised_failure_still_reports_the_tail() -> None:
    from services.demo_clips import _diagnose_bake_failure

    msg = _diagnose_bake_failure("parse error line 3", ["parse error line 3"])
    assert "parse error line 3" in msg

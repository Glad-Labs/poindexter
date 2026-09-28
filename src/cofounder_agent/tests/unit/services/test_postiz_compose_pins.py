"""The Postiz profile's images stay pinned, at or above the leak-free floor.

Glad-Labs/poindexter#1091: up to postiz-app v2.23.0, the bundled Mastra
re-added 22 columns to ``mastra_ai_spans`` at every backend boot, and the
entrypoint's ``prisma db push --accept-data-loss`` dropped them at the next
container start. Postgres never reuses a dropped column's slot, so every restart
spent 22 of the table's 1600, and after ~72 restarts the backend could no longer
boot. v2.24.0 is the first release whose Prisma models match its Mastra schemas.

Three rules, each tied to a way the stack broke or would break:

* The postiz image is a version tag at or above v2.24.0. A ``:latest`` pull or
  a rollback below the floor brings the column churn back.
* Both compose files pin the same version. They share the ``gladlabs-postiz-*``
  volumes, so moving between the consumer and operator stacks must not run
  another version's ``prisma db push`` over the same database.
* ``postiz-temporal`` skips auto-setup's demo search attributes and is pinned.
  On a fresh namespace those demo attributes use two of SQL visibility's three
  Text slots, so Postiz cannot register ``organizationId`` and ``postId`` and
  its backend fails on boot.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE_FILES = ("docker-compose.local.yml", "docker-compose.consumer.yml")
POSTIZ_REPO = "ghcr.io/gitroomhq/postiz-app"
# First postiz-app release whose Prisma models agree with its bundled Mastra.
POSTIZ_FLOOR = (2, 24, 0)
_POSTIZ_TAG = re.compile(rf"^{re.escape(POSTIZ_REPO)}:v(\d+)\.(\d+)\.(\d+)$")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docker-compose.local.yml").is_file():
            return parent
    pytest.skip("compose files not reachable from the test location")


def _service(filename: str, name: str) -> dict:
    compose = yaml.safe_load((_repo_root() / filename).read_text(encoding="utf-8"))
    service = compose["services"].get(name)
    assert service, f"{filename} has no {name!r} service"
    return service


def _environment(service: dict) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env if "=" in item)
    return {str(k): str(v) for k, v in env.items()}


def _postiz_version(filename: str) -> tuple[int, int, int]:
    image = _service(filename, "postiz")["image"]
    match = _POSTIZ_TAG.match(image)
    assert match, (
        f"{filename}: postiz image {image!r} is not a pinned {POSTIZ_REPO}:vX.Y.Z tag. "
        f"A floating tag lets any pull change the Prisma schema that "
        f"`prisma db push --accept-data-loss` applies on the next start."
    )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_postiz_is_pinned_at_or_above_the_leak_free_floor(filename: str) -> None:
    version = _postiz_version(filename)
    assert version >= POSTIZ_FLOOR, (
        f"{filename}: postiz-app v{'.'.join(map(str, version))} is below "
        f"v{'.'.join(map(str, POSTIZ_FLOOR))}. Its Prisma models drop the columns "
        f"its Mastra re-adds, spending mastra_ai_spans attribute slots on every "
        f"restart until the backend cannot boot (Glad-Labs/poindexter#1091)."
    )


def test_both_stacks_pin_the_same_postiz_version() -> None:
    versions = {filename: _postiz_version(filename) for filename in COMPOSE_FILES}
    assert len(set(versions.values())) == 1, (
        f"postiz versions differ between compose files: {versions}. Both stacks "
        f"share the gladlabs-postiz-* volumes."
    )


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_postiz_temporal_leaves_text_slots_for_postiz(filename: str) -> None:
    service = _service(filename, "postiz-temporal")
    assert not str(service["image"]).endswith(":latest"), (
        f"{filename}: postiz-temporal runs Temporal's schema setup on start, so "
        f"it must be pinned, not {service['image']!r}."
    )
    env = _environment(service)
    assert env.get("SKIP_ADD_CUSTOM_SEARCH_ATTRIBUTES", "").lower() == "true", (
        f"{filename}: postiz-temporal must set SKIP_ADD_CUSTOM_SEARCH_ATTRIBUTES=true. "
        f"Without it a fresh namespace fills SQL visibility's Text slots with demo "
        f"attributes, and Postiz cannot register organizationId/postId at boot."
    )

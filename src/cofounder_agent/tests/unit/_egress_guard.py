"""Egress-guard primitives (Glad-Labs/poindexter#1011).

These live in a normal module rather than in ``conftest.py`` on purpose.
pytest imports a conftest under its own rootdir-derived module name, so a test
that does ``from tests.unit.conftest import X`` gets a SECOND, unequal class
object — and ``pytest.raises(X)`` then cannot catch the exception the guard
actually raised. That is the same dual-module-identity trap that broke
``test_litellm_langfuse_callback`` via ``importlib.reload``
(glad-labs-stack#3155): if two paths reach one file, its classes stop being
each other.

conftest imports from here; tests import from here; both get one class.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).parent / "network_egress_baseline.txt"

# Report mode prints offenders instead of failing them.
#
# WHY IT EXISTS: CI runs unit tests as ~13 separate per-directory pytest steps,
# and a failing step halts the job — so enforcing reveals only the FIRST step's
# offenders, and each fix uncovers the next layer. Regenerating the baseline by
# iterating through that is a guessing loop. In report mode every step runs to
# completion and prints its egress, so one CI run yields the whole list.
#
#     EGRESS_GUARD_MODE=report pytest tests/unit/...
#
# grep the output for EGRESS_REPORT_PREFIX to rebuild the baseline. Remember the
# result is per-environment — merge host and CI, per-file max (see the baseline
# file header).
EGRESS_REPORT_PREFIX = "[egress-guard] EGRESS"


def guard_is_enforcing() -> bool:
    return os.environ.get("EGRESS_GUARD_MODE", "enforce").strip().lower() != "report"


def report_sink() -> Path:
    """Where report mode appends its findings.

    A FILE, not stderr: pytest captures stdio and discards it for passing tests,
    so a printed report vanishes in exactly the mode where every test passes.
    A file also survives xdist — each worker is its own process, and the
    controller reads the merged file back at terminal summary.
    """
    return Path(os.environ.get("EGRESS_REPORT_FILE", "/tmp/egress_report.txt"))


def record_egress(nodeid: str, host: object, port: object) -> None:
    """Append one offender line. Best-effort: a broken sink must not fail a run
    that is, by definition, only gathering information."""
    try:
        with report_sink().open("a", encoding="utf-8") as fh:
            fh.write(f"{EGRESS_REPORT_PREFIX} {nodeid} -> {display_host(host)}:{port}\n")
    except OSError:
        pass


class UnitTestNetworkEgress(BaseException):
    """A unit test opened a network connection.

    Derives from **BaseException, not Exception** — and that is load-bearing,
    not style. The code this guard watches is largely best-effort network code
    wrapped in broad ``except Exception`` handlers (this repo baselines 108 of
    them). An ``Exception`` subclass gets swallowed by the very code under test,
    the connection attempt is absorbed, and the test passes green — a guard that
    cannot fail on the paths it most needs to watch.

    Measured: with an ``AssertionError`` base, un-baselining
    ``test_operator_notifier.py`` (which really does open a TLS connection to
    api.telegram.org) still produced ``26 passed``. Changing the base to
    ``BaseException`` made it fail, correctly, on the connect.

    ``pytest.raises(UnitTestNetworkEgress)`` still works: naming a
    BaseException subclass explicitly catches it.
    """


def load_egress_baseline(path: Path | None = None) -> dict[str, int]:
    """Parse ``<count> <path>`` lines. Blank lines and ``#`` comments ignored.

    Returns ``{repo_relative_test_path: allowed_test_count}``. A malformed
    count is skipped rather than raising: a corrupt baseline must not take the
    whole suite down, and the guard failing OPEN here is the safe direction —
    it re-blocks whatever the bad line was trying to allow.
    """
    target = path or BASELINE_PATH
    allowed: dict[str, int] = {}
    if not target.exists():
        return allowed
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        count, _, rel = line.partition(" ")
        try:
            allowed[rel.strip()] = int(count)
        except ValueError:
            continue
    return allowed


# ---------------------------------------------------------------------------
# Production endpoints: refused for EVERY unit test, baselined or not
# ---------------------------------------------------------------------------
#
# The baseline above lets a grandfathered file keep opening sockets, and it was
# written as a test-hygiene ratchet. On the operator box it is more than that.
# The self-hosted CI runners (poindexter-ci-runner-*) are containers on the
# PRODUCTION compose network, so every compose service name resolves to the
# live container. A baselined render test sent `POST /unload {"hard": true}` to
# the real image-gen server about 11 times per CI job: 6,850 hard unloads in the
# 30 days to 2026-09-25, 106 of them declined only because a render happened to
# be in flight, and at least 9 that found image-gen idle with a model loaded and
# made it exit. The same jobs sent ~6,600 TTS requests to the live speaches
# server, ~1,400 hard unloads to wan, and keep_alive:0 evictions to the host
# Ollama the pipeline was writing with.
#
# So these targets are refused for every unit test. A baseline entry does not
# cover them, and neither does @pytest.mark.allow_network:
#
# * NAMES the compose stack answers to: each service key, container_name,
#   hostname, network alias and extra_hosts alias (host.docker.internal is one).
#   They are refused at name resolution. That also closes the host/CI split the
#   baseline header describes: on the host these names do not resolve at all,
#   so a test used to pass there and reach production in CI. Now it fails the
#   same way in both places.
# * PORTS of every GPU service as the operator box publishes them: the
#   published port of each compose service that reserves a GPU, and the port of
#   each host-native Ollama instance. On that box `localhost:<port>` is the same
#   live server a developer's local test run would otherwise reach.
#
# Both sets are DERIVED from the files that define the stack, never hand-listed,
# so a sidecar is protected the day it is added to compose. A hand-kept list is
# how a test file ended up stubbing the wan and ComfyUI unloads but not
# image-gen's.

COMPOSE_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml")

# Host-native GPU services that are not in compose: the Ollama instances, whose
# listen address is set by OLLAMA_HOST in the systemd units and launch scripts
# the repo ships for them.
OLLAMA_LAUNCH_GLOBS = ("infrastructure/systemd/*.service", "scripts/linux/*.sh")
_OLLAMA_HOST_PORT_RE = re.compile(r"OLLAMA_HOST=[\"']?(?:\$\{OLLAMA_HOST:-)?[^\s\"'}]*:(\d{2,5})")
_COMPOSE_VAR_DEFAULT_RE = re.compile(r"\$\{[^}:]*:?-([^}]*)\}")


def find_repo_root(start: Path | None = None) -> Path | None:
    """The first ancestor holding both ``pyproject.toml`` and ``src/``.

    Same rule as ``conftest.find_repo_root``; duplicated rather than imported,
    because importing conftest from here is the dual-module-identity trap this
    module's docstring exists to avoid.
    """
    anchor = (start or Path(__file__)).resolve()
    for candidate in (anchor, *anchor.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "src").is_dir():
            return candidate
    return None


def compose_files(repo_root: Path) -> list[Path]:
    """Every compose file at the repo root, sorted for a stable derivation."""
    found = {path for pattern in COMPOSE_GLOBS for path in repo_root.glob(pattern)}
    return sorted(found)


def _compose_services(repo_root: Path) -> list[tuple[str, dict[str, Any]]]:
    """``(service key, service definition)`` for every service in every file.

    A file that does not parse raises. Skipping it would quietly drop that
    file's sidecars from the refusal set, and the stack is not deployable with
    a broken compose file anyway.
    """
    import yaml  # a locked dependency; production code imports it too

    services: list[tuple[str, dict[str, Any]]] = []
    for path in compose_files(repo_root):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, definition in (data.get("services") or {}).items():
            services.append((str(key), definition if isinstance(definition, dict) else {}))
    return services


def _extra_host_names(extra_hosts: Any) -> set[str]:
    """Names from ``extra_hosts``: a list of ``name:ip`` / ``name=ip``, or a mapping."""
    if isinstance(extra_hosts, dict):
        return {str(name) for name in extra_hosts}
    names: set[str] = set()
    for entry in extra_hosts or []:
        name = re.split(r"[:=]", str(entry), maxsplit=1)[0]
        if name:
            names.add(name)
    return names


def _service_hostnames(key: str, definition: dict[str, Any]) -> set[str]:
    names = {key} | _extra_host_names(definition.get("extra_hosts"))
    for field in ("container_name", "hostname"):
        if definition.get(field):
            names.add(str(definition[field]))
    networks = definition.get("networks")
    if isinstance(networks, dict):
        for network in networks.values():
            if isinstance(network, dict):
                names.update(str(alias) for alias in network.get("aliases") or [])
    return names


def _reserves_gpu(definition: dict[str, Any]) -> bool:
    """True when the service asks Docker for a GPU, in any of compose's spellings."""
    if str(definition.get("runtime") or "").lower() == "nvidia" or definition.get("gpus"):
        return True
    resources = (definition.get("deploy") or {}).get("resources") or {}
    for device in (resources.get("reservations") or {}).get("devices") or []:
        if not isinstance(device, dict):
            continue
        capabilities: set[str] = set()
        for group in device.get("capabilities") or []:
            members = group if isinstance(group, list) else [group]
            capabilities.update(str(member).lower() for member in members)
        if "gpu" in capabilities or str(device.get("driver") or "").lower() == "nvidia":
            return True
    return False


def _published_port(entry: Any) -> int | None:
    """The host port of one compose ``ports:`` entry, or None when it has none.

    Short syntax is ``[ip:]published:target[/proto]``; a bare ``target`` gets an
    ephemeral host port, and a range has no single port to name. ``${VAR:-5433}``
    resolves to its default, which is what an unset environment publishes.
    """
    if isinstance(entry, dict):
        published = entry.get("published")
        return _as_port(_COMPOSE_VAR_DEFAULT_RE.sub(r"\1", str(published))) if published else None
    spec = _COMPOSE_VAR_DEFAULT_RE.sub(r"\1", str(entry)).split("/", 1)[0]
    parts = spec.rsplit(":", 2)
    return _as_port(parts[-2]) if len(parts) >= 2 else None


def _as_port(value: Any) -> int | None:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def host_native_gpu_ports(repo_root: Path) -> set[int]:
    """Ports the repo's Ollama launch files bind (``OLLAMA_HOST=...:<port>``)."""
    ports: set[int] = set()
    for pattern in OLLAMA_LAUNCH_GLOBS:
        for path in repo_root.glob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in _OLLAMA_HOST_PORT_RE.finditer(text):
                port = _as_port(match.group(1))
                if port:
                    ports.add(port)
    return ports


def display_host(host: object) -> str:
    """A host as a person reads it. httpcore resolves with ``bytes`` hosts,
    which would otherwise print as ``b'prometheus'``."""
    if isinstance(host, (bytes, bytearray)):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def _normalize_host(host: object) -> str:
    return display_host(host or "").strip().lower().rstrip(".")


@dataclass(frozen=True)
class ProductionEndpoints:
    """What a unit test may never reach, whatever the baseline says."""

    hostnames: frozenset[str]
    ports: frozenset[int]

    def is_host(self, host: object) -> bool:
        return _normalize_host(host) in self.hostnames

    def is_port(self, port: object) -> bool:
        return _as_port(port) in self.ports


def load_production_endpoints(repo_root: Path | None = None) -> ProductionEndpoints:
    """Derive the refusal set from the compose files and the Ollama launch files.

    Empty outside a checkout (no repo root, e.g. an installed package). Inside
    one, ``test_network_egress_guard.TestProductionEndpointsAreDerived`` fails
    if the derivation comes back without the anchors it must contain, so a
    parser that stops matching cannot disarm the refusal silently.
    """
    root = repo_root or find_repo_root()
    if root is None:
        return ProductionEndpoints(frozenset(), frozenset())
    hostnames: set[str] = set()
    ports: set[int] = set(host_native_gpu_ports(root))
    for key, definition in _compose_services(root):
        hostnames |= _service_hostnames(key, definition)
        if _reserves_gpu(definition):
            for entry in definition.get("ports") or []:
                port = _published_port(entry)
                if port:
                    ports.add(port)
    return ProductionEndpoints(
        hostnames=frozenset(_normalize_host(name) for name in hostnames if name.strip()),
        ports=frozenset(ports),
    )


def production_refusal_message(nodeid: str, host: object, port: object) -> str:
    """Why this egress is fatal even for a baselined or @allow_network test."""
    return (
        f"{nodeid} tried to reach {display_host(host)}:{port}, a PRODUCTION endpoint.\n"
        "The self-hosted CI runners sit on the live compose network, so this "
        "name resolves to the running stack, not to a test double. On the "
        "operator box the same port is the live server. A POST /unload from a "
        "unit test hard-unloads the real image-gen server; that happened about "
        "11 times per CI job until 2026-09-25.\n"
        "network_egress_baseline.txt and @pytest.mark.allow_network do NOT "
        "cover production endpoints. Stub the seam the code reaches through: "
        "the GPU reclaim rungs via tests.unit._gpu_isolation.inert_reclaim_rungs(), "
        "TTS via synthesize_speech, Prometheus reads via the function that "
        "issues them. See docs/architecture/unit-test-network-egress-guard.md."
    )

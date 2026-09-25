"""The egress guard has to be able to fail, or it is decoration.

Glad-Labs/poindexter#1011. A guard nobody has watched fire is indistinguishable
from one that silently allows everything — which is the exact failure mode it
exists to prevent (the hero-VRAM tests patched a seam the code never reached and
looked green for weeks).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tests.unit._egress_guard import (
    BASELINE_PATH,
    UnitTestNetworkEgress,
    _published_port,
    _reserves_gpu,
    find_repo_root,
    guard_is_enforcing,
    load_egress_baseline,
    load_production_endpoints,
)
from tests.unit._nonempty import nonempty

BASELINE = BASELINE_PATH

# The raise-behaviour tests below assert the guard FAILS a connect. Under
# EGRESS_GUARD_MODE=report it deliberately does not — it records and lets the
# connection through — so these would fail for the right reason in the wrong
# mode. Skip rather than weaken the assertions: a harvest run must stay green
# so every CI step completes, which is the entire purpose of report mode.
enforcing_only = pytest.mark.skipif(
    not guard_is_enforcing(),
    reason="guard is in report mode (EGRESS_GUARD_MODE=report); it records instead of raising",
)


@enforcing_only
class TestGuardFires:
    def test_connect_is_refused(self):
        """socket.socket.connect from a non-baselined test must raise."""
        with pytest.raises(UnitTestNetworkEgress) as ei:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("127.0.0.1", 5433))
            finally:
                s.close()
        assert "poindexter#1011" in str(ei.value)

    def test_create_connection_is_refused(self):
        with pytest.raises(UnitTestNetworkEgress):
            socket.create_connection(("127.0.0.1", 5433), timeout=1)

    def test_message_names_the_target_and_the_test(self):
        """The operator has to know WHICH test reached WHERE, or the failure
        is a scavenger hunt."""
        with pytest.raises(UnitTestNetworkEgress) as ei:
            socket.create_connection(("192.0.2.9", 4242), timeout=1)
        msg = str(ei.value)
        assert "192.0.2.9:4242" in msg
        assert "test_message_names_the_target_and_the_test" in msg

    def test_loopback_is_not_exempt(self):
        """127.0.0.1 IS the problem on this box — the services under test run
        locally. An exemption for loopback would exempt the whole bug."""
        with pytest.raises(UnitTestNetworkEgress):
            socket.create_connection(("127.0.0.1", 9836), timeout=1)


@enforcing_only
class TestSurvivesBroadExcept:
    """The guard's base class is load-bearing, not stylistic.

    Most code this watches is best-effort network code inside a broad
    `except Exception` (this repo baselines 108 such handlers). If the guard
    raised an Exception subclass, the code UNDER TEST would swallow it and the
    test would pass green — the guard would be decoration on exactly the paths
    it exists to watch.

    Measured before the fix: un-baselining test_operator_notifier.py, which
    really does open TLS to api.telegram.org, still gave `26 passed`. With
    BaseException it correctly gives 5 failures.
    """

    def test_exception_is_not_swallowed_by_except_exception(self):
        swallowed = False
        try:
            try:
                socket.create_connection(("127.0.0.1", 5433), timeout=1)
            except Exception:            # noqa: BLE001 - the point of the test
                swallowed = True
        except UnitTestNetworkEgress:
            pass
        assert not swallowed, (
            "a broad `except Exception` in code under test absorbed the egress "
            "guard — it must derive from BaseException"
        )

    def test_base_is_baseexception_not_exception(self):
        assert issubclass(UnitTestNetworkEgress, BaseException)
        assert not issubclass(UnitTestNetworkEgress, Exception), (
            "regression: deriving from Exception lets `except Exception` in the "
            "code under test swallow the guard (see class docstring)"
        )


@pytest.mark.allow_network
class TestMarkerEscapeHatch:
    def test_marked_test_may_open_a_socket(self):
        """A deliberate socket user opts out explicitly and greppably."""
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=0.2)
        except UnitTestNetworkEgress:  # pragma: no cover
            pytest.fail("allow_network marker did not bypass the guard")
        except OSError:
            pass  # connection refused is the expected real-world outcome


class TestBaselineRatchet:
    def test_baseline_parses(self):
        allowed = load_egress_baseline()
        assert allowed, "baseline file should not be empty while burn-down is open"
        assert all(isinstance(v, int) and v > 0 for v in allowed.values())

    def test_baseline_entries_still_exist(self):
        """A baselined path that no longer exists means the ratchet is carrying
        a dead entry — it should have been removed with the file."""
        root = Path(__file__).resolve().parents[2]
        missing = [p for p in load_egress_baseline() if not (root / p).exists()]
        assert not missing, f"baseline references files that no longer exist: {missing}"

    def test_baseline_is_sorted_and_unique(self):
        """Keeps the burn-down diff readable — one line changes when one file
        is fixed."""
        lines = [
            ln.strip().split(" ", 1)[1]
            for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        assert lines == sorted(lines), "baseline must stay sorted by path"
        assert len(lines) == len(set(lines)), "baseline has duplicate paths"


# ---------------------------------------------------------------------------
# Production endpoints: refused for every test, baselined or not
# ---------------------------------------------------------------------------
#
# The CI runners sit on the production compose network. Until 2026-09-25 a
# baselined render test sent ~11 hard /unload calls per CI job to the live
# image-gen server, because the baseline let its file open any socket. These
# tests pin that no marker and no baseline entry reaches a production name or
# a GPU service's port.

PRODUCTION = load_production_endpoints()
_OLLAMA_PORT = 11434  # ollama-primary's OLLAMA_HOST (infrastructure/systemd)


@enforcing_only
class TestProductionEndpointsAreRefused:
    def test_compose_name_is_refused_at_resolution(self):
        """Refused at getaddrinfo, before any connect. On the host the name
        never resolved, so a connect-level guard never saw it, while in CI it
        resolved to the live container. Refusing the NAME makes both places
        fail the same way."""
        with pytest.raises(UnitTestNetworkEgress) as ei:
            socket.getaddrinfo("image-gen-server", 9836)
        assert "PRODUCTION" in str(ei.value)
        assert "image-gen-server:9836" in str(ei.value)

    def test_host_docker_internal_is_refused(self):
        """The runner reaches the host Ollama and every published sidecar port
        through this alias; it comes from compose `extra_hosts`."""
        with pytest.raises(UnitTestNetworkEgress):
            socket.getaddrinfo("host.docker.internal", _OLLAMA_PORT)

    def test_gethostbyname_is_guarded_too(self):
        with pytest.raises(UnitTestNetworkEgress):
            socket.gethostbyname("prometheus")

    def test_resolution_is_case_and_dot_insensitive(self):
        with pytest.raises(UnitTestNetworkEgress):
            socket.getaddrinfo("Image-Gen-Server.", 9836)

    def test_gpu_port_on_loopback_is_refused_with_the_production_message(self):
        """On the operator box `localhost:9836` IS the live image-gen server."""
        with pytest.raises(UnitTestNetworkEgress) as ei:
            socket.create_connection(("127.0.0.1", 9836), timeout=1)
        assert "PRODUCTION" in str(ei.value)

    def test_ordinary_names_still_resolve(self):
        """Only production names are refused at resolution; an SSRF check that
        resolves `localhost` must keep working."""
        assert socket.getaddrinfo("localhost", 80)


@pytest.mark.allow_network
class TestEscapeHatchDoesNotCoverProduction:
    """The marker and a baseline entry feed the same `grandfathered` flag in
    the conftest guard, so the marker stands in for a baselined file here."""

    @enforcing_only
    def test_marked_test_cannot_resolve_a_compose_name(self):
        with pytest.raises(UnitTestNetworkEgress):
            socket.getaddrinfo("speaches", 8000)

    @enforcing_only
    def test_marked_test_cannot_reach_a_gpu_port(self):
        with pytest.raises(UnitTestNetworkEgress):
            socket.create_connection(("127.0.0.1", _OLLAMA_PORT), timeout=1)

    def test_marked_test_keeps_its_ordinary_sockets(self):
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=0.2)
        except UnitTestNetworkEgress:  # pragma: no cover
            pytest.fail("allow_network stopped covering a non-production socket")
        except OSError:
            pass


class TestReportModeNeverReachesProduction:
    """Report mode lets ordinary egress through so one CI run yields the whole
    list. For a production target that would do the damage itself, so it
    records the target and then fails it the way the host does, with an
    ordinary exception the code under test already handles."""

    @pytest.fixture
    def report_mode(self, monkeypatch, tmp_path):
        sink = tmp_path / "egress.txt"
        monkeypatch.setenv("EGRESS_GUARD_MODE", "report")
        monkeypatch.setenv("EGRESS_REPORT_FILE", str(sink))
        return sink

    def test_name_is_recorded_and_fails_like_dns(self, report_mode):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("wan-server", 9840)
        assert "wan-server:9840" in report_mode.read_text(encoding="utf-8")

    def test_port_is_recorded_and_refused(self, report_mode):
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", 9836), timeout=1)
        assert "127.0.0.1:9836" in report_mode.read_text(encoding="utf-8")


class TestProductionEndpointsAreDerived:
    """The refusal set comes from the files that define the stack. A parser
    that stopped matching would disarm it silently, so pin the anchors and pin
    that nothing a compose file declares goes missing."""

    def test_anchor_names_are_present(self):
        for name in nonempty(
            ("image-gen-server", "poindexter-image-gen-server", "host.docker.internal", "prometheus"),
            "anchor names",
        ):
            assert PRODUCTION.is_host(name), f"{name} missing from the derived refusal set"

    def test_anchor_ports_are_present(self):
        assert PRODUCTION.is_port(9836), "image-gen's published port (a GPU service)"
        assert PRODUCTION.is_port(_OLLAMA_PORT), "ollama-primary's OLLAMA_HOST port"

    def test_database_ports_are_not_production_ports(self):
        """Baselined DB tests reach Postgres through bootstrap.toml on the host.
        That is a separate, ratcheted concern; the port rule is GPU services only."""
        assert not PRODUCTION.is_port(5432)
        assert not PRODUCTION.is_port(5433)

    def test_every_compose_service_key_is_covered(self):
        import yaml

        from tests.unit._egress_guard import compose_files

        root = find_repo_root()
        assert root is not None
        for path in nonempty(compose_files(root), "compose files at the repo root"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for key in nonempty(data.get("services") or {}, f"services in {path.name}"):
                assert PRODUCTION.is_host(key), f"{path.name}: service {key!r} not refused"

    def test_derivation_from_a_synthetic_stack(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text(
            "services:\n"
            "  gpu-thing:\n"
            "    container_name: acme-gpu-thing\n"
            "    ports: ['7001:7000']\n"
            "    extra_hosts: ['host.example.internal:host-gateway']\n"
            "    networks:\n"
            "      default:\n"
            "        aliases: [thing-alias]\n"
            "    deploy:\n"
            "      resources:\n"
            "        reservations:\n"
            "          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]\n"
            "  db:\n"
            "    ports: ['5555:5432']\n",
            encoding="utf-8",
        )
        (tmp_path / "scripts" / "linux").mkdir(parents=True)
        (tmp_path / "scripts" / "linux" / "ollama-x.sh").write_text(
            'export OLLAMA_HOST="${OLLAMA_HOST:-0.0.0.0:12345}"\n', encoding="utf-8",
        )
        endpoints = load_production_endpoints(tmp_path)
        assert endpoints.hostnames == frozenset(
            {"gpu-thing", "acme-gpu-thing", "host.example.internal", "thing-alias", "db"},
        )
        # The GPU service's published port and the Ollama port; not the DB's.
        assert endpoints.ports == frozenset({7001, 12345})

    @pytest.mark.parametrize(
        ("definition", "expected"),
        [
            ({"deploy": {"resources": {"reservations": {"devices": [{"capabilities": [["gpu"]]}]}}}}, True),
            ({"deploy": {"resources": {"reservations": {"devices": [{"driver": "nvidia"}]}}}}, True),
            ({"runtime": "nvidia"}, True),
            ({"gpus": "all"}, True),
            ({"deploy": {"resources": {"limits": {"memory": "8g"}}}}, False),
            ({}, False),
        ],
    )
    def test_gpu_reservation_spellings(self, definition, expected):
        assert _reserves_gpu(definition) is expected

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ("9836:9836", 9836),
            ("127.0.0.1:8188:8188", 8188),
            ("8001:8000", 8001),
            ("${POSTGRES_HOST_PORT:-5433}:5432", 5433),
            ("7882:7882/udp", 7882),
            ("9836", None),  # target only: Docker picks an ephemeral host port
            ("50000-60000:50000-60000", None),  # a range names no single port
            ({"target": 8000, "published": "8011"}, 8011),
        ],
    )
    def test_published_port_parsing(self, entry, expected):
        assert _published_port(entry) == expected

    def test_bare_directory_is_not_a_repo_root(self, tmp_path):
        assert find_repo_root(tmp_path) is None


class TestReclaimRungHelper:
    """tests/unit/_gpu_isolation.py derives the GPU reclaim rungs instead of
    listing them. A hand-kept list is how a render test stubbed wan and ComfyUI
    but not the image-gen hard unload that reached production."""

    def test_derivation_finds_the_known_rungs(self):
        from tests.unit._gpu_isolation import reclaim_rung_names

        names = set(reclaim_rung_names())
        assert {
            "_unload_image_gen", "_unload_wan", "_unload_comfyui", "_unload_chatterbox",
            "_unload_stable_audio", "_unload_rife", "_unload_ollama_models",
        } <= names

    def test_inert_rungs_leave_no_instance_attribute_behind(self):
        """monkeypatch.setattr would restore the bound method AS an instance
        attribute and shadow the class for the rest of the process."""
        from unittest.mock import AsyncMock

        from poindexter.services.gpu_scheduler import GPUScheduler
        from tests.unit._gpu_isolation import make_reclaim_rungs_inert, reclaim_rung_names

        scheduler = GPUScheduler()
        with pytest.MonkeyPatch.context() as mp:
            mocks = make_reclaim_rungs_inert(mp, scheduler)
            for name in nonempty(reclaim_rung_names(), "reclaim rungs"):
                assert getattr(scheduler, name) is mocks[name]
                assert isinstance(mocks[name], AsyncMock)
        leaked = set(vars(scheduler)) & set(reclaim_rung_names())
        assert not leaked, f"rung mocks outlived the monkeypatch: {sorted(leaked)}"

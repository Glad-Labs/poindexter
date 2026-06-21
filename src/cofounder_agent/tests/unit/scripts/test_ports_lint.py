"""Unit + contract tests for ``scripts/ci/ports_lint.py``.

This is the ratchet that keeps the host-port column of
``docs/operations/ports.md`` in lockstep with the ``ports:`` publishes in
``docker-compose.local.yml`` (and, as a belt-and-suspenders, with
``setup.py::_DEFAULT_LOCAL_DB_PORT``). It exists because of the 2026-06-21
``15432 -> 5433`` local-Postgres host-port move: a hardcoded port literal
drifted from the compose publish after 15432 landed in a Windows Hyper-V
reserved range and became unbindable.

Two layers, mirroring ``test_check_shell_line_endings.py``:

1. **Repo contract** — run the lint over the live tree and assert exit 0.
   Catches a real future drift (someone adds/retires a ``ports:`` mapping
   without touching the table, or vice-versa).
2. **Unit** — pin the pure parsers (compose ``${VAR:-default}`` /
   ``IP:host:container`` / ``host:container/proto`` resolution, the
   markdown host-port column, the setup.py default) and the ``main``
   exit-code contract against synthetic inputs.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

# scripts/ci is a flat directory (no __init__.py); import the linter by file
# path. Same pattern as test_check_shell_line_endings.py / test_grafana_panels_lint.py.
REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "scripts" / "ci" / "ports_lint.py").exists()
)
LINTER_PATH = REPO_ROOT / "scripts" / "ci" / "ports_lint.py"


def _load_linter():
    spec = importlib.util.spec_from_file_location("ports_lint", LINTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_linter()


# ---------------------------------------------------------------------------
# Repo contract — the live tree must stay in sync
# ---------------------------------------------------------------------------


class TestRepoContract:
    def test_repo_passes_lint_in_process(self, capsys) -> None:
        rc = LINT.main([])
        out = capsys.readouterr()
        assert rc == 0, (
            "docker-compose.local.yml host ports and docs/operations/ports.md "
            "have drifted (or the setup.py DB-port invariant broke):\n"
            f"stdout:\n{out.out}\nstderr:\n{out.err}"
        )

    def test_repo_passes_lint_subprocess(self) -> None:
        # Exercise the real entry point (argv parsing + exit code), not just
        # the importable helpers.
        result = subprocess.run(
            [sys.executable, str(LINTER_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Unit — resolve_host_port (the ${VAR:-default} / IP / proto forms)
# ---------------------------------------------------------------------------


class TestResolveHostPort:
    def test_plain_mapping(self) -> None:
        assert LINT.resolve_host_port("9091:9090") == 9091

    def test_same_host_and_container(self) -> None:
        assert LINT.resolve_host_port("3000:3000") == 3000

    def test_var_with_default(self) -> None:
        assert LINT.resolve_host_port("${POSTGRES_HOST_PORT:-5433}:5432") == 5433

    def test_proto_suffix_on_container_side(self) -> None:
        assert LINT.resolve_host_port("7882:7882/udp") == 7882

    def test_ip_prefixed_form_takes_middle(self) -> None:
        assert LINT.resolve_host_port("127.0.0.1:9091:9001") == 9091

    def test_high_port(self) -> None:
        assert LINT.resolve_host_port("18443:80") == 18443

    def test_var_without_default_is_unresolvable(self) -> None:
        assert LINT.resolve_host_port("${SOME_PORT}:5432") is None

    def test_bare_container_only_publish_is_unresolvable(self) -> None:
        # `- "80"` publishes an ephemeral random host port — nothing static
        # to compare against the doc.
        assert LINT.resolve_host_port("80") is None


# ---------------------------------------------------------------------------
# Unit — parse_compose_host_ports
# ---------------------------------------------------------------------------


_SAMPLE_COMPOSE = """\
services:
  alpha:
    image: x
    ports:
      # a comment inside the ports block must be skipped
      - "8080:80"
      - "${ALPHA_PORT:-9000}:9000"
    environment:
      FOO: bar
  beta:
    ports:
      - "7882:7882/udp"
      - "127.0.0.1:6000:6000"
  postgres-local:
    ports:
      - "${POSTGRES_HOST_PORT:-5433}:5432"
    command:
      - "-c"
      - "max_connections=300"
volumes:
  alpha-data:
"""


class TestParseComposeHostPorts:
    def test_resolved_host_port_set(self) -> None:
        ports = LINT.parse_compose_host_ports(_SAMPLE_COMPOSE)
        resolved = {p.port for p in ports if p.port is not None}
        assert resolved == {8080, 9000, 7882, 6000, 5433}

    def test_command_items_are_not_parsed_as_ports(self) -> None:
        # `- "-c"` / `- "max_connections=300"` live under command:, not ports:.
        ports = LINT.parse_compose_host_ports(_SAMPLE_COMPOSE)
        raws = [p.raw for p in ports]
        assert "-c" not in raws
        assert "max_connections=300" not in raws

    def test_service_attribution(self) -> None:
        ports = LINT.parse_compose_host_ports(_SAMPLE_COMPOSE)
        by_port = {p.port: p.service for p in ports if p.port is not None}
        assert by_port[8080] == "alpha"
        assert by_port[7882] == "beta"
        assert by_port[5433] == "postgres-local"

    def test_line_numbers_point_at_the_mapping(self) -> None:
        ports = LINT.parse_compose_host_ports(_SAMPLE_COMPOSE)
        lines = _SAMPLE_COMPOSE.splitlines()
        for p in ports:
            if p.port == 8080:
                assert "8080:80" in lines[p.line - 1]

    def test_postgres_host_port_helper(self) -> None:
        ports = LINT.parse_compose_host_ports(_SAMPLE_COMPOSE)
        assert LINT.postgres_host_port(ports) == 5433


# ---------------------------------------------------------------------------
# Unit — parse_markdown_host_ports
# ---------------------------------------------------------------------------


_SAMPLE_DOC = """\
# Ports

intro prose

| Service | Container | Host port | Container port | URL |
| ------- | --------- | --------- | -------------- | --- |
| Grafana | g         | **3000**  | 3000           | u   |
| Kuma    | k         | 3002      | 3001           | u   |

some prose between tables

| Other | Column |
| ----- | ------ |
| a     | b      |
"""


class TestParseMarkdownHostPorts:
    def test_host_port_column_extracted(self) -> None:
        rows = LINT.parse_markdown_host_ports(_SAMPLE_DOC)
        assert {r.port for r in rows} == {3000, 3002}

    def test_bold_markers_stripped(self) -> None:
        rows = LINT.parse_markdown_host_ports(_SAMPLE_DOC)
        assert 3000 in {r.port for r in rows}  # came in as **3000**

    def test_unrelated_table_ignored(self) -> None:
        # The second table has no "Host port" column — its rows ("a"/"b")
        # must not be parsed.
        rows = LINT.parse_markdown_host_ports(_SAMPLE_DOC)
        assert all(r.port in {3000, 3002} for r in rows)

    def test_line_numbers(self) -> None:
        rows = LINT.parse_markdown_host_ports(_SAMPLE_DOC)
        lines = _SAMPLE_DOC.splitlines()
        for r in rows:
            assert str(r.port) in lines[r.line - 1]


# ---------------------------------------------------------------------------
# Unit — parse_setup_default_db_port
# ---------------------------------------------------------------------------


class TestParseSetupDefaultDbPort:
    def test_extracts_int(self) -> None:
        src = "x = 1\n_DEFAULT_LOCAL_DB_PORT = 5433\ny = 2\n"
        assert LINT.parse_setup_default_db_port(src) == 5433

    def test_missing_returns_none(self) -> None:
        assert LINT.parse_setup_default_db_port("nothing here\n") is None

    def test_survives_syntax_error_via_regex(self) -> None:
        # A file that doesn't ast-parse should still yield the literal.
        src = "def broken(:\n_DEFAULT_LOCAL_DB_PORT = 5433\n"
        assert LINT.parse_setup_default_db_port(src) == 5433


# ---------------------------------------------------------------------------
# Integration — main() exit-code contract against synthetic files
# ---------------------------------------------------------------------------


_COMPOSE_OK = """\
services:
  grafana:
    ports:
      - "3000:3000"
  postgres-local:
    ports:
      - "${POSTGRES_HOST_PORT:-5433}:5432"
"""

_DOC_OK = """\
| Service | Container | Host port | Container port | URL |
| ------- | --------- | --------- | -------------- | --- |
| Grafana | g         | **3000**  | 3000           | u   |
| Postgres| p         | **5433**  | 5432           | u   |
"""

_SETUP_OK = "_DEFAULT_LOCAL_DB_PORT = 5433\n"


def _write(tmp_path: Path, compose: str, doc: str, setup: str | None = _SETUP_OK):
    c = tmp_path / "docker-compose.local.yml"
    d = tmp_path / "ports.md"
    c.write_text(compose, encoding="utf-8")
    d.write_text(doc, encoding="utf-8")
    args = ["--compose", str(c), "--ports-doc", str(d)]
    if setup is not None:
        s = tmp_path / "setup.py"
        s.write_text(setup, encoding="utf-8")
        args += ["--setup", str(s)]
    else:
        args += ["--setup", str(tmp_path / "missing_setup.py")]
    return args


class TestMainContract:
    def test_consistent_inputs_pass(self, tmp_path) -> None:
        rc = LINT.main(_write(tmp_path, _COMPOSE_OK, _DOC_OK))
        assert rc == 0

    def test_compose_port_missing_from_table_fails(self, tmp_path, capsys) -> None:
        compose = _COMPOSE_OK + '  speaches:\n    ports:\n      - "8001:8000"\n'
        rc = LINT.main(_write(tmp_path, compose, _DOC_OK))
        assert rc == 1
        err = capsys.readouterr().err
        assert "8001" in err
        assert "speaches" in err

    def test_table_port_missing_from_compose_fails(self, tmp_path, capsys) -> None:
        doc = _DOC_OK + "| Ghost   | x         | **8003**  | 8003           | u   |\n"
        rc = LINT.main(_write(tmp_path, _COMPOSE_OK, doc))
        assert rc == 1
        err = capsys.readouterr().err
        assert "8003" in err

    def test_setup_default_drift_fails(self, tmp_path, capsys) -> None:
        rc = LINT.main(_write(tmp_path, _COMPOSE_OK, _DOC_OK, setup="_DEFAULT_LOCAL_DB_PORT = 5999\n"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "_DEFAULT_LOCAL_DB_PORT" in err

    def test_duplicate_host_port_in_compose_fails(self, tmp_path, capsys) -> None:
        compose = (
            "services:\n"
            '  a:\n    ports:\n      - "3000:3000"\n'
            '  b:\n    ports:\n      - "3000:3001"\n'
        )
        doc = (
            "| Service | Container | Host port | Container port | URL |\n"
            "| ------- | --------- | --------- | -------------- | --- |\n"
            "| A       | a         | **3000**  | 3000           | u   |\n"
        )
        rc = LINT.main(_write(tmp_path, compose, doc, setup=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "DUPLICATE" in err.upper()

    def test_missing_compose_returns_2(self, tmp_path) -> None:
        d = tmp_path / "ports.md"
        d.write_text(_DOC_OK, encoding="utf-8")
        rc = LINT.main([
            "--compose", str(tmp_path / "nope.yml"),
            "--ports-doc", str(d),
        ])
        assert rc == 2

    def test_missing_setup_is_skipped_not_fatal(self, tmp_path) -> None:
        # A missing setup.py (e.g. path moved) must not fail the table check.
        rc = LINT.main(_write(tmp_path, _COMPOSE_OK, _DOC_OK, setup=None))
        assert rc == 0

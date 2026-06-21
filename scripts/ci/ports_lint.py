#!/usr/bin/env python3
"""Lint host-port drift between the compose file, the ports doc, and setup.py.

``docs/operations/ports.md`` is the human-facing single-source-of-truth for
"which host port reaches which service in the local Docker stack". The
*actual* mapping is owned by ``docker-compose.local.yml``. Those two drift
the moment someone adds/retires a service's ``ports:`` publish without
touching the table (or vice-versa) — and that drift is silent until an
operator hits a connection-refused on a stale port.

This is the belt-and-suspenders the doc has long promised. It is the same
drift class that caused the 2026-06-21 ``15432 -> 5433`` local-Postgres
host-port move: a hardcoded port literal in ``setup.py`` / the docs drifted
from the compose publish after 15432 landed in a Windows Hyper-V reserved
range and became unbindable (WSAEACCES).

CHECKS

1. **Table <-> compose host ports agree (either direction).** Every host
   port published by a ``ports:`` mapping in ``docker-compose.local.yml``
   must appear in the host-port column of the ``docs/operations/ports.md``
   table, and every host port in that column must be published by some
   service. A discrepancy in either direction is a hard fail, reported with
   the offending file + line number.

2. **No duplicate host ports in compose.** Two services publishing the same
   host port can't both bind it — exactly the #377 MinIO/Prometheus ``9091``
   collision. Hard fail.

3. **setup.py DB-port invariant (opt-out via --no-setup-check).**
   ``src/cofounder_agent/poindexter/cli/setup.py::_DEFAULT_LOCAL_DB_PORT``
   must equal the host port the ``postgres-local`` service publishes. This
   centralizes the narrow guard in
   ``tests/.../test_setup.py::TestLocalDbPortInvariant`` onto the
   compose <-> ports.md axis. A missing setup.py (e.g. running against a
   future tree where it moved) is a skipped WARNING, not a failure.

PORT-SPEC FORMS HANDLED (compose short syntax)

  * ``"9091:9090"``                       -> host 9091
  * ``"3000:3000"``                       -> host 3000
  * ``"${POSTGRES_HOST_PORT:-5433}:5432"`` -> host 5433  (``${VAR:-default}``)
  * ``"7882:7882/udp"``                   -> host 7882  (``/proto`` on container side)
  * ``"127.0.0.1:9091:9001"``             -> host 9091  (``IP:host:container``)
  * ``"${SOME_PORT}:5432"`` / ``"80"``     -> unresolvable -> WARNING + skipped

  IPv6-bracketed binds (``[::1]:9091:9001``) and the long mapping syntax
  (``- target: ... / published: ...``) are not used in this repo and are
  not parsed; add them here if they ever land.

EXIT CODES
    0 — in sync
    1 — at least one hard failure (drift / duplicate / DB-port mismatch)
    2 — script error (a required input file is missing)

USAGE
    python scripts/ci/ports_lint.py
    python scripts/ci/ports_lint.py --no-setup-check
    python scripts/ci/ports_lint.py --compose path --ports-doc path --setup path

The script is dependency-free (stdlib only) so it runs in CI without the
project's poetry environment — same posture as scripts/ci/migrations_lint.py.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations``. The
# @dataclass definitions below introspect their field annotations, and under
# stringized (PEP 563) annotations dataclass resolves them via
# ``sys.modules[cls.__module__]`` — which is absent when this file is loaded
# by ``importlib.util.spec_from_file_location`` (the unit test's loader),
# raising AttributeError at class-creation time. Real PEP 604/585 annotations
# (fine on Python 3.10+) keep the module self-contained under any import style.

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.local.yml"
PORTS_DOC_PATH = REPO_ROOT / "docs" / "operations" / "ports.md"
SETUP_PATH = REPO_ROOT / "src" / "cofounder_agent" / "poindexter" / "cli" / "setup.py"

# The standard container port Postgres listens on — used to disambiguate the
# postgres-local publish if the service is ever renamed.
_POSTGRES_CONTAINER_PORT = 5432

_RE_VAR_DEFAULT = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-(\d+)\}$")
# A service key sits at exactly two-space indent with nothing after the colon.
_RE_SERVICE = re.compile(r"^ {2}([A-Za-z0-9_.-]+):\s*$")
_RE_PORTS_KEY = re.compile(r"^\s*ports:\s*$")


@dataclass(frozen=True)
class ComposePort:
    """A single ``ports:`` mapping published in the compose file."""

    port: int | None  # resolved host port, or None if unresolvable
    service: str | None
    container_port: int | None
    line: int  # 1-based line in the compose file
    raw: str


@dataclass(frozen=True)
class TablePort:
    """A single host-port cell from the ports.md markdown table."""

    port: int
    line: int  # 1-based line in the doc
    raw: str


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------


def _split_top_level_colons(spec: str) -> list[str]:
    """Split on ``:`` separators, ignoring colons inside ``${...}``.

    ``${POSTGRES_HOST_PORT:-5433}:5432`` must split into the two fields
    ``["${POSTGRES_HOST_PORT:-5433}", "5432"]`` — a naive ``.split(":")``
    would shred the interpolation's own ``:-`` colon.
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in spec:
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == ":" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _resolve_token(token: str) -> int | None:
    """Resolve a single host-side token to an int, or None if dynamic."""
    token = token.strip()
    m = _RE_VAR_DEFAULT.match(token)
    if m:
        return int(m.group(1))
    if token.startswith("$"):
        return None  # ${VAR} / $VAR with no default -- can't compare statically
    return int(token) if token.isdigit() else None


def resolve_host_port(mapping: str) -> int | None:
    """Extract the published HOST port from a compose short-syntax mapping.

    Returns None for container-only publishes (``"80"``) and dynamic specs
    without a default (``"${VAR}:80"``).
    """
    parts = _split_top_level_colons(mapping.strip())
    if len(parts) == 2:
        host_tok = parts[0]
    elif len(parts) == 3:  # IP:host:container
        host_tok = parts[1]
    else:
        return None
    return _resolve_token(host_tok)


def _container_port(mapping: str) -> int | None:
    parts = _split_top_level_colons(mapping.strip())
    if len(parts) < 2:
        return None
    ctok = parts[-1].split("/", 1)[0].strip()  # drop /tcp|/udp
    return int(ctok) if ctok.isdigit() else None


def _unquote(raw: str) -> str:
    """Strip a YAML scalar's surrounding quotes (or a trailing inline comment)."""
    raw = raw.strip()
    if raw and raw[0] in "\"'":
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    hash_pos = raw.find("#")
    if hash_pos != -1:
        raw = raw[:hash_pos]
    return raw.strip()


def parse_compose_host_ports(text: str) -> list[ComposePort]:
    """Walk ``ports:`` blocks and return every published mapping.

    A lightweight line scanner (no PyYAML dep): track the current service
    name (two-space-indent key), and when a ``ports:`` key appears consume
    the deeper-indented ``- "..."`` list items beneath it, skipping comments
    and blank lines, stopping at the next sibling key / dedent.
    """
    results: list[ComposePort] = []
    lines = text.splitlines()
    current_service: str | None = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            sm = _RE_SERVICE.match(line)
            if sm:
                current_service = sm.group(1)
        if not stripped.startswith("#") and _RE_PORTS_KEY.match(line):
            ports_indent = len(line) - len(line.lstrip(" "))
            j = i + 1
            while j < n:
                pl = lines[j]
                ps = pl.strip()
                if not ps or ps.startswith("#"):
                    j += 1
                    continue
                p_indent = len(pl) - len(pl.lstrip(" "))
                if ps.startswith("- ") and p_indent > ports_indent:
                    value = _unquote(ps[2:])
                    results.append(
                        ComposePort(
                            port=resolve_host_port(value),
                            service=current_service,
                            container_port=_container_port(value),
                            line=j + 1,
                            raw=value,
                        )
                    )
                    j += 1
                    continue
                break  # next key / dedent ends the ports block
            i = j
            continue
        i += 1
    return results


def postgres_host_port(ports: list[ComposePort]) -> int | None:
    """The host port the postgres-local service publishes (5432 container)."""
    for cp in ports:
        if cp.service == "postgres-local" and cp.port is not None:
            return cp.port
    for cp in ports:
        if cp.container_port == _POSTGRES_CONTAINER_PORT and cp.port is not None:
            return cp.port
    return None


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(c and set(c) <= set("-: ") for c in cells)


def parse_markdown_host_ports(text: str) -> list[TablePort]:
    """Extract the host-port column from the ports.md markdown table.

    Finds the table whose header has a ``Host port`` column, then reads each
    data row's cell from that column (stripping ``**bold**`` markers). Other
    pipe-tables (no such column) are ignored, as is the box-drawing ASCII
    diagram (those rows start with U+2502, not an ASCII pipe).
    """
    results: list[TablePort] = []
    host_col: int | None = None
    for idx, line in enumerate(text.splitlines()):
        if not line.lstrip().startswith("|"):
            host_col = None  # left the table; re-detect a header next time
            continue
        cells = _split_row(line)
        if host_col is None:
            lowered = [c.lower() for c in cells]
            for k, c in enumerate(lowered):
                if c.startswith("host port"):
                    host_col = k
                    break
            continue  # header row itself carries no data
        if _is_separator_row(cells):
            continue
        if host_col < len(cells):
            m = re.search(r"\d+", cells[host_col].replace("*", ""))
            if m:
                results.append(
                    TablePort(port=int(m.group()), line=idx + 1, raw=cells[host_col])
                )
    return results


def parse_setup_default_db_port(text: str) -> int | None:
    """Read ``_DEFAULT_LOCAL_DB_PORT = <int>`` from setup.py source."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        m = re.search(r"^_DEFAULT_LOCAL_DB_PORT\s*=\s*(\d+)", text, re.MULTILINE)
        return int(m.group(1)) if m else None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "_DEFAULT_LOCAL_DB_PORT"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, int)
                ):
                    return node.value.value
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lint host-port drift between docker-compose.local.yml, "
            "docs/operations/ports.md, and setup.py."
        ),
    )
    parser.add_argument("--compose", default=str(COMPOSE_PATH))
    parser.add_argument("--ports-doc", default=str(PORTS_DOC_PATH))
    parser.add_argument("--setup", default=str(SETUP_PATH))
    parser.add_argument(
        "--no-setup-check",
        action="store_true",
        help="Skip the setup.py _DEFAULT_LOCAL_DB_PORT invariant.",
    )
    args = parser.parse_args(argv)

    compose_path = Path(args.compose)
    doc_path = Path(args.ports_doc)
    setup_path = Path(args.setup)

    if not compose_path.is_file():
        print(f"ERROR: compose file not found: {compose_path}", file=sys.stderr)
        return 2
    if not doc_path.is_file():
        print(f"ERROR: ports doc not found: {doc_path}", file=sys.stderr)
        return 2

    compose_ports = parse_compose_host_ports(compose_path.read_text(encoding="utf-8"))
    table_ports = parse_markdown_host_ports(doc_path.read_text(encoding="utf-8"))

    errors: list[str] = []
    warnings: list[str] = []

    compose_name = compose_path.name
    doc_name = doc_path.name

    # Build host-port -> source maps, flagging duplicates / unresolvables.
    compose_map: dict[int, tuple[str | None, int]] = {}
    for cp in compose_ports:
        if cp.port is None:
            warnings.append(
                f"unresolved host-port spec {cp.raw!r} "
                f"(service={cp.service}) at {compose_name}:{cp.line} -- skipped"
            )
            continue
        if cp.port in compose_map:
            prev_service, prev_line = compose_map[cp.port]
            errors.append(
                f"DUPLICATE HOST PORT {cp.port} in {compose_name}: "
                f"{prev_service} (line {prev_line}) and {cp.service} "
                f"(line {cp.line}) both bind it -- host ports must be unique."
            )
        else:
            compose_map[cp.port] = (cp.service, cp.line)

    table_map: dict[int, int] = {}
    for tp in table_ports:
        if tp.port in table_map:
            warnings.append(
                f"duplicate host port {tp.port} in {doc_name} "
                f"(lines {table_map[tp.port]} and {tp.line})"
            )
        else:
            table_map[tp.port] = tp.line

    # Check 1 — drift in either direction.
    for port in sorted(set(compose_map) - set(table_map)):
        service, line = compose_map[port]
        errors.append(
            f"MISSING TABLE ROW: host port {port} published by "
            f"{service!r} ({compose_name}:{line}) is not in the {doc_name} "
            f"table. Add a row for it."
        )
    for port in sorted(set(table_map) - set(compose_map)):
        errors.append(
            f"STALE TABLE ROW: host port {port} listed in "
            f"{doc_name}:{table_map[port]} is not published by any service in "
            f"{compose_name}. Remove the row (or fix the port)."
        )

    # Check 3 — setup.py default DB port invariant.
    if not args.no_setup_check:
        if not setup_path.is_file():
            warnings.append(
                f"{setup_path} not found -- skipped _DEFAULT_LOCAL_DB_PORT invariant"
            )
        else:
            db_default = parse_setup_default_db_port(
                setup_path.read_text(encoding="utf-8")
            )
            pg_port = postgres_host_port(compose_ports)
            if db_default is None:
                warnings.append(
                    f"could not find _DEFAULT_LOCAL_DB_PORT in {setup_path.name} "
                    "-- skipped DB-port invariant"
                )
            elif pg_port is None:
                warnings.append(
                    f"could not find the postgres-local host port in {compose_name} "
                    "-- skipped DB-port invariant"
                )
            elif db_default != pg_port:
                errors.append(
                    f"DB PORT DRIFT: {setup_path.name} _DEFAULT_LOCAL_DB_PORT="
                    f"{db_default} but {compose_name} publishes postgres-local on "
                    f"host port {pg_port}. Update the literal to match the compose "
                    "publish (this is the 2026-06-21 15432->5433 drift class)."
                )

    # ----- Report -----
    print(f"[lint] compose host ports: {sorted(compose_map)}")
    print(f"[lint] table host ports:   {sorted(table_map)}")
    for w in warnings:
        print(f"[lint] WARNING: {w}", file=sys.stderr)
    for e in errors:
        print(f"[lint] ERROR: {e}", file=sys.stderr)

    if errors:
        print(
            f"[lint] FAIL -- {len(errors)} error(s), {len(warnings)} warning(s)",
            file=sys.stderr,
        )
        return 1
    print(
        f"[lint] OK -- host ports in sync "
        f"({len(compose_map)} published), {len(warnings)} warning(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

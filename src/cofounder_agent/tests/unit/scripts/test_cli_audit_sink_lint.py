"""Guard for the CLI audit-sink lint.

A ``poindexter <cmd>`` that opens a bare asyncpg pool has no global
``AuditLogger``, so every finding its services emit is DROPPED — silently, by
construction: no failing test, no user-visible error, just an operator page
that never arrives. That shipped for real, dropping a
``pro_delivery_action_needed`` warn finding during the first live Pro purchase
(2026-08-26).

The lint exists because the invariant regressed within hours of being
established: while the fixing PR was open, ``pro relay`` merged to main with
three fresh ``await pool.close()`` calls. Both detected shapes below are
load-bearing — the relay regression used ``open_cli_pool`` correctly and still
broke the drain, so a lint that only looked for ``create_pool`` would have
missed it.

These tests pin the live tree clean AND pin the detection, so the guard can't
rot into a no-op that always passes.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

_LINT = Path(__file__).resolve().parents[5] / "scripts" / "ci" / "cli_audit_sink_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location(_LINT.stem, _LINT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _violations(mod, source: str, tmp_path: Path) -> list[tuple[int, str]]:
    path = tmp_path / "some_cmd.py"
    path.write_text(source, encoding="utf-8")
    return mod._iter_violations(path)


class TestDetectsBypasses:
    def test_flags_bare_create_pool(self, tmp_path: Path):
        mod = _load()
        found = _violations(
            mod,
            "import asyncpg\n"
            "async def _pool():\n"
            "    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)\n",
            tmp_path,
        )
        assert len(found) == 1
        assert "no audit sink" in found[0][1]

    def test_flags_bare_pool_close(self, tmp_path: Path):
        """THE relay regression: open_cli_pool used correctly, drain skipped."""
        mod = _load()
        found = _violations(
            mod,
            "async def _run():\n"
            "    pool, cfg = await _open_ctx()\n"
            "    try:\n"
            "        await do_work(pool)\n"
            "    finally:\n"
            "        await pool.close()\n",
            tmp_path,
        )
        assert len(found) == 1
        assert "skips the drain" in found[0][1]

    def test_reports_every_site_not_just_the_first(self, tmp_path: Path):
        mod = _load()
        found = _violations(
            mod,
            "import asyncpg\n"
            "async def a():\n"
            "    pool = await asyncpg.create_pool(d)\n"
            "    await pool.close()\n"
            "async def b():\n"
            "    pool = await asyncpg.create_pool(d)\n"
            "    await pool.close()\n",
            tmp_path,
        )
        assert len(found) == 4

    def test_remedy_names_both_helpers(self, tmp_path: Path):
        mod = _load()
        found = _violations(mod, "import asyncpg\nx = asyncpg.create_pool(d)\n", tmp_path)
        assert "open_cli_pool" in found[0][1]
        assert "close_cli_pool" in found[0][1]


class TestIgnoresLegitimatePatterns:
    @pytest.mark.parametrize(
        ("source", "why"),
        [
            (
                "import asyncpg\nconn = await asyncpg.connect(_dsn())\nawait conn.close()\n",
                "bare Connection commands need conn.transaction(); not the seam's business",
            ),
            (
                "pool = await open_cli_pool()\nawait close_cli_pool(pool)\n",
                "the seam itself",
            ),
            (
                "await client.close()\nawait session.close()\n",
                "closing a non-pool resource",
            ),
            (
                "# await pool.close()\n",
                "commented out",
            ),
            (
                "print('await pool.close()')\n",
                "inside a string literal",
            ),
        ],
    )
    def test_no_false_positive(self, source: str, why: str, tmp_path: Path):
        mod = _load()
        assert _violations(mod, source, tmp_path) == [], f"false positive ({why})"


class TestLiveTree:
    def test_cli_tree_is_clean(self):
        """The real CLI package must satisfy the invariant."""
        assert _load().main() == 0

    def test_seam_owner_is_skipped_but_would_otherwise_flag(self):
        """_bootstrap.py is exempt because it DEFINES the seam — the exemption
        must be a real carve-out, not a lint that flags nothing."""
        mod = _load()
        seam = Path(mod._CLI_DIR) / mod._SEAM_OWNER
        assert seam.is_file(), "seam owner missing — did _bootstrap.py move?"
        assert mod._iter_violations(seam), (
            "_bootstrap.py no longer contains create_pool/pool.close — either the "
            "seam moved or the detection broke; re-point the exemption."
        )

    def test_detection_targets_still_exist_in_the_codebase(self):
        """Pin the AST shapes: if asyncpg usage is refactored away entirely,
        this lint silently becomes a no-op and should be revisited."""
        mod = _load()
        seam = Path(mod._CLI_DIR) / mod._SEAM_OWNER
        tree = ast.parse(seam.read_text(encoding="utf-8"))
        attrs = {
            n.func.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "create_pool" in attrs
        assert "close" in attrs

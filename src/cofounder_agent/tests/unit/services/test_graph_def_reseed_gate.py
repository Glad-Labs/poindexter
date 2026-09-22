"""CI gate: an atom-contract or topology change to an active graph_def cannot
merge without the reseed migration that carries it to prod.

Background. Every active ``pipeline_templates`` row is stamped per node with
its atom's ``contract_fingerprint()``; ``assert_graph_def_current`` refuses a
row whose stamps drifted, and the boot self-heal restamps only rows carrying
NO fingerprint. So a contract change needs an explicit reseed migration — and
twice the PR that changed the contract shipped without one behind a green CI
(poindexter#1876; glad-labs-stack#3928, which halted every Stage-2 video render
on prod 2026-09-22). Both times the developer did what the failing gate told
them: refreshed ``graph_def_contract_fingerprints.json``. That snapshot was a
stand-in for prod's row that could be made green without touching prod.

This gate reads the migrations instead. A reseed migration declares, per
graph, the graph signature it brings the row to (``_RESEEDS`` 5-tuples, see
``services/graph_def_reseed.py``), and for every active in-tree spec the
NEWEST declaration must equal the signature the live registry produces now.
The only way to turn that green after a contract change is a NEW migration —
merged migrations are never edited — which is exactly the artefact prod needs.

Regenerate the signatures to paste into a new migration::

    REGEN_GRAPH_DEF_FP=1 poetry run pytest \
        tests/unit/services/test_graph_def_reseed_gate.py::test__print_graph_signatures -s
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

import poindexter.services.pipeline_architect as pa
from poindexter.plugins.atom import AtomMeta
from poindexter.services import graph_def_reseed as gr
from poindexter.services.atom_registry import discover

MIGRATIONS_DIR = Path(gr.__file__).resolve().parent / "migrations"
BOOTSTRAP = "20260922_021635_reseed_every_active_graph_def_declaring_the_graph_signature_it_brings_the_row_to.py"
DOC = "docs/operations/migrations.md#reseed-a-graph_def"


def _active_spec(slug: str) -> dict:
    for s, module, attr in gr.ACTIVE_SPECS:
        if s == slug:
            return getattr(importlib.import_module(module), attr)
    raise KeyError(slug)


# ---------------------------------------------------------------------------
# Reseed entries — shape validation
# ---------------------------------------------------------------------------


class TestReseedEntry:
    def test_accepts_five_field_and_legacy_four_field(self):
        five = gr.Reseed.from_tuple(("s", 2, "m", "A", "0123456789ab"))
        assert five.graph_signature == "0123456789ab"
        four = gr.Reseed.from_tuple(("s", 2, "m", "A"))
        assert four.graph_signature is None

    @pytest.mark.parametrize(
        "raw",
        [
            ("s", 2, "m", "A", "not-hex-at-all"),
            ("s", 2, "m", "A", "0123456789AB"),  # uppercase
            ("s", 0, "m", "A", "0123456789ab"),  # version < 1
            ("s", True, "m", "A", "0123456789ab"),  # bool is not a version
            ("", 2, "m", "A", "0123456789ab"),
            ("s", 2, "m"),  # too short
            "not a tuple",
        ],
    )
    def test_rejects_malformed(self, raw):
        with pytest.raises(ValueError):
            gr.Reseed.from_tuple(raw)


# ---------------------------------------------------------------------------
# Declaration scanner — AST, never import/exec
# ---------------------------------------------------------------------------


class TestDeclaredReseeds:
    def test_reads_literal_and_orders_by_migration_name(self, tmp_path):
        (tmp_path / "20260101_000000_b.py").write_text(
            '_RESEEDS = (("g", 2, "m", "A", "0123456789ab"),)\nasync def up(pool): ...\n'
        )
        (tmp_path / "20250101_000000_a.py").write_text(
            '_RESEEDS = (("g", 1, "m", "A"),)\nasync def up(pool): ...\n'
        )
        (tmp_path / "20240101_000000_none.py").write_text("async def up(pool): ...\n")
        decl = gr.declared_reseeds(tmp_path)
        assert [d.migration for d in decl["g"]] == [
            "20250101_000000_a.py",
            "20260101_000000_b.py",
        ]
        assert decl["g"][-1].reseed.graph_signature == "0123456789ab"

    def test_never_executes_the_migration(self, tmp_path):
        marker = tmp_path / "executed"
        (tmp_path / "20260101_000000_x.py").write_text(
            f'open({str(marker)!r}, "w").write("ran")\n'
            '_RESEEDS = (("g", 1, "m", "A", "0123456789ab"),)\n'
        )
        gr.declared_reseeds(tmp_path)
        assert not marker.exists()

    def test_non_literal_declaration_raises(self, tmp_path):
        (tmp_path / "20260101_000000_x.py").write_text("_RESEEDS = build()\n")
        with pytest.raises(ValueError):
            gr.declared_reseeds(tmp_path)

    def test_malformed_entry_names_the_file(self, tmp_path):
        (tmp_path / "20260101_000000_x.py").write_text('_RESEEDS = (("g", 1, "m", "A", "zz"),)\n')
        with pytest.raises(ValueError, match="20260101_000000_x.py"):
            gr.declared_reseeds(tmp_path)


class TestWritesGraphDef:
    def test_detects_the_update_shape_case_insensitively(self):
        assert gr.writes_graph_def("UPDATE pipeline_templates SET graph_def = $1::jsonb")
        assert gr.writes_graph_def("update pipeline_templates set graph_def=$1")
        assert not gr.writes_graph_def("SELECT graph_def FROM pipeline_templates")


# ---------------------------------------------------------------------------
# apply_reseeds — the shared up() body
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self):
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()

    def acquire(self):
        return self.conn


_ENTRY = (
    "seo_refresh",
    99,
    "poindexter.services.seo_refresh_spec",
    "SEO_REFRESH_GRAPH_DEF",
    "0123456789ab",
)


@pytest.mark.asyncio
class TestApplyReseeds:
    async def test_writes_raw_spec_at_version_then_restamps(self, monkeypatch):
        calls: list = []

        async def fake_restamp(pool):
            calls.append(pool)
            return 1

        monkeypatch.setattr(pa, "ensure_active_graph_defs_stamped", fake_restamp)
        pool = _FakePool()

        written = await gr.apply_reseeds(pool, [_ENTRY], log_prefix="t")

        assert written == 1
        ((sql, args),) = pool.conn.executed
        assert "UPDATE pipeline_templates" in sql and "active = true" in sql
        spec, version, slug = args
        assert (version, slug) == (99, "seo_refresh")
        nodes = json.loads(spec)["nodes"]
        assert nodes and all("_contract_fp" not in n for n in nodes), "must write the RAW spec"
        assert calls == [pool], "restamp runs once, on the same pool"

    async def test_registry_import_error_defers_to_boot_self_heal(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_architect(name, *a, **kw):
            if name == "poindexter.services.pipeline_architect":
                raise ImportError("no registry env")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", no_architect)
        pool = _FakePool()
        written = await gr.apply_reseeds(pool, [_ENTRY], log_prefix="t")
        assert written == 1 and len(pool.conn.executed) == 1  # row written, stamping deferred


# ---------------------------------------------------------------------------
# THE gate — live registry vs. the newest declared reseed per active graph
# ---------------------------------------------------------------------------


class TestReseedGate:
    def test_active_specs_match_the_freshness_gate_list(self):
        """One list of active specs, two gates: keep them identical so a new
        graph_def cannot be covered by one and missed by the other."""
        from tests.unit.services.test_graph_def_contract_freshness import _active_specs

        assert {s for s, _m, _a in gr.ACTIVE_SPECS} == set(_active_specs())
        for slug, module, attr in gr.ACTIVE_SPECS:
            assert getattr(importlib.import_module(module), attr)["name"] == slug

    def test_newest_reseed_declares_the_live_signature(self):
        """A changed atom contract or graph topology moves the live signature;
        the only way back to green is a NEW migration declaring the new one."""
        discover()
        declared = gr.declared_reseeds(MIGRATIONS_DIR)
        problems = []
        for slug, _module, _attr in gr.ACTIVE_SPECS:
            live = gr.expected_graph_signature(_active_spec(slug))
            entries = declared.get(slug) or []
            newest = entries[-1] if entries else None
            if newest is None or newest.reseed.graph_signature is None:
                problems.append(f"{slug}: no migration declares a graph signature (live {live})")
            elif newest.reseed.graph_signature != live:
                problems.append(
                    f"{slug}: live signature {live} but the newest reseed "
                    f"{newest.migration} declares {newest.reseed.graph_signature}"
                )
        assert not problems, (
            "An active graph_def changed (atom contract or node/edge) without the "
            "migration that carries it to prod's pipeline_templates row — the stored "
            "row would fail assert_graph_def_current at load and halt that lane "
            "(#1876, stack#3928). Write a NEW reseed migration (never edit a merged "
            f"one) declaring the live signature — see {DOC}; print signatures with "
            "REGEN_GRAPH_DEF_FP=1 pytest …::test__print_graph_signatures -s. Also "
            "refresh graph_def_contract_fingerprints.json.\n  - " + "\n  - ".join(problems)
        )

    def test_versions_strictly_increase_per_slug(self):
        """A copy-pasted version is a reseed that reads as a no-op in
        pipeline_templates.version — catch it at PR time."""
        declared = gr.declared_reseeds(MIGRATIONS_DIR)
        # Non-vacuous: every active graph has at least one declared reseed
        # (the bootstrap), so the loop below always checks something.
        assert {s for s, _m, _a in gr.ACTIVE_SPECS} <= set(declared), sorted(declared)
        for slug, entries in declared.items():
            versions = [d.reseed.version for d in entries]
            assert versions == sorted(versions) and len(set(versions)) == len(versions), (
                f"{slug}: reseed versions must strictly increase in migration order, "
                f"got {[(d.migration[:15], d.reseed.version) for d in entries]}"
            )

    def test_every_graph_def_write_after_the_bootstrap_declares_reseeds(self):
        """The convention going forward: any migration newer than the bootstrap
        that writes pipeline_templates.graph_def must declare 5-field _RESEEDS
        for what it writes — an inline UPDATE would slip past the signature gate."""
        assert (MIGRATIONS_DIR / BOOTSTRAP).exists()
        offenders = []
        for path in sorted(MIGRATIONS_DIR.glob("2*.py")):
            if path.name <= BOOTSTRAP:
                continue
            source = path.read_text(encoding="utf-8")
            calls_helper = "apply_reseeds(" in source
            if not (gr.writes_graph_def(source) or calls_helper):
                continue
            literal = gr._reseeds_literal(source)
            entries = [gr.Reseed.from_tuple(r) for r in (literal or ())]
            if not entries or any(e.graph_signature is None for e in entries):
                offenders.append(path.name)
        assert not offenders, (
            f"migrations writing graph_def without signature-declaring _RESEEDS: {offenders} — see {DOC}"
        )

    def test_simulated_contract_change_trips_the_gate(self, monkeypatch):
        """Not tautological: drift qa.audio's contract and the live signature of
        both graphs that reference it moves away from what the newest reseed
        declares."""
        discover()
        real = pa.get_atom_meta("qa.audio")
        if real is None:
            pytest.skip("qa.audio not in registry in this environment")
        from dataclasses import replace

        original = pa.get_atom_meta
        drifted = replace(real, requires=real.requires + ("__synthetic_drift__",))
        monkeypatch.setattr(
            pa, "get_atom_meta", lambda n: drifted if n == "qa.audio" else original(n)
        )
        declared = gr.declared_reseeds(MIGRATIONS_DIR)
        for slug in ("media_pipeline", "podcast_pipeline"):
            live = gr.expected_graph_signature(_active_spec(slug))
            assert live != declared[slug][-1].reseed.graph_signature

    def test_bootstrap_declares_every_active_graph(self):
        declared = gr.declared_reseeds(MIGRATIONS_DIR)
        boot = {
            d.reseed.slug
            for entries in declared.values()
            for d in entries
            if d.migration == BOOTSTRAP
        }
        assert boot == {s for s, _m, _a in gr.ACTIVE_SPECS}


def _meta(name: str) -> AtomMeta:
    return AtomMeta(
        name=name,
        type="atom",
        version="1.0.0",
        description="d",
        requires=(),
        produces=(),
        inputs=(),
        outputs=(),
    )


def test_expected_graph_signature_moves_on_contract_and_topology(monkeypatch):
    table = {"a.x": _meta("a.x"), "a.y": _meta("a.y")}
    monkeypatch.setattr(pa, "get_atom_meta", lambda n: table.get(n))
    spec = {
        "name": "t",
        "nodes": [{"id": "n1", "atom": "a.x"}, {"id": "n2", "atom": "a.y"}],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    base = gr.expected_graph_signature(spec)
    assert base == gr.expected_graph_signature(spec)  # stable
    table["a.y"] = AtomMeta(
        name="a.y",
        type="atom",
        version="1.0.0",
        description="d",
        requires=("k",),
        produces=(),
        inputs=(),
        outputs=(),
    )
    assert gr.expected_graph_signature(spec) != base  # contract moved
    table["a.y"] = _meta("a.y")
    spec["edges"].append({"from": "n2", "to": "END"})
    assert gr.expected_graph_signature(spec) != base  # topology moved


@pytest.mark.skipif(
    os.environ.get("REGEN_GRAPH_DEF_FP") != "1",
    reason="set REGEN_GRAPH_DEF_FP=1 to print the current graph signatures for a new reseed migration",
)
def test__print_graph_signatures():
    """Dev-only: print a ready-to-paste ``_RESEEDS`` block with the live
    signatures and each slug's next version (newest declared + 1)."""
    discover()
    declared = gr.declared_reseeds(MIGRATIONS_DIR)
    print("\n_RESEEDS = (")
    for slug, module, attr in gr.ACTIVE_SPECS:
        entries = declared.get(slug) or []
        nxt = (entries[-1].reseed.version + 1) if entries else 1
        sig = gr.expected_graph_signature(_active_spec(slug))
        print(f'    ("{slug}", {nxt}, "{module}", "{attr}", "{sig}"),')
    print(")")

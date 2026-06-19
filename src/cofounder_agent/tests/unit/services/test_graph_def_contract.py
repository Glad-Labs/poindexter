"""graph_def contract stamping + drift gate (poindexter#755)."""
import pytest

from plugins.atom import AtomMeta, FieldSpec
import services.pipeline_architect as pa


def _meta(
    name: str,
    *,
    requires: tuple[str, ...] = (),
    produces: tuple[str, ...] = (),
    inputs: tuple[FieldSpec, ...] = (),
    outputs: tuple[FieldSpec, ...] = (),
    description: str = "d",
) -> AtomMeta:
    return AtomMeta(
        name=name,
        type="atom",
        version="1.0.0",
        description=description,
        requires=requires,
        produces=produces,
        inputs=inputs,
        outputs=outputs,
    )


@pytest.fixture
def registry(monkeypatch):
    table = {
        "atoms.draft": _meta("atoms.draft", produces=("draft",)),
        "atoms.title": _meta("atoms.title", requires=("draft",), produces=("title",)),
    }
    monkeypatch.setattr(pa, "get_atom_meta", lambda n: table.get(n))
    return table


def _spec():
    return {
        "name": "t",
        "nodes": [
            {"id": "a", "atom": "atoms.draft", "config": {}},
            {"id": "b", "atom": "atoms.title", "config": {}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
    }


class TestStamp:
    def test_stamps_every_node(self, registry):
        out = pa.stamp_graph_def(_spec())
        for n in out["nodes"]:
            assert n["_contract_fp"] and n["_atom_version"] == "1.0.0"

    def test_does_not_mutate_input(self, registry):
        spec = _spec()
        pa.stamp_graph_def(spec)
        assert "_contract_fp" not in spec["nodes"][0]

    def test_unknown_atom_raises(self, registry):
        spec = _spec()
        spec["nodes"][0]["atom"] = "atoms.nope"
        with pytest.raises(pa.GraphContractError):
            pa.stamp_graph_def(spec)


class TestAssertCurrent:
    def test_passes_when_current(self, registry):
        pa.assert_graph_def_current(pa.stamp_graph_def(_spec()))

    def test_unstamped_node_raises(self, registry):
        with pytest.raises(pa.GraphContractError, match="re-seed"):
            pa.assert_graph_def_current(_spec())

    def test_drift_raises_with_atom_name(self, registry):
        stamped = pa.stamp_graph_def(_spec())
        registry["atoms.title"] = _meta(
            "atoms.title", requires=("draft", "outline"), produces=("title",)
        )
        with pytest.raises(pa.GraphContractError, match="atoms.title"):
            pa.assert_graph_def_current(stamped)

    def test_missing_atom_raises(self, registry):
        stamped = pa.stamp_graph_def(_spec())
        del registry["atoms.title"]
        with pytest.raises(pa.GraphContractError, match="atoms.title"):
            pa.assert_graph_def_current(stamped)


class TestGraphSignature:
    def test_stable(self, registry):
        s = pa.stamp_graph_def(_spec())
        assert pa.graph_signature(s) == pa.graph_signature(pa.stamp_graph_def(_spec()))

    def test_changes_when_node_fp_changes(self, registry):
        s = pa.stamp_graph_def(_spec())
        s["nodes"][0]["_contract_fp"] = "deadbeefcafe"
        assert pa.graph_signature(s) != pa.graph_signature(pa.stamp_graph_def(_spec()))


class TestCacheTemplateStamps:
    @pytest.mark.asyncio
    async def test_cache_template_persists_stamped_spec(self, registry):
        captured: dict = {}

        class _Conn:
            async def execute(self, sql, *args):
                captured["args"] = args

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _Pool:
            def acquire(self):
                return _Conn()

        spec = _spec()
        spec["name"] = "architect_made"
        await pa.cache_template(_Pool(), spec)
        import json as _j

        payload = next(
            a for a in captured["args"] if isinstance(a, str) and "_contract_fp" in a
        )
        assert "_contract_fp" in _j.loads(payload)["nodes"][0]

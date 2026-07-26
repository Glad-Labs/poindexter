"""graph_def contract stamping + drift gate (poindexter#755)."""
import pytest

import services.pipeline_architect as pa
from plugins.atom import AtomMeta, FieldSpec


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


class TestAssertCurrentEmptyRegistry:
    """2026-07-03 (task ba4d627a): a Prefect subprocess with an EMPTY atom
    registry (transient discovery failure) tripped the drift gate with
    'no longer exists in the registry' for EVERY node — a fake drift that
    permanently failed the task. An empty registry is an infra fault, not
    contract drift: it must raise the distinct AtomRegistryUnavailableError
    so the flow can release the task for retry instead of failing it."""

    def test_empty_registry_raises_unavailable_not_drift(self, registry, monkeypatch):
        from services.atom_registry import AtomRegistryUnavailableError

        stamped = pa.stamp_graph_def(_spec())
        monkeypatch.setattr(pa, "registry_is_empty", lambda: True)
        with pytest.raises(AtomRegistryUnavailableError):
            pa.assert_graph_def_current(stamped)

    def test_unavailable_is_not_a_contract_error(self):
        from services.atom_registry import AtomRegistryUnavailableError

        assert not issubclass(AtomRegistryUnavailableError, pa.GraphContractError)

    def test_nodeless_spec_does_not_probe_registry(self, monkeypatch):
        # A spec with no nodes has nothing to validate — must not raise even
        # when the registry is empty.
        monkeypatch.setattr(pa, "registry_is_empty", lambda: True)
        pa.assert_graph_def_current({"name": "empty", "nodes": []})

    def test_populated_registry_keeps_normal_drift_semantics(self, registry, monkeypatch):
        # With a populated registry, a genuinely-missing atom still reports
        # as contract drift (GraphContractError), not as unavailable.
        monkeypatch.setattr(pa, "registry_is_empty", lambda: False)
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

"""End-to-end contract handshake against the real atom registry (poindexter#755).

Exercises stamp_graph_def + assert_graph_def_current with genuine AtomMeta
instances (not synthetic fakes): a stamped graph passes, and a contract change
to one of its atoms makes the load gate refuse. Skips if the registry can't be
discovered in this environment (no atoms importable) so it never goes flaky.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

import services.pipeline_architect as pa
from services.atom_registry import discover, list_atoms


def _first_real_atom():
    discover()
    atoms = list_atoms()
    return atoms[0] if atoms else None


def test_stamped_passes_then_drift_refuses(monkeypatch):
    atom = _first_real_atom()
    if atom is None:
        pytest.skip("atom registry yielded no atoms in this environment")
    assert atom is not None  # narrowed for the type checker (skip raises above)

    spec = {
        "name": "drift_probe",
        "nodes": [{"id": "n0", "atom": atom.name, "config": {}}],
        "edges": [{"from": "n0", "to": "END"}],
    }

    stamped = pa.stamp_graph_def(spec)
    # Current registry → passes cleanly.
    pa.assert_graph_def_current(stamped)

    # Simulate an I/O-contract change to that atom and confirm the gate trips.
    drifted_meta = replace(atom, requires=atom.requires + ("__synthetic_drift__",))
    monkeypatch.setattr(
        pa, "get_atom_meta", lambda n: drifted_meta if n == atom.name else None
    )
    with pytest.raises(pa.GraphContractError, match=atom.name):
        pa.assert_graph_def_current(stamped)

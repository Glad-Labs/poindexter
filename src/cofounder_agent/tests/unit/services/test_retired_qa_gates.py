"""The three retired rails must stay retired until an atom actually wires them.

`guardrails_brand`, `guardrails_competitor` and `url_verifier` read as enabled
for months while producing 0 reviews — the #355 atom cutover left them behind
and every config surface still said "on". These assertions make the *claim* and
the *wiring* fail together, so the next person to flip a gate row has to add
the atom too.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

RETIRED = ("guardrails_brand", "guardrails_competitor", "url_verifier")


def _repo_root() -> pathlib.Path:
    import services
    return pathlib.Path(services.__file__).resolve().parents[2]  # src/cofounder_agent


def test_no_atom_wires_a_retired_rail():
    """The premise of the retirement: nothing on disk runs these."""
    atoms = _repo_root() / "poindexter" / "modules" / "content" / "atoms"
    names = {p.stem for p in atoms.glob("*.py")}
    assert "qa_guardrails" not in names
    assert not [n for n in names if "url_verifier" in n]


def test_retired_rails_are_absent_from_the_canonical_blog_graph():
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    atoms = {n["atom"] for n in CANONICAL_BLOG_GRAPH_DEF["nodes"]}
    for bad in ("qa.guardrails", "qa.url_verifier"):
        assert bad not in atoms


def test_the_master_switch_ships_off():
    """`guardrails_enabled=true` with no consumer is the lie one level up."""
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["guardrails_enabled"] == "false"


def test_baseline_seeds_the_retired_gates_disabled():
    """A fresh install must not inherit the enabled-but-inert state."""
    seeds = (_repo_root() / "poindexter" / "services" / "migrations" / "0000_baseline.seeds.sql").read_text()
    for name in RETIRED:
        line = next(
            (l for l in seeds.splitlines()
             if f"'{name}'" in l and "INSERT INTO qa_gates" in l),
            None,
        )
        assert line, f"no baseline qa_gates seed for {name}"
        # VALUES (... reviewer, required_to_pass, enabled, ...) — both false.
        assert ", false, false," in line, f"{name} is still seeded enabled: {line[:160]}"


def test_the_module_no_longer_claims_a_graph_node():
    """The docstring named `qa.guardrails` — a node #730 deleted. That claim is
    how the dormancy stayed invisible."""
    src = (_repo_root() / "poindexter" / "services" / "guardrails_rails.py").read_text()
    assert "RETIRED" in src
    assert not re.search(r"run as the ``qa\.guardrails`` atom", src)


def test_the_architecture_doc_marks_them_retired():
    doc = (_repo_root().parent.parent / "docs" / "architecture"
           / "anti-hallucination.md")
    if not doc.exists():          # stripped from the public mirror tree
        pytest.skip("anti-hallucination.md not present in this tree")
    body = doc.read_text()
    for name in RETIRED:
        assert f"`{name}` **(RETIRED)**" in body, f"{name} still reads as live"

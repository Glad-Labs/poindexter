"""Tests for the requires/produces reachability check in _validate_spec
(Glad-Labs/poindexter#355 atom-cutover Plan 1)."""

from unittest.mock import patch

from plugins.atom import AtomMeta
from services import pipeline_architect


def _meta(name, *, requires=(), produces=()):
    return AtomMeta(
        name=name, type="atom", version="1.0.0", description=name,
        requires=tuple(requires), produces=tuple(produces),
    )


def _spec(nodes, edges, *, entry=None):
    return {"name": "t", "entry": entry or nodes[0]["id"], "nodes": nodes, "edges": edges}


def _fake_get_atom_meta(catalog):
    return lambda atom: catalog.get(atom)


def test_unsatisfied_requires_fails():
    catalog = {"a": _meta("a"), "b": _meta("b", requires=("x",))}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}],
        [{"from": "na", "to": "nb"}, {"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("nb" in e and "x" in e for e in errors), errors


def test_requires_satisfied_by_upstream_produces():
    catalog = {"a": _meta("a", produces=("x",)), "b": _meta("b", requires=("x",))}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}],
        [{"from": "na", "to": "nb"}, {"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors


def test_requires_satisfied_by_config():
    catalog = {"b": _meta("b", requires=("x",))}
    spec = _spec(
        [{"id": "nb", "atom": "b", "config": {"x": 1}}],
        [{"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors


def test_requires_satisfied_by_seed_state():
    catalog = {"b": _meta("b", requires=("task_id",))}
    spec = _spec(
        [{"id": "nb", "atom": "b"}],
        [{"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys={"task_id"})
    assert ok is True, errors


def test_default_seed_keys_come_from_pipeline_state():
    catalog = {"b": _meta("b", requires=("task_id",))}
    spec = _spec(
        [{"id": "nb", "atom": "b"}],
        [{"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec)
    assert ok is True, errors


import pytest


@pytest.mark.asyncio
async def test_wrap_atom_observes_node_duration_seconds():
    """_wrap_atom must observe NODE_DURATION_SECONDS so atom durations appear
    in the Pipeline dashboard (poindexter#652 regression guard).

    The histogram is labeled by (node, outcome). Both the success and error
    branches must call .labels(...).observe(elapsed_seconds).
    """
    from unittest.mock import MagicMock, patch

    import services.template_runner as _tr
    from services.pipeline_architect import _wrap_atom

    mock_histogram = MagicMock()

    async def _fast_atom(state):
        return {"out_key": "done"}

    with patch.object(_tr, "NODE_DURATION_SECONDS", mock_histogram):
        node_fn = _wrap_atom(_fast_atom, "atoms.test_atom", "node_ok", record_sink=None)
        await node_fn({}, None)

    mock_histogram.labels.assert_called_once()
    call_kwargs = mock_histogram.labels.call_args
    assert call_kwargs.kwargs.get("node") == "atoms.test_atom"
    assert call_kwargs.kwargs.get("outcome") in ("ok", "halted")
    mock_histogram.labels.return_value.observe.assert_called_once()
    elapsed = mock_histogram.labels.return_value.observe.call_args.args[0]
    assert elapsed >= 0


@pytest.mark.asyncio
async def test_wrap_atom_observes_error_outcome():
    """Exceptions from the atom fn must emit outcome='error' to NODE_DURATION_SECONDS."""
    from unittest.mock import MagicMock, patch

    import services.template_runner as _tr
    from services.pipeline_architect import _wrap_atom

    mock_histogram = MagicMock()

    async def _failing_atom(state):
        raise ValueError("test failure")

    with patch.object(_tr, "NODE_DURATION_SECONDS", mock_histogram):
        node_fn = _wrap_atom(_failing_atom, "atoms.fail_atom", "node_err", record_sink=None)
        result = await node_fn({}, None)

    assert result.get("_halt") is True
    mock_histogram.labels.assert_called_once_with(node="atoms.fail_atom", outcome="error")
    mock_histogram.labels.return_value.observe.assert_called_once()


def test_real_registered_atoms_validate_with_defaults():
    """A spec of real registered atoms whose requires are seed/config/upstream
    satisfied must pass with default seed_keys — the new check must not break
    the architect's compose() path."""
    from services.atom_registry import discover
    from services.atom_registry import get_atom_meta as real_get

    discover()  # idempotent
    gate = real_get("atoms.approval_gate")
    assert gate is not None, "approval_gate atom must be registered"
    spec = {
        "name": "gate_only",
        "entry": "g",
        "nodes": [{"id": "g", "atom": "atoms.approval_gate", "config": {"gate_name": "preview"}}],
        "edges": [{"from": "g", "to": "END"}],
    }
    ok, errors = pipeline_architect._validate_spec(spec)
    assert ok is True, errors


# ---------------------------------------------------------------------------
# QA rescue cycle: loop-flagged back-edges are exempt from DAG validation,
# while unflagged accidental cycles still fail loud.
# ---------------------------------------------------------------------------


def test_loop_flagged_back_edge_validates():
    # a -> b -> c, with c -> a flagged "loop": the designated rescue cycle.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c")}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na", "loop": True},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors


def test_unflagged_back_edge_still_errors():
    # Same shape but WITHOUT the loop flag — an accidental cycle must fail loud.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c")}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na"},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("cycle" in e.lower() for e in errors), errors


def test_loop_edge_does_not_drop_downstream_require_check():
    # The loop edge must not inflate the loopback target's indegree and silently
    # drop the whole chain from the requires-reachability pass. nc requires "k"
    # which nothing produces -> the check must still fire and error on nc.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c", requires=("k",))}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na", "loop": True},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("nc" in e and "k" in e for e in errors), errors


# ---------------------------------------------------------------------------
# The architect system prompt names atoms in prose (COMPOSITION HEURISTICS).
# Those names must track the live registry or compose() grounds the LLM on
# atoms that build_graph_from_spec then rejects. #355 renamed the QA atoms and
# #2278 deleted atoms.review_with_critic, but the prompt lagged — this guard
# makes the next such drift fail loud in CI instead of in a live architect run.
# ---------------------------------------------------------------------------


def test_architect_prompt_references_only_live_atoms():
    """Every atom the architect system prompt names must exist in the registry.

    Two complementary checks, applied to BOTH copies of the prompt — the inline
    fallback constant (the last-resort text served when the prompt registry is
    unreachable) and the SKILL.md source of truth (what _resolve_system_prompt
    serves on the happy path):

      (1) every *namespaced* atom token (atoms./qa./stage./content./seo./media./
          podcast./social.) must resolve — namespaces are derived from the live
          catalog so the check can't rot; and
      (2) the pre-#355 *bare* names (aggregate_reviews / review_with_critic) must
          be gone — they carry no namespace prefix, so check (1) can't see them.
    """
    import re

    from services import atom_registry
    from services.prompt_manager import UnifiedPromptManager

    atom_registry.discover()  # idempotent
    live = atom_registry.list_atoms()
    namespaces = sorted({a.name.split(".", 1)[0] for a in live if "." in a.name})
    assert namespaces, "atom registry surfaced no namespaced atoms"

    # A literal "qa.*" / "atoms.*" wildcard in the prose is not a reference:
    # \w+ won't match "*", so those are skipped by construction.
    pattern = r"\b(?:" + "|".join(map(re.escape, namespaces)) + r")\.\w+"

    sources = {
        "inline_fallback": pipeline_architect._ARCHITECT_SYSTEM_PROMPT_FALLBACK,
        "skill_md": UnifiedPromptManager().prompts[
            "atoms.pipeline_architect.system_prompt"
        ]["template"],
    }
    for label, prompt in sources.items():
        referenced = set(re.findall(pattern, prompt))
        missing = sorted(n for n in referenced if atom_registry.get_atom_meta(n) is None)
        assert not missing, f"{label}: architect prompt names nonexistent atom(s): {missing}"
        for dead in ("aggregate_reviews", "review_with_critic"):
            assert dead not in prompt, (
                f"{label}: architect prompt still references renamed/deleted atom {dead!r}"
            )


def test_resolve_system_prompt_renders_site_name_registry_up():
    """The resolved architect prompt injects the brand from site_config.

    Regression guard for the double-brace fallback bug (#2284 follow-up):
    ``_resolve_system_prompt`` used to call ``get_prompt`` with no kwargs, so
    the required ``{site_name}`` var raised KeyError and every call fell back to
    the inline constant — whose ``{{site_name}}`` then rendered as a *literal*
    ``{site_name}`` in compose(). The registry-up path must serve the SKILL.md
    copy with the real brand substituted and no placeholder or escaped brace
    left behind.
    """
    from services.site_config import SiteConfig

    sc = SiteConfig(
        initial_config={"site_name": "Glad Labs", "site_url": "https://gladlabs.io"}
    )
    rendered = pipeline_architect._resolve_system_prompt(sc)

    assert "Glad Labs" in rendered, "brand was not injected into the architect prompt"
    assert "{site_name}" not in rendered, "left a literal {site_name} placeholder"
    assert "{{" not in rendered, "JSON-schema braces were not rendered to single braces"
    # The JSON schema block must survive as valid single-brace text.
    assert '"name":' in rendered


def test_resolve_system_prompt_renders_site_name_registry_down():
    """Same brand-render guarantee on the inline-fallback path.

    When the prompt registry is unreachable the resolver renders the inline
    fallback constant itself (single ``.format`` pass), so it must produce the
    same fully-rendered shape — brand present, no literal ``{site_name}``, JSON
    braces collapsed — never the raw ``{{site_name}}`` / ``{{`` template.
    """
    from services.site_config import SiteConfig

    sc = SiteConfig(
        initial_config={"site_name": "Glad Labs", "site_url": "https://gladlabs.io"}
    )
    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("registry down"),
    ):
        rendered = pipeline_architect._resolve_system_prompt(sc)

    assert "Glad Labs" in rendered, "brand was not injected into the fallback prompt"
    assert "{site_name}" not in rendered, "left a literal {site_name} placeholder"
    assert "{{" not in rendered, "fallback JSON-schema braces were not rendered"
    assert '"name":' in rendered


def test_validate_rejects_template_placeholders_in_config() -> None:
    """First live media plan wrote config task_id='${task_id}' — there is
    no substitution engine, so the literal would shadow the real state
    value. The validator turns it into a FIX retry signal."""
    spec = {
        "name": "podcast_plan",
        "nodes": [
            {"id": "s", "atom": "atoms.set_task_status",
             "config": {"task_id": "${task_id}",
                        "target_status": "in_progress"}},
        ],
        "edges": [],
    }
    ok, errors = pipeline_architect._validate_spec(spec)
    assert not ok
    assert any("template syntax" in e and "'s'" in e for e in errors)


def test_validate_placeholder_scan_reaches_nested_config() -> None:
    spec = {
        "name": "p",
        "nodes": [
            {"id": "n", "atom": "atoms.set_task_status",
             "config": {"opts": {"list": ["ok", "${post_id}"]}}},
        ],
        "edges": [],
    }
    ok, errors = pipeline_architect._validate_spec(spec)
    assert not ok and any("template syntax" in e for e in errors)

"""Tests for the requires/produces reachability check in _validate_spec
(Glad-Labs/poindexter#355 atom-cutover Plan 1)."""

from unittest.mock import patch

from poindexter.plugins.atom import AtomMeta
from poindexter.services import pipeline_architect


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

    import poindexter.services.template_runner as _tr
    from poindexter.services.pipeline_architect import _wrap_atom

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

    import poindexter.services.template_runner as _tr
    from poindexter.services.pipeline_architect import _wrap_atom

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
    from poindexter.services.atom_registry import discover
    from poindexter.services.atom_registry import get_atom_meta as real_get

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

    from poindexter.services import atom_registry
    from poindexter.services.prompt_manager import UnifiedPromptManager

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
    from poindexter.services.site_config import SiteConfig

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
    from poindexter.services.site_config import SiteConfig

    sc = SiteConfig(
        initial_config={"site_name": "Glad Labs", "site_url": "https://gladlabs.io"}
    )
    with patch(
        "poindexter.services.prompt_manager.get_prompt_manager",
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


# --- version suffix tolerance (live compose failure, 2026-09-15) ------------


@pytest.mark.parametrize(
    "written, bare",
    [
        ("a v1.0.0", "a"),
        ("a@1.0.0", "a"),
        ("a v2", "a"),
        ("a  v1.2.3 ", "a"),
    ],
)
def test_versioned_atom_reference_validates_and_is_rewritten(written, bare):
    """The catalog header reads "<name> v<version>" and rule 1 says "exactly as
    it appears", so the model copies the version. It is not part of the name:
    the spec must validate and carry the bare name for compilation."""
    catalog = {"a": _meta("a")}
    spec = _spec([{"id": "na", "atom": written}], [{"from": "na", "to": "END"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors
    assert spec["nodes"][0]["atom"] == bare


def test_digit_in_atom_name_is_not_a_version():
    assert pipeline_architect._strip_atom_version("qa.self_claim2") == "qa.self_claim2"
    assert pipeline_architect._strip_atom_version("image.flux.2") == "image.flux.2"


def test_versioned_unknown_atom_still_fails_with_the_bare_name_in_the_hint():
    catalog = {"a": _meta("a")}
    spec = _spec([{"id": "nb", "atom": "zzz v1.0.0"}], [{"from": "nb", "to": "END"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)), \
         patch.object(pipeline_architect, "list_atoms", lambda: [_meta("a")]):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("'zzz'" in e and "not in catalog" in e for e in errors), errors


# --- structural faults a requires/produces walk cannot see (2026-09-15) -----


def _meta_par(name, parallelizable, *, requires=(), produces=()):
    return AtomMeta(
        name=name, type="atom", version="1.0.0", description=name,
        requires=tuple(requires), produces=tuple(produces),
        parallelizable=parallelizable,
    )


class TestConcurrentFanOutOntoAnExclusiveNode:
    """Live failure, plan task 59151278: the architect wired
    transcribe_narration to BOTH video renders. LangGraph runs sibling
    branches concurrently in one process, both reached for the exclusive
    gpu.lock('video'), and the loser waited out the 900s timeout and raised
    GpuLockTimeoutError — the task wedged in_progress."""

    def _catalog(self):
        return {
            "a": _meta_par("a", True, produces=("x",)),
            "long": _meta_par("long", False, requires=("x",)),
            "short": _meta_par("short", False, requires=("x",)),
            "ok_sibling": _meta_par("ok_sibling", True, requires=("x",)),
        }

    def test_two_exclusive_siblings_are_rejected(self):
        spec = _spec(
            [{"id": "n1", "atom": "a"}, {"id": "nl", "atom": "long"}, {"id": "ns", "atom": "short"}],
            [{"from": "n1", "to": "nl"}, {"from": "n1", "to": "ns"},
             {"from": "nl", "to": "END"}, {"from": "ns", "to": "END"}],
            entry="n1",
        )
        with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(self._catalog())):
            ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
        assert ok is False
        assert any("cannot run concurrently" in e for e in errors), errors
        assert any("Chain them instead" in e for e in errors), errors

    def test_an_exclusive_node_beside_a_parallel_one_is_still_rejected(self):
        """The exclusive node is the one that breaks — it cannot tolerate ANY
        sibling, however well-behaved."""
        spec = _spec(
            [{"id": "n1", "atom": "a"}, {"id": "nl", "atom": "long"}, {"id": "nk", "atom": "ok_sibling"}],
            [{"from": "n1", "to": "nl"}, {"from": "n1", "to": "nk"},
             {"from": "nl", "to": "END"}, {"from": "nk", "to": "END"}],
            entry="n1",
        )
        with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(self._catalog())):
            ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
        assert ok is False
        assert any("'nl'" in e and "cannot run concurrently" in e for e in errors), errors

    def test_parallelizable_siblings_are_fine(self):
        catalog = dict(self._catalog())
        catalog["p1"] = _meta_par("p1", True, requires=("x",))
        catalog["p2"] = _meta_par("p2", True, requires=("x",))
        spec = _spec(
            [{"id": "n1", "atom": "a"}, {"id": "q1", "atom": "p1"}, {"id": "q2", "atom": "p2"}],
            [{"from": "n1", "to": "q1"}, {"from": "n1", "to": "q2"},
             {"from": "q1", "to": "END"}, {"from": "q2", "to": "END"}],
            entry="n1",
        )
        with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
            ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
        assert ok is True, errors

    def test_a_branch_fan_out_is_not_concurrency(self):
        """The QA rescue cycle and the preview_gate regen paths fan out on
        branch/loop edges — conditional routes, never concurrent ones."""
        spec = _spec(
            [{"id": "n1", "atom": "a"}, {"id": "nl", "atom": "long"}, {"id": "ns", "atom": "short"}],
            [{"from": "n1", "to": "nl"},
             {"from": "n1", "to": "ns", "branch": True},
             {"from": "nl", "to": "END"}, {"from": "ns", "to": "END"}],
            entry="n1",
        )
        with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(self._catalog())):
            ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
        assert ok is True, errors


class TestUnreachableNode:
    """Same live spec hung ensure_terminal_status off qa.audio, which had NO
    inbound edge — so the terminal status could never be written and the task
    sat in_progress until a sweep reclaimed it."""

    def test_a_node_nothing_reaches_is_rejected(self):
        catalog = {"a": _meta_par("a", True, produces=("x",)), "b": _meta_par("b", True)}
        spec = _spec(
            [{"id": "n1", "atom": "a"}, {"id": "orphan", "atom": "b"}, {"id": "term", "atom": "b"}],
            [{"from": "n1", "to": "END"}, {"from": "orphan", "to": "term"}],
            entry="n1",
        )
        with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
            ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
        assert ok is False
        assert any("'orphan'" in e and "nothing reaches it" in e for e in errors), errors
        assert any("'term'" in e for e in errors), errors

    def test_a_fully_reachable_chain_passes(self):
        catalog = {"a": _meta_par("a", True, produces=("x",)), "b": _meta_par("b", True, requires=("x",))}
        spec = _spec(
            [{"id": "n1", "atom": "a"}, {"id": "n2", "atom": "b"}],
            [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "END"}],
            entry="n1",
        )
        with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
            ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
        assert ok is True, errors


def test_the_shipped_render_atoms_declare_they_are_exclusive():
    """AtomMeta.parallelizable means 'safe to run concurrently with siblings'.
    Both video renders hold the exclusive gpu.lock('video'), so both must say
    False — the True they shipped with is what let the architect fan them out."""
    from poindexter.modules.content.atoms import (
        media_render_long_video,
        media_render_short_video,
    )

    assert media_render_long_video.ATOM_META.parallelizable is False
    assert media_render_short_video.ATOM_META.parallelizable is False


class TestExistingPostIsReadOnly:
    """poindexter#1056 — asked for eleven media atoms to render a video for a
    published post, the architect returned a graph that also rewrote the draft
    and SEO metadata and ran content.republish_post. The validator now refuses
    any post-writing atom in a composed plan built on content.load_existing_post,
    keyed on the atoms' declared side_effects."""

    _MEDIA = [
        "content.load_existing_post", "stage.generate_media_scripts",
        "stage.generate_video_shot_list", "media.render_narration",
        "media.persist", "atoms.approval_gate",
    ]

    def _chain(self, atoms):
        from poindexter.services.atom_registry import discover

        discover()
        nodes = [{"id": f"n{i}", "atom": a} for i, a in enumerate(atoms)]
        edges = [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(len(atoms) - 1)]
        edges.append({"from": nodes[-1]["id"], "to": "END"})
        return _spec(nodes, edges)

    def _post_write_errors(self, atoms):
        _ok, errors = pipeline_architect._validate_spec(self._chain(atoms))
        return [e for e in errors if "writes or publishes a post" in e]

    def test_the_measured_rewrite_and_republish_plan_is_refused(self):
        bad = self._MEDIA[:1] + ["stage.generate_content", "stage.generate_seo_metadata"] \
            + self._MEDIA[1:] + ["content.republish_post", "atoms.set_task_status"]
        errors = self._post_write_errors(bad)
        assert len(errors) == 1
        assert "content.republish_post" in errors[0]
        # The FIX tells the model to remove the node, never how to unlock it.
        assert "remove it" in errors[0]

    def test_the_requested_media_plan_passes_this_rule(self):
        assert self._post_write_errors(self._MEDIA) == []

    def test_persist_and_auto_publish_are_refused_too(self):
        errors = self._post_write_errors(
            self._MEDIA + ["content.persist_task", "content.evaluate_auto_publish"]
        )
        assert len(errors) == 2

    def test_a_new_post_plan_may_still_persist(self):
        """Without load_existing_post there is no live post to protect."""
        assert self._post_write_errors(
            ["stage.verify_task", "content.generate_draft", "content.persist_task"]
        ) == []

    def test_the_post_writing_atoms_declare_it(self):
        from poindexter.services.atom_registry import discover, get_atom_meta

        discover()
        for name in ("content.republish_post", "content.persist_task",
                     "content.evaluate_auto_publish"):
            assert pipeline_architect.POST_WRITE_EFFECT in get_atom_meta(name).side_effects, name
        assert pipeline_architect.POST_WRITE_EFFECT not in (
            get_atom_meta("content.load_existing_post").side_effects
        )

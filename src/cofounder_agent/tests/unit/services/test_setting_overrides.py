"""Plan- and step-scoped setting overrides (services/setting_overrides.py).

"Make Kai calmer in this one" must change this run and nothing else: the
override is a view over the settings, never a write; only creative keys are
open to a plan; a plan's override reaches every step and a step's beats it;
and a reload through the view still reaches the real settings (casting
reloads so later steps see the personas it made).
"""

from __future__ import annotations

import pytest

from poindexter.services import setting_overrides as so
from poindexter.services.site_config import ScopedSiteConfig, SiteConfig


def _sc(**extra):
    base = {
        "persona.kai.voice_exaggeration": "0.75",
        "persona.kai.appearance": "a teen in an orange hoodie",
        "story_portrait_attempts": "3",
        "story_writer_model": "anthropic/x",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


class TestScopedSiteConfig:
    def test_overrides_win_and_everything_else_reads_through(self):
        base = _sc()
        view = base.with_overrides({"persona.kai.voice_exaggeration": 0.5})
        assert view.get("persona.kai.voice_exaggeration") == "0.5"
        assert view.get_float("persona.kai.voice_exaggeration") == 0.5
        assert view.get("story_portrait_attempts") == "3"
        assert view.all()["persona.kai.voice_exaggeration"] == "0.5"
        assert base.get("persona.kai.voice_exaggeration") == "0.75"  # never written
        assert isinstance(view, SiteConfig)

    def test_reads_are_recorded_on_the_base(self):
        base = _sc()
        base.drain_read_keys()
        view = base.with_overrides({"persona.kai.voice_exaggeration": "0.5"})
        view.get("story_portrait_attempts")
        view.require("persona.kai.voice_exaggeration")
        assert sorted(base.drain_read_keys()) == ["persona.kai.voice_exaggeration", "story_portrait_attempts"]

    @pytest.mark.asyncio
    async def test_a_reload_reaches_the_base(self):
        base = _sc()
        seen = []

        async def reload(pool):
            seen.append(pool)
            return 1

        base.reload = reload  # type: ignore[method-assign]  # stand-in for the DB reload
        await base.with_overrides({"x": "1"}).reload("pool")
        assert seen == ["pool"]

    def test_layers_flatten_and_the_inner_layer_wins(self):
        view = _sc().with_overrides({"a": "1", "b": "1"}).with_overrides({"b": "2"})
        assert isinstance(view, ScopedSiteConfig) and not isinstance(view._base, ScopedSiteConfig)
        assert (view.get("a"), view.get("b")) == ("1", "2")

    def test_no_overrides_is_the_same_instance(self):
        base = _sc()
        assert base.with_overrides({}) is base


class TestPolicy:
    def test_creative_keys_are_open_and_models_are_not(self):
        sc = _sc()
        assert so.is_overridable("persona.kai.voice_exaggeration", sc)
        assert so.is_overridable("story_portrait_attempts", sc)
        assert not so.is_overridable("story_writer_model", sc)  # cost
        assert not so.is_overridable("story_cast_voice_dir", sc)  # a path
        assert not so.is_overridable("anthropic_api_key", sc)
        assert not so.is_overridable("site_url", sc)

    def test_the_lists_are_settings(self):
        sc = _sc(architect_setting_overrides_allow="site_*", architect_setting_overrides_deny="")
        assert so.is_overridable("site_url", sc) and not so.is_overridable("story_portrait_attempts", sc)

    def test_check_names_each_problem(self):
        errors = so.check_overrides(
            {"story_writer_model": "x", "story_nope": "1", "story_portrait_attempts": "lots",
             "persona.kai.voice_exaggeration": 0.5},
            _sc(), where="node 'cast'",
        )
        assert len(errors) == 3
        assert any("story_writer_model cannot be overridden" in e for e in errors)
        assert any("story_nope is not a setting" in e for e in errors)
        assert any("story_portrait_attempts must be an integer" in e for e in errors)
        assert all(e.startswith("FIX node 'cast'") for e in errors)

    def test_a_spec_is_checked_at_both_levels(self):
        spec = {"setting_overrides": {"story_writer_model": "x"},
                "nodes": [{"id": "n", "atom": "a", "config": {"setting_overrides": "loud"}}]}
        errors = so.check_spec_overrides(spec, _sc())
        assert [e.split(":")[0] for e in errors] == ["FIX plan", "FIX node 'n'"]

    def test_the_step_wins_over_the_plan(self):
        assert so.merged_overrides({"a": 1, "b": 1}, {"b": 2}) == {"a": "1", "b": "2"}
        assert so.merged_overrides(None, None) == {}

    def test_apply_refuses_what_the_policy_refuses(self):
        view = so.apply_overrides(_sc(), {"story_writer_model": "cheap", "story_portrait_attempts": "1"})
        assert view.get("story_writer_model") == "anthropic/x"
        assert view.get("story_portrait_attempts") == "1"

    def test_the_architect_sees_open_keys_with_current_values(self):
        block = so.overridable_settings_block(_sc())
        assert "- persona.kai.voice_exaggeration = 0.75" in block
        assert "story_writer_model" not in block and "story_portrait_attempts = 3" in block


class TestRunner:
    @pytest.mark.asyncio
    async def test_a_step_runs_with_the_plan_and_step_overrides(self):
        from poindexter.services import pipeline_architect as pa

        seen = {}

        async def atom(state):
            sc = state["site_config"]
            seen["kai"] = sc.get("persona.kai.voice_exaggeration")
            seen["attempts"] = sc.get("story_portrait_attempts")
            seen["leaked"] = "setting_overrides" in state
            return {}

        base = _sc()
        node = pa._wrap_atom(
            atom, "story.cast_characters", "cast", None,
            node_config={"setting_overrides": {"story_portrait_attempts": "1"}},
            setting_overrides=so.merged_overrides(
                {"persona.kai.voice_exaggeration": "0.5", "story_portrait_attempts": "5"},
                {"story_portrait_attempts": "1"},
            ),
        )
        await node({"site_config": base}, {"configurable": {}})
        assert seen == {"kai": "0.5", "attempts": "1", "leaked": False}
        assert base.get("persona.kai.voice_exaggeration") == "0.75"


def test_the_plan_card_lists_every_override():
    spec = {"setting_overrides": {"persona.kai.voice_exaggeration": 0.5},
            "nodes": [{"id": "cast", "atom": "story.cast_characters",
                       "config": {"setting_overrides": {"story_portrait_attempts": "1"}}},
                      {"id": "write", "atom": "story.write_screenplay"}]}
    assert so.describe_overrides(spec) == [
        "every step: persona.kai.voice_exaggeration = 0.5",
        "cast: story_portrait_attempts = 1",
    ]


def test_an_override_that_changes_nothing_is_a_fix():
    """The architect copied Kai's 0.75 and Mira's look unchanged in two of
    three composes (2026-10-13): an override equal to the value in force."""
    errors = so.check_overrides(
        {"persona.kai.voice_exaggeration": 0.75, "persona.kai.appearance": "a teen in an orange hoodie"},
        _sc(), where="plan",
    )
    assert len(errors) == 2 and all("changes nothing" in e for e in errors)
    assert so.check_overrides({"persona.kai.voice_exaggeration": "0.5"}, _sc()) == []


class TestNamedCast:
    @staticmethod
    def _spec(cast, narrator=None):
        config = {"screenplay_cast": cast}
        if narrator:
            config["screenplay_narrator"] = narrator
        return {"nodes": [{"id": "write", "atom": "story.write_screenplay", "config": config}]}

    @staticmethod
    def _roster():
        return _sc(**{
            "persona.kai.display_name": "Kai", "persona.kai.enabled": "true",
            "persona.mira.display_name": "Mira", "persona.mira.enabled": "true",
            "persona.marta.display_name": "Marta", "persona.marta.enabled": "true",
        })

    def _errors(self, spec, intent):
        from poindexter.services.pipeline_architect import _named_cast_errors

        return _named_cast_errors(spec, intent, self._roster())

    def test_the_named_cast_passes(self):
        assert self._errors(self._spec(["kai", "mira"]), "Kai and Mira explain VRAM") == []

    def test_a_swapped_name_is_caught_both_ways(self):
        errors = self._errors(self._spec(["kai", "marta"]), "Kai and Mira explain VRAM")
        assert any('names Mira; add "mira"' in e for e in errors)
        assert any('lists "marta"' in e for e in errors)

    def test_a_named_narrator_counts(self):
        assert self._errors(self._spec(["kai"], narrator="mira"), "Kai explains VRAM; Mira narrates") == []

    def test_no_names_no_rule(self):
        assert self._errors(self._spec(["marta"]), "a cartoon about VRAM") == []

    def test_an_unasked_narrator_is_dropped(self):
        """A cyberpunk fairytale got the shepherd boy as narrator (2026-10-13)."""
        errors = self._errors(self._spec([], narrator="kai"), "a cyberpunk fairytale")
        assert len(errors) == 1 and 'narrator is "kai"' in errors[0]
        assert self._errors(self._spec([], narrator="none"), "a cyberpunk fairytale") == []

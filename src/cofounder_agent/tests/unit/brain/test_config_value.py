"""Unit tests for ``brain/config_value.py``.

The shared parts of "page when a JSON list setting cannot be used as written",
used by ``scheduled_workflow_watch`` and ``data_freshness_probe``. Each probe's
own suite pins its pages end to end; this file pins the helpers' contract,
above all that ``digest`` is stable. A digest that changed across a deploy
would give every open config episode a new signature, and every one would
page again as "changed".
"""

from __future__ import annotations

import json

import pytest

from poindexter.brain import config_value as cv

_REF = "app_settings.some_list"


@pytest.mark.unit
class TestLoadList:
    def test_a_list_comes_back_as_it_parsed(self):
        assert cv.load_list('[1, {"a": "b"}]', _REF) == [1, {"a": "b"}]
        assert cv.load_list("[]", _REF) == []

    def test_invalid_json_names_where_it_broke(self):
        problem = cv.load_list('[{"a": 1} {"b": 2}]', _REF)

        assert isinstance(problem, cv.ValueProblem)
        assert problem.kind == "invalid-json:char-10"
        assert problem.summary == f"{_REF} is not valid JSON (Expecting ',' delimiter at line 1, column 11)"
        assert problem.problem == (
            f"{_REF} is not valid JSON: Expecting ',' delimiter at line 1, column 11, "
            f'near `[{{"a": 1}} {{"b": 2}}]`.'
        )
        assert problem.hint == ""

    @pytest.mark.parametrize("raw, kind, noun_hint", [
        ('{"a": 1}', "object", "It must be a list even for one feed: wrap the object in [ ]."),
        (json.dumps(json.dumps([1])), "string",
         "The string itself holds a JSON list, so the value was encoded twice: "
         "store the list, not a string that contains it."),
        ('"just text"', "string", ""),
        ("3", "number", ""),
        ("2.5", "number", ""),
        ("false", "boolean", ""),
        ("null", "null", ""),
    ])
    def test_json_that_is_not_a_list_says_what_it_is(self, raw, kind, noun_hint):
        problem = cv.load_list(raw, _REF, noun="feed")

        assert isinstance(problem, cv.ValueProblem)
        assert problem.kind == f"not-a-list:{kind}"
        assert problem.summary == f"{_REF} holds a JSON {kind}, not a list"
        assert problem.hint == noun_hint

    @pytest.mark.parametrize("raw, kind", [
        ("[" + "1" * 5000 + "]", "invalid-json:ValueError"),
        ("[" * 100_000 + "]" * 100_000, "invalid-json:RecursionError"),
    ], ids=["huge-integer", "deep-nesting"])
    def test_what_json_loads_refuses_is_a_problem_not_an_exception(self, raw, kind):
        problem = cv.load_list(raw, _REF)

        assert isinstance(problem, cv.ValueProblem)
        assert problem.kind == kind
        assert problem.problem.startswith(f"{_REF} cannot be read as JSON: ")
        assert len(problem.summary) < 250


@pytest.mark.unit
class TestRendering:
    def test_show_quotes_the_value_and_clips_it(self):
        assert cv.show("abc") == '"abc"'
        assert cv.show(None) == "null"
        assert cv.show(float("inf")) == "Infinity"
        assert cv.show("é") == '"é"'
        long = cv.show("x" * 100, limit=20)
        assert len(long) == 20 and long.endswith("…")

    def test_excerpt_centres_on_the_error_on_one_line(self):
        raw = "[\n" + " " * 40 + '{"a": 1}\n  {"b": 2},\n' + " " * 40 + '{"c": 3}\n]'
        pos = raw.index('{"b"')

        excerpt = cv.excerpt(raw, pos, width=10)

        assert "\n" not in excerpt
        assert excerpt.startswith("…") and excerpt.endswith("…")
        assert '{"b"' in excerpt

    def test_excerpt_swaps_backticks_so_the_page_markup_survives(self):
        assert "`" not in cv.excerpt("[`oops`]", 1)


@pytest.mark.unit
class TestDigest:
    def test_the_digest_is_pinned(self):
        """Pinned to a literal on purpose: if this changes, every open config
        episode gets a new signature at deploy and pages again as "changed"."""
        ignored = [cv.Ignored(3, "workflow", "entry 3: ...", {"repo": "a/b", "workflow": "x"})]

        assert cv.digest(ignored) == "4e3a3fda"

    def test_the_digest_follows_what_is_wrong_not_the_wording(self):
        entry = {"repo": "a/b", "workflow": "x"}
        a = cv.digest([cv.Ignored(3, "workflow", "one wording", entry)])

        assert cv.digest([cv.Ignored(3, "workflow", "another wording", entry)]) == a
        assert cv.digest([cv.Ignored(4, "workflow", "one wording", entry)]) != a
        assert cv.digest([cv.Ignored(3, "repo", "one wording", entry)]) != a
        assert cv.digest([cv.Ignored(3, "workflow", "one wording", {**entry, "workflow": "y"})]) != a

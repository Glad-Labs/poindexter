"""Per-niche topic scope (Glad-Labs/poindexter#1127).

Covers the scope block, the batched LLM scope check (including its fail-open
paths), the sweep's scope filter, the NICHE_DEPTH subject anchor, the scope
reaching the ranking prompt, row mapping, and the CLI argument helpers.
"""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import click
import pytest

from poindexter.cli.topics import _merge_scope, _parse_weight_pairs
from poindexter.services import topic_ranking, topic_scope
from poindexter.services.niche_service import Niche, NicheGoal, _row_to_niche
from poindexter.services.site_config import SiteConfig
from poindexter.services.topic_batch_service import TopicBatchService
from poindexter.services.topic_scope import ScopeItem, ScopeResult, check_scope, scope_block

_async = pytest.mark.asyncio(loop_scope="session")

SUBJECT = "AI and computer hardware"


def _niche(subject: str | None = SUBJECT, exclusions=(), scope_filter: bool = True) -> Niche:
    return Niche(
        id=uuid4(), slug="hw", name="HW", active=True, target_audience_tags=[],
        writer_prompt_override=None, batch_size=5, discovery_cadence_minute_floor=60,
        topic_subject=subject, topic_exclusions=tuple(exclusions),
        topic_scope_filter=scope_filter,
    )


# --- scope_block ---------------------------------------------------------------


def test_scope_block_is_empty_without_a_subject():
    assert scope_block(_niche(subject=None)) == ""
    assert scope_block(_niche(subject="   ")) == ""
    assert scope_block(None) == ""


def test_scope_block_names_subject_and_exclusions():
    block = scope_block(_niche(exclusions=("video game news", "startup marketing")))
    assert block.startswith(f"In scope: {SUBJECT}")
    assert "video game news; startup marketing" in block


# --- check_scope ---------------------------------------------------------------


def _fake_chat(reply: str | Exception, calls: list):
    async def fake(prompt, *, model, site_config):
        calls.append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply
    return fake


@_async
async def test_check_scope_sorts_verdicts_and_keeps_skipped_ids(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        topic_ranking, "_ollama_chat_json",
        _fake_chat(json.dumps({"a": True, "b": False, "c": "false"}), calls),
    )
    items = [ScopeItem("a", "llama.cpp vs vLLM"), ScopeItem("b", "Market research"),
             ScopeItem("c", "Game news"), ScopeItem("d", "Skipped by the model")]
    result = await check_scope(items, _niche(), site_config=SiteConfig(), model="m")
    assert result.in_scope == {"a"}
    assert result.out_of_scope == {"b", "c"}
    assert result.unjudged == {"d"}
    # A skipped id is kept, and the result says why (the finding body prints it).
    assert result.errors == ["no verdict for 1 of 4 candidate(s) in a chunk"]
    assert SUBJECT in calls[0] and "[a] llama.cpp vs vLLM" in calls[0]


# The reply qwen3-vl gave for the last chunk of every glad-labs sweep from
# 2026-10-06 on: keyed by the candidate LINE, not the id. Read verbatim, no key
# was an id, so the whole chunk went unjudged with an empty error list.
_LINE_KEYED_REPLY = {
    "[i9] Re-ranker Improvement": True,
    "[i10] Re-ranker Update Impact": True,
    "[i15] Dynamic Learning Through Collaboration": False,
    "[i16] GPU Selection Challenges": True,
    "[i18] Data Mining for Insights": False,
}


@_async
async def test_check_scope_reads_a_reply_keyed_by_candidate_line(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        topic_ranking, "_ollama_chat_json", _fake_chat(json.dumps(_LINE_KEYED_REPLY), calls),
    )
    items = [ScopeItem("i9", "Re-ranker Improvement"), ScopeItem("i10", "Re-ranker Update Impact"),
             ScopeItem("i15", "Dynamic Learning Through Collaboration"),
             ScopeItem("i16", "GPU Selection Challenges"), ScopeItem("i18", "Data Mining for Insights")]
    result = await check_scope(items, _niche(), site_config=SiteConfig(), model="m")
    assert result.in_scope == {"i9", "i10", "i16"}
    assert result.out_of_scope == {"i15", "i18"}
    assert result.unjudged == set() and result.errors == []


@pytest.mark.parametrize("key", ["[i9] Re-ranker Improvement", "[i9]", "i9: Re-ranker", " [ i9 ] x"])
def test_a_wrapped_key_resolves_to_its_id(key):
    assert topic_scope._parse_verdicts(json.dumps({key: True}), {"i9", "i10"}) == {"i9": True}


@_async
async def test_a_wrapped_key_for_another_chunks_id_is_not_adopted(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        topic_ranking, "_ollama_chat_json",
        _fake_chat(json.dumps({"[e3] Some other title": True}), calls),
    )
    result = await check_scope(
        [ScopeItem("i9", "Re-ranker Improvement")], _niche(), site_config=SiteConfig(), model="m",
    )
    assert result.unjudged == {"i9"} and result.in_scope == set()
    assert "[e3] Some other title" in result.errors[0]


def test_the_prompt_shows_the_bare_id_key():
    from poindexter.services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        topic_scope.PROMPT_KEY, scope_block="In scope: x", cand_block="[e0] a",
    )
    assert '"e0": true' in prompt and "without its brackets" in prompt


@_async
async def test_check_scope_fails_open_on_a_bad_reply(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(topic_ranking, "_ollama_chat_json", _fake_chat("not json", calls))
    items = [ScopeItem("a", "x"), ScopeItem("b", "y")]
    result = await check_scope(items, _niche(), site_config=SiteConfig(), model="m")
    assert result.unjudged == {"a", "b"}
    assert result.out_of_scope == set()
    assert len(result.errors) == 1


@_async
async def test_check_scope_chunks_by_setting(monkeypatch):
    calls: list[str] = []

    async def fake(prompt, *, model, site_config):
        calls.append(prompt)
        ids = [line[1:line.index("]")] for line in prompt.splitlines() if line.startswith("[")]
        return json.dumps(dict.fromkeys(ids, True))

    monkeypatch.setattr(topic_ranking, "_ollama_chat_json", fake)
    sc = SiteConfig(initial_config={"niche_topic_scope_check_chunk_size": "2"})
    items = [ScopeItem(str(i), f"t{i}") for i in range(5)]
    result = await check_scope(items, _niche(), site_config=sc, model="m")
    assert len(calls) == 3
    assert result.in_scope == {"0", "1", "2", "3", "4"}


@_async
async def test_check_scope_does_nothing_without_a_subject(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(topic_ranking, "_ollama_chat_json", _fake_chat("{}", calls))
    result = await check_scope(
        [ScopeItem("a", "x")], _niche(subject=None), site_config=SiteConfig(), model="m",
    )
    assert calls == [] and result == ScopeResult()


# --- the sweep's scope filter ----------------------------------------------------


def _ext(title: str) -> dict:
    return {"kind": "external", "data": {"id": title, "title": title, "summary": ""}}


@_async
async def test_filter_drops_out_of_scope_and_reports(monkeypatch):
    async def fake_check(items, niche, *, site_config, model=None):
        return ScopeResult(in_scope={"e0"}, out_of_scope={"e1"})

    monkeypatch.setattr(topic_scope, "check_scope", fake_check)
    svc = TopicBatchService(None, site_config=SiteConfig())
    with patch("poindexter.services.topic_batch_service.emit_finding") as emit:
        ext, internal = await svc._apply_topic_scope(
            _niche(), [_ext("llama.cpp vs vLLM"), _ext("Market research")], [],
        )
    assert [e["data"]["title"] for e in ext] == ["llama.cpp vs vLLM"]
    assert internal == []
    kinds = [c.kwargs["kind"] for c in emit.call_args_list]
    assert kinds == ["topic_scope_filtered"]


@_async
async def test_filter_keeps_unjudged_and_says_so(monkeypatch):
    async def fake_check(items, niche, *, site_config, model=None):
        return ScopeResult(unjudged={"e0", "e1"}, errors=["TimeoutError: slow"])

    monkeypatch.setattr(topic_scope, "check_scope", fake_check)
    svc = TopicBatchService(None, site_config=SiteConfig())
    with patch("poindexter.services.topic_batch_service.emit_finding") as emit:
        ext, _ = await svc._apply_topic_scope(_niche(), [_ext("a"), _ext("b")], [])
    assert len(ext) == 2
    assert [c.kwargs["kind"] for c in emit.call_args_list] == ["topic_scope_check_failed"]


@pytest.mark.parametrize("niche", [_niche(subject=None), _niche(scope_filter=False)])
@_async
async def test_filter_is_a_no_op_without_subject_or_with_filter_off(monkeypatch, niche):
    async def boom(*a, **k):
        raise AssertionError("scope check must not run")

    monkeypatch.setattr(topic_scope, "check_scope", boom)
    svc = TopicBatchService(None, site_config=SiteConfig())
    items = [_ext("a")]
    assert await svc._apply_topic_scope(niche, items, []) == (items, [])


# --- NICHE_DEPTH anchor + ranking prompt -----------------------------------------


@_async
async def test_niche_depth_is_anchored_on_the_subject(monkeypatch):
    embedded: list[str] = []

    async def fake_embed(text, *, site_config):
        embedded.append(text)
        return [float(len(text))]

    monkeypatch.setattr(topic_ranking, "_embed_text_cached", fake_embed)
    monkeypatch.setattr(topic_ranking, "_GOAL_VEC_CACHE", {})
    monkeypatch.setattr(topic_ranking, "_SUBJECT_VEC_CACHE", {})
    goals = [NicheGoal("NICHE_DEPTH", 50), NicheGoal("TRAFFIC", 50)]
    vecs = await topic_ranking.goal_vectors_for_niche(goals, _niche(), site_config=SiteConfig())
    assert vecs["NICHE_DEPTH"] == [float(len(SUBJECT))]
    assert SUBJECT in embedded

    embedded.clear()
    plain = await topic_ranking.goal_vectors_for_niche(
        goals, _niche(subject=None), site_config=SiteConfig(),
    )
    assert SUBJECT not in embedded
    assert plain["NICHE_DEPTH"] != [float(len(SUBJECT))]


@_async
async def test_ranking_prompt_carries_the_scope(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(topic_ranking, "_ollama_chat_json", _fake_chat('{"c1": 70}', calls))
    cand = topic_ranking.ScoredCandidate(
        id="c1", title="llama.cpp vs vLLM", summary="", embedding_score=0.5,
        score_breakdown={},
    )
    await topic_ranking.llm_final_score(
        [cand], [NicheGoal("NICHE_DEPTH", 100)], model="m",
        site_config=SiteConfig(), niche=_niche(exclusions=("video game news",)),
    )
    assert f"In scope: {SUBJECT}" in calls[0]
    assert "video game news" in calls[0]

    calls.clear()
    await topic_ranking.llm_final_score(
        [replace(cand)], [NicheGoal("NICHE_DEPTH", 100)], model="m",
        site_config=SiteConfig(),
    )
    assert "In scope:" not in calls[0]


# --- row mapping --------------------------------------------------------------------


def _row(**extra):
    base = {
        "id": uuid4(), "slug": "hw", "name": "HW", "active": True,
        "target_audience_tags": [], "writer_prompt_override": None, "batch_size": 5,
        "discovery_cadence_minute_floor": 60,
    }
    base.update(extra)
    return base


def test_row_without_scope_columns_maps_to_no_scope():
    n = _row_to_niche(_row())
    assert n.topic_subject is None and n.topic_exclusions == () and n.topic_scope_filter
    assert not n.has_topic_scope


def test_row_with_scope_columns_maps_them():
    n = _row_to_niche(_row(
        topic_subject=SUBJECT, topic_exclusions=["games"], topic_scope_filter=False,
    ))
    assert n.has_topic_scope and n.topic_exclusions == ("games",)
    assert n.topic_scope_filter is False


# --- CLI helpers --------------------------------------------------------------------


def test_parse_weight_pairs():
    assert _parse_weight_pairs(("internal_rag=25", "devto=15:off")) == [
        ("internal_rag", 25, True), ("devto", 15, False),
    ]


@pytest.mark.parametrize("bad", [("x",), ("x=a",), ("x=-1",), ("x=5:maybe",), ("x=1", "x=2"), ()])
def test_parse_weight_pairs_rejects_bad_input(bad):
    with pytest.raises(click.BadParameter):
        _parse_weight_pairs(bad)


def _merge(**kw):
    base = dict(
        current_subject=SUBJECT, current_exclusions=("games", "careers"),
        current_filter=True, subject=None, exclude=(), remove_exclude=(),
        clear_exclusions=False, scope_filter=None, clear=False,
    )
    base.update(kw)
    return _merge_scope(**base)


def test_merge_scope_edits_only_what_it_names():
    assert _merge() == (SUBJECT, ["games", "careers"], True)
    assert _merge(exclude=("politics",)) == (SUBJECT, ["games", "careers", "politics"], True)
    assert _merge(remove_exclude=("GAMES",)) == (SUBJECT, ["careers"], True)
    assert _merge(clear_exclusions=True, exclude=("x",)) == (SUBJECT, ["x"], True)
    assert _merge(scope_filter=False)[2] is False
    assert _merge(subject="New")[0] == "New"
    assert _merge(clear=True) == (None, [], True)


# --- model choice + preview errors (2026-10-01 follow-up) ---------------------


@_async
async def test_check_scope_uses_the_configured_model(monkeypatch):
    seen: list[str] = []

    async def fake(prompt, *, model, site_config):
        seen.append(model)
        return json.dumps({"a": True})

    monkeypatch.setattr(topic_ranking, "_ollama_chat_json", fake)
    sc = SiteConfig(initial_config={"niche_topic_scope_check_model": "ollama/judge:30b"})
    await check_scope([ScopeItem("a", "x")], _niche(), site_config=sc)
    assert seen == ["ollama/judge:30b"]


@_async
async def test_preview_returns_why_candidates_are_unjudged(monkeypatch):
    async def fake_check(items, niche, *, site_config, model=None):
        return ScopeResult(unjudged={"e0"}, errors=["ConnectError: no route"])

    async def fake_read_pool(self, niche):
        return [_ext("a")], [], set()

    niche = _niche()

    class FakeNiches:
        async def get_by_id(self, niche_id):
            return niche

    monkeypatch.setattr(topic_scope, "check_scope", fake_check)
    monkeypatch.setattr(TopicBatchService, "_read_pool", fake_read_pool)
    svc = TopicBatchService(None, site_config=SiteConfig())
    svc._niche_svc = FakeNiches()
    rows, errors = await svc.preview_scope(niche_id=niche.id)
    assert rows == [{"pool": "external", "title": "a", "verdict": "unjudged"}]
    assert errors == ["ConnectError: no route"]

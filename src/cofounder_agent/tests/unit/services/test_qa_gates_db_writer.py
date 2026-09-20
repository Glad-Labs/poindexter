"""Unit tests for ``services.qa_gates_db_writer``.

The writer is the missing half of the qa_gates telemetry contract —
``qa_gates_db.py`` (read) was always there, this file (write) was
discovered to be missing on 2026-05-09 when every gate showed
``last_run_at = NEVER``. These tests pin the contract so the gap can't
silently reappear.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from poindexter.services.qa_gates_db_writer import _REVIEWER_TO_GATE, record_chain_run


# Anchor on a sentinel, not a parents[N] depth: the poindexter#1046 namespace
# move pushed every file a level deeper and a baked-in depth would have quietly
# pointed this guard at the wrong tree.
def _find_migrations_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "poindexter" / "services" / "migrations"
        if candidate.is_dir():
            return candidate
    raise RuntimeError("could not locate poindexter/services/migrations")


_MIGRATIONS_DIR = _find_migrations_dir()


class _Review:
    """Minimal duck-type for ReviewerResult."""

    def __init__(self, reviewer: str, approved: bool = True, advisory: bool = False):
        self.reviewer = reviewer
        self.approved = approved
        self.advisory = advisory


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def transaction(self):
        return self

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        return "UPDATE 1"


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    def acquire(self):
        return _FakeConn(self)


@pytest.mark.asyncio
async def test_pool_none_no_ops():
    """Match the read-side fallback shape: pool=None must not raise."""
    await record_chain_run(None, [_Review("programmatic_validator")])


@pytest.mark.asyncio
async def test_empty_reviews_no_ops():
    pool = _FakePool()
    await record_chain_run(pool, [])
    assert pool.executes == []


@pytest.mark.asyncio
async def test_unknown_reviewer_skipped():
    """Inline reviewers without a qa_gates row must NOT trigger an UPDATE
    — there's no row to bump (``reviewer_to_gate`` returns None and
    ``record_chain_run`` skips them).

    NB: ``rendered_preview`` USED to be the example here, but it was aliased
    to vision_gate in #563 (both vision legs share that row), so it now bumps
    a counter — see test_alias_mapping_rendered_preview_to_vision_gate.
    citation_verifier / topic_delivery likewise moved out when they were
    given gate rows on 2026-06-03 (#659/#658)."""
    pool = _FakePool()
    await record_chain_run(
        pool,
        [
            _Review("some_reviewer_with_no_gate_row"),
            _Review("another_unmapped_reviewer"),
        ],
    )
    assert pool.executes == []


@pytest.mark.asyncio
async def test_known_reviewer_bumps_counter():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("programmatic_validator", approved=True)])
    assert len(pool.executes) == 1
    query, args = pool.executes[0]
    assert "UPDATE qa_gates" in query
    assert "total_runs = total_runs + 1" in query
    assert args == ("programmatic_validator", "passed", 0)


@pytest.mark.asyncio
async def test_rejected_review_increments_rejections():
    pool = _FakePool()
    await record_chain_run(
        pool,
        [
            _Review("programmatic_validator", approved=False),
        ],
    )
    _, args = pool.executes[0]
    assert args == ("programmatic_validator", "rejected", 1)


@pytest.mark.asyncio
async def test_alias_mapping_image_relevance_to_vision_gate():
    """The inline reviewer name 'image_relevance' must update the
    qa_gates row named 'vision_gate'."""
    pool = _FakePool()
    await record_chain_run(pool, [_Review("image_relevance", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "vision_gate"


@pytest.mark.asyncio
async def test_alias_mapping_internal_consistency_to_consistency():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("internal_consistency", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "consistency"


@pytest.mark.asyncio
async def test_alias_mapping_ollama_critic_to_llm_critic():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("ollama_critic", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "llm_critic"


@pytest.mark.asyncio
async def test_alias_mapping_rendered_preview_to_vision_gate():
    """The rendered-preview vision check shares the vision_gate row with
    image_relevance — both are vision rails. Without the alias, a
    preview-only review left vision_gate looking absent and a required
    gate failed closed (Glad-Labs/poindexter#563)."""
    pool = _FakePool()
    await record_chain_run(pool, [_Review("rendered_preview", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "vision_gate"


@pytest.mark.asyncio
async def test_duplicate_reviewer_collapses_to_one_update():
    """url_verifier appends a ReviewerResult on both the dead-link and
    the bonus path. The writer must collapse those into a single
    UPDATE so total_runs doesn't double-count one execution."""
    pool = _FakePool()
    await record_chain_run(
        pool,
        [
            _Review("url_verifier", approved=True),
            _Review("url_verifier", approved=True),
        ],
    )
    assert len(pool.executes) == 1


@pytest.mark.asyncio
async def test_full_chain_writes_one_update_per_gate():
    """End-to-end: a typical chain emits 4-7 reviews; each maps to one
    gate row UPDATE."""
    pool = _FakePool()
    await record_chain_run(
        pool,
        [
            _Review("programmatic_validator", approved=True),
            _Review("ollama_critic", approved=True),
            _Review("internal_consistency", approved=True),
            _Review("web_factcheck", approved=True),
            _Review("url_verifier", approved=True),
            # citation_verifier + topic_delivery now HAVE gate rows (seeded
            # #659/#658 on 2026-06-03) so they bump too:
            _Review("citation_verifier", approved=True),
            _Review("topic_delivery", approved=True),
            # rendered_preview now aliases to vision_gate (#563) — it bumps the
            # vision_gate counter alongside image_relevance:
            _Review("rendered_preview", approved=True),
        ],
    )
    bumped_gates = {args[0] for _, args in pool.executes}
    assert bumped_gates == {
        "programmatic_validator",
        "llm_critic",
        "consistency",
        "web_factcheck",
        "url_verifier",
        "citation_verifier",
        "topic_delivery",
        "vision_gate",
    }


def _seeded_gate_names() -> set[str]:
    """Every ``qa_gates.name`` any in-repo migration or seed file inserts.

    Parsed from the migration tree rather than hand-listed, because a
    hand-listed expectation is exactly what failed eight times (see the test
    below). Handles both insert shapes in use:

        VALUES ('<uuid>', 'programmatic_validator', ...)   -- baseline seeds
        VALUES ($1, 'person_mention', ...)                 -- migration files

    The gate name is the first single-quoted literal after ``VALUES`` that
    looks like an identifier; a UUID contains dashes and so is skipped.
    """
    names: set[str] = set()
    for path in sorted(_MIGRATIONS_DIR.iterdir()):
        if path.suffix not in (".py", ".sql"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for hit in re.finditer(r"INSERT\s+INTO\s+qa_gates\b", text, re.IGNORECASE):
            segment = text[hit.end() : hit.end() + 1200]
            values = re.search(r"\bVALUES\b", segment, re.IGNORECASE)
            if values is None:
                continue
            for literal in re.findall(r"'([^']*)'", segment[values.end() : values.end() + 300]):
                if re.fullmatch(r"[a-z][a-z0-9_]*", literal):
                    names.add(literal)
                    break
    return names


# Gate rows that deliberately have no reviewer feeding them. Keep this EMPTY
# unless a gate genuinely cannot produce a ReviewerResult, and say why.
_GATES_WITHOUT_A_REVIEWER: set[str] = set()


def test_seed_parser_actually_finds_gates():
    """Scan floor. A parser that finds nothing would make the guard below
    vacuously green — which is the failure mode this whole file exists for."""
    seeded = _seeded_gate_names()
    assert len(seeded) >= 20, (
        f"only parsed {len(seeded)} qa_gates names from {_MIGRATIONS_DIR} — the "
        "insert shape changed and this guard is now reading nothing. Fix the "
        "parser; do not relax the assertion."
    )
    # Anchors: one from the baseline seeds, one from a later migration.
    assert "programmatic_validator" in seeded
    assert "person_mention" in seeded


def test_every_seeded_gate_has_a_reviewer_alias():
    """Every gate row seeded in-repo must be the TARGET of an alias.

    This replaces a hand-maintained expectation that could not catch the bug it
    was written for. Its own comments admitted as much twice — "this one
    slipped past this guard too, because the name was never listed here" (the
    fifth recurrence) and "the second to slip past this guard by never being
    listed" (the sixth). A guard whose expected set is typed by the same person
    who forgot the alias will agree with them every time.

    Eight recurrences, all identical: ship a rail, seed its gate row, forget
    ``_REVIEWER_TO_GATE``. The rail then runs normally and scores normally
    while its gate row reads ``total_runs=0 / last_run_at=NEVER`` — which on
    the operator dashboard is indistinguishable from "this rail never ran".
    Found the seventh and eighth on 2026-09-20 by comparing ``atom_runs``
    against ``qa_gates`` on prod: ``qa_numeric_fidelity`` 37 runs / gate 0,
    ``qa_person_mention`` 3 runs / gate 0.

    Deriving the expectation from the seeds means a new gate row fails this
    test the moment it is added, with no list for anyone to forget.
    """
    seeded = _seeded_gate_names()
    aliased_targets = set(_REVIEWER_TO_GATE.values())
    missing = sorted(seeded - aliased_targets - _GATES_WITHOUT_A_REVIEWER)

    assert not missing, (
        "qa_gates rows are seeded but no reviewer alias targets them: "
        f"{missing}\n\n"
        "Their rails will run and score normally while the gate row stays at "
        "total_runs=0 / last_run_at=NEVER, which the operator dashboard shows "
        "as 'never ran'.\n"
        'Fix: add `"<reviewer name>": "<gate name>"` to _REVIEWER_TO_GATE in '
        "services/qa_gates_db_writer.py (the reviewer name is the `reviewer=` "
        "value the rail passes to ReviewerResult). If the gate genuinely has no "
        "reviewer, add it to _GATES_WITHOUT_A_REVIEWER above with a reason."
    )


def test_alias_targets_are_all_real_gate_rows():
    """The reverse direction: an alias pointing at a gate row that no migration
    seeds would silently write to nothing."""
    seeded = _seeded_gate_names()
    # url_verifier / guardrails_* were retired 2026-09-10 but their rows remain
    # seeded in-repo, so they still parse; nothing here should be unseeded.
    phantom = sorted(set(_REVIEWER_TO_GATE.values()) - seeded)
    assert not phantom, (
        f"_REVIEWER_TO_GATE targets gate rows nothing seeds: {phantom} — "
        "these UPDATEs match no row and are silently discarded."
    )


@pytest.mark.asyncio
async def test_new_oss_rails_bump_their_gate_counters():
    """Regression test for the 2026-05-27 silent-skip discovery: the
    deepeval/guardrails/ragas reviewers were producing ReviewerResults
    on every QA pass but record_chain_run was silently dropping them
    because their names weren't in _REVIEWER_TO_GATE. Pin the wiring
    so the bug can't reappear."""
    pool = _FakePool()
    await record_chain_run(
        pool,
        [
            _Review("deepeval_g_eval", approved=True, advisory=True),
            _Review("deepeval_faithfulness", approved=True, advisory=True),
            _Review("deepeval_brand_fabrication", approved=True, advisory=True),
            _Review("guardrails_brand", approved=True, advisory=True),
            _Review("guardrails_competitor", approved=True, advisory=True),
            _Review("ragas_eval", approved=True, advisory=True),
        ],
    )
    bumped_gates = {args[0] for _, args in pool.executes}
    assert bumped_gates == {
        "deepeval_g_eval",
        "deepeval_faithfulness",
        "deepeval_brand_fabrication",
        "guardrails_brand",
        "guardrails_competitor",
        "ragas_eval",
    }


@pytest.mark.asyncio
async def test_restored_rail_gates_bump_their_counters():
    """Regression for the 2026-06-11 alias-drop recurrence (the THIRD).

    The citation_verifier / topic_delivery / self_consistency rails were
    restored/added as qa.* atoms (#659 / #658 / #621) and seeded their own
    qa_gates rows, but their reviewer names were never added to
    _REVIEWER_TO_GATE — so record_chain_run silently dropped the counter
    and `poindexter qa-gates list` showed total_runs=0 while audit_log
    proved 97 / 49 / 24 real runs. Pin the wiring so it can't regress."""
    pool = _FakePool()
    await record_chain_run(
        pool,
        [
            _Review("citation_verifier", approved=True, advisory=True),
            _Review("topic_delivery", approved=True, advisory=True),
            _Review("self_consistency", approved=True, advisory=False),
            # poindexter#765 — the new advisory unlinked-attribution rail seeds its
            # own gate row and must bump its counter too.
            _Review("unlinked_attribution", approved=True, advisory=True),
            # poindexter#765 follow-up — the grounded-LLM citation_grounding rail
            # is advisory-by-construction (approved=False when it fires) and must
            # bump its own counter, not silently drop to total_runs=0.
            _Review("citation_grounding", approved=False, advisory=True),
        ],
    )
    bumped_gates = {args[0] for _, args in pool.executes}
    assert bumped_gates == {
        "citation_verifier",
        "topic_delivery",
        "self_consistency",
        "unlinked_attribution",
        "citation_grounding",
    }


@pytest.mark.asyncio
async def test_accepts_dict_shaped_reviews():
    """qa.aggregate (the graph_def QA path since #355) carries the rail
    reviews as ``reviewer_to_dict()`` dicts on the ``qa_rail_reviews``
    channel, NOT as ``ReviewerResult`` objects. The writer must read its
    fields from dicts as well as attributes — otherwise ``getattr`` returns
    the default for every dict, no gate matches, and ``total_runs`` stays
    frozen at 0 on the prod path (poindexter#553). Pin both shapes so a
    future serializer change can't silently re-break the counter."""
    pool = _FakePool()
    await record_chain_run(
        pool,
        [
            {
                "reviewer": "ollama_critic",
                "approved": True,
                "advisory": False,
                "score": 90.0,
                "provider": "ollama",
            },
            {
                "reviewer": "ragas_eval",
                "approved": False,
                "advisory": True,
                "score": 40.0,
                "provider": "ollama",
            },
        ],
    )
    bumped = {args[0]: tuple(args[1:]) for _, args in pool.executes}
    # ollama_critic aliases to the llm_critic gate row.
    assert bumped["llm_critic"] == ("passed", 0)
    # A failing rail (even advisory) records a rejection on its own counter.
    assert bumped["ragas_eval"] == ("rejected", 1)

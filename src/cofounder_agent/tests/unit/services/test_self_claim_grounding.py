"""services.self_claim_grounding — ground first-person claims in our own records.

The sentences here are from real October 2026 drafts. Two were invented and
reached the approval queue ("we used Properties → Installed Files → Move
Install Folder", "the benchmarks said no"); others were true and must pass.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from poindexter.services import self_claim_grounding as g
from poindexter.services.site_config import SiteConfig

_async = pytest.mark.asyncio(loop_scope="session")


# --- extraction -------------------------------------------------------------------


@pytest.mark.parametrize("sentence", [
    "We moved it to a slower PCIe 4 NVMe and noticed nothing.",
    "When we looked at moving to vLLM, the benchmarks said no.",
    "We found 14 files bypassing our LiteLLM dispatcher.",
    "We spent a morning chasing why regen-image needed three attempts.",
])
def test_reports_of_what_we_did_are_extracted(sentence):
    assert [c.sentence for c in g.extract_experiential_claims(sentence)] == [sentence]


@pytest.mark.parametrize("sentence", [
    "If we upgrade the drive, the games load faster.",            # hypothetical
    "Let's look at the numbers.",                                   # reader-inclusive
    "We run Ollama on a single desktop.",                           # standing fact, no event
    "We wrote up the 27B variant separately.",                      # pointer at our own post
    "The drive moved to the top of the chart.",                     # no first person
    "The Register said US model makers trained on 12 billion pages.",  # US, the country
    'A company argued "our tool changed nothing about 3 markets" in court.',  # quoted speech
])
def test_advice_standing_facts_and_post_pointers_are_not(sentence):
    assert g.extract_experiential_claims(sentence) == []


def test_the_previous_sentence_rides_along_as_context():
    claims = g.extract_experiential_claims(
        "Steam's move tool copies, then deletes. We did exactly that with a 680 GiB library.",
    )
    assert claims[0].context == "Steam's move tool copies, then deletes."


def test_markdown_noise_is_stripped_before_splitting():
    md = (
        "## Heading we moved\n\n<img src='x' alt='we moved'/>\n\n"
        "We [switched](https://example.com) to the PCIe 4 drive."
    )
    assert [c.sentence for c in g.extract_experiential_claims(md)] == [
        "We switched to the PCIe 4 drive.",
    ]


@pytest.mark.parametrize("sentence", [
    "We'd built the engine and forgotten to bolt it to the car.",
    "That's why we picked it first.",
])
def test_rhetoric_with_nothing_a_record_could_hold_is_skipped(sentence):
    assert g.extract_experiential_claims(sentence) == []


# --- echo filter --------------------------------------------------------------------


def test_a_review_session_quoting_the_claim_is_an_echo():
    claim = "We moved it to a slower PCIe 4 NVMe and noticed nothing."
    snippet = (
        "ASSISTANT: line 27 says 'We moved it to a slower PCIe 4 NVMe and noticed "
        "nothing' and there is no record of that."
    )
    assert g.is_echo(claim, snippet)


def test_a_record_of_the_event_is_not_an_echo():
    claim = "We moved it to a slower PCIe 4 NVMe and noticed nothing."
    snippet = "reclaim p6 as a games volume and move SteamLibrary to it; Steam lands on a PCIe4 NVMe"
    assert not g.is_echo(claim, snippet)


# --- quote check ----------------------------------------------------------------------


def _ev(ref: str, text: str) -> g.Evidence:
    return g.Evidence(ref=ref, created_at=datetime(2026, 9, 15), excerpt=text, rrf=0.03)


def test_a_quote_must_be_in_the_cited_record():
    evidence = [_ev("claude_sessions:a", "Steam copy at 61 / 682 GiB, roughly an hour to go"),
                _ev("memory:b", "vLLM rejected: wrong tool for a serialized pipeline")]
    assert g.quote_holds("61 / 682 GiB", "claude_sessions:a", evidence)
    assert g.quote_holds("61 / 682 GiB", "[claude_sessions:a]", evidence)  # brackets tolerated
    assert not g.quote_holds("61 / 682 GiB", "memory:b", evidence)        # wrong record
    assert not g.quote_holds("the benchmarks said no", "memory:b", evidence)
    assert not g.quote_holds("vLLM", "memory:b", evidence)               # too short to mean anything


def _fake_judge(monkeypatch, reply: dict):
    from poindexter.services import topic_ranking

    async def fake(prompt, *, model, pool=None, site_config):
        return json.dumps(reply)

    monkeypatch.setattr(topic_ranking, "_ollama_chat_json", fake)


@_async
async def test_a_supported_verdict_without_a_real_quote_becomes_no_evidence(monkeypatch):
    # First prototype run: "the benchmarks said no" marked supported by a git-log record.
    _fake_judge(monkeypatch, {"verdict": "supported", "record": "claude_sessions:a",
                              "quote": "the benchmarks said no"})
    claim = g.Claim(sentence="When we looked at moving to vLLM, the benchmarks said no.", context="")
    out = await g.judge_claim(
        claim, [_ev("claude_sessions:a", "git log --oneline -15")],
        site_config=SiteConfig(), model="m", prompt_template="{context}{claim}{evidence}",
    )
    assert out.verdict == "no_evidence" and out.judge_verdict == "supported"


@_async
async def test_a_supported_verdict_with_its_quote_stands(monkeypatch):
    _fake_judge(monkeypatch, {"verdict": "supported", "record": "memory:x",
                              "quote": "found 14 files that bypassed the dispatcher"})
    claim = g.Claim(sentence="We found 14 files bypassing our LiteLLM dispatcher.", context="")
    out = await g.judge_claim(
        claim, [_ev("memory:x", "Audit: found 14 files that bypassed the dispatcher; three-PR plan")],
        site_config=SiteConfig(), model="m", prompt_template="{context}{claim}{evidence}",
    )
    assert out.verdict == "supported" and out.judge_verdict == ""


@_async
async def test_vague_and_no_evidence_need_no_quote(monkeypatch):
    for verdict in ("vague", "no_evidence"):
        _fake_judge(monkeypatch, {"verdict": verdict})
        out = await g.judge_claim(
            g.Claim(sentence="We saw the same thing.", context=""), [],
            site_config=SiteConfig(), model="m", prompt_template="{context}{claim}{evidence}",
        )
        assert out.verdict == verdict


@_async
async def test_a_broken_reply_is_an_error_not_a_raise(monkeypatch):
    from poindexter.services import topic_ranking

    async def boom(prompt, *, model, pool=None, site_config):
        return "not json"

    monkeypatch.setattr(topic_ranking, "_ollama_chat_json", boom)
    out = await g.judge_claim(
        g.Claim(sentence="We moved it.", context=""), [],
        site_config=SiteConfig(), model="m", prompt_template="{context}{claim}{evidence}",
    )
    assert out.verdict == "error"


def test_posts_are_never_evidence():
    assert "posts" not in g.DEFAULT_EVIDENCE_TABLES

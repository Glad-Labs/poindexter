"""Unit tests for content.llm_reconcile_citations (grounded-LLM citation pass, #765).

Two layers, both DB-free / LLM-free where possible:
- the pure verify-and-apply core (apply_verified_links / candidate_corpus_sources) —
  the safety-critical deterministic half; the LLM never touches prose;
- the atom run() with the dispatcher, gate lookup, and findings stubbed.
"""

from __future__ import annotations

from modules.content.atoms._citation_match import parse_corpus
from modules.content.atoms.content_llm_reconcile_citations import (
    apply_verified_links,
    candidate_corpus_sources,
)

CORPUS = parse_corpus(
    "- [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot): a\n"
    "- [Topular Strategy](https://www.topularstrategy.com/blog/x): b\n"
)
URLS = {
    "https://www.fullbrimsafety.com/p/autopilot",
    "https://www.topularstrategy.com/blog/x",
}


# --- apply_verified_links (safety core) -------------------------------------

def test_apply_links_verbatim_span():
    body = "what Full Brim Safety calls low-power mode."
    pairs = [{"text": "Full Brim Safety", "url": "https://www.fullbrimsafety.com/p/autopilot"}]
    new, applied = apply_verified_links(body, pairs, URLS)
    assert "[Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot)" in new
    assert applied == [
        {"text": "Full Brim Safety", "url": "https://www.fullbrimsafety.com/p/autopilot"}
    ]


def test_apply_drops_hallucinated_url():
    body = "what Full Brim Safety calls low-power mode."
    pairs = [{"text": "Full Brim Safety", "url": "https://evil.example.com/made-up"}]
    new, applied = apply_verified_links(body, pairs, URLS)
    assert new == body and applied == []


def test_apply_drops_non_verbatim_span():
    body = "what Full Brim Safety calls low-power mode."
    pairs = [{"text": "Full Brim Safety Inc", "url": "https://www.fullbrimsafety.com/p/autopilot"}]
    new, applied = apply_verified_links(body, pairs, URLS)
    assert new == body and applied == []


def test_apply_skips_already_linked():
    body = "what [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot) calls it."
    pairs = [{"text": "Full Brim Safety", "url": "https://www.fullbrimsafety.com/p/autopilot"}]
    new, applied = apply_verified_links(body, pairs, URLS)
    assert new == body and applied == []  # idempotent


# --- candidate_corpus_sources (cost gate) -----------------------------------

def test_candidate_gate_detects_unlinked_source():
    body = "what Full Brim Safety calls low-power mode."  # brand present, url absent
    assert [s.url for s in candidate_corpus_sources(body, CORPUS)] == [
        "https://www.fullbrimsafety.com/p/autopilot"
    ]


def test_candidate_gate_skips_already_linked_source():
    body = "see [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot) for more."
    assert candidate_corpus_sources(body, CORPUS) == []


# --- run() (dispatcher / gate / findings stubbed) ---------------------------

import json  # noqa: E402

from modules.content.atoms import content_llm_reconcile_citations as atom  # noqa: E402


class _Cfg:
    """Minimal SiteConfig stub."""

    def __init__(self, **overrides):
        self._d = {
            "citation_reconcile_llm_enabled": "true",
            "citation_reconcile_llm_model": "",
            "citation_reconcile_llm_timeout_seconds": "60",
            "citation_reconcile_llm_max_content_chars": "24000",
            "structured_extraction_model": "qwen2.5:7b",
            **overrides,
        }

    def get(self, k, d=None):
        return self._d.get(k, d)

    def get_bool(self, k, d=False):
        return str(self._d.get(k, d)).lower() in ("true", "1", "yes")

    def get_int(self, k, d=0):
        return int(self._d.get(k, d))

    def get_float(self, k, d=0.0):
        return float(self._d.get(k, d))


RESEARCH = (
    "- [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot): a\n"
    "- [Topular Strategy](https://www.topularstrategy.com/blog/x): b\n"
)
BODY = "what Full Brim Safety calls low-power mode. A Bogus Source piece makes a point."


async def test_run_links_grounded_and_flags_ungrounded(monkeypatch):
    async def fake_llm(prompt, **kw):
        return json.dumps({
            "links": [{"text": "Full Brim Safety",
                       "url": "https://www.fullbrimsafety.com/p/autopilot"}],
            "ungrounded": ["Bogus Source"],
        })

    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    findings = []
    monkeypatch.setattr(atom, "emit_finding", lambda **kw: findings.append(kw))
    state = {
        "content": BODY, "research_context": RESEARCH, "site_config": _Cfg(),
        "database_service": None, "settings_service": None,
    }
    out = await atom.run(state)
    assert "[Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot)" in out["content"]
    assert findings and findings[0]["kind"] == "unlinked_named_sources"
    review = out["qa_rail_reviews"][0]
    assert review["reviewer"] == "citation_grounding"
    assert review["advisory"] is True and review["approved"] is False


async def test_run_noop_when_no_candidate(monkeypatch):
    called = {"llm": False}

    async def fake_llm(prompt, **kw):
        called["llm"] = True
        return "{}"

    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    body = ("[Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot) and "
            "[Topular Strategy](https://www.topularstrategy.com/blog/x).")
    out = await atom.run({"content": body, "research_context": RESEARCH,
                          "site_config": _Cfg(), "database_service": None})
    assert out == {} and called["llm"] is False


async def test_run_noop_when_disabled(monkeypatch):
    called = {"llm": False}

    async def fake_llm(p, **k):
        called["llm"] = True
        return "{}"

    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    out = await atom.run({"content": BODY, "research_context": RESEARCH,
                          "site_config": _Cfg(citation_reconcile_llm_enabled="false")})
    assert out == {} and called["llm"] is False


async def test_run_failopen_on_bad_json(monkeypatch):
    async def fake_llm(p, **k):
        return "not json at all <think>oops</think>"

    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    monkeypatch.setattr(atom, "emit_finding", lambda **kw: None)
    out = await atom.run({"content": BODY, "research_context": RESEARCH,
                          "site_config": _Cfg(), "database_service": None})
    assert out == {}  # no crash, nothing verified -> no-op


# --- _build_ungrounded_review (advisory-by-construction) --------------------

def test_ungrounded_review_is_always_advisory():
    """The rail only emits a review when it DETECTS ungrounded mentions — it is
    silent on clean drafts and on the disabled / no-candidate / LLM-error
    fail-open paths. A *required* gate combined with a sometimes-silent rail
    would trip qa.aggregate's vacuous-pass guard (missing_required_gates) and
    hard-reject every clean post. So the review is advisory-by-construction:
    always advisory=True / approved=False, with NO gate-config lookup that could
    flip it to a hard veto. The seeded qa_gates.citation_grounding row
    (required_to_pass=false) is for run-counter telemetry + dashboard
    visibility, not graduation."""
    review = atom._build_ungrounded_review(["Bogus Source", "Made Up Co"])
    assert review["advisory"] is True
    assert review["approved"] is False
    assert review["reviewer"] == "citation_grounding"
    # score = max(60, 100 - 6*n); n=2 -> 88
    assert review["score"] == 88.0


def test_ungrounded_review_score_floors_at_60():
    """Many ungrounded names still floor at 60 — the rail never zeroes the
    weighted-score contribution (though as advisory it's excluded from the
    gated mean anyway)."""
    review = atom._build_ungrounded_review([f"Src {i}" for i in range(20)])
    assert review["score"] == 60.0

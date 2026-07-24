"""Topic-echo guard behavior in seo.generate_all_metadata (2026-07-24).

The seo_title must never be the raw topic/assignment directive parroted back
(task 1afabaf9 shipped seo_title = "Expand coverage of the Insights category —
only Insights (3)"). Pin the ladder: corrective retry → canonical-title/H1
fallback → keep-the-echo-loudly (warn finding).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from modules.content.atoms import _seo_common as sc
from modules.content.atoms import seo_generate_all_metadata as atom

_TOPIC = "Expand coverage of the Insights category — only Insights (3)"

_ECHO_JSON = json.dumps({
    "title": _TOPIC,
    "description": "A description of the insights article, active voice, long enough to be kept.",
    "keywords": "insights, category, coverage",
})

_FIXED_JSON = json.dumps({
    "title": "Why Our Insights Category Stayed Empty",
    "description": "A description of the insights article, active voice, long enough to be kept.",
    "keywords": "insights, category, coverage",
})


class _Cfg:
    """Minimal SiteConfig stub with real integer settings."""

    def get(self, key, default=None):
        return default

    def get_int(self, key, default=0):
        return default

    def get_float(self, key, default=0.0):
        return default


def _capture_findings(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


def _state(**over):
    base = {
        "content": (
            "The insights category sat empty for three weeks. "
            "This article explains the three-post problem and the fix.\n\n"
            "## The Three-Post Problem\n\nDetails here."
        ),
        "topic": _TOPIC,
        "title": "The Three-Post Problem: Why Our Insights Category Stayed Empty",
        "tags": ["insights"],
        "task_id": "1afabaf9",
        "site_config": _Cfg(),
        "database_service": None,
    }
    base.update(over)
    return base


async def test_echoed_title_corrected_by_retry(monkeypatch):
    llm = AsyncMock(side_effect=[_ECHO_JSON, _FIXED_JSON])
    monkeypatch.setattr(sc, "run_seo_llm", llm)
    findings = _capture_findings(monkeypatch)

    out = await atom.run(_state())

    assert out["seo_title"] == "Why Our Insights Category Stayed Empty"
    # The corrective retry carried the echo-specific instruction.
    assert llm.call_count == 2
    assert "copied the TOPIC" in llm.call_args_list[1].kwargs["prompt_suffix"]
    # Healed → info breadcrumb on the Findings board.
    echo_findings = [f for f in findings if f["kind"] == "seo_title_topic_echo"]
    assert len(echo_findings) == 1
    assert echo_findings[0]["severity"] == "info"


async def test_retry_still_echoes_falls_back_to_canonical_title(monkeypatch):
    llm = AsyncMock(side_effect=[_ECHO_JSON, _ECHO_JSON])
    monkeypatch.setattr(sc, "run_seo_llm", llm)
    findings = _capture_findings(monkeypatch)

    out = await atom.run(_state())

    # derive_seo_title clips the canonical display title to <=60 chars.
    assert out["seo_title"].startswith("The Three-Post Problem")
    assert not sc.is_topic_echo(out["seo_title"], _TOPIC)
    assert llm.call_count == 2
    echo_findings = [f for f in findings if f["kind"] == "seo_title_topic_echo"]
    assert len(echo_findings) == 1
    assert echo_findings[0]["severity"] == "info"


async def test_no_non_echo_candidate_keeps_echo_with_warn_finding(monkeypatch):
    llm = AsyncMock(side_effect=[_ECHO_JSON, _ECHO_JSON])
    monkeypatch.setattr(sc, "run_seo_llm", llm)
    findings = _capture_findings(monkeypatch)

    # No canonical title in state and a body with no H1/H2 heading.
    out = await atom.run(_state(title=None, content="Plain prose body only."))

    assert sc.is_topic_echo(out["seo_title"], _TOPIC)
    echo_findings = [f for f in findings if f["kind"] == "seo_title_topic_echo"]
    assert len(echo_findings) == 1
    assert echo_findings[0]["severity"] == "warn"
    assert "retitle" in echo_findings[0]["title"].lower() or "retitle" in echo_findings[0]["body"].lower()


async def test_h1_rescues_when_canonical_title_missing(monkeypatch):
    llm = AsyncMock(side_effect=[_ECHO_JSON, _ECHO_JSON])
    monkeypatch.setattr(sc, "run_seo_llm", llm)
    _capture_findings(monkeypatch)

    out = await atom.run(_state(
        title=None,
        content="# The Empty Category Post-Mortem\n\nBody prose about insights.",
    ))

    assert out["seo_title"] == "The Empty Category Post-Mortem"


async def test_degraded_path_checks_echo_without_llm_retry(monkeypatch):
    llm = AsyncMock(side_effect=RuntimeError("ollama down"))
    monkeypatch.setattr(sc, "run_seo_llm", llm)
    findings = _capture_findings(monkeypatch)

    # No canonical title → fallback_title degrades to the raw topic → the
    # guard must catch it WITHOUT calling the (already failing) LLM again,
    # and rescue via the body H1.
    out = await atom.run(_state(
        title=None,
        content="# The Empty Category Post-Mortem\n\nBody prose about insights.",
    ))

    assert llm.call_count == 1  # the primary call only; no corrective retry
    assert out["seo_title"] == "The Empty Category Post-Mortem"
    echo_findings = [f for f in findings if f["kind"] == "seo_title_topic_echo"]
    assert len(echo_findings) == 1 and echo_findings[0]["severity"] == "info"


async def test_non_echo_title_passes_untouched(monkeypatch):
    llm = AsyncMock(return_value=_FIXED_JSON)
    monkeypatch.setattr(sc, "run_seo_llm", llm)
    findings = _capture_findings(monkeypatch)

    out = await atom.run(_state())

    assert out["seo_title"] == "Why Our Insights Category Stayed Empty"
    assert llm.call_count == 1
    assert [f for f in findings if f["kind"] == "seo_title_topic_echo"] == []

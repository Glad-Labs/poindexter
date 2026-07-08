# Citation Reconciliation — Deterministic Fix + Grounded-LLM Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real research-corpus sources cited by name but left unlinked get linked (deterministically where possible, via a grounded-LLM pass for the phrasing tail), and ungrounded named-source mentions get surfaced (finding + advisory QA score).

**Architecture:** Two layers. Part A closes two proven bugs in the deterministic `_citation_match` repair (grammar frames + multi-word-brand domain match). Part B adds `content.llm_reconcile_citations`, an atom that runs after the deterministic pass and asks a structured-extraction LLM for `{text_span, url}` link pairs which code then verifies against the corpus and applies — the LLM never edits prose.

**Tech Stack:** Python 3.11 · asyncpg · LangGraph graph_def atoms · pytest · Ollama/LiteLLM dispatcher (`services/llm_text.py`).

## Global Constraints

- All changes via PR to `Glad-Labs/glad-labs-stack`, squash-merged, off fresh `origin/main`. Never push `main`. (`feedback_all_changes_via_pr`, `feedback_linear_history`)
- Config in `app_settings` via `services/settings_defaults.py` (every-boot seeder), **not** migration files. (`feedback_seed_data_in_baseline`)
- Data-plane rows (`qa_gates`) + `pipeline_templates.graph_def` reseeds reach prod via a timestamped **convergence migration** (`INSERT … ON CONFLICT DO NOTHING` / `UPDATE`), since neither has a boot-time seeder. `baseline.seeds.sql` is a squash-time snapshot — do **not** hand-edit it.
- Required settings fail loud if missing; advisory enhancements fail **open** (never break the pipeline). (`feedback_no_silent_defaults`)
- Prompts DB-configurable via `UnifiedPromptManager`, inline fallback only for bootstrap/test. (`feedback_prompts_must_be_db_configurable`)
- Every change ships tests + doc updates. (`feedback_docs_and_tests_default`)
- Local LLM by default; the atom resolves its model via `resolve_structured_model` (`structured_extraction_model` pin). (`feedback_no_paid_apis`)
- Run backend tests from `src/cofounder_agent`: `poetry run pytest <path> -q`.

---

# Phase 1 — PR 1: deterministic fix

**Branch:** `claude/citation-reconciliation` (already created off `origin/main`; the spec doc is already committed here).
**Files touched:** `src/cofounder_agent/modules/content/atoms/_citation_match.py`, `src/cofounder_agent/tests/unit/services/atoms/test_citation_match.py`.

### Task 1: Multi-word-brand domain match (A2)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_citation_match.py` (function `_domain_match`, ~line 174)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_citation_match.py`

**Interfaces:**

- Consumes: `CorpusSource`, `_domain_handles`, `_registrable_and_sld`, `_subject_tokens` (existing, unchanged).
- Produces: `_domain_match(subject: str, sources: list[CorpusSource]) -> CorpusSource | None` — now also matches a space-collapsed multi-word subject against the domain sld.

- [ ] **Step 1: Write the failing test**

Add to `test_citation_match.py` (import the module the same way the existing tests in that file do — reuse their `CorpusSource`/`_domain_match` import):

```python
def test_domain_match_collapses_multiword_brand_to_sld():
    from modules.content.atoms._citation_match import CorpusSource, _domain_match
    src = CorpusSource(
        url="https://www.fullbrimsafety.com/p/mental-focus-the-autopilot-trap",
        title="Mental Focus | Full Brim Safety", text="full brim safety",
    )
    # multi-word brand -> concatenated domain sld
    assert _domain_match("Full Brim Safety", [src]) is src
    # two-word brand
    src2 = CorpusSource(
        url="https://www.topularstrategy.com/blog/the-professional-autopilot-trap",
        title="The Professional Autopilot Trap | Topular Strategy", text="topular strategy",
    )
    assert _domain_match("Topular Strategy", [src2]) is src2


def test_domain_match_collapse_is_high_precision():
    from modules.content.atoms._citation_match import CorpusSource, _domain_match
    src = CorpusSource(url="https://www.fullbrimsafety.com/x", title="", text="")
    # a partial / different brand must NOT match (equality to sld, not substring)
    assert _domain_match("Full Brim", [src]) is None
    assert _domain_match("Brim Safety Supplies", [src]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_citation_match.py::test_domain_match_collapses_multiword_brand_to_sld -q`
Expected: FAIL (`assert None is src`).

- [ ] **Step 3: Write minimal implementation**

In `_domain_match`, add the space-collapsed form. Replace the loop body's match condition:

```python
def _domain_match(subject: str, sources: list[CorpusSource]) -> CorpusSource | None:
    """Repair-grade match: subject (or a token) equals a corpus domain handle."""
    subj = subject.strip().lower().rstrip(".")
    toks = _subject_tokens(subject)
    # Space/punct-collapsed form: "full brim safety" -> "fullbrimsafety",
    # "Kore.ai" -> "koreai" — so a multi-word brand matches its concatenated
    # domain sld (which _domain_handles already exposes as a handle when its
    # length >= 3). High precision: EQUALITY to a handle, never substring
    # (#765 follow-up — the multi-word-brand miss on task 249a74ca).
    collapsed = re.sub(r"[^a-z0-9]", "", subj)
    for src in sources:
        handles = _domain_handles(src)
        if not handles:
            continue
        if subj in handles or collapsed in handles or any(t in handles for t in toks):
            return src
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/atoms/test_citation_match.py -q -k "collapse"`
Expected: PASS (both new tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_citation_match.py src/cofounder_agent/tests/unit/services/atoms/test_citation_match.py
git commit -m "fix(citation): match multi-word brands to their concatenated domain (#765)"
```

### Task 2: Repair-only verb + "piece" frames (A1)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_citation_match.py` (`_REPAIR_EXTRA_VERBS` ~line 241; new regexes near the other frame regexes ~line 244-254; `find_attributions` frame list ~line 326)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_citation_match.py`

**Interfaces:**

- Consumes: `_SUBJECT_CS`, `_REPAIR_SUBJECT_VERBS`, `find_attributions(content, sources, *, repair=False)`, `link_matched_attributions` (existing).
- Produces: `find_attributions(..., repair=True)` now detects `what X calls Y`, `(a|an|the) X <content-noun> <verb>`, and `a <content-noun> (on|from|by|in) X`. The advisory frame (`repair=False`) is unchanged.

- [ ] **Step 1: Write the failing test**

```python
def _autopilot_corpus():
    from modules.content.atoms._citation_match import parse_corpus
    return parse_corpus(
        "RECENT WEB SOURCES:\n"
        "- [x | Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot): a\n"
        "- [The Professional Autopilot Trap | Topular Strategy]"
        "(https://www.topularstrategy.com/blog/the-professional-autopilot-trap): b\n"
        "- [Autopilot Trap | Sastry](https://www.linkedin.com/pulse/autopilot-trap-sastry-x): c\n"
    )


def test_repair_links_what_x_calls_frame():
    from modules.content.atoms._citation_match import link_matched_attributions
    body = "the brain shifts into what Full Brim Safety calls low-power mode."
    new, linked = link_matched_attributions(body, _autopilot_corpus())
    assert "[Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot)" in new
    assert linked and linked[0]["subject"] == "Full Brim Safety"


def test_repair_links_brand_piece_frame():
    from modules.content.atoms._citation_match import link_matched_attributions
    body = "A Topular Strategy piece makes a point worth sitting with: autopilot."
    new, linked = link_matched_attributions(body, _autopilot_corpus())
    assert "[Topular Strategy](https://www.topularstrategy.com/blog/the-professional-autopilot-trap)" in new


def test_repair_links_piece_on_brand_frame():
    from modules.content.atoms._citation_match import link_matched_attributions
    body = "A piece on LinkedIn frames this well: teams get faster."
    new, linked = link_matched_attributions(body, _autopilot_corpus())
    assert "[LinkedIn](https://www.linkedin.com/pulse/autopilot-trap-sastry-x)" in new


def test_advisory_scan_does_not_flag_calls_in_plain_prose():
    # "calls" is repair-ONLY: the advisory scan must not start flagging ordinary
    # prose (regression guard for the over-flag risk).
    from modules.content.atoms._citation_match import find_attributions
    body = "The function calls the API and the retry policy calls it again."
    assert find_attributions(body, _autopilot_corpus()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_citation_match.py -q -k "repair_links or advisory_scan_does_not_flag_calls"`
Expected: the three `repair_links_*` tests FAIL (no link inserted); `advisory_scan_does_not_flag_calls` PASSES already (guard).

- [ ] **Step 3: Write minimal implementation**

(a) Extend the repair-only verb set (~line 241):

```python
_REPAIR_EXTRA_VERBS = (
    r"describes|described|describe|calls|call|called|frames|framed|"
    r"dubs|dubbed|terms|termed|labels|labelled|labeled|brands|branded"
)
```

(b) Add the two "piece" frame regexes next to the other subject-first repair regex (~after `_SUBJ_FIRST_REPAIR_RE`, ~line 252):

```python
# Repair-only "piece" frames (#765 follow-up): the brand is separated from
# the attribution verb by a content noun ("piece"/"post"/…), or sits as a
# prepositional object ("a piece on X"), so the subject-first frame can't
# span it. Repair-only — every candidate must still clear _domain_match.
_CONTENT_NOUN = (
    r"(?:piece|post|article|report|analysis|study|write-?up|breakdown|"
    r"newsletter|blog|column|essay|thread|take)"
)
_PIECE_VERB = (
    r"(?:makes?|made|frames?|framed|calls?|called|argues?|argued|notes?|"
    r"noted|offers?|offered|puts?|says?|said|has|had|explains?|explained|"
    r"shows?|showed)"
)
# "(a|an|the) <Brand> <content-noun> <verb>"
_SUBJ_PIECE_REPAIR_RE = re.compile(
    rf"\b(?:a|an|the)\s+({_SUBJECT_CS})\s+{_CONTENT_NOUN}\s+{_PIECE_VERB}\b",
    re.IGNORECASE,
)
# "(a|an|the) <content-noun> (on|from|by|in|at) <Brand>"
_PIECE_ON_REPAIR_RE = re.compile(
    rf"\b(?:a|an|the)\s+{_CONTENT_NOUN}\s+(?:on|from|by|in|at)\s+({_SUBJECT_CS})",
    re.IGNORECASE,
)
```

(c) In `find_attributions`, add the piece frames to the scanned set when `repair=True` (~line 322-326):

```python
    subj_first_rx = _SUBJ_FIRST_REPAIR_RE if repair else _SUBJ_FIRST_RE
    link_spans = _markdown_link_text_spans(content)
    seen: set[int] = set()
    results: list[Attribution] = []
    frames = [_PREP_RE, subj_first_rx, _ACCORDING_RE, _PAREN_RE]
    if repair:
        frames.extend([_SUBJ_PIECE_REPAIR_RE, _PIECE_ON_REPAIR_RE])
    for rx in frames:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/atoms/test_citation_match.py -q`
Expected: PASS (all, including the pre-existing tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_citation_match.py src/cofounder_agent/tests/unit/services/atoms/test_citation_match.py
git commit -m "fix(citation): detect 'what X calls' + 'an X piece' attribution frames (repair-only, #765)"
```

### Task 3: Full citation suite + open PR 1

- [ ] **Step 1: Run the citation-related suites**

Run:

```bash
poetry run pytest tests/unit/services/atoms/test_citation_match.py tests/unit/services/atoms/test_citation_atoms.py tests/unit/services/atoms/test_youtube_attribution.py -q
```

Expected: PASS (no regressions in the deterministic repair/strip/advisory behavior).

- [ ] **Step 2: Push and open PR 1**

```bash
git push -u origin claude/citation-reconciliation
gh pr create --repo Glad-Labs/glad-labs-stack --base main --title "fix(citation): relink multi-word + unusual-frame corpus sources (#765)" --body "<summary: the two proven bugs from task 249a74ca — multi-word-brand domain miss + missing verb/piece frames — with the design spec. Part 1 of 2; Part 2 adds the grounded-LLM tail pass.>"
```

- [ ] **Step 3: Verify CI green, then merge** (`feedback_ci_is_the_review_gate`, `feedback_manage_prs_yourself`).

---

# Phase 2 — PR 2: grounded-LLM pass

**Branch:** create `claude/citation-llm-pass` off fresh `origin/main` (after PR 1 merges) — its files are disjoint from PR 1 except tests, so it can also branch off PR 1 if stacking is preferred.

**New/modified files:**

- Create: `src/cofounder_agent/modules/content/atoms/content_llm_reconcile_citations.py`
- Create: `src/cofounder_agent/tests/unit/services/atoms/test_llm_reconcile_citations.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (new `citation_reconcile_llm_*` keys)
- Modify: `src/cofounder_agent/services/canonical_blog_spec.py` (node + edge)
- Modify: `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py` (`test_node_count_is_41` → 42)
- Modify: `src/cofounder_agent/tests/unit/services/graph_def_contract_fingerprints.json` (regenerated)
- Create: `src/cofounder_agent/services/migrations/<ts>_reseed_canonical_blog_llm_reconcile.py` (graph_def reseed + `citation_grounding` gate row)
- Modify: `docs/architecture/anti-hallucination.md`, `CLAUDE.md` (node count 38→39 narrative / pipeline list)

### Task 4: Config defaults

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (after the `citation_*` block, ~line 831)

**Interfaces:**

- Produces app_settings keys: `citation_reconcile_llm_enabled`, `citation_reconcile_llm_model`, `citation_reconcile_llm_timeout_seconds`, `citation_reconcile_llm_max_content_chars`.

- [ ] **Step 1: Add the defaults** (after `'citation_repoint_multitenant_hosts': '',`):

```python
    # why: master switch for the grounded-LLM citation pass (content.
    # llm_reconcile_citations) — the tail-catcher for named-source mentions the
    # deterministic repair can't frame-match. On by default; fail-open.
    'citation_reconcile_llm_enabled': 'true',
    # why: model pin for the pass; empty -> structured_extraction_model (a
    # JSON-reliable instruct model, NOT the reasoning writer). Local by default.
    'citation_reconcile_llm_model': '',
    # why: per-call timeout for the pass (advisory enhancement — keep short).
    'citation_reconcile_llm_timeout_seconds': '60',
    # why: bound the prompt — skip the LLM call when the draft exceeds this many
    # chars (avoids a giant prompt on an outlier post; deterministic pass already ran).
    'citation_reconcile_llm_max_content_chars': '24000',
```

- [ ] **Step 2: Verify defaults load**

Run: `poetry run pytest tests/unit -q -k "settings_defaults or seed_all_defaults"`
Expected: PASS (no duplicate-key / format failures).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py
git commit -m "feat(citation): app_settings defaults for the grounded-LLM reconcile pass"
```

### Task 5: Pure verify-and-apply core (no LLM, no DB)

This is the safety-critical deterministic core: given LLM-proposed pairs + the corpus + the draft, apply only the safe ones. Kept as pure functions so they unit-test without a dispatcher.

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/content_llm_reconcile_citations.py` (helpers only in this task)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_llm_reconcile_citations.py`

**Interfaces:**

- Consumes: `CorpusSource`, `parse_corpus`, `_markdown_link_text_spans`, `_overlaps` from `_citation_match`.
- Produces:
  - `apply_verified_links(content: str, pairs: list[dict], corpus_urls: set[str]) -> tuple[str, list[dict]]` — applies only pairs whose `url` ∈ `corpus_urls` and whose `text` is a verbatim, not-already-linked substring; returns `(new_content, applied)`.
  - `candidate_corpus_sources(content: str, sources: list[CorpusSource]) -> list[CorpusSource]` — corpus sources whose brand token appears in `content` but whose `url` does not (the cheap pre-check gate).

- [ ] **Step 1: Write the failing test**

```python
import pytest
from modules.content.atoms.content_llm_reconcile_citations import (
    apply_verified_links, candidate_corpus_sources,
)
from modules.content.atoms._citation_match import parse_corpus

CORPUS = parse_corpus(
    "- [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot): a\n"
    "- [Topular Strategy](https://www.topularstrategy.com/blog/x): b\n"
)
URLS = {"https://www.fullbrimsafety.com/p/autopilot", "https://www.topularstrategy.com/blog/x"}


def test_apply_links_verbatim_span():
    body = "what Full Brim Safety calls low-power mode."
    pairs = [{"text": "Full Brim Safety", "url": "https://www.fullbrimsafety.com/p/autopilot"}]
    new, applied = apply_verified_links(body, pairs, URLS)
    assert "[Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot)" in new
    assert applied == [{"text": "Full Brim Safety", "url": "https://www.fullbrimsafety.com/p/autopilot"}]


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
    body = "what [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot) calls low-power mode."
    pairs = [{"text": "Full Brim Safety", "url": "https://www.fullbrimsafety.com/p/autopilot"}]
    new, applied = apply_verified_links(body, pairs, URLS)
    assert new == body and applied == []  # idempotent


def test_candidate_gate_detects_unlinked_source():
    body = "what Full Brim Safety calls low-power mode."  # brand present, url absent
    assert [s.url for s in candidate_corpus_sources(body, CORPUS)] == [
        "https://www.fullbrimsafety.com/p/autopilot"
    ]


def test_candidate_gate_skips_already_linked_source():
    body = "see [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot) for more."
    assert candidate_corpus_sources(body, CORPUS) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_llm_reconcile_citations.py -q`
Expected: FAIL (`ModuleNotFoundError` / functions not defined).

- [ ] **Step 3: Write the helpers**

Create `content_llm_reconcile_citations.py` with the module docstring + these helpers (the `run()`/`ATOM_META` come in Task 6):

```python
"""content.llm_reconcile_citations — grounded-LLM citation repair (#765 follow-up).

Runs AFTER the deterministic content.reconcile_citations, so it only sees the
residual: named-source mentions whose attribution frame the regex grammar can't
match. It asks a structured-extraction LLM for {text_span, url} link pairs and
{ungrounded} names, then applies ONLY the pairs that survive deterministic
verification — the LLM never edits the prose. Ungrounded mentions become a
finding + an advisory qa_rail_reviews entry (never a hard veto by default).

Fail-open: disabled / no corpus / no candidate / LLM error / bad JSON -> no-op.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from modules.content.atoms._citation_match import (
    CorpusSource, _STOPWORD_TOKENS, _markdown_link_text_spans, _overlaps,
    _domain_handles, parse_corpus,
)

logger = logging.getLogger(__name__)


def candidate_corpus_sources(content: str, sources: list[CorpusSource]) -> list[CorpusSource]:
    """Corpus sources plausibly cited but unlinked: a brand handle/word appears
    in ``content`` while the source URL does not. Cheap gate to avoid an LLM
    call on drafts with nothing to reconcile.

    Deliberately PERMISSIVE (>=3-char non-stopword title words as needles, so a
    short-word multi-word brand like "Big Sky" is still caught): a false positive
    only wastes one cheap local call, but a false negative silently skips a real
    repair. Correctness beats saving a call."""
    if not content or not sources:
        return []
    low = content.lower()
    out: list[CorpusSource] = []
    for src in sources:
        if src.url in content:
            continue  # already linked / present verbatim
        handles = {h for h in _domain_handles(src) if "." not in h}  # sld only
        title_tokens = {
            t for t in re.findall(r"[a-z][a-z0-9]{2,}", src.text)
            if t not in _STOPWORD_TOKENS
        }
        needles = handles | title_tokens
        if any(n in low for n in needles):
            out.append(src)
    return out


def apply_verified_links(
    content: str, pairs: list[dict], corpus_urls: set[str],
) -> tuple[str, list[dict]]:
    """Apply LLM-proposed link pairs, keeping only the safe ones.

    A pair ``{"text", "url"}`` is applied only when: ``url`` is verbatim in
    ``corpus_urls`` (no hallucinated targets); ``text`` occurs verbatim in
    ``content`` at a span NOT already inside a markdown link (no prose mangling,
    idempotent). First unlinked occurrence; edits applied right-to-left.
    """
    if not content or not pairs:
        return content, []
    link_spans = _markdown_link_text_spans(content)
    edits: list[tuple[int, int, str, str]] = []
    used: list[tuple[int, int]] = []
    for pair in pairs:
        text = (pair.get("text") or "").strip()
        url = (pair.get("url") or "").strip()
        if not text or url not in corpus_urls:
            continue
        start = _first_free_occurrence(content, text, link_spans + used)
        if start is None:
            continue
        end = start + len(text)
        used.append((start, end))
        edits.append((start, end, text, url))
    if not edits:
        return content, []
    new = content
    for start, end, text, url in sorted(edits, key=lambda e: e[0], reverse=True):
        new = f"{new[:start]}[{text}]({url}){new[end:]}"
    applied = [{"text": t, "url": u} for _s, _e, t, u in
               sorted(edits, key=lambda e: e[0])]
    return new, applied


def _first_free_occurrence(content: str, text: str, taken: list[tuple[int, int]]) -> int | None:
    """First index of ``text`` in ``content`` whose span overlaps none of
    ``taken`` (existing link text spans + already-chosen spans)."""
    idx = content.find(text)
    while idx != -1:
        if not _overlaps(idx, idx + len(text), taken):
            return idx
        idx = content.find(text, idx + 1)
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/unit/services/atoms/test_llm_reconcile_citations.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/content_llm_reconcile_citations.py src/cofounder_agent/tests/unit/services/atoms/test_llm_reconcile_citations.py
git commit -m "feat(citation): verify-and-apply core for the grounded-LLM reconcile pass"
```

### Task 6: The atom — prompt, LLM call, run()

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/content_llm_reconcile_citations.py` (add `ATOM_META`, `run`, prompt, LLM+parse)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_llm_reconcile_citations.py`

**Interfaces:**

- Consumes: `services.llm_text.ollama_chat_text` + `resolve_structured_model`; `modules.content.multi_model_qa.{MultiModelQA, ReviewerResult}`; `modules.content.atoms._qa_rail_common.{resolve_gate_states, reviewer_to_dict}`; `utils.findings.emit_finding`; the Task-5 helpers.
- Produces: `ATOM_META` (name `content.llm_reconcile_citations`, `requires=("content",)`, `produces=("content","qa_rail_reviews")`), `async def run(state: dict) -> dict`.

- [ ] **Step 1: Write the failing tests** (dispatcher + gate + findings stubbed — no real LLM/DB)

```python
import json
import types
import pytest
from modules.content.atoms import content_llm_reconcile_citations as atom


class _Cfg:
    def __init__(self, **kw): self._d = {
        "citation_reconcile_llm_enabled": "true",
        "citation_reconcile_llm_model": "",
        "citation_reconcile_llm_timeout_seconds": "60",
        "citation_reconcile_llm_max_content_chars": "24000",
        "structured_extraction_model": "qwen2.5:7b", **kw}
    def get(self, k, d=None): return self._d.get(k, d)
    def get_bool(self, k, d=False): return str(self._d.get(k, d)).lower() in ("true","1","yes")
    def get_int(self, k, d=0): return int(self._d.get(k, d))
    def get_float(self, k, d=0.0): return float(self._d.get(k, d))


RESEARCH = (
    "- [Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot): a\n"
    "- [Topular Strategy](https://www.topularstrategy.com/blog/x): b\n"
)
BODY = ("what Full Brim Safety calls low-power mode. "
        "A Bogus Source piece makes a point.")


@pytest.mark.asyncio
async def test_run_links_grounded_and_flags_ungrounded(monkeypatch):
    async def fake_llm(prompt, **kw):
        return json.dumps({
            "links": [{"text": "Full Brim Safety", "url": "https://www.fullbrimsafety.com/p/autopilot"}],
            "ungrounded": ["Bogus Source"],
        })
    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    findings = []
    monkeypatch.setattr(atom, "emit_finding", lambda **kw: findings.append(kw))
    # advisory-gate machinery: no pool -> resolve_gate_states returns {}
    state = {"content": BODY, "research_context": RESEARCH, "site_config": _Cfg(),
             "database_service": None, "settings_service": None}
    out = await atom.run(state)
    assert "[Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot)" in out["content"]
    # ungrounded -> finding
    assert findings and findings[0]["kind"] == "unlinked_named_sources"
    # ungrounded -> advisory review appended, never a veto
    review = out["qa_rail_reviews"][0]
    assert review["reviewer"] == "citation_grounding"
    assert review["advisory"] is True and review["approved"] is False


@pytest.mark.asyncio
async def test_run_noop_when_no_candidate(monkeypatch):
    called = {"llm": False}
    async def fake_llm(prompt, **kw):
        called["llm"] = True
        return "{}"
    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    # every corpus source already linked -> gate skips -> no LLM call
    body = ("[Full Brim Safety](https://www.fullbrimsafety.com/p/autopilot) and "
            "[Topular Strategy](https://www.topularstrategy.com/blog/x).")
    out = await atom.run({"content": body, "research_context": RESEARCH,
                          "site_config": _Cfg(), "database_service": None})
    assert out == {} and called["llm"] is False


@pytest.mark.asyncio
async def test_run_noop_when_disabled(monkeypatch):
    called = {"llm": False}
    async def fake_llm(p, **k): called["llm"] = True; return "{}"
    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    out = await atom.run({"content": BODY, "research_context": RESEARCH,
                          "site_config": _Cfg(citation_reconcile_llm_enabled="false")})
    assert out == {} and called["llm"] is False


@pytest.mark.asyncio
async def test_run_failopen_on_bad_json(monkeypatch):
    async def fake_llm(p, **k): return "not json at all <think>oops</think>"
    monkeypatch.setattr(atom, "ollama_chat_text", fake_llm)
    monkeypatch.setattr(atom, "emit_finding", lambda **kw: None)
    out = await atom.run({"content": BODY, "research_context": RESEARCH,
                          "site_config": _Cfg(), "database_service": None})
    # no crash, no content change (nothing verified) -> no-op dict
    assert out == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_llm_reconcile_citations.py -q -k run_`
Expected: FAIL (`run` / `ATOM_META` / `ollama_chat_text` symbol not defined in the atom).

- [ ] **Step 3: Implement `ATOM_META`, prompt, parsing, and `run`**

Append to `content_llm_reconcile_citations.py`:

```python
from plugins.atom import AtomMeta, FieldSpec
from services.llm_text import ollama_chat_text, resolve_structured_model
from utils.findings import emit_finding

ATOM_META = AtomMeta(
    name="content.llm_reconcile_citations",
    type="atom",
    version="1.0.0",
    description=(
        "Grounded-LLM citation repair (#765): after the deterministic pass, asks "
        "a structured-extraction model for {text,url} link pairs + ungrounded "
        "names; applies only corpus-verified verbatim spans (LLM never edits "
        "prose); ungrounded -> finding + advisory qa_rail_reviews. Fail-open."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to repair"),
        FieldSpec(name="research_context", type="str", description="research corpus", required=False),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="body with corpus-verified links applied"),
        FieldSpec(name="qa_rail_reviews", type="list[dict]", description="advisory ungrounded-citation review"),
    ),
    requires=("content",),
    produces=("content", "qa_rail_reviews"),
    capability_tier="budget",  # lab/router observability; model pin resolves the concrete model
    cost_class="compute",
    idempotent=True,
    side_effects=("calls the LLM to match named sources to the corpus", "emits findings"),
    parallelizable=False,
)

_PROMPT_KEY = "atoms.content.llm_reconcile_citations"
_PROMPT_FALLBACK = """\
You are a citation auditor. Below is an article and a list of research SOURCES
(name and URL). Find every place the article refers to one of these SOURCES **as
the source of a claim or framing** (e.g. "X says", "according to X", "what X
calls", "an X piece argues") but does NOT already link it.

Return ONLY compact JSON, no prose, no code fence:
{{"links":[{{"text":"<exact verbatim phrase from the article naming the source>","url":"<the matching SOURCE url, copied verbatim>"}}],
 "ungrounded":["<name of any source the article attributes a claim to that is NOT in the SOURCES list>"]}}

Rules:
- Use ONLY urls copied verbatim from the SOURCES list. Never invent a url.
- "text" MUST be an exact substring of the article (do not paraphrase).
- Only source-attribution mentions — ignore names mentioned in passing.
- If nothing matches, return {{"links":[],"ungrounded":[]}}.

SOURCES:
{sources}

ARTICLE:
{content}
"""


def _resolve_prompt(*, sources: str, content: str) -> str:
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(_PROMPT_KEY, sources=sources, content=content)
    except Exception as exc:  # noqa: BLE001 — registry unreachable (bootstrap/test)
        logger.warning("[llm_reconcile_citations] prompt lookup failed (%s) — inline fallback", exc)
        return _PROMPT_FALLBACK.format(sources=sources, content=content)


def _parse_llm_json(raw: str) -> dict:
    """Defensive parse: strip fences/reasoning, take the outer {...}, json.loads.
    Returns {} on any failure (fail-open)."""
    if not raw:
        return {}
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _sources_block(sources: list[CorpusSource]) -> str:
    return "\n".join(f"- {s.title or s.url}: {s.url}" for s in sources)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "")
    site_config = state.get("site_config")
    if not content.strip() or site_config is None:
        return {}
    try:
        if not site_config.get_bool("citation_reconcile_llm_enabled", True):
            return {}
        max_chars = site_config.get_int("citation_reconcile_llm_max_content_chars", 24000)
    except Exception:  # noqa: BLE001 — config read must never break the pipeline
        return {}
    if len(content) > max_chars:
        return {}

    sources = parse_corpus(state.get("research_context") or "")
    candidates = candidate_corpus_sources(content, sources)
    if not candidates:
        return {}  # nothing plausibly unlinked -> no LLM call (cost gate)

    # One structured-extraction call. Fail-open on any error.
    try:
        model = (site_config.get("citation_reconcile_llm_model", "") or "").strip() or None
        resolved = resolve_structured_model(model, site_config=site_config)
        prompt = _resolve_prompt(sources=_sources_block(sources), content=content)
        raw = await ollama_chat_text(
            prompt, model=resolved, site_config=site_config,
            pool=getattr(state.get("database_service"), "pool", None),
            tier="budget", timeout_setting="citation_reconcile_llm_timeout_seconds",
            timeout_default=60.0, task_id=state.get("task_id"),
            phase="llm_reconcile_citations", think=False,
        )
    except Exception as exc:  # noqa: BLE001 — advisory enhancement, never break the pipeline
        logger.warning("[llm_reconcile_citations] LLM call failed (%s) — no-op", exc)
        return {}

    parsed = _parse_llm_json(raw)
    corpus_urls = {s.url for s in sources}
    new_content, applied = apply_verified_links(
        content, parsed.get("links") or [], corpus_urls,
    )
    ungrounded = [str(n).strip() for n in (parsed.get("ungrounded") or []) if str(n).strip()]

    result: dict[str, Any] = {}
    if new_content != content:
        result["content"] = new_content
        logger.info(
            "[llm_reconcile_citations] linked %d source(s) (task=%s): %s",
            len(applied), str(state.get("task_id") or "?")[:8],
            ", ".join(a["text"] for a in applied[:5]),
        )
    if ungrounded:
        review = await _build_ungrounded_review(state, site_config, ungrounded)
        if review is not None:
            result["qa_rail_reviews"] = [review]
        emit_finding(
            source="modules.content.atoms.content_llm_reconcile_citations",
            kind="unlinked_named_sources",
            title=f"{len(ungrounded)} named source(s) cited with no corpus match",
            body=(
                "The writer attributed a claim to named source(s) not present in "
                "the research corpus — verify or soften before publishing:\n- "
                + "\n- ".join(ungrounded)
            ),
            severity="warn",
            dedup_key=f"unlinked_named_sources:{state.get('task_id') or '?'}",
            extra={"task_id": state.get("task_id"), "sources": ungrounded},
        )
    return result


async def _build_ungrounded_review(
    state: dict[str, Any], site_config: Any, ungrounded: list[str],
) -> dict | None:
    """Advisory ReviewerResult for ungrounded named sources. DEFAULTS to advisory
    and only becomes a hard gate when an operator has explicitly set
    ``qa_gates.citation_grounding.required_to_pass=true`` — an ABSENT gate row
    (fresh install pre-migration / read blip) stays advisory so this rail can
    never accidentally hard-reject a post."""
    from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
    from modules.content.multi_model_qa import MultiModelQA, ReviewerResult
    penalty = 6
    score = float(max(60, 100 - penalty * len(ungrounded)))
    review = ReviewerResult(
        reviewer="citation_grounding", approved=False, score=score,
        feedback=f"{len(ungrounded)} named source(s) with no corpus match: "
                 + "; ".join(ungrounded[:5]),
        provider="citation_grounding", advisory=True,
    )
    try:
        qa = MultiModelQA(
            pool=getattr(state.get("database_service"), "pool", None),
            settings_service=state.get("settings_service"),
            site_config=site_config, platform=state.get("platform"),
        )
        gate_states = await resolve_gate_states(qa)
    except Exception:  # noqa: BLE001 — gate read failed -> stay advisory (safe)
        gate_states = {}
    if "citation_grounding" in gate_states:
        # honor an explicit operator graduation to required_to_pass=true
        MultiModelQA._mark_advisory_if_configured(review, gate_states, "citation_grounding")
    return reviewer_to_dict(review)


__all__ = ["ATOM_META", "run", "apply_verified_links", "candidate_corpus_sources"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/unit/services/atoms/test_llm_reconcile_citations.py -q`
Expected: PASS (all Task-5 + Task-6 tests). Ensure `pytest-asyncio` marker style matches the repo (check a sibling `test_*asyncio*` atom test; if the repo uses `asyncio_mode=auto`, drop the `@pytest.mark.asyncio` decorators).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/content_llm_reconcile_citations.py src/cofounder_agent/tests/unit/services/atoms/test_llm_reconcile_citations.py
git commit -m "feat(citation): content.llm_reconcile_citations grounded-LLM pass (#765)"
```

### Task 7: Graph wiring + node-count + fingerprint snapshot

**Files:**

- Modify: `src/cofounder_agent/services/canonical_blog_spec.py` (node list + edges)
- Modify: `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py`
- Modify: `src/cofounder_agent/tests/unit/services/graph_def_contract_fingerprints.json` (regenerated)

**Interfaces:**

- Produces: `CANONICAL_BLOG_GRAPH_DEF` has a `llm_reconcile_citations` node between `reconcile_citations` and `quality_evaluation`.

- [ ] **Step 1: Update the node-count test first (TDD)**

In `test_canonical_blog_spec.py`, rename `test_node_count_is_41` → `test_node_count_is_42` and bump the assertion + comment:

```python
    def test_node_count_is_42(self):
        # 41 + content.llm_reconcile_citations (grounded-LLM citation tail, #765)
        assert len(CANONICAL_BLOG_GRAPH_DEF["nodes"]) == 42
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/unit/services/test_canonical_blog_spec.py -q -k node_count`
Expected: FAIL (`assert 41 == 42`).

- [ ] **Step 3: Add the node + rewire the edge**

In `canonical_blog_spec.py`, in `"nodes"`, immediately after the `reconcile_citations` node (~line 91):

```python
        {"id": "reconcile_citations", "atom": "content.reconcile_citations"},
        # Grounded-LLM tail (#765): links corpus sources the deterministic pass
        # couldn't frame-match; flags ungrounded named sources. Runs before the
        # QA rails so inserted links flow through qa.citations' dead-link check.
        {"id": "llm_reconcile_citations", "atom": "content.llm_reconcile_citations"},
```

In `"edges"`, replace the `reconcile_citations → quality_evaluation` edge (~line 170-171):

```python
        {"from": "resolve_internal_link_placeholders", "to": "reconcile_citations"},
        {"from": "reconcile_citations", "to": "llm_reconcile_citations"},
        {"from": "llm_reconcile_citations", "to": "quality_evaluation"},
```

- [ ] **Step 4: Regenerate the contract-fingerprint snapshot**

Run:

```bash
REGEN_GRAPH_DEF_FP=1 poetry run pytest tests/unit/services/test_graph_def_contract_freshness.py::test__regenerate_snapshot -q
```

This rewrites `graph_def_contract_fingerprints.json` to include `content.llm_reconcile_citations`.

- [ ] **Step 5: Run the spec + freshness suites**

Run:

```bash
poetry run pytest tests/unit/services/test_canonical_blog_spec.py tests/unit/services/test_graph_def_contract_freshness.py -q
```

Expected: PASS (node count 42, DAG valid, atom resolves, snapshot fresh, no orphan fingerprints).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/canonical_blog_spec.py src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py src/cofounder_agent/tests/unit/services/graph_def_contract_fingerprints.json
git commit -m "feat(citation): wire content.llm_reconcile_citations into canonical_blog"
```

### Task 8: Reseed migration (graph_def + advisory gate row)

**Files:**

- Create: `src/cofounder_agent/services/migrations/<UTC-ts>_reseed_canonical_blog_llm_reconcile.py`

Generate the filename: `python scripts/new-migration.py "reseed canonical_blog llm reconcile citations node and add citation_grounding gate"` (creates the timestamped stub), then replace its body.

**Interfaces:**

- Produces: prod's active `canonical_blog` `pipeline_templates.graph_def` gains the new node (unstamped → boot self-heal stamps it); a `citation_grounding` advisory row exists in `qa_gates`.

- [ ] **Step 1: Write the migration**

```python
"""Reseed canonical_blog with the content.llm_reconcile_citations node (#765)
and add the advisory ``citation_grounding`` qa_gate row.

Convergence migration (no boot-time seeder for graph_def / qa_gates):
- UPDATE the active canonical_blog graph_def to the current in-tree spec, written
  UNSTAMPED so ensure_active_graph_defs_stamped() re-stamps it on next boot.
- INSERT the advisory citation_grounding gate (required_to_pass=false) ON CONFLICT
  DO NOTHING. Fresh installs get both here too (baseline predates this change).
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    graph_json = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        updated = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            graph_json,
        )
        await conn.execute(
            """
            INSERT INTO qa_gates
                (name, stage_name, execution_order, reviewer, required_to_pass, enabled, config, metadata)
            VALUES
                ('citation_grounding', 'qa', 162, 'citation_grounding', false, true, '{}'::jsonb,
                 '{"atom": "content.llm_reconcile_citations", "rail": "citation_grounding",
                   "description": "Ungrounded named-source advisory (grounded-LLM pass, #765)"}'::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
        )
    logger.info("reseed_canonical_blog_llm_reconcile up: graph_def %s + citation_grounding gate", updated)


async def down(pool) -> None:
    # No-op: the graph_def reseed is forward-only (a stale re-seed would reintroduce
    # the pre-node spec); the gate row is harmless if left. Mirrors the forward-only
    # posture of other reseed migrations.
    logger.info("reseed_canonical_blog_llm_reconcile down: no-op")
```

Note: confirm the `qa_gates` unique constraint column — the baseline seed uses `ON CONFLICT (id)`, but `id` is a generated UUID here. Use `ON CONFLICT (name)` **only if** `qa_gates.name` has a UNIQUE constraint; otherwise generate a fixed UUID literal and use `ON CONFLICT (id)`. Check with: `grep -n "qa_gates" 0000_baseline.schema.sql` for `UNIQUE`/`PRIMARY KEY`.

- [ ] **Step 2: Lint + smoke the migration**

Run:

```bash
python scripts/ci/migrations_lint.py
python scripts/ci/migrations_smoke.py
```

Expected: PASS (migration applies to a fresh DB; no collision).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/migrations/*reseed_canonical_blog_llm_reconcile.py
git commit -m "feat(citation): reseed canonical_blog graph_def + citation_grounding gate (#765)"
```

### Task 9: Prompt default (DB-configurable) + docs

**Files:**

- Create/modify: the SKILL.md (or YAML) prompt default for `atoms.content.llm_reconcile_citations` — locate the content prompt pack with `grep -rl "atoms.two_pass_writer" skills/ prompts/` and add the key alongside, matching that file's format. (The atom already works via inline fallback; this makes it Langfuse-mirrored + DB-tunable.)
- Modify: `docs/architecture/anti-hallucination.md` — add a row for the grounded-LLM pass under the citation-repair layer.
- Modify: `CLAUDE.md` — bump the canonical_blog node count (38→39 in the narrative is now 41→42; update the content-pipeline-stages list to include `content.llm_reconcile_citations` after `content.reconcile_citations`).

- [ ] **Step 1:** Add the prompt default (byte-identical to `_PROMPT_FALLBACK` so the snapshot/registry-parity holds if a parity test exists).
- [ ] **Step 2:** Update `anti-hallucination.md` + `CLAUDE.md`.
- [ ] **Step 3: Commit**

```bash
git add skills/ docs/architecture/anti-hallucination.md CLAUDE.md
git commit -m "docs(citation): document the grounded-LLM reconcile pass + prompt default"
```

### Task 10: Full backend suite + open PR 2

- [ ] **Step 1: Run the touched suites + a broad sweep**

Run:

```bash
poetry run pytest tests/unit/services/atoms/ tests/unit/services/test_canonical_blog_spec.py tests/unit/services/test_graph_def_contract_freshness.py -q
poetry run pytest tests/unit -q
```

Expected: green (matches the last-recorded nightly posture: 0 failures / 0 collection errors).

- [ ] **Step 2: Push and open PR 2**

```bash
git push -u origin claude/citation-llm-pass
gh pr create --repo Glad-Labs/glad-labs-stack --base main --title "feat(citation): grounded-LLM reconcile pass for the attribution tail (#765)" --body "<summary: Part 2 of 2 — content.llm_reconcile_citations. LLM proposes {text,url} + ungrounded; code verifies against the corpus and applies (never edits prose); ungrounded -> finding + advisory QA score. Gated so no LLM call fires unless a corpus source looks unlinked. Reseed migration + config + docs.>"
```

- [ ] **Step 3: Verify CI green, then merge.** After merge, rebuild/restart the worker so the reseed migration + new atom take effect (`feedback_rebuild_authority`): `docker compose up -d --build poindexter-prefect-worker`.

---

## Self-Review

**Spec coverage:**

- Part A grammar → Task 2 ✓ · Part A handle matcher → Task 1 ✓
- Part B atom (propose-verify-apply) → Tasks 5–6 ✓ · placement after reconcile, before QA → Task 7 ✓
- Ungrounded → finding **and** advisory QA score → Task 6 (`emit_finding` + `_build_ungrounded_review`) ✓
- Cost gate (no LLM call unless a corpus source looks unlinked) → Task 5 `candidate_corpus_sources`, Task 6 `run` ✓
- Config keys → Task 4 ✓ · qa_gates advisory row → Task 8 ✓ · graph reseed → Task 8 ✓
- Fingerprint snapshot + node count → Task 7 ✓ · prompt DB-config + docs → Task 9 ✓
- Acceptance (task-249a74ca replay: FBS/Topular/LinkedIn) → covered by Task 2 (deterministic) + Task 6 (LLM) tests ✓

**Placeholder scan:** Two explicit "confirm before proceeding" notes remain and are intentional verifications, not gaps: Task 6 Step 4 (asyncio marker style) and Task 8 Step 1 (`qa_gates` conflict target column). Both give the exact command to resolve them. No `TBD`/"handle edge cases"/vague-test placeholders.

**Type consistency:** `apply_verified_links(content, pairs, corpus_urls) -> (str, list[dict])`, `candidate_corpus_sources(content, sources) -> list[CorpusSource]`, `run(state) -> dict`, `_build_ungrounded_review(...) -> dict | None` are used consistently across Tasks 5–7. Reviewer/gate name `citation_grounding` matches between the atom (Task 6) and the migration (Task 8). Atom name `content.llm_reconcile_citations` matches between the atom, the graph node (Task 7), and the migration comment.

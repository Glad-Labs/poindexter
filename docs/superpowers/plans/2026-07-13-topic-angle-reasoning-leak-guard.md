# Topic/Angle Leaked-Reasoning Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (subagent dispatch is disabled for this project per CLAUDE.md's `feedback_no_subagent_delegation` — execute inline, sequentially, not via superpowers:subagent-driven-development). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject a distiller's leaked task-narration/meta-commentary in `InternalRagSource._distill_topic_angle()`'s `topic`/`angle` output instead of letting it flow into `internal_topic_candidates`.

**Architecture:** A new pure function `detect_leaked_reasoning()` in `services/topic_sanity.py` runs two independent signals — reuse `strip_reasoning_artifacts()` as a "if we had to clean it, don't trust it" trigger, plus a compound first-person-opener + extraction-task-vocabulary regex fingerprint (and a standalone numbered-field-echo pattern). `_distill_topic_angle()` calls it on both `topic` and `angle` right after its existing empty-topic check, using the same log-and-skip idiom as its sibling checks.

**Tech Stack:** Python 3.13, pytest (`pytest-asyncio`, `loop_scope="session"`), stdlib `re`/`json`.

## Global Constraints

- Full spec: [`docs/superpowers/specs/2026-07-13-topic-angle-reasoning-leak-guard-design.md`](../specs/2026-07-13-topic-angle-reasoning-leak-guard-design.md) — read it if any task below is ambiguous.
- All changes via PR, never push to `main` directly (`feedback_all_changes_via_pr`). This branch (`claude/elastic-jones-ad5399`) already tracks `origin` and has open PR #2453 — new commits pushed to it appear on that PR automatically; no new PR needed.
- TDD: write the failing test before the implementation, every task.
- This is a fresh git worktree with **no poetry venv of its own**. Run all pytest invocations using the **main checkout's** venv python against the worktree's source (`reference_run_worktree_tests`):
  - Venv python: `C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe` (re-resolve with `poetry env info --path` from `C:\Users\mattm\glad-labs-website\src\cofounder_agent` if this ever 404s — the hash suffix changes if the env is recreated).
  - Run from cwd `C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399\src\cofounder_agent` (worktree-relative first-party imports resolve via pytest's `pythonpath` config; the venv only supplies third-party deps).
  - Always pass `-o addopts="" -q -p no:cacheprovider` — `-o addopts=""` drops the repo-default `--forked` (pytest-forked needs `os.fork`, absent on Windows).
- No subagent delegation — execute every step in this session, sequentially (`feedback_no_subagent_delegation`).
- Frequent, small commits — one per task, not one giant commit at the end.

---

## Task 1: `detect_leaked_reasoning()` in `topic_sanity.py`

**Files:**

- Modify: `src/cofounder_agent/services/topic_sanity.py`
- Test: `src/cofounder_agent/tests/unit/services/test_topic_sanity.py`

**Interfaces:**

- Produces: `detect_leaked_reasoning(text: str | None) -> str | None` — returns `REASON_CONTROL_TOKEN` (`"control_token_artifact"`), `REASON_META_COMMENTARY` (`"meta_commentary"`), or `None`. All three names importable from `services.topic_sanity`. Task 2 consumes this exact signature.

- [ ] **Step 1: Write the failing tests**

Edit `src/cofounder_agent/tests/unit/services/test_topic_sanity.py` — update the import block:

```python
from services.topic_sanity import (
    DEFAULT_MIN_ALPHA_WORDS,
    MIN_ALPHA_WORDS_KEY,
    REASON_CONTROL_TOKEN,
    REASON_META_COMMENTARY,
    TopicSanityError,
    count_alpha_words,
    detect_leaked_reasoning,
    evaluate_topic_sanity,
    resolve_min_alpha_words,
)
```

Then append this new section at the end of the file:

```python
# ---------------------------------------------------------------------------
# detect_leaked_reasoning — poindexter row 5b662b41 (2026-07-13)
# ---------------------------------------------------------------------------


# The real distilled_angle from internal_topic_candidates row
# 5b662b41-66c0-403f-945a-b750e922340f, verbatim — truncated mid-word by
# the original niche_internal_rag_snippet_max_chars cutoff, exactly as
# stored in the DB.
LEAKED_REASONING_ANGLE = (
    "How conflicting own pull requests can silently stop workflows from "
    "dispatching, creating a//trap where updates are<|channel>thought: I "
    "need to extract the proposed blog post topic and unique angle from "
    "the provided same snippets. 1. Topic: The silent failure/trap of "
    "conflicting PRs affecting workflo"
)


class TestDetectLeakedReasoning:
    """poindexter row 5b662b41-66c0-403f-945a-b750e922340f: the distiller's
    own task narration leaked into distilled_angle instead of real content.
    strip_reasoning_artifacts alone can't fix this — the surrounding prose
    IS the leak, not a wrapper around otherwise-clean content."""

    def test_real_incident_row_rejected(self):
        assert detect_leaked_reasoning(LEAKED_REASONING_ANGLE) == REASON_CONTROL_TOKEN

    def test_clean_angle_passes(self):
        angle = (
            "Most teams patch CI around flaky tests instead of fixing the "
            "root cause, and that habit quietly compounds into an "
            "unmaintainable suite."
        )
        assert detect_leaked_reasoning(angle) is None

    def test_clean_topic_passes(self):
        assert detect_leaked_reasoning(
            "Why RTX 5090 thermals matter for small-form-factor builds"
        ) is None

    def test_dev_diary_first_person_angle_not_false_positive(self):
        # dev_diary is intentionally founder-voice/first-person
        # (feedback_content_voice) — a bare "I need to" match would
        # false-reject genuine content. The compound signal requires
        # extraction-task vocabulary alongside the opener, which this
        # angle never uses.
        angle = "Why I need to rethink our flaky CI before it rots the whole pipeline"
        assert detect_leaked_reasoning(angle) is None

    def test_numbered_field_echo_alone_rejected(self):
        # No first-person opener at all — the numbered label echo is
        # distinctive enough to stand alone.
        angle = "1. Topic: Understanding container health checks in Kubernetes"
        assert detect_leaked_reasoning(angle) == REASON_META_COMMENTARY

    def test_token_artifact_alone_rejected(self):
        # No semantic phrase match at all — Signal 1 fires independently.
        angle = "The road ahead for solid-state batteries <|im_end|>"
        assert detect_leaked_reasoning(angle) == REASON_CONTROL_TOKEN

    def test_prose_meta_commentary_without_token_rejected(self):
        # No control token at all — Signal 2 fires independently.
        angle = (
            "Let me extract the topic and angle from the provided "
            "snippets about GPU thermals"
        )
        assert detect_leaked_reasoning(angle) == REASON_META_COMMENTARY

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_empty_and_none_pass_through(self, text):
        assert detect_leaked_reasoning(text) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd "C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399\src\cofounder_agent"
"C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/test_topic_sanity.py -o addopts="" -q -p no:cacheprovider
```

Expected: a **collection error** for the whole file — `ImportError: cannot import name 'detect_leaked_reasoning' from 'services.topic_sanity'` (or `REASON_CONTROL_TOKEN`/`REASON_META_COMMENTARY`, whichever the interpreter hits first). This is expected: the entire file fails to collect because the import doesn't exist yet, not just the new tests — that's fine, it proves the test file is exercising code that doesn't exist yet.

- [ ] **Step 3: Implement `detect_leaked_reasoning()` in `topic_sanity.py`**

(a) Add the import, right after the existing `logger_config` import:

```python
from services.llm_providers.thinking_models import strip_reasoning_artifacts
from services.logger_config import get_logger

logger = get_logger(__name__)
```

(b) Add two new reason constants, right after `REASON_TRUNCATED`:

```python
REASON_EMPTY = "empty"
REASON_NO_ALPHA = "no_alphabetic_content"
REASON_TOO_FEW = "too_few_alpha_words"
REASON_SENTINEL = "failure_sentinel"
REASON_TRUNCATED = "truncated_title"
REASON_CONTROL_TOKEN = "control_token_artifact"
REASON_META_COMMENTARY = "meta_commentary"
```

(c) Add three new regex constants, right after `_TRAILING_NONWORD_RE` (before the `TopicSanityResult` dataclass):

```python
_TRAILING_NONWORD_RE = re.compile(r"[\W_]+$")

# A first-person/modal opener that only makes sense as the model narrating
# its OWN next action — never legitimate blog-post topic/angle content.
_META_OPENER_RE = re.compile(
    r"\b(I need to|I will|I'll|let me|I should|I'm going to)\b",
    re.IGNORECASE,
)
# Extraction-task vocabulary. Co-occurring with an opener above within 80
# chars means the model is describing the topic/angle-extraction task
# itself, not writing a topic/angle — a real angle essentially never talks
# about "extracting a topic from snippets."
_META_TASK_VOCAB_RE = re.compile(
    r"\b(extract|distill|the provided|the following)\b.{0,80}\b(topic|angle|snippet)s?\b"
    r"|\b(topic|angle)\b.{0,80}\b(extract|distill|snippet)s?\b",
    re.IGNORECASE,
)
# The model echoing its own extraction-schema field labels back
# ("1. Topic: ...", "2. Angle: ...") — distinctive enough to stand alone,
# unlike the opener/vocab pair which needs the compound-AND above.
_NUMBERED_FIELD_ECHO_RE = re.compile(r"\b\d+\.\s*(Topic|Angle)\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class TopicSanityResult:
```

(d) Add the new function right after `evaluate_topic_sanity()` returns (before `def resolve_min_alpha_words`):

```python
    return TopicSanityResult(
        ok=True, reason=None, alpha_word_count=words, detail="ok",
    )


def detect_leaked_reasoning(text: str | None) -> str | None:
    """Detect a distiller leaking its own task narration into a topic/angle string.

    Two independent signals; either one flags ``text`` as suspect:

    1. A literal reasoning/chat-template control-token artifact survived —
       ``strip_reasoning_artifacts`` would have changed the string.
    2. A first-person/modal opener co-occurs with extraction-task
       vocabulary, or the model echoes its own numbered field labels
       ("1. Topic:") — the model is narrating the extraction task instead
       of producing its output.

    Real incident: ``internal_topic_candidates`` row
    ``5b662b41-66c0-403f-945a-b750e922340f``'s ``distilled_angle`` leaked
    "I need to extract the proposed blog post topic and unique angle from
    the provided snippets. 1. Topic: ..." verbatim, mixed in after real
    content, truncated mid-word.

    Returns the matched reason (``REASON_CONTROL_TOKEN`` /
    ``REASON_META_COMMENTARY``) when ``text`` is suspect, ``None`` when
    clean. Pure function — no DB, no LLM, no clock — mirrors
    :func:`evaluate_topic_sanity`.
    """
    if not text:
        return None
    if strip_reasoning_artifacts(text) != text:
        return REASON_CONTROL_TOKEN
    if _NUMBERED_FIELD_ECHO_RE.search(text):
        return REASON_META_COMMENTARY
    if _META_OPENER_RE.search(text) and _META_TASK_VOCAB_RE.search(text):
        return REASON_META_COMMENTARY
    return None


def resolve_min_alpha_words(site_config: Any) -> int:
```

(e) Update `__all__`:

```python
__all__ = [
    "DEFAULT_MIN_ALPHA_WORDS",
    "MIN_ALPHA_WORDS_KEY",
    "REASON_CONTROL_TOKEN",
    "REASON_EMPTY",
    "REASON_META_COMMENTARY",
    "REASON_NO_ALPHA",
    "REASON_SENTINEL",
    "REASON_TOO_FEW",
    "REASON_TRUNCATED",
    "TopicSanityError",
    "TopicSanityResult",
    "count_alpha_words",
    "detect_leaked_reasoning",
    "evaluate_topic_sanity",
    "resolve_min_alpha_words",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd "C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399\src\cofounder_agent"
"C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/test_topic_sanity.py -o addopts="" -q -p no:cacheprovider
```

Expected: `57 passed` (the file's existing 49 plus the 8 new `TestDetectLeakedReasoning` tests).

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399"
git add src/cofounder_agent/services/topic_sanity.py src/cofounder_agent/tests/unit/services/test_topic_sanity.py
git commit -m "feat(topics): detect leaked distiller reasoning in topic/angle strings

Adds detect_leaked_reasoning() to topic_sanity.py: token-strip-then-suspect
(reuse strip_reasoning_artifacts) plus a compound first-person-opener +
extraction-task-vocabulary fingerprint, tightened so genuine first-person
dev_diary content doesn't false-positive. Real incident:
internal_topic_candidates row 5b662b41-66c0-403f-945a-b750e922340f.

Not yet wired into any caller — see follow-up task."
```

---

## Task 2: Wire the guard into `_distill_topic_angle()`

**Files:**

- Modify: `src/cofounder_agent/services/internal_rag_source.py`
- Test: `src/cofounder_agent/tests/unit/services/test_internal_rag_source.py`

**Interfaces:**

- Consumes: `detect_leaked_reasoning(text: str | None) -> str | None` from Task 1 (`services.topic_sanity`).
- Produces: `InternalRagSource._distill_topic_angle()` now additionally returns `None` (same skip contract as its existing empty-topic/invalid-JSON/not-storyworthy checks) when either the extracted `topic` or `angle` looks like leaked reasoning.

- [ ] **Step 1: Write the failing tests**

Edit `src/cofounder_agent/tests/unit/services/test_internal_rag_source.py` — add `import json` to the top imports:

```python
import json
from unittest.mock import AsyncMock

import pytest
```

Then add these three tests immediately after `test_distill_skips_not_storyworthy_verdict` (i.e. right before `test_distill_passes_niche_context_to_prompt`):

```python
async def test_distill_rejects_leaked_reasoning_in_angle(monkeypatch):
    # Real poindexter row 5b662b41-66c0-403f-945a-b750e922340f: the
    # distiller's own task narration leaked into distilled_angle instead
    # of real content, truncated mid-word. strip_reasoning_artifacts alone
    # can't fix this — detect_leaked_reasoning rejects the whole candidate.
    import services.llm_text as llm_text
    import services.prompt_manager as pm
    import services.topic_ranking as tr

    leaked_angle = (
        "How conflicting own pull requests can silently stop workflows from "
        "dispatching, creating a//trap where updates are<|channel>thought: I "
        "need to extract the proposed blog post topic and unique angle from "
        "the provided same snippets. 1. Topic: The silent failure/trap of "
        "conflicting PRs affecting workflo"
    )
    src = InternalRagSource(_FakePool(), site_config=SiteConfig())
    monkeypatch.setattr(llm_text, "resolve_structured_model", lambda **kw: "gemma3:27b")
    monkeypatch.setattr(
        tr, "_ollama_chat_json",
        AsyncMock(return_value=json.dumps(
            {"topic": "Conflicting PRs stall CI", "angle": leaked_angle}
        )),
    )
    fake_pm = AsyncMock()
    fake_pm.get_prompt = lambda *a, **k: "prompt"
    monkeypatch.setattr(pm, "get_prompt_manager", lambda: fake_pm)

    assert await src._distill_topic_angle(["snippet"]) is None


async def test_distill_rejects_leaked_reasoning_in_topic(monkeypatch):
    # Same guard, but the leak lands in the topic field instead of angle —
    # both fields go through detect_leaked_reasoning.
    import services.llm_text as llm_text
    import services.prompt_manager as pm
    import services.topic_ranking as tr

    leaked_topic = "1. Topic: Understanding container health checks in Kubernetes"
    src = InternalRagSource(_FakePool(), site_config=SiteConfig())
    monkeypatch.setattr(llm_text, "resolve_structured_model", lambda **kw: "gemma3:27b")
    monkeypatch.setattr(
        tr, "_ollama_chat_json",
        AsyncMock(return_value=json.dumps(
            {"topic": leaked_topic, "angle": "A clean angle"}
        )),
    )
    fake_pm = AsyncMock()
    fake_pm.get_prompt = lambda *a, **k: "prompt"
    monkeypatch.setattr(pm, "get_prompt_manager", lambda: fake_pm)

    assert await src._distill_topic_angle(["snippet"]) is None


async def test_distill_passes_clean_first_person_angle(monkeypatch):
    # dev_diary is intentionally founder-voice/first-person
    # (feedback_content_voice) — confirm the guard doesn't false-reject
    # genuine first-person content that never mentions the extraction task.
    import services.llm_text as llm_text
    import services.prompt_manager as pm
    import services.topic_ranking as tr

    clean_angle = "Why I need to rethink our flaky CI before it rots the whole pipeline"
    src = InternalRagSource(_FakePool(), site_config=SiteConfig())
    monkeypatch.setattr(llm_text, "resolve_structured_model", lambda **kw: "gemma3:27b")
    monkeypatch.setattr(
        tr, "_ollama_chat_json",
        AsyncMock(return_value=json.dumps(
            {"topic": "Flaky CI", "angle": clean_angle}
        )),
    )
    fake_pm = AsyncMock()
    fake_pm.get_prompt = lambda *a, **k: "prompt"
    monkeypatch.setattr(pm, "get_prompt_manager", lambda: fake_pm)

    assert await src._distill_topic_angle(["snippet"]) == ("Flaky CI", clean_angle)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd "C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399\src\cofounder_agent"
"C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/test_internal_rag_source.py -k "leaked_reasoning or clean_first_person" -o addopts="" -q -p no:cacheprovider
```

Expected: **2 failed, 1 passed** —

- `test_distill_rejects_leaked_reasoning_in_angle` FAILS (`_distill_topic_angle` currently returns the tuple, not `None` — nothing rejects it yet).
- `test_distill_rejects_leaked_reasoning_in_topic` FAILS, same reason.
- `test_distill_passes_clean_first_person_angle` already PASSES — there's no guard yet to reject anything, so the unchanged-tuple assertion holds trivially. That's expected: this test is a non-regression guard for Step 4, not new behavior being driven by TDD here.

- [ ] **Step 3: Wire the guard into `_distill_topic_angle()`**

In `src/cofounder_agent/services/internal_rag_source.py`, replace:

```python
        topic = str(parsed.get("topic") or "").strip()
        if not topic:
            logger.warning(
                "[internal_rag] distill returned no topic (model=%s) — "
                "skipping candidate", model,
            )
            return None
        return topic, str(parsed.get("angle") or "").strip()
```

with:

```python
        topic = str(parsed.get("topic") or "").strip()
        if not topic:
            logger.warning(
                "[internal_rag] distill returned no topic (model=%s) — "
                "skipping candidate", model,
            )
            return None
        angle = str(parsed.get("angle") or "").strip()
        # The distiller occasionally leaks its own task narration into the
        # topic/angle instead of producing content (poindexter row
        # 5b662b41-66c0-403f-945a-b750e922340f: "I need to extract the
        # proposed blog post topic and unique angle from the provided
        # snippets. 1. Topic: ..."). strip_reasoning_artifacts alone can't
        # fix this — the surrounding prose IS the leak.
        from services.topic_sanity import detect_leaked_reasoning
        for field_name, field_value in (("topic", topic), ("angle", angle)):
            leak_reason = detect_leaked_reasoning(field_value)
            if leak_reason:
                logger.warning(
                    "[internal_rag] distill %s looks like leaked reasoning "
                    "(reason=%s, model=%s): %r — skipping candidate",
                    field_name, leak_reason, model, field_value[:120],
                )
                return None
        return topic, angle
```

- [ ] **Step 4: Run tests to verify they pass**

Run the targeted tests, then the full file to confirm no regressions:

```bash
cd "C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399\src\cofounder_agent"
"C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/test_internal_rag_source.py -k "leaked_reasoning or clean_first_person" -o addopts="" -q -p no:cacheprovider
```

Expected: `3 passed`.

```bash
cd "C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399\src\cofounder_agent"
"C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/test_internal_rag_source.py tests/unit/services/test_topic_sanity.py -o addopts="" -q -p no:cacheprovider
```

Expected: all pass, no failures (confirms the existing `{"topic": "T", "angle": "A"}` -style fixtures elsewhere in the file are untouched — "T"/"A"/"some angle" never trip either signal).

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\mattm\glad-labs-website\.claude\worktrees\elastic-jones-ad5399"
git add src/cofounder_agent/services/internal_rag_source.py src/cofounder_agent/tests/unit/services/test_internal_rag_source.py
git commit -m "fix(topics): reject leaked distiller reasoning in _distill_topic_angle

Wires detect_leaked_reasoning() (topic_sanity.py) into both the topic and
angle fields, using the same log-and-skip idiom as the function's existing
empty-topic/invalid-JSON/not-storyworthy checks. Closes the gap behind
internal_topic_candidates row 5b662b41-66c0-403f-945a-b750e922340f."
```

---

## Rollout

Both commits land on `claude/elastic-jones-ad5399`, which is already pushed with open PR #2453 (currently docs-only). After Task 2's commit, push the branch again (`git push`) — the existing PR updates automatically, no new PR needed. Worth editing the PR description at that point to note code has landed and drop the "no code changes yet" line from the original summary.

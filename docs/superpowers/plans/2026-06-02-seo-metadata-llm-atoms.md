# SEO metadata LLM atoms — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `stage.generate_seo_metadata` node in the `canonical_blog` graph_def with three sequential LLM-driven atoms (`seo.generate_title` → `seo.generate_description` → `seo.extract_keywords`) that produce genuinely SEO-optimized output, preserving the context contract `finalize_task` consumes.

**Architecture:** Three atoms under `services/atoms/`, each mirroring the `qa_ragas.py` shape (`ATOM_META` + `async def run(state)`). A shared `_seo_common.py` helper owns the LLM call (with in-atom retry, since the runner does NOT enforce `ATOM_META.retry`), the programmatic fallbacks, and the degradation logger. Prompts are DB-configurable via 3 new keys in the existing `skills/content/atoms/SKILL.md` pack.

**Tech Stack:** Python/asyncio, LangGraph (`PipelineState` TypedDict channels), `services.llm_text.ollama_chat_text` → dispatcher, `UnifiedPromptManager`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-02-seo-metadata-llm-atoms-design.md`
**Issue:** Glad-Labs/poindexter#362 (umbrella #355)

---

## File Structure

- Create `services/atoms/_seo_common.py` — LLM-call+retry helper, fallbacks, `degraded()`, text utils. Underscore prefix → atom_registry skips it (not an atom).
- Create `services/atoms/seo_generate_title.py` — `seo.generate_title`
- Create `services/atoms/seo_generate_description.py` — `seo.generate_description`
- Create `services/atoms/seo_extract_keywords.py` — `seo.extract_keywords`
- Modify `services/template_runner.py` — add `seo_keywords_list: list[str]` channel to `PipelineState`
- Modify `services/canonical_blog_spec.py` — swap node + edges
- Modify `skills/content/atoms/SKILL.md` — add 3 prompt keys (frontmatter + sections)
- Test `tests/unit/services/atoms/test_seo_common.py`
- Test `tests/unit/services/atoms/test_seo_generate_title.py`
- Test `tests/unit/services/atoms/test_seo_generate_description.py`
- Test `tests/unit/services/atoms/test_seo_extract_keywords.py`
- Test `tests/unit/services/atoms/test_seo_wiring.py`

All commands run from `src/cofounder_agent/` unless noted. Test command prefix: `poetry run pytest`.

---

### Task 1: Declare the `seo_keywords_list` channel

**Files:**

- Modify: `services/template_runner.py` (PipelineState, after the `seo_keywords` line ~272)

- [ ] **Step 1: Add the channel.** In `class PipelineState`, immediately after `seo_keywords: list[str]`, add:

```python
    seo_keywords_list: list[str]
```

Rationale: LangGraph only persists declared channels. `seo_keywords_list` was undeclared, so the graph_def path silently dropped it — `finalize_task` then read `[]`. Declaring it fixes a latent keyword-loss bug and lets the new keyword atom write the structured list.

- [ ] **Step 2: Commit.**

```bash
git add services/template_runner.py
git commit -m "fix(pipeline): declare seo_keywords_list channel (#362) — was dropped on graph_def path"
```

---

### Task 2: `_seo_common.py` helper (TDD)

**Files:**

- Create: `services/atoms/_seo_common.py`
- Test: `tests/unit/services/atoms/test_seo_common.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/unit/services/atoms/test_seo_common.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.atoms import _seo_common as sc

pytestmark = pytest.mark.asyncio


def _state(**over):
    s = {"content": "We shipped a regex fix for the validator. It fired 8x per post.",
         "topic": "regex validator bug", "title": "The Regex Validator Bug",
         "site_config": MagicMock(), "database_service": None}
    s.update(over)
    return s


def test_clean_oneline_strips_quotes_and_collapses():
    assert sc.clean_oneline('  "Hello   world\n"  ') == "Hello world"


def test_clamp_words_truncates_at_word_boundary():
    out = sc.clamp_words("alpha beta gamma delta", 12)
    assert out == "alpha beta" and len(out) <= 12


def test_clamp_words_passes_short_text():
    assert sc.clamp_words("short", 160) == "short"


def test_fallback_title_uses_canonical(monkeypatch):
    assert sc.fallback_title(_state()).startswith("The Regex")


def test_fallback_description_uses_first_paragraph():
    out = sc.fallback_description(_state(content="First para text.\n\n# Heading\n\nSecond."))
    assert out.startswith("First para") and len(out) <= 160


def test_fallback_keywords_delegates(monkeypatch):
    monkeypatch.setattr(sc, "extract_keywords_from_text", lambda t, count=5: ["regex", "validator"])
    assert sc.fallback_keywords(_state()) == ["regex", "validator"]


async def test_run_seo_llm_returns_text(monkeypatch):
    monkeypatch.setattr(sc, "ollama_chat_text", AsyncMock(return_value="  Best Title  "))
    monkeypatch.setattr(sc, "get_prompt_manager", lambda: MagicMock(get_prompt=lambda key, **k: "PROMPT"))
    out = await sc.run_seo_llm(_state(), "atoms.seo.generate_title", topic="x")
    assert out == "Best Title"


async def test_run_seo_llm_retries_then_raises(monkeypatch):
    call = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(sc, "ollama_chat_text", call)
    monkeypatch.setattr(sc, "get_prompt_manager", lambda: MagicMock(get_prompt=lambda key, **k: "P"))
    monkeypatch.setattr(sc.asyncio, "sleep", AsyncMock())  # no real backoff
    with pytest.raises(RuntimeError):
        await sc.run_seo_llm(_state(), "atoms.seo.generate_title", max_attempts=2)
    assert call.await_count == 2


def test_degraded_logs_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        sc.degraded("title", RuntimeError("x"))
    assert "degraded" in caplog.text.lower()
```

- [ ] **Step 2: Run, verify fail.**

Run: `poetry run pytest tests/unit/services/atoms/test_seo_common.py -q`
Expected: FAIL (module `_seo_common` not found).

- [ ] **Step 3: Implement the helper.**

```python
# services/atoms/_seo_common.py
"""Shared helpers for the SEO atoms (seo.generate_title / .generate_description
/ .extract_keywords). Underscore-prefixed so atom_registry skips it.

Owns the one LLM-call path (with retry — the TemplateRunner does NOT enforce
ATOM_META.retry, so retry lives here), the programmatic fallbacks reused for
graceful degradation, and the degradation logger. Issue #362 (umbrella #355).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.llm_text import ollama_chat_text
from services.prompt_manager import get_prompt_manager
from utils.text_utils import extract_keywords_from_text
from utils.title_utils import derive_seo_title

logger = logging.getLogger(__name__)

# Cost tier handed to the dispatcher. capability_tier in ATOM_META is the
# semantic slug ("cheap_critic"); this is the concrete cost tier the
# dispatcher resolves a provider/model from. budget = cheap instruction-follower.
_SEO_TIER = "budget"
_EXCERPT_CHARS = 1500


def content_excerpt(content: str, limit: int = _EXCERPT_CHARS) -> str:
    return (content or "").strip()[:limit]


def clean_oneline(text: str) -> str:
    """Strip surrounding quotes/markdown and collapse whitespace to one line."""
    t = (text or "").strip()
    for q in ('"', "'", "`"):
        t = t.strip(q)
    return " ".join(t.split())


def clamp_words(text: str, limit: int) -> str:
    """Trim to <= limit chars at a word boundary, dropping trailing punctuation."""
    t = clean_oneline(text)
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0] or t[:limit]
    return cut.rstrip(",.;:- ")


async def run_seo_llm(
    state: dict[str, Any],
    prompt_key: str,
    *,
    max_attempts: int = 2,
    backoff_s: float = 2.0,
    **prompt_vars: Any,
) -> str:
    """Render prompt_key with prompt_vars, call the LLM at the SEO tier, return
    stripped text. Retries transient failures up to max_attempts; raises the
    last exception on persistent failure (the calling atom catches → fallback)."""
    prompt = get_prompt_manager().get_prompt(prompt_key, **prompt_vars)
    site_config = state.get("site_config")
    pool = getattr(state.get("database_service"), "pool", None)
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            text = await ollama_chat_text(
                prompt, site_config=site_config, pool=pool, tier=_SEO_TIER
            )
            return (text or "").strip()
        except Exception as exc:  # noqa: BLE001 — retry any transient transport error
            last_exc = exc
            if attempt < max_attempts:
                await asyncio.sleep(backoff_s)
    assert last_exc is not None
    raise last_exc


def degraded(field: str, exc: Exception) -> None:
    """Log the LLM→programmatic degradation. WARNING (Loki) is the floor; a
    best-effort metric is emitted if the exporter exposes a counter helper."""
    logger.warning("[seo.%s] LLM failed, degraded to programmatic: %s", field, exc)
    try:
        from services.metrics_exporter import increment_seo_degraded  # type: ignore
        increment_seo_degraded(field)
    except Exception:  # noqa: BLE001 — metric is best-effort; the WARNING is the floor
        pass


def fallback_title(state: dict[str, Any]) -> str:
    canonical = (
        state.get("canonical_title")
        or state.get("title")
        or state.get("topic")
        or ""
    )
    return derive_seo_title(canonical, max_len=60)


def fallback_description(state: dict[str, Any]) -> str:
    content = state.get("content") or ""
    topic = state.get("topic") or ""
    paragraphs = content.split("\n\n")
    excerpt = next(
        (p for p in paragraphs if p.strip() and not p.startswith("#")),
        content[:200],
    )
    return (excerpt.strip() or topic)[:160]


def fallback_keywords(state: dict[str, Any], count: int = 5) -> list[str]:
    return extract_keywords_from_text(state.get("content") or "", count=count)


__all__ = [
    "content_excerpt", "clean_oneline", "clamp_words", "run_seo_llm",
    "degraded", "fallback_title", "fallback_description", "fallback_keywords",
]
```

Note: `increment_seo_degraded` may not exist in `metrics_exporter` — the `try/except` keeps `degraded()` robust. If the exporter has a generic counter API, wire it during Task 6 review; otherwise the WARNING log is the shipped floor.

- [ ] **Step 4: Run, verify pass.**

Run: `poetry run pytest tests/unit/services/atoms/test_seo_common.py -q`
Expected: PASS (all 9).

- [ ] **Step 5: Commit.**

```bash
git add services/atoms/_seo_common.py tests/unit/services/atoms/test_seo_common.py
git commit -m "feat(seo): _seo_common helper — LLM call+retry, fallbacks, degrade (#362)"
```

---

### Task 3: `seo.generate_title` atom (TDD)

**Files:**

- Create: `services/atoms/seo_generate_title.py`
- Test: `tests/unit/services/atoms/test_seo_generate_title.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/unit/services/atoms/test_seo_generate_title.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.atoms import seo_generate_title as atom
from services.atoms import _seo_common as sc

pytestmark = pytest.mark.asyncio


def _state(**over):
    s = {"content": "Body about the regex validator bug and the fix.",
         "topic": "regex validator", "title": "Regex Validator",
         "tags": ["regex", "validators"], "site_config": MagicMock()}
    s.update(over)
    return s


def test_atom_meta_contract():
    assert atom.ATOM_META.name == "seo.generate_title"
    assert atom.ATOM_META.produces == ("seo_title",)
    assert "content" in atom.ATOM_META.requires


async def test_generates_and_caps_title(monkeypatch):
    long = "A Very Long SEO Title That Definitely Exceeds Sixty Characters For Sure Indeed"
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=f'"{long}"'))
    out = await atom.run(_state())
    assert out["seo_title"] and len(out["seo_title"]) <= 60
    assert '"' not in out["seo_title"]


async def test_empty_content_noops(monkeypatch):
    assert await atom.run(_state(content="")) == {}


async def test_llm_failure_degrades(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(side_effect=RuntimeError("boom")))
    warned = MagicMock()
    monkeypatch.setattr(sc, "degraded", warned)
    out = await atom.run(_state())
    assert out["seo_title"].startswith("Regex Validator")
    warned.assert_called_once()


async def test_blank_llm_output_falls_back(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value="   "))
    out = await atom.run(_state())
    assert out["seo_title"]  # non-empty via fallback
```

- [ ] **Step 2: Run, verify fail.**

Run: `poetry run pytest tests/unit/services/atoms/test_seo_generate_title.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the atom.**

```python
# services/atoms/seo_generate_title.py
"""seo.generate_title — LLM-written, SEO-optimized blog title atom.

Replaces the title branch of the old generate_seo_metadata stage, which
just truncated the raw article title. Degrades to that programmatic
derivation on persistent LLM failure (logged, not silent). Issue #362.
"""
from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.atoms import _seo_common as sc
from utils.title_utils import derive_seo_title

ATOM_META = AtomMeta(
    name="seo.generate_title",
    type="atom",
    version="1.0.0",
    description="LLM-written SEO title (<=60 chars, primary keyword first); degrades to programmatic derivation on LLM failure.",
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="tags", type="list[str]", description="tags; tags[0] is the primary keyword", required=False),
    ),
    outputs=(FieldSpec(name="seo_title", type="str", description="<=60 char SEO title"),),
    requires=("content",),
    produces=("seo_title",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    if not content or state.get("site_config") is None:
        return {}
    topic = state.get("topic") or ""
    tags = state.get("tags") or []
    primary_keyword = (tags[0] if tags else topic) or topic
    try:
        raw = await sc.run_seo_llm(
            state, "atoms.seo.generate_title",
            topic=topic, primary_keyword=primary_keyword,
            content=sc.content_excerpt(content),
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
        title = derive_seo_title(sc.clean_oneline(raw), max_len=60) if raw.strip() else ""
        if not title:
            title = sc.fallback_title(state)
    except Exception as exc:  # noqa: BLE001 — degrade, never propagate
        sc.degraded("title", exc)
        title = sc.fallback_title(state)
    return {"seo_title": title}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run, verify pass.**

Run: `poetry run pytest tests/unit/services/atoms/test_seo_generate_title.py -q`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add services/atoms/seo_generate_title.py tests/unit/services/atoms/test_seo_generate_title.py
git commit -m "feat(seo): seo.generate_title atom (#362)"
```

---

### Task 4: `seo.generate_description` atom (TDD)

**Files:**

- Create: `services/atoms/seo_generate_description.py`
- Test: `tests/unit/services/atoms/test_seo_generate_description.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/unit/services/atoms/test_seo_generate_description.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.atoms import seo_generate_description as atom
from services.atoms import _seo_common as sc

pytestmark = pytest.mark.asyncio


def _state(**over):
    s = {"content": "First paragraph about the fix.\n\n# H\n\nMore.",
         "topic": "regex validator", "seo_title": "Fixing the Regex Validator",
         "site_config": MagicMock()}
    s.update(over)
    return s


def test_atom_meta_requires_seo_title():
    assert atom.ATOM_META.name == "seo.generate_description"
    assert "seo_title" in atom.ATOM_META.requires
    assert atom.ATOM_META.produces == ("seo_description",)


async def test_caps_at_160(monkeypatch):
    long = "word " * 60
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=long))
    out = await atom.run(_state())
    assert len(out["seo_description"]) <= 160


async def test_blank_falls_back(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=""))
    out = await atom.run(_state())
    assert out["seo_description"].startswith("First paragraph")


async def test_llm_failure_degrades(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(sc, "degraded", MagicMock())
    out = await atom.run(_state())
    assert out["seo_description"]
    sc.degraded.assert_called_once()


async def test_empty_content_noops():
    assert await atom.run(_state(content="")) == {}
```

- [ ] **Step 2: Run, verify fail.** `poetry run pytest tests/unit/services/atoms/test_seo_generate_description.py -q` → FAIL.

- [ ] **Step 3: Implement.**

```python
# services/atoms/seo_generate_description.py
"""seo.generate_description — LLM-written meta description (150-160 chars).

Reads the freshly-generated seo_title so the description complements the
title. Replaces the old stage's first-paragraph slice. Degrades to that
slice on persistent LLM failure. Issue #362.
"""
from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.atoms import _seo_common as sc

ATOM_META = AtomMeta(
    name="seo.generate_description",
    type="atom",
    version="1.0.0",
    description="LLM-written meta description (<=160 chars) coherent with seo_title; degrades to first-paragraph slice on LLM failure.",
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="seo_title", type="str", description="title from seo.generate_title"),
        FieldSpec(name="topic", type="str", description="article topic", required=False),
    ),
    outputs=(FieldSpec(name="seo_description", type="str", description="<=160 char meta description"),),
    requires=("content", "seo_title"),
    produces=("seo_description",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    if not content or state.get("site_config") is None:
        return {}
    topic = state.get("topic") or ""
    seo_title = state.get("seo_title") or topic
    try:
        raw = await sc.run_seo_llm(
            state, "atoms.seo.generate_description",
            seo_title=seo_title, topic=topic,
            content=sc.content_excerpt(content),
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
        desc = sc.clamp_words(raw, 160) if raw.strip() else ""
        if not desc:
            desc = sc.fallback_description(state)
    except Exception as exc:  # noqa: BLE001
        sc.degraded("description", exc)
        desc = sc.fallback_description(state)
    return {"seo_description": desc}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run, verify pass.** → PASS.

- [ ] **Step 5: Commit.**

```bash
git add services/atoms/seo_generate_description.py tests/unit/services/atoms/test_seo_generate_description.py
git commit -m "feat(seo): seo.generate_description atom (#362)"
```

---

### Task 5: `seo.extract_keywords` atom (TDD)

**Files:**

- Create: `services/atoms/seo_extract_keywords.py`
- Test: `tests/unit/services/atoms/test_seo_extract_keywords.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/unit/services/atoms/test_seo_extract_keywords.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.atoms import seo_extract_keywords as atom
from services.atoms import _seo_common as sc

pytestmark = pytest.mark.asyncio


def _state(**over):
    s = {"content": "regex validator firing eight times per post markdown links prose",
         "topic": "regex validator", "seo_title": "Regex Validator Bug",
         "site_config": MagicMock()}
    s.update(over)
    return s


def test_atom_meta_produces():
    assert atom.ATOM_META.name == "seo.extract_keywords"
    assert set(atom.ATOM_META.produces) >= {"seo_keywords", "seo_keywords_list"}


async def test_dedupes_lowercases_caps_and_sets_flag(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(
        return_value="Regex, regex, Validator, markdown, prose, links, post"))
    out = await atom.run(_state())
    kws = out["seo_keywords_list"]
    assert kws == [k for i, k in enumerate(kws) if k not in kws[:i]]  # deduped
    assert all(k == k.lower() for k in kws)
    assert len(kws) <= 10
    assert out["seo_keywords"] == ", ".join(kws)
    assert out["stages"]["4_seo_metadata_generated"] is True


async def test_drops_hallucinated_keyword(monkeypatch):
    # "blockchain" is absent from the content → must be dropped
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value="regex, blockchain, validator"))
    out = await atom.run(_state())
    assert "blockchain" not in out["seo_keywords_list"]
    assert "regex" in out["seo_keywords_list"]


async def test_backfills_when_under_three(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value="regex"))
    monkeypatch.setattr(sc, "fallback_keywords", lambda s, count=10: ["validator", "markdown", "prose"])
    out = await atom.run(_state())
    assert len(out["seo_keywords_list"]) >= 3


async def test_llm_failure_degrades(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(sc, "degraded", MagicMock())
    monkeypatch.setattr(sc, "fallback_keywords", lambda s, count=5: ["Regex", "Validator"])
    out = await atom.run(_state())
    assert out["seo_keywords_list"] == ["regex", "validator"]
    sc.degraded.assert_called_once()
```

- [ ] **Step 2: Run, verify fail.** → FAIL.

- [ ] **Step 3: Implement.**

```python
# services/atoms/seo_extract_keywords.py
"""seo.extract_keywords — LLM-proposed, content-grounded SEO keywords.

Replaces the old word-frequency extractor. LLM proposes search-intent
keywords; a programmatic guard dedupes, lowercases, drops any keyword whose
tokens don't appear in content+title (anti-hallucination), caps at 10, and
backfills from the frequency extractor to a floor of 3. Degrades to pure
frequency extraction on LLM failure. Writes the final SEO metadata flag.
Issue #362.
"""
from __future__ import annotations

import re
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.atoms import _seo_common as sc

ATOM_META = AtomMeta(
    name="seo.extract_keywords",
    type="atom",
    version="1.0.0",
    description="LLM SEO keywords grounded in content (dedup/cap/anti-hallucination guard); degrades to frequency extraction on LLM failure.",
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="seo_title", type="str", description="title from seo.generate_title"),
        FieldSpec(name="topic", type="str", description="article topic", required=False),
    ),
    outputs=(
        FieldSpec(name="seo_keywords", type="str", description="comma-joined keywords"),
        FieldSpec(name="seo_keywords_list", type="list[str]", description="structured keywords"),
        FieldSpec(name="stages", type="dict", description="sets 4_seo_metadata_generated"),
    ),
    requires=("content", "seo_title"),
    produces=("seo_keywords", "seo_keywords_list", "stages"),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)

_MIN_KEYWORDS = 3
_MAX_KEYWORDS = 10


def _parse(raw: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,\n]", raw or ""):
        kw = part.strip().lstrip("-*0123456789. ").strip().lower()
        if kw:
            out.append(kw)
    return out


def _finish(state: dict[str, Any], kws: list[str]) -> dict[str, Any]:
    stages = dict(state.get("stages") or {})
    stages["4_seo_metadata_generated"] = True
    return {"seo_keywords": ", ".join(kws), "seo_keywords_list": kws, "stages": stages}


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    if not content or state.get("site_config") is None:
        return {}
    topic = state.get("topic") or ""
    seo_title = state.get("seo_title") or topic
    haystack = (content + " " + seo_title).lower()
    try:
        raw = await sc.run_seo_llm(
            state, "atoms.seo.extract_keywords",
            seo_title=seo_title, topic=topic,
            content=sc.content_excerpt(content),
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
        seen: set[str] = set()
        kws: list[str] = []
        for kw in _parse(raw):
            if kw in seen:
                continue
            if all(tok in haystack for tok in kw.split()):  # anti-hallucination
                seen.add(kw)
                kws.append(kw)
            if len(kws) >= _MAX_KEYWORDS:
                break
        if len(kws) < _MIN_KEYWORDS:
            for kw in sc.fallback_keywords(state, count=_MAX_KEYWORDS):
                k = kw.lower()
                if k not in seen:
                    seen.add(k)
                    kws.append(k)
                if len(kws) >= _MIN_KEYWORDS:
                    break
        if not kws:
            kws = [k.lower() for k in sc.fallback_keywords(state)]
    except Exception as exc:  # noqa: BLE001
        sc.degraded("keywords", exc)
        kws = [k.lower() for k in sc.fallback_keywords(state)]
    return _finish(state, kws[:_MAX_KEYWORDS])


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run, verify pass.** → PASS.

- [ ] **Step 5: Commit.**

```bash
git add services/atoms/seo_extract_keywords.py tests/unit/services/atoms/test_seo_extract_keywords.py
git commit -m "feat(seo): seo.extract_keywords atom (#362)"
```

---

### Task 6: Prompts in SKILL.md

**Files:**

- Modify: `skills/content/atoms/SKILL.md` (frontmatter `metadata.prompts` + 3 body sections)

- [ ] **Step 1: Add 3 entries to `metadata.prompts` (after the pipeline_architect entry):**

```yaml
- key: atoms.seo.generate_title
  category: seo_metadata
  output_format: text
  description: 'SEO title generator for atoms.seo.generate_title — rewrites the draft into a <=60 char search-optimized title leading with {primary_keyword}.'
- key: atoms.seo.generate_description
  category: seo_metadata
  output_format: text
  description: 'Meta description generator for atoms.seo.generate_description — 150-160 char description coherent with {seo_title}.'
- key: atoms.seo.extract_keywords
  category: seo_metadata
  output_format: text
  description: 'SEO keyword generator for atoms.seo.extract_keywords — 5-10 comma-separated search-intent keywords grounded in the article.'
```

- [ ] **Step 2: Append 3 sections to the body (after the pipeline_architect section).** Use single-brace placeholders only (no literal `{`/`}` elsewhere — `.format()` renders these):

````markdown
## atoms.seo.generate_title

```text
You are an SEO editor. Write ONE blog post title, 60 characters or fewer,
for the article below.

- Lead with the primary keyword "{primary_keyword}" when it reads naturally.
- Be specific and compelling; promise the article's actual value.
- No clickbait, no quotes, no markdown, no trailing punctuation.
- Output the title only — no preamble, no alternatives.

TOPIC: {topic}

ARTICLE:
{content}
```

## atoms.seo.generate_description

```text
You are an SEO editor. Write ONE meta description for an article titled
"{seo_title}".

- 150 to 160 characters.
- Summarize the concrete value a reader gets; weave in the topic naturally.
- Active voice; end on a complete sentence (no ellipsis, no truncation).
- No quotes, no markdown. Output the description only.

TOPIC: {topic}

ARTICLE:
{content}
```

## atoms.seo.extract_keywords

```text
You are an SEO strategist. List the search keywords and phrases a person
would type into Google to find the article titled "{seo_title}".

- 5 to 10 keywords/phrases, most important first.
- Lowercase, comma-separated, on a single line.
- Only terms actually supported by the article text — do not invent topics.
- Output the comma-separated list only — no numbering, no preamble.

TOPIC: {topic}

ARTICLE:
{content}
```
````

- [ ] **Step 3: Verify prompts load + render.**

Run:

```bash
poetry run python -c "from services.prompt_manager import get_prompt_manager as g; pm=g(); print(pm.get_prompt('atoms.seo.generate_title', topic='t', primary_keyword='k', content='c')[:40]); print(pm.get_prompt('atoms.seo.generate_description', seo_title='s', topic='t', content='c')[:40]); print(pm.get_prompt('atoms.seo.extract_keywords', seo_title='s', topic='t', content='c')[:40])"
```

Expected: three non-empty rendered snippets, no `KeyError`.

- [ ] **Step 4: Commit.**

```bash
git add skills/content/atoms/SKILL.md
git commit -m "feat(seo): DB-configurable prompts for the seo.* atoms (#362)"
```

---

### Task 7: Wire atoms into the canonical_blog graph_def + wiring test

**Files:**

- Modify: `services/canonical_blog_spec.py`
- Test: `tests/unit/services/atoms/test_seo_wiring.py`

- [ ] **Step 1: Write failing wiring test.**

```python
# tests/unit/services/atoms/test_seo_wiring.py
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as G
from services import atom_registry


def _node_atom(nid):
    return next(n["atom"] for n in G["nodes"] if n["id"] == nid)


def test_seo_nodes_present_and_old_stage_gone():
    ids = {n["id"] for n in G["nodes"]}
    assert {"seo_title", "seo_description", "seo_keywords"} <= ids
    assert "generate_seo_metadata" not in ids
    assert _node_atom("seo_title") == "seo.generate_title"
    assert _node_atom("seo_description") == "seo.generate_description"
    assert _node_atom("seo_keywords") == "seo.extract_keywords"


def test_seo_chain_edges():
    edges = {(e["from"], e["to"]) for e in G["edges"]}
    assert ("qa_aggregate", "seo_title") in edges
    assert ("seo_title", "seo_description") in edges
    assert ("seo_description", "seo_keywords") in edges
    assert ("seo_keywords", "generate_media_scripts") in edges
    # old direct edges removed
    assert ("qa_aggregate", "generate_seo_metadata") not in edges


def test_atoms_discoverable_in_registry():
    atom_registry.discover()
    names = {a.name for a in atom_registry.list_atoms()}
    assert {"seo.generate_title", "seo.generate_description", "seo.extract_keywords"} <= names
```

- [ ] **Step 2: Run, verify fail.** → FAIL (old node still present, atoms not yet imported in a discoverable form — they are, but the wiring assertions fail).

- [ ] **Step 3: Edit `CANONICAL_BLOG_GRAPH_DEF`.** In `services/canonical_blog_spec.py`:

Replace the node line:

```python
        {"id": "generate_seo_metadata", "atom": "stage.generate_seo_metadata"},
```

with:

```python
        {"id": "seo_title", "atom": "seo.generate_title"},
        {"id": "seo_description", "atom": "seo.generate_description"},
        {"id": "seo_keywords", "atom": "seo.extract_keywords"},
```

Replace the two edges:

```python
        {"from": "qa_aggregate", "to": "generate_seo_metadata"},
        {"from": "generate_seo_metadata", "to": "generate_media_scripts"},
```

with:

```python
        {"from": "qa_aggregate", "to": "seo_title"},
        {"from": "seo_title", "to": "seo_description"},
        {"from": "seo_description", "to": "seo_keywords"},
        {"from": "seo_keywords", "to": "generate_media_scripts"},
```

Also update the module docstring + `description` field to say "13 coarse stages" → "the SEO stage is now the seo.\* atom chain".

- [ ] **Step 4: Run wiring test + the full atoms suite.**

Run: `poetry run pytest tests/unit/services/atoms/ -q`
Expected: PASS (all SEO tests + existing atom tests).

- [ ] **Step 5: Commit.**

```bash
git add services/canonical_blog_spec.py tests/unit/services/atoms/test_seo_wiring.py
git commit -m "feat(seo): wire seo.* atom chain into canonical_blog graph_def (#362)"
```

---

### Task 8: Full-suite regression + parity/quality smoke

- [ ] **Step 1: Run the broader unit suite touching SEO + the spec + finalize.**

Run:

```bash
poetry run pytest tests/unit/services/atoms/ tests/unit/services/stages/test_remaining_stages_smoke.py tests/unit/services/test_canonical_blog_spec.py -q
```

Expected: PASS (or no NEW failures vs baseline; note any pre-existing collection errors per CLAUDE.md).

- [ ] **Step 2: Build-graph smoke (atoms resolve in the live spec).**

Run:

```bash
poetry run python -c "from services import atom_registry; atom_registry.discover(); from services.pipeline_architect import _validate_spec; from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as G; errs=_validate_spec(G, set(a.name for a in atom_registry.list_atoms())); print('VALIDATION ERRORS:', errs)"
```

Expected: `VALIDATION ERRORS: []` (the requires/produces of the seo.\* chain resolve — `seo_title` produced before description/keywords require it). If `_validate_spec` has a different signature, adapt to the call used in `tests/unit/services/test_pipeline_architect_validate.py`.

- [ ] **Step 3: Update the spec's out-of-scope note → done.** No code; mention in the #362 comment that the SEO stage is decomposed and the `seo_keywords_list` channel bug is fixed.

- [ ] **Step 4: Final commit (if Step 2 required a tweak) + push decision.** Defer push/PR to the operator.

---

## Self-Review

**Spec coverage:** title/description/keywords atoms (Tasks 3–5) ✓; sequential coherence via `seo_title` in state + `requires` (Tasks 4–5) ✓; DB-configurable prompts (Task 6) ✓; retry+graceful fallback in `_seo_common` (Task 2) ✓; graph wiring (Task 7) ✓; contract keys `seo_title`/`seo_description`/`seo_keywords`/`seo_keywords_list`/`stages` ✓ (+ channel declared, Task 1); observability via `degraded()` WARNING (+best-effort metric) ✓; testing per atom + wiring ✓; parity-waived/quality acceptance ✓ (Task 8 smoke).

**Placeholder scan:** none — every step has real code/commands.

**Type consistency:** `run_seo_llm`, `clean_oneline`, `clamp_words`, `fallback_*`, `degraded` names match across helper + atoms + tests. `ATOM_META.retry.max_attempts`/`backoff_s` used consistently. Atom names (`seo.generate_title` etc.) match graph_def + wiring test.

**One known soft spot:** `increment_seo_degraded` in `metrics_exporter` may not exist — guarded by try/except, WARNING is the floor; confirm/replace with the exporter's real counter API during Task 6/8 review.

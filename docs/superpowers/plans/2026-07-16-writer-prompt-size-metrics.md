# Writer Prompt-Size Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project override:** this repo runs with subagent delegation disabled
> (billing policy — see `CLAUDE.md`). Use **superpowers:executing-plans**
> (inline execution in the current session) — do NOT dispatch subagents for
> any task in this plan.

**Goal:** Measure the total character size of the prompt sent to the writer LLM on the niche/two-pass path (draft + revise), broken down by context section, and surface it on the Pipeline Grafana dashboard.

**Architecture:** Capture `len()` at the points in `atoms/two_pass_writer.py` where each context section is already built as a string, thread the counts up through the existing `result` dict → `metrics` dict chain (the same seam `prompt_template_key`/`variant_id` already use), and let them land in `atom_runs.metrics` (JSONB) via the existing `StageResult.metrics` capture. Add a Grafana panel row reading `atom_runs` directly.

**Tech Stack:** Python 3.13, LangGraph (`_State` TypedDict + graph nodes), pytest + pytest-asyncio, Grafana dashboard JSON (Postgres datasource, `rawSql` panels).

## Global Constraints

- Spec: [docs/superpowers/specs/2026-07-16-writer-prompt-size-metrics-design.md](../specs/2026-07-16-writer-prompt-size-metrics-design.md)
- Issue: `Closes Glad-Labs/poindexter#868`
- PR: [Glad-Labs/glad-labs-stack#2639](https://github.com/Glad-Labs/glad-labs-stack/pull/2639) (draft) — push commits to branch `claude/writer-context-handoff-50c318`, do not open a second PR.
- **Test runner (this worktree has no venv of its own — do not run bare `poetry run`, it creates a new empty venv):**
  ```
  cd src/cofounder_agent
  "C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest <path> -o addopts="" -q
  ```
  Verified baseline: `tests/unit/services/atoms/test_two_pass_writer.py` → 102 passed.
- Scope: two-pass writer path only (`atoms/two_pass_writer.py`'s `_draft_node`/`_revise_node`). The legacy `content_generator.generate_blog_post()` path is explicitly out of scope — verify it stays untouched.
- No new DB table/migration. No new `app_settings` key. No Prometheus exporter changes.
- Field names (exact, used throughout): `writer_prompt_draft_chars`, `writer_prompt_snippet_chars`, `writer_prompt_research_chars`, `writer_prompt_context_bundle_chars`, `writer_prompt_override_chars`, `writer_prompt_internal_grounding_chars`, `writer_prompt_revise_chars`, `writer_prompt_revise_calls`.

---

### Task 1: `generate_with_context()` gains an optional `prompt_metrics` output parameter

**Files:**

- Modify: `src/cofounder_agent/modules/content/ai_content_generator.py:1343-1404`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py` (append near the existing `test_generate_with_context_forwards_target_length_to_prompt` at line 1025 — tests for this function are colocated here, not in `test_ai_content_generator.py`)

**Interfaces:**

- Consumes: nothing new.
- Produces: `generate_with_context(..., prompt_metrics: dict[str, int] | None = None) -> str` — when `prompt_metrics` is a dict, it is populated in place with `{"prompt_chars": int, "snippet_chars": int}` after the prompt is rendered, before the LLM call. `None` (the default) is a no-op — return value and behavior are unchanged for every existing caller.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/services/atoms/test_two_pass_writer.py` (after `test_generate_with_context_forwards_target_length_to_prompt`, i.e. after line 1057):

```python
async def test_generate_with_context_populates_prompt_metrics(monkeypatch):
    """poindexter#868: a passed prompt_metrics dict gets populated with the
    exact size of the rendered prompt and its snippet_block portion."""
    import modules.content.ai_content_generator as acg

    def fake_get_prompt(key, **kwargs):
        return f"INSTRUCTIONS:{kwargs['instructions']}|SNIPPETS:{kwargs['snippet_block']}"
    monkeypatch.setattr(
        "modules.content.ai_content_generator.get_prompt_manager",
        lambda: MagicMock(get_prompt=MagicMock(side_effect=fake_get_prompt)),
    )

    async def fake_resolve(*, site_config=None):
        return "glm-4.7-5090:latest"
    monkeypatch.setattr(
        "modules.content.ai_content_generator._resolve_rag_writer_model",
        fake_resolve,
    )

    async def fake_text(prompt, **kwargs):
        return "draft body"
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_text)

    snippets = [{"source": "posts", "ref": "1", "snippet": "hello world"}]
    metrics: dict = {}
    content = await acg.generate_with_context(
        topic="t", angle="a", snippets=snippets,
        extra_instructions="write it", site_config=_fake_site_config(),
        pool=_fake_pool_with_no_snippets(), prompt_metrics=metrics,
    )

    assert content == "draft body"
    expected_snippet_block = acg._format_snippet_block(snippets, 500)
    expected_prompt = f"INSTRUCTIONS:write it|SNIPPETS:{expected_snippet_block}"
    assert metrics["prompt_chars"] == len(expected_prompt)
    assert metrics["snippet_chars"] == len(expected_snippet_block)


async def test_generate_with_context_prompt_metrics_none_is_noop(monkeypatch):
    """Existing callers that don't pass prompt_metrics see no behavior
    change (default None is a no-op, not a crash)."""
    import modules.content.ai_content_generator as acg

    monkeypatch.setattr(
        "modules.content.ai_content_generator.get_prompt_manager",
        lambda: MagicMock(get_prompt=MagicMock(return_value="PROMPT")),
    )

    async def fake_resolve(*, site_config=None):
        return "glm-4.7-5090:latest"
    monkeypatch.setattr(
        "modules.content.ai_content_generator._resolve_rag_writer_model",
        fake_resolve,
    )

    async def fake_text(prompt, **kwargs):
        return "draft body"
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_text)

    content = await acg.generate_with_context(
        topic="t", angle="a", snippets=[],
        extra_instructions="write it", site_config=_fake_site_config(),
        pool=_fake_pool_with_no_snippets(),
    )
    assert content == "draft body"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_two_pass_writer.py -o addopts="" -q -k "prompt_metrics"
```

Expected: FAIL — `TypeError: generate_with_context() got an unexpected keyword argument 'prompt_metrics'` on the first test; the second test currently passes already (it's the no-op case) — that's fine, it becomes a real regression guard once Step 3 lands.

- [ ] **Step 3: Implement**

In `src/cofounder_agent/modules/content/ai_content_generator.py`, replace the `generate_with_context` function (lines 1343-1404):

```python
async def generate_with_context(
    *, topic: str, angle: str, snippets: list[dict],
    extra_instructions: str | None = None,
    site_config: Any,
    pool: Any = None,
    task_id: str | None = None,
    target_length: int = 1200,
    think: bool | None = None,
    prompt_metrics: dict[str, int] | None = None,
) -> str:
    """Build a prompt using the snippets as background context, generate the
    draft. Wraps the existing generation path; tests can monkeypatch here.

    Per-snippet length cap is operator-tunable via
    ``writer_rag_context_snippet_max_chars``. Writer model is resolved
    from the per-step ``app_settings.pipeline_writer_model`` pin (fails loud
    when unset; the cost_tier.* fallback was removed).

    ``site_config`` is REQUIRED (#272 Phase-2c) — the ``two_pass_writer``
    atom threads its run-bound instance.

    ``think`` (2026-07-06): when ``False``, disables the writer model's
    reasoning channel so a thinking-capable model doesn't burn its generation
    budget reasoning and truncate the visible draft. Threaded to
    ``ollama_chat_text``; ``None`` leaves the call unchanged.

    ``prompt_metrics`` (poindexter#868): when passed a dict, populates it
    with ``{"prompt_chars": <int>, "snippet_chars": <int>}`` — the size of
    the fully-rendered prompt actually sent to the model, and the portion of
    it from the snippet block. Optional and side-effect-only: every existing
    caller (including test fakes that stub this function out entirely) is
    unaffected. ``None`` (the default) is a no-op.
    """
    from services.llm_text import ollama_chat_text

    _sc = site_config
    snippet_max_chars = _sc.get_int(
        "writer_rag_context_snippet_max_chars", 500,
    )
    model = await _resolve_rag_writer_model(site_config=_sc)
    snippet_block = _format_snippet_block(snippets, snippet_max_chars)
    instructions = extra_instructions or ""
    prompt = get_prompt_manager().get_prompt(
        "atoms.two_pass_writer.generate_with_context",
        topic=topic,
        angle=angle,
        instructions=instructions,
        snippet_block=snippet_block,
        target_length=target_length,
    )
    if prompt_metrics is not None:
        prompt_metrics["prompt_chars"] = len(prompt)
        prompt_metrics["snippet_chars"] = len(snippet_block)
    # 2026-06-02 (poindexter#572): switched from ``_ollama_chat_json``
    # (which forces ``format=json`` on Ollama) to the plain-text
    # ``ollama_chat_text`` helper — mirrors the same fix already applied
    # to ``_revise_node`` on 2026-05-16. Thinking models (glm-4.7-5090,
    # qwen3) under ``response_format=json_object`` spend their whole token
    # budget in the reasoning channel and return EMPTY ``content`` — which
    # surfaced as canonical_blog "no content produced" on every task.
    # ``ollama_chat_text`` routes through ``dispatch_complete`` when a pool
    # is available and runs ``maybe_unwrap_json`` as belt-and-braces if a
    # model still emits a JSON envelope unprompted.
    return await ollama_chat_text(
        prompt,
        model=model,
        site_config=_sc,
        pool=pool,
        timeout_setting="niche_ollama_chat_timeout_seconds",
        task_id=task_id,
        phase="draft_generation",
        think=think,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_two_pass_writer.py -o addopts="" -q
```

Expected: `104 passed` (102 baseline + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/ai_content_generator.py src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py
git commit -m "feat(content): add optional prompt_metrics output param to generate_with_context

Glad-Labs/poindexter#868"
```

---

### Task 2: Draft-side breakdown — `_draft_node` + `_State` fields + `run()` surfacing

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` (three edits: `_State` TypedDict ~line 257, `_draft_node` lines 755-861, `run()`'s return dict lines 2216-2264)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py`

**Interfaces:**

- Consumes: `generate_with_context(..., prompt_metrics=...)` from Task 1.
- Produces: `_State` gains 6 new optional keys (`writer_prompt_draft_chars`, `writer_prompt_snippet_chars`, `writer_prompt_research_chars`, `writer_prompt_context_bundle_chars`, `writer_prompt_override_chars`, `writer_prompt_internal_grounding_chars`), all `int`. `two_pass_writer.run()`'s return dict carries the same 6 keys (via `final.get(key, 0)`) for Task 4 to read.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/services/atoms/test_two_pass_writer.py`:

```python
async def test_draft_prompt_size_breakdown_all_sections_present(monkeypatch):
    """Every context section that reaches the draft prompt must be measured
    and returned on the result dict.

    ``snippet_chars`` isn't asserted to an exact value here — its correctness
    (that it equals ``len(_format_snippet_block(...))``) is Task 1's test.
    This test's job is confirming the wiring survives _draft_node -> run(),
    which draft_chars already demonstrates: both are set in the same
    `if prompt_metrics is not None:` block off the same `_call_draft()` call,
    so draft_chars > 0 confirms that block ran and populated the dict
    _draft_node reads both fields from."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(post_row={"slug": "vram-spill"}),
        site_config=_grounding_site_config(site_url="https://www.gladlabs.io"),
        research_context="Source A: something (https://example.com).",
        writer_prompt_override="Niche house style: short sentences.",
        context_bundle={"merged_prs": [{"number": 1, "title": "T", "url": "u", "body": "b"}]},
        internal_grounding={"source_table": "posts", "source_id": "42",
                            "preview": "How we cut VRAM spill.", "similarity": 0.7},
    )
    assert result["writer_prompt_draft_chars"] > 0
    assert isinstance(result["writer_prompt_snippet_chars"], int)
    assert result["writer_prompt_research_chars"] == len("Source A: something (https://example.com).")
    assert result["writer_prompt_override_chars"] == len("Niche house style: short sentences.")
    assert result["writer_prompt_context_bundle_chars"] > 0
    assert result["writer_prompt_internal_grounding_chars"] > 0


async def test_draft_prompt_size_breakdown_zero_when_sections_absent(monkeypatch):
    """No override/context_bundle/research_context/internal_grounding →
    every breakdown field is exactly 0 (not missing, not None)."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert result["writer_prompt_draft_chars"] > 0
    assert result["writer_prompt_research_chars"] == 0
    assert result["writer_prompt_override_chars"] == 0
    assert result["writer_prompt_context_bundle_chars"] == 0
    assert result["writer_prompt_internal_grounding_chars"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_two_pass_writer.py -o addopts="" -q -k "prompt_size_breakdown"
```

Expected: FAIL — `KeyError: 'writer_prompt_draft_chars'` (the key doesn't exist on `result` yet).

- [ ] **Step 3: Implement — `_State` TypedDict**

In `src/cofounder_agent/modules/content/atoms/two_pass_writer.py`, find this existing block (part of the `_State` TypedDict body):

```python
    research_context: str
    # Phase 0 lab observability (2026-05-28) — populated by
    # _revise_node when it resolves a prompt via UnifiedPromptManager.
```

Replace with:

```python
    research_context: str
    # Writer prompt-size observability (poindexter#868) — captured by
    # _draft_node at each context-section assembly point, before
    # concatenation onto `instruction`. 0 (not missing) when a section is
    # absent for this task.
    writer_prompt_draft_chars: int
    writer_prompt_snippet_chars: int
    writer_prompt_research_chars: int
    writer_prompt_context_bundle_chars: int
    writer_prompt_override_chars: int
    writer_prompt_internal_grounding_chars: int
    # Phase 0 lab observability (2026-05-28) — populated by
    # _revise_node when it resolves a prompt via UnifiedPromptManager.
```

- [ ] **Step 4: Implement — `_draft_node`**

Replace the whole `_draft_node` function (lines 755-861) with:

```python
async def _draft_node(state: _State) -> _State:
    from modules.content.ai_content_generator import generate_with_context
    instruction = (
        "Write a first-draft blog post drawing ONLY from the provided internal "
        "snippets. Do NOT make up external facts, statistics, or quotes you cannot "
        "ground in a snippet. If you need an outside fact you don't have, mark it "
        "[EXTERNAL_NEEDED: <description>] in the draft so a follow-up pass can fill it in. "
        "[EXTERNAL_NEEDED: ...] is the ONLY placeholder you may emit. Never invent a "
        "citation stand-in: do not write labels like [INTERNAL SNIPPET], a bare `source` "
        "tag, or a markdown link whose target is not a real URL (e.g. (url), (link), "
        "(internal_context_link)). If you cannot cite a real URL for a claim, state the "
        "claim plainly with no citation marker at all."
    )
    # Prepend the niche-level writer prompt override (when present) so
    # niche-specific anti-hallucination rules / brand voice / scope
    # restrictions arrive before the mode-specific TWO_PASS instruction.
    # Wired in by migration 0141 + this PR; empty string when the niche
    # has no override set (preserves historical behaviour).
    override = (state.get("writer_prompt_override") or "").strip()
    if override:
        instruction = f"{override}\n\n---\n\n{instruction}"
    # Inject the context_bundle (set by the dev_diary job for dev_diary
    # tasks) as a GROUND TRUTH section. The writer must base claims on
    # these entries; when present, this is the authoritative source —
    # not the topic string, not the snippets. Closes #353. For niche-
    # batch / ad-hoc tasks this is empty and the section is skipped.
    bundle = state.get("context_bundle") or {}
    context_bundle_chars = 0
    if bundle:
        ground_truth = _format_bundle_for_prompt(bundle)
        if ground_truth:
            instruction = (
                f"{instruction}\n\n---\n\n"
                f"GROUND TRUTH (today's actual activity — base every "
                f"claim on these entries, do NOT infer or invent details "
                f"the bundle doesn't contain. When you reference a PR or "
                f"commit, use the exact title and link to the URL given):\n\n"
                f"{ground_truth}"
            )
            context_bundle_chars = len(ground_truth)
    # Inject the pre-collected external research corpus (ResearchService +
    # RAG, threaded from GenerateContentStage via run()) as a SOURCES
    # section. The QA critic grades the draft against this same corpus, so
    # without surfacing it to the writer the niche path drafted research-
    # blind and was rejected for "ignoring the SOURCES corpus" (2026-06-09).
    # Phrased to override the "ONLY internal snippets" line above: these are
    # vetted facts the writer SHOULD use in addition to the snippets.
    research_context = (state.get("research_context") or "").strip()
    if research_context:
        instruction = (
            f"{instruction}\n\n---\n\n"
            f"SOURCES (vetted external research already gathered for this "
            f"article — use these IN ADDITION to the internal snippets: ground "
            f"your key claims in them and cite them inline as markdown links "
            f"using the exact URLs provided. Do not invent other external facts "
            f"or sources beyond these and the snippets. If a claim has no matching "
            f"SOURCE URL, write it without any citation marker — never a placeholder "
            f"like [INTERNAL SNIPPET] or a link whose target is not a real URL"
            f"):\n\n{research_context}"
        )
    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    pool = _POOL_REGISTRY.get(state["pool_thread"])
    # Prior-work anchor (#822) — optional soft framing section appended AFTER
    # the SOURCES block. Fail-open: any issue → no section, prompt unchanged.
    ig = state.get("internal_grounding") or {}
    ig_injected = False
    ig_source_table: str | None = None
    internal_grounding_chars = 0
    if _internal_grounding_enabled(site_config) and ig:
        section, ig_source_table = await _build_internal_grounding_section(
            ig, site_config=site_config, pool=pool,
        )
        if section:
            instruction = f"{instruction}\n\n---\n\n{section}"
            ig_injected = True
            internal_grounding_chars = len(section)

    draft_metrics: dict[str, int] = {}

    async def _call_draft() -> str:
        return await generate_with_context(
            topic=state["topic"], angle=state["angle"],
            snippets=state["snippets"], extra_instructions=instruction,
            site_config=site_config, pool=pool,
            task_id=state.get("task_id"),
            target_length=state.get("target_length", 1200),
            # Disable the writer model's reasoning channel (default) so a
            # thinking-capable model doesn't burn its budget reasoning and
            # truncate the visible draft. 2026-07-06 investigation.
            think=_resolve_writer_think(site_config),
            prompt_metrics=draft_metrics,
        )

    min_substance_words = _resolve_min_substance_words(site_config)
    draft = await _call_draft()
    # poindexter#806 — a writer model that burns its generation budget in a
    # hidden reasoning channel can emit a near-empty, non-blank response
    # (e.g. a handful of dots). Retry once before accepting it: a cheap
    # self-heal that avoids handing a degenerate draft to the rest of the
    # pipeline (revise/expand/QA/image-gen all run on whatever this
    # returns). No prior draft exists yet, so a still-degenerate retry is
    # kept as-is (never fabricated) with a visibility finding.
    if _classify_draft_substance(draft, min_words=min_substance_words) != "ok":
        retry_draft = await _call_draft()
        if _classify_draft_substance(retry_draft, min_words=min_substance_words) == "ok":
            draft = retry_draft
        else:
            _emit_degenerate_first_draft_kept_finding(task_id=state.get("task_id"))
    return {
        **state,
        "draft": draft,
        "internal_grounding_injected": ig_injected,
        "internal_grounding_source_table": ig_source_table,
        "writer_prompt_draft_chars": draft_metrics.get("prompt_chars", 0),
        "writer_prompt_snippet_chars": draft_metrics.get("snippet_chars", 0),
        "writer_prompt_research_chars": len(research_context),
        "writer_prompt_context_bundle_chars": context_bundle_chars,
        "writer_prompt_override_chars": len(override),
        "writer_prompt_internal_grounding_chars": internal_grounding_chars,
    }
```

Note: `draft_metrics` is reused (mutated in place) across the first attempt and the retry. This is safe because `instruction`/`topic`/`angle`/`snippets` — everything that determines the rendered prompt — are identical between the two `_call_draft()` invocations; only the model's _output_ differs between attempts, never the prompt sent. So whichever call's metrics end up in `draft_metrics`, the values are the same.

- [ ] **Step 5: Implement — `run()`'s return dict**

In the same file, find this block inside `run()`'s final `return` (around line 2254-2263):

```python
            "prompt_template_key": final.get("prompt_template_key"),
            "prompt_template_version": final.get("prompt_template_version"),
```

Replace with:

```python
            "prompt_template_key": final.get("prompt_template_key"),
            "prompt_template_version": final.get("prompt_template_version"),
            # Writer prompt-size observability (poindexter#868).
            "writer_prompt_draft_chars": final.get("writer_prompt_draft_chars", 0),
            "writer_prompt_snippet_chars": final.get("writer_prompt_snippet_chars", 0),
            "writer_prompt_research_chars": final.get("writer_prompt_research_chars", 0),
            "writer_prompt_context_bundle_chars": final.get("writer_prompt_context_bundle_chars", 0),
            "writer_prompt_override_chars": final.get("writer_prompt_override_chars", 0),
            "writer_prompt_internal_grounding_chars": final.get("writer_prompt_internal_grounding_chars", 0),
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_two_pass_writer.py -o addopts="" -q
```

Expected: `106 passed` (104 + 2 new). No prior test should regress — the new `_State` keys are additive and `_draft_node`'s return dict only gained keys, it didn't remove any.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/two_pass_writer.py src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py
git commit -m "feat(content): capture draft-side writer prompt-size breakdown

Glad-Labs/poindexter#868"
```

---

### Task 3: Revise-side accumulation — `_revise_node` + `_State` field + `run()` surfacing

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` (`_State` TypedDict, `_revise_node` lines 1073-1208, `run()`'s return dict)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py`

**Interfaces:**

- Consumes: nothing new from other tasks (independent of Task 2's fields, though it edits the same three regions of the same file — do this task after Task 2 lands to avoid a merge conflict inside `_State`).
- Produces: `_State["writer_prompt_revise_chars"]: int`, accumulated across every `_revise_node` invocation in a run (mirrors how `revision_loops` already accumulates). `run()`'s return dict carries `writer_prompt_revise_chars`; `revision_loops` (already returned) doubles as the revise-call count — no new counter field needed.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/services/atoms/test_two_pass_writer.py` (this extends the exact pattern of the existing `test_external_needed_triggers_research_and_revise` at line 297, driving two loops instead of one):

```python
async def test_revise_chars_accumulate_across_two_loops(monkeypatch):
    """Two revise passes (each still carrying an [EXTERNAL_NEEDED] marker on
    the first) must sum their prompt lengths into writer_prompt_revise_chars,
    and revision_loops must land at 2."""
    drafts = iter([
        "First draft with [EXTERNAL_NEEDED: a fact] inside.",
        "Revised once, still needs [EXTERNAL_NEEDED: another fact].",
        "Revised twice, now clean.",
    ])
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return next(drafts)
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)

    revise_prompts: list[str] = []
    async def fake_revise(prompt, **kwargs):
        revise_prompts.append(prompt)
        return next(drafts)
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    async def fake_research(query, max_sources=2, *, site_config=None):
        return f"External research result for: {query}"
    monkeypatch.setattr("services.research_service.research_topic", fake_research, raising=False)
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert result["revision_loops"] == 2
    assert len(revise_prompts) == 2
    assert result["writer_prompt_revise_chars"] == sum(len(p) for p in revise_prompts)


async def test_revise_chars_zero_when_no_revision(monkeypatch):
    """A clean first draft (no EXTERNAL_NEEDED marker) never enters
    _revise_node, so writer_prompt_revise_chars stays 0."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert result["revision_loops"] == 0
    assert result["writer_prompt_revise_chars"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_two_pass_writer.py -o addopts="" -q -k "revise_chars"
```

Expected: FAIL — `KeyError: 'writer_prompt_revise_chars'`.

- [ ] **Step 3: Implement — `_State` TypedDict**

Find (the block Task 2 Step 3 just inserted):

```python
    writer_prompt_internal_grounding_chars: int
    # Phase 0 lab observability (2026-05-28) — populated by
    # _revise_node when it resolves a prompt via UnifiedPromptManager.
```

Replace with:

```python
    writer_prompt_internal_grounding_chars: int
    # Accumulated across every _revise_node invocation in this run (mirrors
    # how revision_loops already accumulates below). 0 when no revision ran.
    writer_prompt_revise_chars: int
    # Phase 0 lab observability (2026-05-28) — populated by
    # _revise_node when it resolves a prompt via UnifiedPromptManager.
```

- [ ] **Step 4: Implement — `_revise_node`**

Find this block inside `_revise_node` (around line 1118-1122):

```python
    revise_prompt, prompt_template_key, prompt_template_version = (
        _resolve_revise_prompt(
            draft=state["draft"], aug_block=aug_block,
        )
    )
```

Replace with:

```python
    revise_prompt, prompt_template_key, prompt_template_version = (
        _resolve_revise_prompt(
            draft=state["draft"], aug_block=aug_block,
        )
    )
    # Writer prompt-size observability (poindexter#868) — measured once per
    # node invocation, not once per underlying _call() retry: a retry (main
    # attempt + variant-fallback retry) resends this exact same
    # revise_prompt, so counting it twice would double-count identical text.
    revise_prompt_chars = len(revise_prompt)
```

Then find the `_revise_node` return statement (around line 1200-1208):

```python
    return {
        **state,
        "draft": new_draft,
        "revision_loops": state.get("revision_loops", 0) + 1,
        # Stash on state so run() can surface them on the writer return
        # for the caller stage to forward into capability_outcomes.
        "prompt_template_key": prompt_template_key,
        "prompt_template_version": prompt_template_version,
    }
```

Replace with:

```python
    return {
        **state,
        "draft": new_draft,
        "revision_loops": state.get("revision_loops", 0) + 1,
        # Stash on state so run() can surface them on the writer return
        # for the caller stage to forward into capability_outcomes.
        "prompt_template_key": prompt_template_key,
        "prompt_template_version": prompt_template_version,
        "writer_prompt_revise_chars": (
            state.get("writer_prompt_revise_chars", 0) + revise_prompt_chars
        ),
    }
```

- [ ] **Step 5: Implement — `run()`'s return dict**

Find the block Task 2 Step 5 just inserted:

```python
            "writer_prompt_internal_grounding_chars": final.get("writer_prompt_internal_grounding_chars", 0),
```

Replace with:

```python
            "writer_prompt_internal_grounding_chars": final.get("writer_prompt_internal_grounding_chars", 0),
            "writer_prompt_revise_chars": final.get("writer_prompt_revise_chars", 0),
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_two_pass_writer.py -o addopts="" -q
```

Expected: `108 passed` (106 + 2 new).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/two_pass_writer.py src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer.py
git commit -m "feat(content): accumulate revise-pass prompt size across QA rescue loops

Glad-Labs/poindexter#868"
```

---

### Task 4: Forward into `StageResult.metrics` (`writer_core.py`)

**Files:**

- Modify: `src/cofounder_agent/modules/content/writer_core.py` (two edits: `_generate_via_two_pass_atom` ~line 1044-1049, `execute()`'s `stage_metrics` block ~line 464-472)
- Test A (two-pass forwarding): `src/cofounder_agent/tests/unit/services/atoms/test_writer_atom_variant_hook.py` — **this file already exists** and already tests `_generate_via_two_pass_atom`'s metrics-forwarding for the variant hook (`test_no_active_experiment_state_unchanged` etc.) with a proven patching pattern (`_fake_database_service`, `_fake_site_config`, `_passthrough_lock` are already defined at module level — reuse them, don't redefine).
- Test B (legacy-path negative case): `src/cofounder_agent/tests/unit/services/stages/test_generate_content.py` — **this file already exists**, already tests the legacy path end-to-end via its `_patch_everything()` helper (see `test_included_in_legacy_path_style_context` at line 882 for the exact proven pattern to adapt).

**Interfaces:**

- Consumes: `two_pass_writer.run()`'s return dict (Tasks 2 + 3) — reads the 7 `writer_prompt_*` keys plus the existing `revision_loops`.
- Produces: `GenerateContentStage.execute()`'s `StageResult.metrics` dict gains the 8 `writer_prompt_*` keys (7 char-counts + `writer_prompt_revise_calls`, sourced from `revision_loops`) on the two-pass path only; absent entirely on the legacy path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/services/atoms/test_writer_atom_variant_hook.py` (after `test_no_active_experiment_state_unchanged`, reusing that test's exact fixtures — no new imports needed, `patch`/`AsyncMock`/`_fake_database_service`/`_fake_site_config`/`_passthrough_lock` are already imported/defined in this file):

```python
async def test_prompt_size_metrics_forwarded_to_stage_metrics() -> None:
    """poindexter#868: two_pass_writer.run()'s writer_prompt_* fields (plus
    revision_loops) must land on the metrics dict _generate_via_two_pass_atom
    returns, with revision_loops renamed writer_prompt_revise_calls."""
    from modules.content.stages.generate_content import GenerateContentStage

    stage = GenerateContentStage()
    db = _fake_database_service()

    fake_atom_result = {
        "draft": "draft body",
        "model_used": "default-writer:1b",
        "snippets_used": [],
        "writer_prompt_draft_chars": 5000,
        "writer_prompt_snippet_chars": 2000,
        "writer_prompt_research_chars": 1500,
        "writer_prompt_context_bundle_chars": 0,
        "writer_prompt_override_chars": 300,
        "writer_prompt_internal_grounding_chars": 200,
        "writer_prompt_revise_chars": 800,
        "revision_loops": 1,
    }

    with patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_writer_prompt_override",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_context_bundle",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms.two_pass_writer.run",
        new=AsyncMock(return_value=fake_atom_result),
    ), patch(
        "services.experiment_runner.pick_variant",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.gpu_scheduler.gpu.lock",
        new=_passthrough_lock,
    ):
        _content, _model_used, metrics = await stage._generate_via_two_pass_atom(
            topic="t", style="", tone="", tags=[],
            database_service=db, task_id="task-prompt-metrics",
            niche_slug="glad-labs",
            site_config=_fake_site_config(),
        )

    for key in (
        "writer_prompt_draft_chars", "writer_prompt_snippet_chars",
        "writer_prompt_research_chars", "writer_prompt_context_bundle_chars",
        "writer_prompt_override_chars", "writer_prompt_internal_grounding_chars",
        "writer_prompt_revise_chars",
    ):
        assert metrics[key] == fake_atom_result[key]
    assert metrics["writer_prompt_revise_calls"] == 1


async def test_prompt_size_metrics_absent_when_atom_doesnt_return_them() -> None:
    """If two_pass_writer.run() ever returns without the new keys (e.g. an
    older in-flight LangGraph checkpoint from before this deploy), the
    forwarding block must not KeyError — it omits them, exactly like the
    existing prompt_template_key guard already does."""
    from modules.content.stages.generate_content import GenerateContentStage

    stage = GenerateContentStage()
    db = _fake_database_service()

    fake_atom_result = {
        "draft": "draft body",
        "model_used": "default-writer:1b",
        "snippets_used": [],
    }

    with patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_writer_prompt_override",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.stages.generate_content.GenerateContentStage._read_context_bundle",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms.two_pass_writer.run",
        new=AsyncMock(return_value=fake_atom_result),
    ), patch(
        "services.experiment_runner.pick_variant",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.gpu_scheduler.gpu.lock",
        new=_passthrough_lock,
    ):
        _content, _model_used, metrics = await stage._generate_via_two_pass_atom(
            topic="t", style="", tone="", tags=[],
            database_service=db, task_id="task-no-metrics",
            niche_slug="glad-labs",
            site_config=_fake_site_config(),
        )

    assert "writer_prompt_draft_chars" not in metrics
```

Append to `tests/unit/services/stages/test_generate_content.py` (after `test_included_in_legacy_path_style_context` at line 925 — reuses that test's exact `_patch_everything()` + `_FakeDb` + `_read_niche_slug` override pattern, no new imports needed):

```python
    async def test_legacy_path_stage_metrics_has_no_prompt_size_keys(self):
        """poindexter#868: the legacy (non-niche) generate_blog_post path
        must not emit any writer_prompt_* key — those are two-pass-only."""
        ctx: dict[str, Any] = {
            "task_id": "tlegacy-prompt-metrics",
            "topic": "AI trends",
            "style": "tech",
            "tone": "neutral",
            "target_length": 1200,
            "tags": [],
            "models_by_phase": {},
            "database_service": _FakeDb(),
            "site_config": SiteConfig(initial_config={}),
        }
        stage = GenerateContentStage()
        patches = _patch_everything()
        for p in patches:
            p.start()
        try:
            with patch.object(stage, "_read_niche_slug", AsyncMock(return_value=None)):
                result = await stage.execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()

        assert result.ok is True
        for key in (
            "writer_prompt_draft_chars", "writer_prompt_snippet_chars",
            "writer_prompt_research_chars", "writer_prompt_context_bundle_chars",
            "writer_prompt_override_chars", "writer_prompt_internal_grounding_chars",
            "writer_prompt_revise_chars", "writer_prompt_revise_calls",
        ):
            assert key not in result.metrics
```

This last test must be indented as a method — check whether `test_included_in_legacy_path_style_context` sits inside a `class Test...:` block (it does, per its 4-space method indentation at line 882) and add this new test as a sibling method in that same class, not as a bare module-level function.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_writer_atom_variant_hook.py tests/unit/services/stages/test_generate_content.py -o addopts="" -q -k "prompt_size or prompt_metrics"
```

Expected: FAIL — `KeyError: 'writer_prompt_draft_chars'` on the first two tests; the legacy-path test currently passes vacuously (the loop body's `assert key not in result.metrics` is trivially true today since none of these keys exist anywhere yet) — that's fine, it becomes a real regression guard once Step 3 lands.

- [ ] **Step 3: Implement — `_generate_via_two_pass_atom`**

In `src/cofounder_agent/modules/content/writer_core.py`, find this block (near the end of `_generate_via_two_pass_atom`):

```python
        if result.get("prompt_template_key") is not None:
            metrics["prompt_template_key"] = result.get("prompt_template_key")
        if result.get("prompt_template_version") is not None:
            metrics["prompt_template_version"] = result.get(
                "prompt_template_version"
            )
```

Replace with:

```python
        if result.get("prompt_template_key") is not None:
            metrics["prompt_template_key"] = result.get("prompt_template_key")
        if result.get("prompt_template_version") is not None:
            metrics["prompt_template_version"] = result.get(
                "prompt_template_version"
            )
        # Writer prompt-size observability (poindexter#868) — present
        # whenever the graph reached _draft_node (i.e. always, on this
        # two-pass path). writer_prompt_revise_calls reuses the same
        # revision_loops counter already forwarded above rather than
        # introducing a second independent counter that could drift.
        if result.get("writer_prompt_draft_chars") is not None:
            metrics["writer_prompt_draft_chars"] = result.get("writer_prompt_draft_chars", 0)
            metrics["writer_prompt_snippet_chars"] = result.get("writer_prompt_snippet_chars", 0)
            metrics["writer_prompt_research_chars"] = result.get("writer_prompt_research_chars", 0)
            metrics["writer_prompt_context_bundle_chars"] = result.get("writer_prompt_context_bundle_chars", 0)
            metrics["writer_prompt_override_chars"] = result.get("writer_prompt_override_chars", 0)
            metrics["writer_prompt_internal_grounding_chars"] = result.get("writer_prompt_internal_grounding_chars", 0)
            metrics["writer_prompt_revise_chars"] = result.get("writer_prompt_revise_chars", 0)
            metrics["writer_prompt_revise_calls"] = result.get("revision_loops", 0)
```

- [ ] **Step 4: Implement — `execute()`'s `stage_metrics`**

Find this block inside `GenerateContentStage.execute()`:

```python
        stage_metrics: dict[str, Any] = {
            "content_length": len(content_text),
            "model_used": model_used,
        }
        if metrics.get("prompt_template_key") is not None:
            stage_metrics["prompt_template_key"] = metrics.get("prompt_template_key")
        if metrics.get("prompt_template_version") is not None:
            stage_metrics["prompt_template_version"] = metrics.get(
                "prompt_template_version"
            )
        niche_for_metrics = context.get("niche_slug")
        if niche_for_metrics:
            stage_metrics["niche_slug"] = niche_for_metrics
```

Replace with:

```python
        stage_metrics: dict[str, Any] = {
            "content_length": len(content_text),
            "model_used": model_used,
        }
        if metrics.get("prompt_template_key") is not None:
            stage_metrics["prompt_template_key"] = metrics.get("prompt_template_key")
        if metrics.get("prompt_template_version") is not None:
            stage_metrics["prompt_template_version"] = metrics.get(
                "prompt_template_version"
            )
        # Writer prompt-size observability (poindexter#868) — only present
        # on the two-pass (niche) path; the legacy generate_blog_post path
        # never populates these keys in `metrics`, so `stage_metrics` simply
        # omits them here rather than writing zeros for a path that never
        # measured anything.
        if metrics.get("writer_prompt_draft_chars") is not None:
            for _key in (
                "writer_prompt_draft_chars",
                "writer_prompt_snippet_chars",
                "writer_prompt_research_chars",
                "writer_prompt_context_bundle_chars",
                "writer_prompt_override_chars",
                "writer_prompt_internal_grounding_chars",
                "writer_prompt_revise_chars",
                "writer_prompt_revise_calls",
            ):
                stage_metrics[_key] = metrics.get(_key, 0)
        niche_for_metrics = context.get("niche_slug")
        if niche_for_metrics:
            stage_metrics["niche_slug"] = niche_for_metrics
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_writer_atom_variant_hook.py tests/unit/services/stages/test_generate_content.py -o addopts="" -q
```

Verified baseline (before this task's 3 new tests): `47 passed`.
Expected now: `50 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/writer_core.py src/cofounder_agent/tests/unit/services/atoms/test_writer_atom_variant_hook.py src/cofounder_agent/tests/unit/services/stages/test_generate_content.py
git commit -m "feat(content): forward writer prompt-size metrics into StageResult.metrics

Glad-Labs/poindexter#868"
```

---

### Task 5: Grafana panel row (Pipeline dashboard)

**Files:**

- Modify: `infrastructure/grafana/dashboards/pipeline-merged.json`

**Interfaces:**

- Consumes: `atom_runs.metrics` JSONB rows written by the `atom_runs_capture_enabled`-gated capture path once Task 4 is deployed and at least one two-pass task has run.
- Produces: nothing consumed by later tasks — this is a leaf task.

- [ ] **Step 1: Append the new row + 5 panels**

The dashboard's `panels` array currently ends at line 5127 (the last panel, id `126`, closes with `}`) followed by `],` on line 5128. New panel IDs `200`-`204` are unused anywhere in the file (verified: max existing id is `126`). The new section starts at `y: 296` (right after the lowest existing panel, id `125`/`126`, which end at `y: 287, h: 9` → bottom `296`) so it cannot overlap any existing panel.

Insert this block immediately after line 5127's `}` (i.e., change line 5127-5128 from:

```json
      ]
    }
  ],
```

to:

```json
      ]
    },
    {
      "type": "row",
      "title": "Writer Context Size",
      "collapsed": false,
      "gridPos": { "h": 1, "w": 24, "x": 0, "y": 296 },
      "panels": [],
      "id": 200
    },
    {
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "description": "Average total draft-call and revise-call prompt size sent to the writer, over time. poindexter#868.",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "palette-classic" },
          "custom": {
            "drawStyle": "line",
            "lineWidth": 1,
            "fillOpacity": 10,
            "showPoints": "auto",
            "axisPlacement": "auto"
          },
          "unit": "none",
          "min": 0
        },
        "overrides": []
      },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 297 },
      "id": 201,
      "options": {
        "tooltip": { "mode": "multi", "sort": "desc" },
        "legend": { "displayMode": "list", "placement": "bottom" }
      },
      "pluginVersion": "11.0.0",
      "targets": [
        {
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "format": "time_series",
          "rawQuery": true,
          "rawSql": "SELECT $__timeGroupAlias(created_at, '$__interval'), AVG((metrics ->> 'writer_prompt_draft_chars')::numeric) AS \"draft_chars\", AVG((metrics ->> 'writer_prompt_revise_chars')::numeric) AS \"revise_chars\" FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(created_at) GROUP BY 1 ORDER BY 1;",
          "refId": "A"
        }
      ],
      "title": "Avg Writer Prompt Size Over Time",
      "type": "timeseries"
    },
    {
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "description": "Average draft-call prompt size broken down by context section — answers which section dominates. poindexter#868.",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "continuous-BlPu" },
          "mappings": [],
          "min": 0,
          "unit": "none"
        }
      },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 297 },
      "id": 202,
      "options": {
        "displayMode": "gradient",
        "orientation": "horizontal",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "showUnfilled": true
      },
      "pluginVersion": "11.0.0",
      "targets": [
        {
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT 'snippets' AS metric, AVG((metrics ->> 'writer_prompt_snippet_chars')::numeric) AS value FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(created_at) UNION ALL SELECT 'research', AVG((metrics ->> 'writer_prompt_research_chars')::numeric) FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(created_at) UNION ALL SELECT 'context_bundle', AVG((metrics ->> 'writer_prompt_context_bundle_chars')::numeric) FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(created_at) UNION ALL SELECT 'override', AVG((metrics ->> 'writer_prompt_override_chars')::numeric) FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(created_at) UNION ALL SELECT 'internal_grounding', AVG((metrics ->> 'writer_prompt_internal_grounding_chars')::numeric) FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(created_at) ORDER BY value DESC;",
          "refId": "A"
        }
      ],
      "title": "Avg Chars by Context Section (draft call)",
      "type": "bargauge"
    },
    {
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "description": "Percent of tasks whose writer went through at least one QA-rescue revise loop. poindexter#868.",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "mappings": [],
          "max": 100,
          "min": 0,
          "thresholds": { "mode": "absolute", "steps": [{ "color": "blue", "value": null }] },
          "unit": "percent"
        }
      },
      "gridPos": { "h": 4, "w": 6, "x": 0, "y": 305 },
      "id": 203,
      "options": {
        "colorMode": "background",
        "graphMode": "area",
        "justifyMode": "center",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "value_and_name",
        "wideLayout": true
      },
      "pluginVersion": "11.0.0",
      "targets": [
        {
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE (metrics ->> 'writer_prompt_revise_calls')::int > 0)::numeric / NULLIF(COUNT(*), 0), 1) AS \"Revised %\" FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(created_at);",
          "refId": "A"
        }
      ],
      "title": "Tasks With a Revise Loop",
      "type": "stat"
    },
    {
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "description": "Average chars added by revise passes, among tasks that revised at least once. poindexter#868.",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "mappings": [],
          "min": 0,
          "thresholds": { "mode": "absolute", "steps": [{ "color": "blue", "value": null }] },
          "unit": "none"
        }
      },
      "gridPos": { "h": 4, "w": 6, "x": 6, "y": 305 },
      "id": 204,
      "options": {
        "colorMode": "background",
        "graphMode": "area",
        "justifyMode": "center",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "value_and_name",
        "wideLayout": true
      },
      "pluginVersion": "11.0.0",
      "targets": [
        {
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT ROUND(AVG((metrics ->> 'writer_prompt_revise_chars')::numeric), 0) AS \"Avg Revise Chars\" FROM atom_runs WHERE atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars' AND (metrics ->> 'writer_prompt_revise_calls')::int > 0 AND $__timeFilter(created_at);",
          "refId": "A"
        }
      ],
      "title": "Avg Revise Chars Added",
      "type": "stat"
    },
    {
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
      "description": "Most recent 25 draft calls by total prompt size — spot-check outliers. poindexter#868.",
      "fieldConfig": { "defaults": { "custom": { "align": "auto", "cellOptions": { "type": "auto" } }, "mappings": [] }, "overrides": [] },
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 309 },
      "id": 205,
      "options": {
        "cellHeight": "sm",
        "footer": { "countRows": false, "fields": "", "reducer": ["sum"], "show": false },
        "showHeader": true
      },
      "pluginVersion": "11.0.0",
      "targets": [
        {
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "local-brain-db" },
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT ar.created_at AS \"Time\", ar.task_id AS \"Task\", LEFT(pt.topic, 60) AS \"Topic\", (ar.metrics ->> 'writer_prompt_draft_chars')::int AS \"Draft Chars\", (ar.metrics ->> 'writer_prompt_revise_chars')::int AS \"Revise Chars\", ((ar.metrics ->> 'writer_prompt_draft_chars')::int + (ar.metrics ->> 'writer_prompt_revise_chars')::int) AS \"Total Chars\", (ar.metrics ->> 'writer_prompt_revise_calls')::int AS \"Revise Calls\" FROM atom_runs ar LEFT JOIN pipeline_tasks pt ON pt.task_id = ar.task_id WHERE ar.atom = 'content.generate_draft' AND ar.metrics ? 'writer_prompt_draft_chars' AND $__timeFilter(ar.created_at) ORDER BY \"Total Chars\" DESC LIMIT 25;",
          "refId": "A"
        }
      ],
      "title": "Recent Tasks by Total Prompt Size",
      "type": "table"
    }
  ],
```

This adds 6 new panel objects total (1 row + 5 content panels), ids `200` through `205` — all previously unused in this file.

Also bump `"version": 2` to `"version": 3` near the bottom of the file (cosmetic revision counter; not load-bearing for file-provisioned dashboards, but keep it consistent with the convention).

- [ ] **Step 2: Validate the JSON parses**

Run:

```
cd infrastructure/grafana/dashboards
python -m json.tool pipeline-merged.json > /dev/null && echo "valid JSON"
```

Expected: `valid JSON`. If this fails, the error message gives a line number — fix the syntax (almost certainly a missing/extra comma from the splice) and re-run.

- [ ] **Step 3: Validate the SQL against a live DB (if available)**

If the local Docker stack is up and `DATABASE_URL` is reachable, run:

```
cd C:/Users/mattm/glad-labs-website/.claude/worktrees/tts-audio-fidelity-0c23bc
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/poindexter \
  "C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" \
  scripts/ci/grafana_panels_lint.py infrastructure/grafana/dashboards/pipeline-merged.json
```

Expected: no `FAIL` rows for the 5 new panels (ids 201-205). `metrics ? 'writer_prompt_draft_chars'` and the `->>`/`::int`/`::numeric` casts are valid against `atom_runs`'s existing `metrics jsonb` column regardless of whether any row has that key yet — the lint runs `EXPLAIN`, not the query itself, so zero matching rows is fine.

If the stack isn't reachable in this environment, skip this step — the same script runs in CI on the PR and will catch a syntax/column error there; note in the PR description that local validation wasn't possible.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/grafana/dashboards/pipeline-merged.json
git commit -m "feat(observability): add Writer Context Size panel row to Pipeline dashboard

Glad-Labs/poindexter#868"
```

---

### Task 6: Docs update + final verification + mark PR ready

**Files:**

- Modify: `docs/architecture/rag-retrieval-stack.md` (append a short section)

- [ ] **Step 1: Add the doc section**

Read the end of `docs/architecture/rag-retrieval-stack.md` first to match its heading level and tone, then append a new `##`-level section:

```markdown
## Writer prompt-size observability (poindexter#868)

Every `content.generate_draft` run on the two-pass (niche) path records how
large the assembled writer prompt actually was, broken down by which part of
this RAG/research stack contributed how many characters. Fields live on
`atom_runs.metrics` (JSONB) for rows where `atom = 'content.generate_draft'`:

| Field                                    | What it measures                                                                                                                         |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `writer_prompt_draft_chars`              | Total size of the fully-rendered draft prompt                                                                                            |
| `writer_prompt_snippet_chars`            | Portion from this atom's own internal RAG snippet block                                                                                  |
| `writer_prompt_research_chars`           | Portion from the `research_context`/SOURCES block (caller-attached + `ResearchService.build_context()` + `build_rag_context()`, layered) |
| `writer_prompt_context_bundle_chars`     | Portion from the dev_diary GROUND TRUTH bundle (0 outside dev_diary)                                                                     |
| `writer_prompt_override_chars`           | Portion from the niche `writer_prompt_override` + operator `writing_style_reference`                                                     |
| `writer_prompt_internal_grounding_chars` | Portion from the #822 prior-work-anchor section                                                                                          |
| `writer_prompt_revise_chars`             | Sum of every QA-rescue revise-pass prompt for this task                                                                                  |
| `writer_prompt_revise_calls`             | How many revise passes ran (same value as `revision_loops`)                                                                              |

Visible on the **Pipeline** dashboard's "Writer Context Size" row. See
[the design doc](../superpowers/specs/2026-07-16-writer-prompt-size-metrics-design.md)
for the full rationale and the forks considered.
```

- [ ] **Step 2: Commit the doc**

```bash
git add docs/architecture/rag-retrieval-stack.md
git commit -m "docs(rag): document the writer prompt-size metrics

Glad-Labs/poindexter#868"
```

- [ ] **Step 3: Run every touched suite together**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_two_pass_writer.py tests/unit/services/test_ai_content_generator.py tests/unit/services/atoms/test_writer_atom_variant_hook.py tests/unit/services/stages/test_generate_content.py -o addopts="" -q
```

Verified combined baseline (before any of this plan's tests): `246 passed`.
Expected now: `255 passed` (9 new tests: 2 from Task 1, 2 from Task 2, 2 from Task 3, 3 from Task 4). 0 failures, 0 errors.

- [ ] **Step 4: Lint + type-check the touched Python files**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m ruff check modules/content/ai_content_generator.py modules/content/atoms/two_pass_writer.py modules/content/writer_core.py
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m mypy modules/content/ai_content_generator.py modules/content/atoms/two_pass_writer.py modules/content/writer_core.py --explicit-package-bases
```

Expected: no new errors (pre-existing errors in these files, if any, aren't this task's responsibility to fix — only confirm no NEW ones were introduced by comparing against a `git stash` baseline if unsure).

- [ ] **Step 5: Push and mark the PR ready for review**

```bash
git push origin claude/writer-context-handoff-50c318
gh pr ready 2639 --repo Glad-Labs/glad-labs-stack
```

- [ ] **Step 6: Report**

Report the final PR URL (`https://github.com/Glad-Labs/glad-labs-stack/pull/2639`) and a one-line summary of what shipped, per this repo's "CI passing is the gate" convention — don't wait for explicit merge approval on this routine, fully-tested change; once CI is green, merge it.

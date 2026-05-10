# Anti-Hallucination Architecture

Poindexter ships with a three-layer guard against AI fabrication. The
guards are layered intentionally: each catches a different failure mode,
and the cheap layers run first so we don't pay LLM cycles on drafts that
a regex would have caught for free.

This doc maps each layer to its source files so you can audit, tune, or
extend the behavior.

## Pipeline ordering

CLAUDE.md's "Content pipeline stages" lists these as separate steps:

> 3.5. Programmatic validator → 3.7. Cross-model review

In code, both happen inside the single `cross_model_qa` stage
(`src/cofounder_agent/services/stages/cross_model_qa.py`). That stage
calls `MultiModelQA.review()`
(`src/cofounder_agent/services/multi_model_qa.py:276`), which runs the
programmatic validator first internally, then fans out to the LLM and
HTTP reviewers. The two stage numbers are kept separate in CLAUDE.md
for narrative clarity, but they share one orchestrator entry point.

## Layer 1 — Prompt-level guards

Files:

- `src/cofounder_agent/prompts/blog_generation.yaml`
- `src/cofounder_agent/prompts/system.yaml`
- `src/cofounder_agent/services/ai_content_generator.py:248-327`
  (`_load_prompts_for_generation` — fetches templates via
  `prompt_manager.get_prompt(...)`)

The public `blog_generation.yaml` and `system.yaml` files are
**intentionally minimal**. They tell the writer what the article is
about, what length to hit, and the bare-minimum hygiene rules ("write
ONLY the article in markdown", "do NOT include image descriptions").
They do not contain the dense fabrication-avoidance instructions, voice
calibration, citation framing, or anti-pattern catalogues that production
content relies on.

Those production-grade instructions live in a separate **Glad Labs
Premium Prompts** pack (sold separately, not part of the public OSS
release). Each public template carries the description:

> "Default prompt — upgrade to Glad Labs Premium Prompts for
> production-quality output"

This is deliberate — it's a freemium gap, not an oversight. See the
`feedback_prompt_quality_gap` design note for the rationale.

**What this layer catches with the public prompts:**

- Image-prompt leakage in the article body (the system prompt forbids
  image descriptions, alt text, and italic scene placeholders)
- Wrong output format (markdown vs JSON for the SEO/social step)

**What it doesn't catch on its own:** every fabrication category
covered by Layers 2 and 3. The minimal public prompt does not try to
talk the model out of inventing people, stats, citations, or company
claims — that work is done downstream where regex and a second LLM can
enforce it deterministically.

## Layer 2 — Programmatic validator

File: `src/cofounder_agent/services/content_validator.py`

Entry point: `validate_content(title, content, topic, tags)` at line
`686`. Runs synchronously, no LLM calls, returns a `ValidationResult`
with a list of `ValidationIssue` objects (each tagged `severity` =
`critical` | `warning` and a `category`).

A second async entry point, `verify_content_urls(content)` at line
`1086`, makes HTTP HEAD requests against every cited URL and is run
separately by the orchestrator (see Layer 3 below).

### Rule groups

| Category                 | Severity           | What it catches                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------ | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `fake_person`            | critical           | Common LLM-fabricated name + title combos (`FAKE_NAME_PATTERNS`, line 98). Examples: "Sarah Chen, CTO at Acme", "Dr. John Smith".                                                                                                                                                                                                                                                                                                                                                |
| `fake_stat`              | critical           | Suspiciously round percentage claims and "according to a 2024 study" patterns (`FAKE_STAT_PATTERNS`, line 105).                                                                                                                                                                                                                                                                                                                                                                  |
| `glad_labs_claim`        | critical           | Impossible company claims — N years of operation, N-person team, named clients, specific revenue figures. Uses the configured company name from `GLAD_LABS_FACTS` (`GLAD_LABS_IMPOSSIBLE`, line 114).                                                                                                                                                                                                                                                                            |
| `fake_quote`             | critical           | Quoted speech attributed to a name, with no link or citation (`FAKE_QUOTE_PATTERNS`, line 123).                                                                                                                                                                                                                                                                                                                                                                                  |
| `fabricated_experience`  | critical           | First-person anecdotes the AI made up — "I was on a call with...", "at my company...", "saved us $X" (`FABRICATED_EXPERIENCE_PATTERNS`, line 166).                                                                                                                                                                                                                                                                                                                               |
| `hallucinated_link`      | critical           | Phrases claiming an internal article exists when none does — "as we discussed in our guide on...", "check out our post" (`HALLUCINATED_LINK_PATTERNS`, line 129).                                                                                                                                                                                                                                                                                                                |
| `unlinked_citation`      | warning → critical | Paper/study references with no URL — "introduced in <Title>", "et al.", bare `arXiv:` and `doi:` IDs (`UNLINKED_CITATION_PATTERNS`, line 139). Promoted to critical when a named source ("Medium", "blog post", "documentation") appears without a URL within 100 chars.                                                                                                                                                                                                         |
| `brand_contradiction`    | warning            | Recommends paid cloud APIs in violation of the local-Ollama brand stance (`BRAND_CONTRADICTION_PATTERNS`, line 159).                                                                                                                                                                                                                                                                                                                                                             |
| `image_placeholder`      | critical           | LLM left literal `[IMAGE: ...]`, `[FIGURE: ...]`, etc. in the body (`IMAGE_PLACEHOLDER_PATTERNS`, line 297).                                                                                                                                                                                                                                                                                                                                                                     |
| `leaked_image_prompt`    | warning            | Italic image-description text the writer was supposed to suppress (`LEAKED_IMAGE_PROMPT_PATTERNS`, line 182).                                                                                                                                                                                                                                                                                                                                                                    |
| `known_wrong_fact`       | configurable       | Patterns loaded from the `fact_overrides` DB table at runtime, cached 5 min (`_load_fact_overrides_sync`, line 212). Each row carries its own severity and explanation, manageable via pgAdmin without a redeploy. Special handling: a fact-only rejection gets a second chance via web fact-check (see Layer 3).                                                                                                                                                                |
| `filler_phrase`          | warning            | LLM crutch phrases — "many organizations have found", "in today's fast-paced", "unlock the full potential of" (`FILLER_PHRASE_PATTERNS`, line 285).                                                                                                                                                                                                                                                                                                                              |
| `filler_intro`           | warning            | "In this post...", "In today's digital..." openers in the first 500 chars.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `banned_header`          | warning            | Generic section titles — `## Introduction`, `## Conclusion`, `## Summary`, `## Background`.                                                                                                                                                                                                                                                                                                                                                                                      |
| `late_acronym_expansion` | warning            | An acronym was used 2+ times bare and only expanded later — "CRM (Customer Relationship Management)" after several uses.                                                                                                                                                                                                                                                                                                                                                         |
| `truncated_content`      | critical           | Content longer than 200 chars that doesn't end with terminal punctuation, code fence, list item, or heading. Indicates the LLM hit its token limit mid-sentence.                                                                                                                                                                                                                                                                                                                 |
| `title_diversity`        | warning            | Title starts with an overused opener — "Beyond the", "Unlocking", "The Ultimate", "Mastering", etc.                                                                                                                                                                                                                                                                                                                                                                              |
| `hallucinated_reference` | warning → critical | Library / API names that don't appear in the Python stdlib, top-500 PyPI packages, or known Ollama models (`_detect_hallucinated_references`, line 611). Pulls candidates from backtick-wrapped tokens and narrative prose ("explore CadQuery to see..."). Also fires when a known library is mentioned in a topic-mismatched post. Source lists live in `brain/hallucination-check/` (`stdlib-python-312.txt`, `pypi-top-500.txt`, `ollama-models.txt`, `library-topics.json`). |

### Severity promotion (GH-91)

Two passes after the main rule sweep, in `validate_content()` around
line 970:

1. **Per-category threshold** — if any single warning category fires
   more than `content_validator_warning_reject_threshold` times
   (default 3, DB-tunable), every warning in that category is promoted
   to critical. This catches "writer hallucinated 9 Medium articles"
   patterns that would otherwise pass.
2. **Named-source-without-URL** — for every `unlinked_citation`
   warning whose matched text contains source-type keywords ("Medium",
   "article", "blog post", "documentation", "paper", "study"), if no
   URL appears within ~100 chars of the match, that warning is
   individually promoted to critical.

### Scoring

`score_penalty = 10 × critical_count + 3 × warning_count` (line 1060).
The `MultiModelQA` orchestrator turns this into the validator's
sub-score: `100 - score_penalty` (capped at 0). It also applies an
**additional** flat penalty to the final aggregated QA score:
`warning_count × content_validator_warning_qa_penalty` (default 3, line
524 of `multi_model_qa.py`). This is GH-91's fix for the case where
9 warnings only shaved ~11 pts off the weighted average — not enough to
cross the Q70 reject threshold when the LLM critic scored 85.

A post fails the validator outright if it has any **critical** issue
remaining after promotion.

## Layer 3 — Cross-model review

File: `src/cofounder_agent/services/multi_model_qa.py`

Entry point: `MultiModelQA.review(title, content, topic,
research_sources, preview_url)` at line `276`. Returns a
`MultiModelResult` with a final aggregate score, an approval boolean,
and the per-reviewer `ReviewerResult` list.

The review function calls Layer 2's `validate_content()` first (line
309). If the validator produces any critical issue **other than**
`known_wrong_fact`, it short-circuits and returns immediately — no LLM
cycles spent on drafts that can't be saved. A `known_wrong_fact`-only
rejection is held for the web fact-check gate to confirm or override.

### Reviewers

Each reviewer is independent. A None return means "skipped, no veto"
(reviewer was disabled, the model was unreachable, or there was
nothing to evaluate).

| Reviewer                     | Provider tag       | Source line                          | Prompt / mechanism                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ---------------------------- | ------------------ | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `programmatic_validator`     | `programmatic`     | 309                                  | Calls Layer 2's `validate_content()`. Score = `100 - score_penalty`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `citation_verifier`          | `http_head`        | 946 (`_check_citations`)             | HTTP HEAD against every external URL via `services.citation_verifier`. Fails if dead-link ratio > `qa_citation_max_dead_ratio` (default 0.30) or count < `qa_citation_min_count`.                                                                                                                                                                                                                                                                                                                                                                                    |
| `ollama_critic`              | `ollama`           | 652 (`_review_with_ollama`)          | Runs the `qa.review` YAML prompt (in `prompts/content_qa.yaml`, sourced through `UnifiedPromptManager`) on local Ollama. Configurable model via `pipeline_critic_model` (default `gemma3:27b`). The prompt explicitly handles the training-cutoff case: "do NOT automatically reject just because you lack knowledge", and grounds factual claims against the optional `SOURCES` block built from `ResearchService.build_context()`.                                                                                                                                 |
| `topic_delivery`             | `consistency_gate` | 1038 (`_check_topic_delivery`)       | Runs the `qa.topic_delivery` YAML prompt (in `prompts/content_qa.yaml`) — checks numeric promises ("10 X" → does the body actually list 10?), named entities ("Llama 4" → not Llama 3), format promise (guide vs opinion), and angle/thesis. Hard binary veto when it fails — bait-and-switch can't be fixed by targeted edits.                                                                                                                                                                                                                                      |
| `internal_consistency`       | `consistency_gate` | 1055 (`_check_internal_consistency`) | Runs the `qa.consistency` YAML prompt (in `prompts/content_qa.yaml`) — looks for recommendation contradictions ("don't use React" + "use Next.js"), factual contradictions, principle contradictions, and code-vs-prose contradictions. Soft veto: only fires a hard reject when its own score is unambiguously low (< `qa_consistency_veto_threshold`, default 50).                                                                                                                                                                                                 |
| `image_relevance`            | `vision_gate`      | 1071 (`_check_image_relevance`)      | Opt-in via `qa_vision_check_enabled` (default false). Downloads up to `qa_vision_max_images` (default 3) inline images, base64-encodes them, sends to `qa_vision_model` (default `qwen3-vl:30b`) with a "rate 0-100 how well the image represents the article's subject" prompt. Catches stock-photo-for-a-FastAPI-post mismatches.                                                                                                                                                                                                                                  |
| `web_factcheck`              | `web_factcheck`    | 1457 (`_web_fact_check`)             | Opt-in via `qa_web_factcheck_enabled` (default true). Extracts product / hardware / version claims via regex (RTX/Llama/Python version patterns), runs DuckDuckGo searches via `WebResearcher`, scores by fuzzy term-match ratio. The **fix** for the training-cutoff problem: local critics reject "RTX 5090 has 32GB VRAM" because they were trained before release; this gate confirms it on the live web. Special role: if the validator's only critical issue was `known_wrong_fact` and this gate confirms the claim, the validator's rejection is overridden. |
| `url_verifier`               | `programmatic`     | 416-465 (inline in `review()`)       | Calls Layer 2's `verify_content_urls()`. Dead links → score=`max(0, 100 - 20×dead_count)`, approved=False (hard veto). All URLs alive → score=`min(100, 80 + 5×external_citation_count)`, capped +15 bonus. Carrot-and-stick: dead links block, good citations are rewarded.                                                                                                                                                                                                                                                                                         |
| `rendered_preview`           | `vision_gate`      | 1276 (`_check_rendered_preview`)     | Opt-in via `qa_preview_screenshot_enabled` (default false) AND requires the orchestrator to pass a `preview_url`. Captures a full-page Playwright screenshot, sends to `qa_preview_vision_model` (default `qwen3-vl:30b`) for layout / readability / broken-image / mangled-HTML detection. The final "yup looks good" sanity check that no text-only QA can do.                                                                                                                                                                                                     |
| `deepeval_brand_fabrication` | `deepeval`         | 1044 (`_check_deepeval_brand`)       | DeepEval-wrapped regex check — wraps Layer 2's `FAKE_*` / `HALLUCINATED_*` / `BRAND_CONTRADICTION` patterns as a `BaseMetric`. Pure-CPU, no LLM call. Score is binary (0.0 or 1.0). First production wire-in of DeepEval (#329 sub-issue 1, advisory by default).                                                                                                                                                                                                                                                                                                    |
| `deepeval_g_eval`            | `deepeval`         | (`_check_deepeval_g_eval`)           | DeepEval `GEval` — chain-of-thought LLM-judge metric grading the post against `deepeval_g_eval_criterion` (default: groundedness + internal consistency + no invented facts). Threshold via `deepeval_threshold_g_eval` (default 0.7), judge model via `deepeval_judge_model` (default `glm-4.7-5090`). Advisory by default.                                                                                                                                                                                                                                         |
| `deepeval_faithfulness`      | `deepeval`         | (`_check_deepeval_faithfulness`)     | DeepEval `FaithfulnessMetric` — every claim in the post must be attributable to a paragraph chunk of `research_sources` (the corpus the writer was given). Skips entirely without research. Threshold via `deepeval_threshold_faithfulness` (default 0.8). Advisory by default.                                                                                                                                                                                                                                                                                      |
| `guardrails_brand`           | `guardrails`       | (`_check_guardrails_brand`)          | guardrails-ai-wrapped `BrandFabricationValidator` — same regex patterns as content_validator, routed through guardrails-ai's `Validator` / `Guard` framework. Cross-framework parallel signal (brand check now reports through three lenses: `programmatic_validator`, `deepeval_brand_fabrication`, and this rail; correlation drift = framework wrapper bug). Master switch `guardrails_enabled` (default true). Advisory.                                                                                                                                         |
| `guardrails_competitor`      | `guardrails`       | (`_check_guardrails_competitor`)     | guardrails-ai `CompetitorMentionValidator` — flags when any name in `app_settings.guardrails_competitor_list` (CSV) appears in the post body. Word-boundary, case-insensitive match. Skipped entirely when the list is empty (no list = no enforcement). Fills a gap DeepEval doesn't cover. Advisory.                                                                                                                                                                                                                                                               |
| `ragas_eval`                 | `ragas`            | (`_check_ragas_eval`)                | Ragas RAG-quality reviewer — averages `faithfulness` + `answer_relevancy` + `context_precision` into one score; per-metric breakdown surfaces in the feedback string. Disabled by default (qa_gates row + `ragas_enabled` master switch both default off) because each call costs ~6K judge tokens. Skipped when `research_sources` is empty (Ragas needs grounding context). Judge model resolved via `cost_tier='budget'` (Lane B). Advisory when enabled.                                                                                                         |

### Aggregation

Inside `MultiModelQA.review()` around line 485:

1. **Filter** — only reviewers with `score > 0` count toward the
   weighted average (`scored_reviews` at line 496). A skipped reviewer
   doesn't drag anything down.
2. **Weighted average** — weights are keyed by `provider`, all
   DB-tunable via `app_settings`:
   - `programmatic` → `qa_validator_weight` (default 0.4)
   - `anthropic` / `google` / `ollama` → `qa_critic_weight`
     (default 0.6)
   - `consistency_gate` / `vision_gate` / `web_factcheck` /
     `url_verifier` → `qa_gate_weight` (default 0.3)
   - `deepeval` (brand-fab, g-eval, faithfulness) → 0.5 fallback
     (no dedicated weight key yet — the rails are advisory while
     we calibrate against published-post archives, so the
     fallback weight is the intentional default)
   - `guardrails` (brand, competitor) → 0.5 fallback (same
     calibration posture as the deepeval rails)
   - `ragas` (single combined eval) → 0.5 fallback (default-off so
     it doesn't normally enter the average; flips on once the
     operator opts in to the RAG-quality signal)
3. **Direct warning penalty** — `final_score -= warning_count ×
content_validator_warning_qa_penalty` (default 3 pts/warning, line
   524). GH-91 fix: this lands on the final score, not the validator
   sub-score, so 9 warnings shave 27 pts instead of 11.
4. **Asymmetric vetoes** — `_reviewer_vetoes()` at line 553. Most
   non-approved reviewers veto outright. The `internal_consistency`
   gate is asymmetric: its non-approval only counts as a veto if its
   own score is < `qa_consistency_veto_threshold` (default 50). A flaky
   "I think section 1 contradicts section 3" report from the critic
   model won't kill an otherwise 85-scoring post.
5. **Fact-check override** — if Layer 2 rejected for
   `known_wrong_fact`-only and the `web_factcheck` reviewer approved
   the claim, the validator's rejection is reversed (line 568).
6. **Final decision** — `approved = all_passed and final_score >=
qa_final_score_threshold` (default 70).

### Degraded-pool guard

When the cross-model critic is unreachable (Ollama down, model not
pulled, timeout), `_review_with_cloud_model()` first tries the
`qa_fallback_critic_model` (default `gemma3:27b`). If that also fails,
`cross_result` is None and the orchestrator sets `critic_skipped = True`
(line 367). At aggregation time, the final score collapses back to the
validator's raw score (line 561) — the system does **not** pretend the
critic passed. A `critic_fallback` audit-log event is also emitted so
the degradation shows up on the `/pipeline` dashboard instead of
silently rotting.

The same "skip, don't veto" pattern applies to every other LLM-backed
reviewer (`topic_delivery`, `internal_consistency`, `image_relevance`,
`rendered_preview`, `web_factcheck`). They return `None` on
unavailability and are dropped from `scored_reviews`. Combined with the
`score > 0` filter, this means a fully-degraded environment with only
the validator running still produces a coherent score, instead of
artificially passing because all the critics returned 0.

### Rewrite loop

Owned by the stage, not the orchestrator:
`src/cofounder_agent/services/stages/cross_model_qa.py`. When
`MultiModelQA.review()` returns `approved=False` AND
`aggregate_issues_to_fix()` finds at least one blocking issue, the
stage calls `_rewrite_draft()` with the `qa.aggregate_rewrite`
prompt (in `prompts/content_qa.yaml`). The prompt feeds every
flagged issue (validator + LLM critics + consistency checker) into a
single targeted rewrite — minimum changes, same structure, same
length within 10%. Up to `qa_max_rewrites` attempts (default 2). A
topic-delivery failure bails immediately — those can't be patched.

If the primary writer returns less than 50% of the original length
(thinking-mode models eating their token budget on `<think>` tags),
the rewrite falls back to `qa_fallback_writer_model` (default
`gemma3:27b`) and emits a `writer_fallback` audit event.

## What still slips through

- **Plausible-but-wrong factual claims** that don't trigger a regex
  rule, aren't named in the `fact_overrides` table, and don't trip the
  LLM critic or the web fact-check. The web gate is a fuzzy term-match,
  not real verification — it confirms "the words appear together
  somewhere on the web" rather than "the claim is true".
- **Fabricated reasoning** the critic doesn't catch — a coherent
  argument built on top of one false premise reads as internally
  consistent. The consistency gate looks for self-contradiction, not
  truth.
- **Stylistic AI-tells** the filler-phrase list doesn't yet cover.
- **Hallucinated libraries with names that happen to look real** — a
  fake `pyrequests-async` package name beats both the stdlib list and
  the top-500 PyPI list.
- **Prompt-level fabrication discipline** in the public OSS release.
  The freemium prompt gap is intentional; production-grade
  fabrication-avoidance language ships in Glad Labs Premium Prompts.

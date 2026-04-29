# The three layers of anti-hallucination

**Last updated:** 2026-04-29
**Tracks:** [#184](https://github.com/Glad-Labs/poindexter/issues/184)
**Source-of-truth files:**

- `src/cofounder_agent/services/content_validator.py`
- `src/cofounder_agent/services/multi_model_qa.py`
- `src/cofounder_agent/prompts/blog_generation.yaml`,
  `src/cofounder_agent/prompts/system.yaml`

CLAUDE.md claims the pipeline has "three layers" of anti-hallucination
protection. This doc names them, says what each one catches, and is
honest about what slips through.

The phrase comes from CLAUDE.md's **Key Principles** section:
"_Anti-hallucination: Three layers — prompts, LLM QA, programmatic
validator._" Below is what those three are in code.

---

## Pipeline ordering

From CLAUDE.md ("Content pipeline stages"), the layers fire in this
order on every draft:

1. **Stage 2 — Draft (Ollama writer)** — Layer 1 (prompt-level guards)
   acts here, by shaping the writer's behavior before any text exists.
2. **Stage 3.5 — Programmatic validator** — Layer 2. Deterministic
   regex / list checks on the finished draft. Runs first inside
   `MultiModelQA.review()`.
3. **Stage 3.7 — Cross-model review (Claude Haiku / fallback Ollama
   critic)** — Layer 3. A different model than the writer reads the
   draft and votes.

The validator and the cross-model critic both live inside the
`cross_model_qa` stage (`src/cofounder_agent/services/stages/cross_model_qa.py`)
because `MultiModelQA.review()` runs the validator as its first step
(`multi_model_qa.py:432-447`) and short-circuits on critical issues
before paying any LLM tokens (`multi_model_qa.py:460-467`).

---

## Layer 1 — Prompt-level guards

**What it is.** Instructions baked into the system + draft prompts
telling the writer model what NOT to fabricate.

**Canonical files.**

- `src/cofounder_agent/prompts/blog_generation.yaml` — the
  default/free-tier prompts shipped in the public repo. These are
  intentionally minimal (e.g. `blog_generation.initial_draft` just
  forbids image placeholders / leaked image prompts; see lines
  12-17).
- `src/cofounder_agent/prompts/system.yaml` — default system prompt
  for the content writer (10 lines, generic).
- The richer anti-fabrication prompts live in the **premium prompts
  pack** (`glad-labs-prompts` repo) and are loaded out of the
  `app_settings` / `prompts` DB table at runtime by
  `services/prompt_manager.py`. The OSS repo intentionally ships weak
  defaults — see `feedback_prompt_quality_gap.md` (the "widen free vs
  premium prompt gap" guideline). Operators can override any prompt
  via OpenClaw without code changes.

**What it catches.**

- Image placeholder leakage — `Do NOT include lines like
"*A dramatic scene of...*" or "[IMAGE: ...]" or "![description]"`
  (`blog_generation.yaml:14-17`).
- Stylistic guardrails — tone, length window, target audience shape
  (`blog_generation.blog_system_prompt`).
- The premium prompt pack adds explicit anti-fabrication instructions
  ("don't invent people, statistics, quotes, or citations") — those
  text strings live in DB rows, not in this repo.

**What slips through.** Almost everything that matters. Prompt-level
guards are advisory: a 27B-parameter writer model under temperature

> 0 will routinely ignore "do not invent statistics" and produce
> "according to a 2024 McKinsey report, 73% of teams..." anyway.
> Prompts shape the prior; they do not enforce truth. The two layers
> below exist precisely because Layer 1 is unreliable.

**Honest note:** in the public OSS repo today, the default prompts
do **not** carry strong anti-fabrication language. The "prompt-level
guard" claim in CLAUDE.md is mostly satisfied by the premium prompt
pack and by operator-edited rows in the `prompts` table — not by
what's checked into `prompts/*.yaml` here. If you fork Poindexter and
do nothing else, Layer 1 is essentially "be a polite blog writer".
Layers 2 and 3 are what actually protect a fork from publishing
fabrications.

---

## Layer 2 — Programmatic validator (deterministic, no LLM)

**What it is.** Pure-Python regex + list rules in
`services/content_validator.py`. No LLM judgment — pattern matching
that either fires or doesn't. Runs in microseconds and returns a
`ValidationResult` with critical / warning issues and a numeric score
penalty.

**Canonical entry point.**
`validate_content(title, content, topic, tags, *, site_config)` —
`content_validator.py:812`.

**What it catches** (pattern groups defined in the same file):

| Rule group                          | Lines                                              | Catches                                                                                                                             |
| ----------------------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `FAKE_NAME_PATTERNS`                | 127-131                                            | "Sarah Chen, CEO of Glad Labs", "Dr. Foo Bar"                                                                                       |
| `FAKE_STAT_PATTERNS`                | 134-139                                            | "73% reduction in...", "according to a 2024 McKinsey report"                                                                        |
| `GLAD_LABS_IMPOSSIBLE`              | 143-149                                            | "our team of 50 engineers", "we have spent years..." (operator brand is 12mo old, solo)                                             |
| `FAKE_QUOTE_PATTERNS`               | 152-155                                            | `"X solved everything," said Jane Smith, CEO`                                                                                       |
| `HALLUCINATED_LINK_PATTERNS`        | 158-162                                            | "see our guide on X", "as we discussed in a previous post"                                                                          |
| `UNLINKED_CITATION_PATTERNS`        | 168-193                                            | "introduced in I-DLM: Introspective...", "Smith et al. (2023)", `arXiv:2401.12345` without a URL, "as noted in this Medium article" |
| `BRAND_CONTRADICTION_PATTERNS`      | 196-200                                            | "OpenAI API pricing", "bill from Anthropic" — Poindexter is Ollama-first                                                            |
| `FABRICATED_EXPERIENCE_PATTERNS`    | 203-216                                            | "I was on a call with a CTO last week", "saved us $1,200/month"                                                                     |
| `FIRST_PERSON_TITLE_PATTERNS`       | 234-239                                            | Titles containing "we / our / I" — operator is solo+AI, "How We Built X" implies a team                                             |
| `IMAGE_PLACEHOLDER_PATTERNS`        | 356-362                                            | `[IMAGE-1: description]`, `[FIGURE: ...]` left in the body                                                                          |
| `FALLBACK_TEMPLATE_PATTERNS`        | 345-352                                            | Phrases from the deleted template-fallback path (#121) — defense-in-depth                                                           |
| Hallucinated library/API references | 574-595, `_detect_hallucinated_references` 737-809 | `` `schedule_callback(event)` `` (not a real asyncio API), "explore CadQuery" in an AI/ML post (off-topic library)                  |
| `fact_overrides` table              | loaded by `_load_fact_overrides_sync` 249-311      | Operator-curated "known wrong fact" patterns, hot-editable in pgAdmin without redeploy                                              |
| Truncation detection                | 1096-1115                                          | Content ending mid-sentence (LLM hit token limit)                                                                                   |
| Late-acronym expansion              | 1081-1091                                          | "CRM (Customer Relationship Management)" appearing after CRM was already used three times                                           |

**Severity promotion** (`content_validator.py:1139-1232`):

- If a single warning category fires more than
  `content_validator_warning_reject_threshold` (default 3) times,
  every warning in that category is upgraded to critical. So 9
  unlinked citations stop being a "minor warning" and start blocking
  the post.
- Any `unlinked_citation` whose matched text names a source type
  ("Medium", "article", "blog post", "documentation", "paper",
  "study") AND has no URL within 100 chars is promoted to critical
  individually — that's the "as noted in this Medium article"
  hallucinated-attribution pattern.

**Plus a separate async pass:** `verify_content_urls()`
(`content_validator.py:1261-1354`) HEAD-requests every external URL
in the content. Dead links (HTTP >= 400) become critical issues.
This runs as part of the URL-verifier QA gate (`url_verifier`
reviewer in `multi_model_qa.py:652-713`), not from `validate_content`
itself.

**What slips through.**

- **Anything semantic.** The validator is regex; if the writer says
  "Postgres uses MVCC for transaction isolation" and that claim is
  subtly wrong in context, no pattern fires.
- **Plausible-looking statistics that don't match the listed
  shapes.** `FAKE_STAT_PATTERNS` catches "73% reduction in latency"
  but "latency improved roughly threefold" wears different clothes.
- **Real-looking names that aren't on the bad-pattern list.** A
  fabricated quote attributed to "Linus Torvalds" doesn't trip
  `FAKE_NAME_PATTERNS` (which keys on Sarah/John/Emily/etc.) — it'd
  need the cross-model critic to catch it.
- **Library names not on the stdlib / top-500 PyPI / Ollama lists**
  in `brain/hallucination-check/` are flagged as "likely
  hallucinated" — including real-but-niche packages. Tuned for low
  false-positive rate via a generous whitelist
  (`_HALLUCINATION_WHITELIST`, lines 600-674), but a brand-new
  legitimate library can still get warned.
- **Off-list "known wrong facts."** Only patterns in the
  `fact_overrides` DB table fire. If the operator hasn't seeded a
  pattern for "the RTX 5090 has 32GB VRAM" (it has 32GB; common
  mistake is to say 24GB), the validator won't catch the wrong
  number.

---

## Layer 3 — Cross-model LLM review (different "DNA" than the writer)

**What it is.** A different LLM than the writer reads the draft and
returns a structured verdict. Runs as one or more reviewers inside
`MultiModelQA.review()`. The writer is local Ollama (default
`glm-4.7-5090` per `feedback_model_selection.md`); the critic is a
different Ollama model by default (`gemma3:27b`) or — when the
operator opts in via `pipeline_critic_model` — Anthropic Claude
Haiku. Different training data → different blind spots.

**Canonical files.**

- `src/cofounder_agent/services/multi_model_qa.py` — the orchestrator
  and the prompt strings (`QA_PROMPT` line 214, `TOPIC_DELIVERY_PROMPT`
  line 148, `CONSISTENCY_PROMPT` line 183).
- `src/cofounder_agent/services/stages/cross_model_qa.py` — the
  pipeline stage that wires `MultiModelQA` into the workflow runner.

**What it catches** (each is a separate reviewer with its own gate
row in the `qa_gates` DB table):

| Reviewer                        | Code (`multi_model_qa.py`)                                 | What it checks                                                                                                                                                                                              |
| ------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `programmatic_validator`        | 432-447                                                    | (Layer 2 — see above; runs first inside the same stage.)                                                                                                                                                    |
| `url_verifier`                  | 652-713                                                    | Dead links + bonus for verified external citations. Dead links veto.                                                                                                                                        |
| `ollama_critic` / Claude critic | `_review_with_ollama` 991+, prompt at `QA_PROMPT` line 214 | "Is the content factually accurate? Flag any claims that seem fabricated. Are there hallucinated people, statistics, or quotes?" Returns JSON `{approved, quality_score, feedback}`.                        |
| `topic_delivery`                | `_check_topic_delivery` 1374-1389, prompt 148-180          | Bait-and-switch detection: title says "11 indie hackers" but body lists three and pivots. Binary veto.                                                                                                      |
| `internal_consistency`          | `_check_internal_consistency` 1391-1405, prompt 183-211    | Section 1 says "don't use React" and section 3 says "use Next.js" (Next.js is React). Advisory unless score < 50.                                                                                           |
| `image_relevance`               | `_check_image_relevance` 1407+                             | Inline images actually match the surrounding text (vision model).                                                                                                                                           |
| `rendered_preview`              | `_check_rendered_preview` 1615+                            | Screenshots `/preview/{hash}` and asks a vision model whether the rendered page looks broken (overflowing tables, missing CSS, mangled HTML).                                                               |
| `web_factcheck`                 | `_web_fact_check` 1799+                                    | DuckDuckGo lookup to verify claims the validator or critic flagged — overrides false positives caused by the critic's training cutoff (see `feedback_qa_critic_cutoff.md` / `project_qa_critic_cutoff.md`). |

**Optional parallel rails** (off by default, opt-in via
`app_settings`): `guardrails_brand_rail`, `deepeval_brand_rail`,
`self_consistency_rail` — `multi_model_qa.py:491-541`. These are
"learning artifacts" that join the same vote aggregation when
enabled but don't change baseline behavior.

**Aggregation** (`multi_model_qa.py:731-825`). Reviewer scores are
weighted (validator 0.4, critic 0.6, gates 0.3 by default — all
DB-tunable). A post must clear `qa_final_score_threshold` (default 70) AND have no vetoing reviewer to be approved. The "vetoing"
logic is asymmetric on purpose: `topic_delivery` and `url_verifier`
hard-veto; `internal_consistency` only vetoes if its own score
< `qa_consistency_veto_threshold` (default 50) because Ollama
critics over-report contradictions.

**Degraded-pool guard** (`multi_model_qa.py:827+`). If more than
`multi_model_qa_max_reviewer_error_rate` (default 0.5) of the
reviewers throw, the result auto-rejects regardless of the survivors'
votes — a single happy reviewer is not adversarial QA.

**What slips through.**

- **The critic's own training cutoff.** Claude Haiku trained pre-2024
  flags real post-cutoff products as fabricated. The `web_factcheck`
  gate exists specifically to compensate — see the prompt at
  `multi_model_qa.py:231-247`, which explicitly instructs the
  critic: "I have not heard of this is not the same as this does
  not exist." It mostly works, but a critic that's _aggressively_
  certain something is fake can still drag the score below threshold.
- **Subtle factual errors the critic doesn't notice.** If the writer
  is wrong about "Postgres MVCC isolation levels" and the critic
  also doesn't know better, both pass and the lie ships. Adversarial
  QA only works when the two models actually disagree.
- **Cost.** Every cross-model review costs tokens (zero $ for the
  Ollama critic, ~$0.001 per review for Claude Haiku). The
  `cost_guard` will skip the cloud critic when the daily budget is
  exhausted, leaving only the local Ollama critic — a real reviewer
  but lower-leverage than a different-vendor critic.
- **Vision gates need a vision model.** `image_relevance` and
  `rendered_preview` skip silently when no vision-capable Ollama
  model is configured. Layout / image-relevance bugs ship.
- **Reviewer errors are silent absences.** When a reviewer throws,
  the score is computed without its vote. The
  `errored_reviewers` field on `MultiModelResult` and the
  `qa_reviewer_error` audit-log row exist so this is at least
  _visible_ (`multi_model_qa.py:310-346`) — but a half-quiet pool
  still scores posts.

---

## Summary table

| Layer                     | When                    | Cost                      | Determinism        | Best at                                                                                 | Worst at                                                                  |
| ------------------------- | ----------------------- | ------------------------- | ------------------ | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 1. Prompt guards          | Before draft exists     | Free                      | Probabilistic      | Shaping format, banning known leakage patterns                                          | Stopping confident fabrication mid-paragraph                              |
| 2. Programmatic validator | Stage 3.5, on the draft | ~ms, free                 | 100% deterministic | Named bad patterns (fake stats / quotes / citations / placeholders / impossible claims) | Anything semantic; novel-looking lies                                     |
| 3. Cross-model LLM review | Stage 3.7, on the draft | Tokens + ~5-30s wall time | LLM judgment       | Topic delivery, internal contradiction, plausibility, off-list fabrications             | Errors the critic shares with the writer; training-cutoff false positives |

The layers are complementary: Layer 1 reduces the rate at which lies
appear; Layer 2 catches the predictable shapes Layer 1 misses; Layer
3 catches what Layer 2 has no rule for. None of them on their own is
sufficient, which is the whole point of running all three.

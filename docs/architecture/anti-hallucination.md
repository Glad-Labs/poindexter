# Anti-Hallucination Architecture

Poindexter ships with a three-layer guard against AI fabrication. The
guards are layered intentionally: each catches a different failure mode,
and the cheap layers run first so we don't pay LLM cycles on drafts that
a regex would have caught for free.

This doc maps each layer to its source files so you can audit, tune, or
extend the behavior.

## Pipeline ordering

The programmatic validator and the cross-model review were historically
one step: the `cross_model_qa` stage called `MultiModelQA.review()`,
which ran the programmatic validator first internally, then fanned out
to the LLM and HTTP reviewers.

**Atom-cutover #355 (live 2026-06-02) split that apart.** The
`cross_model_qa` stage is deleted; on the live `canonical_blog`
`graph_def` path the cross-model review runs as composable atoms in
`src/cofounder_agent/modules/content/atoms/` — `qa.programmatic` → `qa.critic` →
`qa.deepeval` → `qa.ragas` → `qa.vision` →
`qa.topic_delivery` → `qa.citations` → `qa.unlinked_attribution` →
`qa.consistency` → `qa.self_consistency` → `qa.web_factcheck` → `qa.aggregate`.
Each rail atom delegates to the matching `MultiModelQA` rail methods (the
`_review_with_cloud_model` critic plus the per-rail DeepEval, Ragas, vision,
topic-delivery, citation, consistency, self-consistency, and web-factcheck
checks) and appends its `ReviewerResult` to the `qa_rail_reviews` state channel.
`qa.aggregate` combines them into the gate decision and halts the graph
on reject. `multi_model_qa.py` stays as the rail library the atoms
delegate to.

#### The scoring contract: advisory rails inform, they don't gate (2026-06)

`aggregate_rail_reviews` (`modules/content/atoms/_qa_rail_common.py`) folds the
rail reviews into one decision via two **independent** gates:

1. **Veto** — any _non-advisory_ rail with `approved=False` rejects outright
   (`vetoed_by`), regardless of score.
2. **Threshold** — `qa_final_score` = the provider-weighted mean of the
   _non-advisory_ rail scores; a pass also needs
   `qa_final_score >= qa_final_score_threshold` (prod `80`).

**Advisory rails (`qa_gates.<rail>.required_to_pass=false`) are excluded from
both gates** — they cannot veto _and_ their scores no longer feed the weighted
mean. Previously they were vetoless but still averaged in, so a cluster of
low-scoring advisory rails (`deepeval_g_eval`, `ragas_eval`,
`internal_consistency`, all ~65-70) silently dragged otherwise-clean posts under
the 80 bar and rejected them — the 2026-06 "nothing passes QA" throughput
incident. The required rails alone (`programmatic_validator`, `llm_critic`,
`deepeval_brand_fabrication`, `deepeval_faithfulness`) average ~87, comfortably
clear of 80, so dropping the advisory drag makes the gate reachable _without
lowering the threshold_. A degenerate all-advisory review set falls back to
scoring everything, so the score can't collapse to a spurious `0`. Pinned by
`test_qa_rail_common.py::test_advisory_reviews_excluded_from_score`.

The programmatic validator's warning penalty is gentler and DB-tunable too: each
non-critical warning shaves `qa_validator_warning_penalty` points (default `5`,
was a hard-coded `10`) so soft nits nudge the score instead of sinking a clean
draft (7 warnings → 65, not 30). A _critical_ fabrication still zeroes the score
and vetoes.

#### The bounded rescue cycle: one rewrite pass before a salvageable reject

Before hard-rejecting, `qa.aggregate` checks whether the reject is _rescuable_
(`_qa_rail_common.is_rescuable_reject`): a soft LLM-critic veto (every vetoing
reviewer's `provider ∈ {anthropic, google, ollama}`), or a below-threshold
score with no hard veto at all (`vetoed_by == []`, `final_score < threshold` —
exactly the scoring-contract reject described above). A `programmatic_validator`
veto (fabrication), a gate-provider veto (consistency / vision / web_factcheck /
url), or a synthetic `missing_required:*` veto is **never** rescuable. On a
rescuable reject (and while the durable `qa_rewrite_attempts` counter is under
`app_settings.qa_rewrite_max_attempts`, default 1), `qa.aggregate` defers — it
emits `_goto="qa_rewrite"` instead of persisting the reject, and the compiler's
branch router routes to the `qa.rewrite` atom for one targeted revision pass.
`qa.rewrite` increments the counter and resets the `qa_rail_reviews` channel (a
`{"__reset__": True}` sentinel honored by the `_merge_rail_reviews` reducer) so
the re-run scores the revised draft cleanly; a `loop`-flagged back-edge re-runs
the whole QA block. The counter lives in the LangGraph checkpoint, so the cycle
is bounded even across a kill-and-resume. Like the `known_wrong_fact` rescue
below, this only ever PREVENTS a salvageable hard-reject — a fabrication veto
still halts immediately. Pinned by `test_qa_rail_common.py`
(`is_rescuable_reject`), `test_qa_rewrite_atom.py`, and
`test_qa_aggregate_atom.py::TestQaAggregateRescueDispatch`.

#### Self-heal before paging: flag-and-continue, never silent-discard (2026-06)

Hard QA was throwing away finished drafts. A single rail veto — frequently a
false positive — discarded an avg-79 draft outright, even though a human
approval gate (`awaiting_approval`) already sits downstream. That is the content
analogue of paging an operator instead of self-healing, except worse: it
self-_destructs_. The redesign makes `qa.aggregate`'s terminal behavior a
DB-master-switched choice (`app_settings.qa_flag_instead_of_reject`):

- **`false` (legacy)** — a non-rescuable reject halts the graph and persists
  `status=rejected` (`persist_qa_reject` → `_halt`). The draft is discarded.
- **`true` (self-heal)** — `qa.aggregate` does **not** halt. It sets the
  `qa_flagged` state channel `True`, emits a `qa_flagged_surfaced` audit row
  (severity `warning`, with `final_score` / `threshold` / `vetoed_by` /
  `attempts`), and rides the existing forward edge to `awaiting_approval` like
  any other draft. `compile_meta` formats the rail breakdown into `qa_feedback`
  and `persist_task` writes it; the flag rides `task_metadata->>'qa_flagged'`.
  The bounded rewrite cycle above still runs first — flag-and-continue is only
  what happens _after_ a rescue is exhausted or was never eligible.

Under the self-heal switch the rescue eligibility also **broadens**
(`is_rescuable_reject(..., broaden=True)`): every veto becomes regen-eligible
except the two that text can't fix — `vision_gate` and `url_verifier`
(`_NON_TEXT_FIXABLE_PROVIDERS`) — so even a programmatic/fabrication veto gets
its one rewrite pass before being flagged rather than discarded.

A flagged draft is never auto-published: `auto_publish_gate.evaluate(...,
qa_flagged=True)` short-circuits to `gate_state="block_qa_flagged"`,
`would_fire=False`. **`rejected_final` is now operator-only** — the pipeline
flags and surfaces; the operator (CLI `poindexter pipeline qa <task>`, the `⚑`
marker in `pipeline list-paused` / MCP `list_tasks`, the **Self-Heal Before
Paging** row on the QA Rails dashboard) decides discard vs. approve vs. regen.
The switch ships `false` (inert) and is flipped to `true` after the in-Docker
e2e. Pinned by `test_qa_aggregate_atom.py` (flag-and-continue) +
`test_qa_rail_common.py` (`broaden`).

The rails call the individual per-rail check methods, **not** the full
`MultiModelQA.review()` — so the programmatic validator that `review()`
ran as its first step (Layer 2, below) is no longer co-located in the QA
block. The validator code still exists (`MultiModelQA.review()` and the
`atoms.run_validators` atom both wrap `content_validator.validate_content`),
but it is not currently wired as a `canonical_blog` graph_def node; the
pattern-based `quality_evaluation` stage (node 5) is the only
programmatic scorer on the live graph_def path.

#### Grounding the rails: `research_context` must ride `context_updates`

The two grounding rails — `ragas_eval` and `deepeval_faithfulness` — score
content against the **retrieved research corpus** the writer consulted.
On the graph_def path that corpus reaches them through the
`research_context` PipelineState channel: `stage.generate_content` builds
it (`_collect_research_context` — caller-attached context + `ResearchService`

- pgvector RAG) and the `qa.ragas` / `qa.deepeval` atoms read
  `state.get("research_context")`, forwarding it to `_check_ragas_eval` /
  `_check_deepeval_faithfulness`.

The thread only survives if the producer returns `research_context` in its
**`StageResult.context_updates`**. `make_stage_node`
(`services/template_runner.py`) merges _only_ `context_updates` back into the
shared LangGraph state — a bare `context["research_context"] = ...` mutation
of the stage's local dict is discarded. Glad-Labs/poindexter#553 was exactly
this: `generate_content` mutated the local context but didn't echo the value
into `context_updates`, so after the #355 graph_def flip every grounding rail
read `None` and skipped 100% of the time ("research_sources empty — needs a
corpus"). The fix returns `research_context` in `context_updates`; the
`test_research_context_threading.py` suite pins the producer→adapter→state
contract so this can't silently regress. (This is the same channel-dropping
failure mode the `seo_keywords_list` channel hit — see the PipelineState
comment in `template_runner.py`.)

#### Grounding the writer: the corpus must carry real source text, not just links

Threading the corpus into state (above) is necessary but not sufficient — the
corpus also has to _contain_ something worth grounding on.
`ResearchService.build_context` assembles three source kinds into
`research_context`: verified reference links, internal post links, and **fresh
web sources**. The web slice is where the writer gets current, citable
facts/numbers, so two settings govern how much real text it carries:

- **`research_extract_web_content`** (default `true`) — when on, `_web_search`
  calls `WebResearcher.search()`, which fetches each DuckDuckGo result and
  extracts up to `web_research_max_content_chars` (2000) of clean page text via
  BeautifulSoup. When off, it falls back to `search_simple()` — titles, URLs,
  and DDG snippets only, **no page text**. Snippet-only grounding starves the
  writer: the prompt carries a title and a ~100-char teaser per source and zero
  numbers, so the model fills the gap by inventing them — which `qa.critic` and
  the `ragas` / `faithfulness` rails then (correctly) flag. This is the
  fabrication-pressure sibling of the RAG-corpus-pollution failure
  (`rag_source_filter`): one _starves_ the corpus, the other _poisons_ it; both
  surface as "QA rejects everything."
- **`research_web_content_chars_per_source`** (default `600`) — caps how much of
  each source's extracted text is injected into the generation prompt (~one
  substantial paragraph × up-to-5 sources ≈ 3 KB), keeping the prompt lean while
  still handing the writer real sourced material. Tune up for denser grounding,
  down for tighter token budgets.

`build_context` keeps the one-line DDG snippet **and** appends a bounded
`Source text:` excerpt per source, so the writer sees a titled, summarised, and
substantiated source rather than a bare link.

#### Assembling the draft: the canonical_blog writer prompt must carry structure

Good grounding (above) is necessary but not sufficient: a local writer model
also has to _assemble_ 1,000+ words cleanly. The `canonical_blog` niche writer
is `atoms.two_pass_writer`, whose draft prompt is
`atoms.two_pass_writer.generate_with_context`
(`skills/content/two-pass-writer/SKILL.md`) — **not** the richer
`blog-generation/SKILL.md` (that's the non-niche `generate_with_ai` path). When
the niche prompt was bare (topic + angle + snippets, no structure/length/voice
guidance), the local model rambled: it duplicated whole sections, left
placeholder scaffolding unresolved, emitted prose citations instead of inline
links, and overran into a mid-sentence truncation — the `ollama_critic` +
`programmatic` hard-gate vetoes that reject a draft even when
`deepeval_faithfulness` scores a perfect 100 (grounding fine, assembly broken;
glad-labs-stack#1672 follow-up).

The enriched prompt carries the discipline the rails grade against: real `##`
H2 headings (not bold-text fakes), "cover each point once — no duplication or
padding, finish on a complete sentence", positive citation ("use the numbers,
link each inline to its source URL"), and **grounded first person** — "we"/"our"
is welcome for work that appears in a source, never for invented work. The voice
change ships with its scorer: `qa_allow_first_person_niches` gains `glad-labs`
so `quality_scorers`' `first_person_claims` rail stops penalising the
publisher's real first-hand voice. The `revise_prompt` carries the same
"return the post exactly once, do not duplicate" guard, and its inline
`_REVISE_PROMPT_FALLBACK` is pinned byte-for-byte to the skill default by
`test_two_pass_writer_prompts.py`.

Re-running the enriched prompt end-to-end (task `601283cc`) cleared all four
assembly vetoes — the draft came back tight (5K chars, no duplication, no
`[verbatim]` echoes, complete ending) and `ollama_critic` rose 35 → 92 — leaving
two narrower residuals that the **citation-discipline** follow-up targets. (1)
The writer invented a statistic ("~25% increase") with no source; the prompt now
bans it outright — "never invent a statistic, percentage, benchmark, or version
number; use a figure only when it appears verbatim in a source". (2) The writer
echoed the internal `[source/ref]` labels its background snippets were _shown_
in, inline, as pseudo-citations (`[token_efficiency.md feedback_token]`). The
root cause is the snippet renderer itself, so the fix lives there
(`ai_content_generator._format_snippet_block`): background notes are now rendered
as plain `From <source>:` prose with **no inline-bracket template to copy** and
the `ref` slug dropped (it was the most-echoed token), and the prompt reinforces
that "the only square brackets in the post are real markdown links — never
reproduce a background-note label". Both prompt directives are pinned by
`test_two_pass_writer_skill.py::test_generate_prompt_carries_citation_and_antifabrication_directives`
and the renderer by its `test_format_snippet_block_*` cases.

The re-run of _that_ fix (task `2b0255ad`) cleared the fabrication veto and
**passed the gate** (`awaiting_approval`, score 88 — the first canonical*blog
post through QA in the thread), but the citation behaviour \_mutated* rather than
resolved: denied the inline `[source/ref]` echo, the writer switched to academic
footnotes (`[^1]`) plus a bottom reference block of guessed/placeholder URLs
(`[markaicode.com/...]`, a literal "Placeholder URLs derived from text snippet
domains" note). The fix is structural — the writer has internal facts with no
URL and keeps trying to cite them — so the prompt now bans the leaky forms
outright: no footnote markers, no "Sources/References/Footnotes/Further reading"
section, no placeholder/guessed URLs. Un-URLed (internal) facts go in plain prose
with no marker; the pipeline's `resolve_internal_link_placeholders` /
`internal_link_coherence` stages add the real `/posts/<slug>` links afterward.
Pinned by `test_generate_prompt_bans_footnotes_and_placeholder_urls`.

#### The draft-presence gate: the writer must produce a non-empty draft

The `qa.*` rails all guard `if not content: return {}` — so on an **empty
draft** every rail emits nothing, `qa.aggregate` sees zero reviews, and
`aggregate_rail_reviews([])` rejects at `score 0` with `reviewer_count:0,
vetoed_by=[]`. That reject is a lie: the post wasn't bad, the writer produced
nothing, and the real cause is buried in the worker logs. A reasoning writer
model can intermittently emit all of its tokens into the thinking channel and
return an empty `content` field (the same failure mode that
`services/llm_text.py::resolve_structured_model` exists to avoid for
structured-extraction calls). Glad-Labs/poindexter#691 was exactly this — an
empty revise pass overwrote a good draft with `''`, which then flowed through
the whole graph to a misleading `reviewer_count:0` reject.

Two layers fix it, both before the rails ever see the draft:

- **Root cause (`two_pass_writer._revise_node`, default path):** an empty
  revise response is retried once with the same model (preserving writer
  quality — no downgrade to a weaker model for the article body), and if it's
  still empty the **prior draft is kept** (unresolved `[EXTERNAL_NEEDED]`
  markers stripped so the loop terminates) rather than zeroed. A
  `writer_empty_draft_kept_prior` finding keeps the self-heal visible.
- **Fail-loud guard (`GenerateContentStage`):** when the final draft is empty
  or shorter than `writer_min_draft_chars` (default 200 — a real
  canonical*blog post is never a single sentence) the stage does a
  **load-bearing terminal write** (`status='failed'` + a specific
  `error_message` + a `writer_empty_draft` finding) \_before* raising. The
  status sticks even though the graph_def node wrapper swallows the raise into
  an (unhonored) `_halt` and keeps running — the GH-90 terminal-write guard
  then blocks the downstream QA-reject write, so the writer-empty cause is what
  surfaces. This is the same load-bearing-DB-write idiom `qa.aggregate` uses on
  reject. `test_generate_content.py` + `test_two_pass_writer.py` pin both
  layers.

#### The vision/preview gate: `preview_url` must reach `qa.vision`

The `qa.vision` rail runs two vision-model checks the deleted
`MultiModelQA.review()` ran inline:

- **Image relevance** (`_check_image_relevance` → reviewer `image_relevance`,
  aliased to the `vision_gate` qa_gates row) — does each image match the
  content? Scores the featured/hero image (`state['featured_image_url']`)
  **plus** the inline body images, deduped via `_images_to_score`. The hero
  leads the scan set so it always survives the `qa_vision_max_images` cap (an
  N≥cap inline-image post would otherwise push the hero past truncation and
  leave it unscored). Opt-in via `qa_vision_check_enabled`. Needs no URL.
- **Rendered-preview screenshot** (`_check_rendered_preview` → reviewer
  `rendered_preview`) — screenshots the post's `/preview/{token}` page via
  headless chromium and feeds the PNG to a vision model to catch layout
  breaks, missing CSS, overflowing tables, broken images. Opt-in via
  `qa_preview_screenshot_enabled`. **Needs a `preview_url`.**

Both checks went cold after the #355 cutover (Glad-Labs/poindexter#563): they
only ever lived inside `review()`, which the live path stopped calling, and
the rendered-preview gate had a `if preview_url:` guard that was never reached
on the pipeline because `review()` was never called with a `preview_url`. The
fix wires a `qa.vision` rail (between `qa.ragas` and `qa.aggregate`) that runs
both checks and appends their `vision_gate`-provider reviews to
`qa_rail_reviews`.

The screenshot leg needs a URL **during** the QA block, which runs BEFORE
`finalize_task` — and `finalize_task` is where the `preview_token` used to be
minted. So `stage.verify_task` now mints the token at the top of the pipeline
and surfaces `preview_token` + `preview_url` in its `context_updates` (the
same ride-`context_updates` requirement as `research_context` above);
`finalize_task` reuses that token rather than minting a second one, keeping the
dashboard link and the QA screenshot pointed at the same URL. `qa.vision`
reads `preview_url` softly from state. `test_qa_vision_atom.py` +
`test_verify_task_preview.py` pin the threading.

##### Graduating `vision_gate` to `required_to_pass` (#563)

Wiring the rail was necessary but not sufficient: when `vision_gate` was first
flipped to `required_to_pass=true` it rejected **100%** of posts. Three
independent defects stacked, each only fatal once the gate was load-bearing:

1. **WebP is undecodable by the vision model.** image-gen inline images are stored
   as WebP on R2 (`r2_upload_service` converts PNG/JPEG → WebP for the web), but
   `qwen3-vl` (via Ollama) cannot decode WebP — it receives no image and returns
   an empty / "no image" verdict. `_check_image_relevance` now normalizes every
   downloaded image to JPEG in-memory (`_normalize_image_for_vision`; Pillow,
   leaving the published WebP untouched). JPEG/PNG pass through unchanged.
2. **The `<think>` trace truncated the JSON verdict.** `qwen3-vl` emits a long
   reasoning trace even with `think=False`, and it shares the `num_predict`
   budget with the answer — at 400 the JSON (`{"scores":…,"overall":…}`) got cut
   off and the rail returned `None`. Budget is now the DB-tunable
   `qa_vision_num_predict` (default 1024).
3. **A vacuous run failed closed.** When neither leg produced a review,
   `qa.vision` returned `{}` — which the `qa.aggregate` vacuous-pass guard
   correctly reads as "required rail absent → reject". It now emits a
   **deliberate, advisory, non-vetoing** review instead (`reviewer=image_relevance`,
   which aliases to `vision_gate`): a _pass by vacuity_ when there are genuinely
   no inline images, or a _fail-open pass + operator page_ when images were
   present but the model couldn't assess them (operator policy:
   keep the pipeline moving, alert to fix the model — vision is 1 of 12 rails).
   Advisory means it satisfies the required-gate presence check without vetoing
   or feeding a fabricated score into the weighted mean.

A latent fourth bug: `rendered_preview` was missing from `_REVIEWER_TO_GATE`, so
a preview-only review didn't satisfy `vision_gate`; it now aliases there too
(both vision legs share the `vision_gate` row). `test_qa_vision_atom.py`,
`test_vision_image_normalization.py`, and `test_qa_gates_db_writer.py` pin all four.

#### Four more dropped checks: topic-delivery, citations, consistency, web-factcheck

Four additional `MultiModelQA.review()` checks went cold the same way the
vision gate did — they only ever lived inside the deleted `review()`, so the
#355 cutover stopped running them on the live path. Each is restored as a thin
rail atom (between `qa.vision` and `qa.aggregate`), mirroring `qa.vision`'s
shape, and is **advisory-first**: it scores + surfaces feedback but neither
vetoes nor feeds the gated score (advisory rails are excluded from the weighted
mean — see the scoring contract above), to be graduated later via
`qa_gates.<gate>.required_to_pass` (poindexter#454) once verified live.

- **`qa.topic_delivery`** (`_check_topic_delivery` → reviewer `topic_delivery`,
  `consistency_gate` provider; Glad-Labs/poindexter#658) — the bait-and-switch
  veto (title promises something the body never delivers). It ran
  _unconditionally_ in `review()` (no gate row); the fix seeds a NEW
  `qa_gates.topic_delivery` row, advisory-first (`required_to_pass=false`).
  Flip it `true` to restore the legacy binary veto.
- **`qa.citations`** (`_check_citations` → reviewer `citation_verifier`,
  `http_head` provider; Glad-Labs/poindexter#659) — the default-on dead-link /
  minimum-citation gate (the `qa_citation_*` settings family). Distinct from
  the advisory `url_verifier` rail (which the live path never halted on). The
  fix seeds a NEW `qa_gates.citation_verifier` row, advisory-first.
- **`qa.consistency`** (`_check_internal_consistency` → reviewer
  `internal_consistency`, `consistency_gate` provider; Glad-Labs/poindexter#660)
  — cross-section self-contradiction. The baseline `qa_gates.consistency` row
  was already advisory; the rail just makes it run again. The legacy low-score
  hard-veto escape (`< qa_consistency_veto_threshold`) lived in `review()`, not
  the rail aggregator, and is intentionally NOT re-introduced — that would be a
  new veto path, out of scope for the additive restore.
- **`qa.web_factcheck`** (`_web_fact_check` → reviewer `web_factcheck`,
  `web_factcheck` provider; Glad-Labs/poindexter#661) — DuckDuckGo product/spec
  verification (the training-cutoff override). Ordered **last** in the qa block,
  immediately before `qa.aggregate`, because of the rescue below.

##### The `known_wrong_fact` web-rescue (the #661 regression fix)

`review()` had a rescue: when the programmatic validator's _only_ critical was a
`known_wrong_fact` (the stale-regex false-positive on a real post-cutoff
product — see Layer 2's `known_wrong_fact` rule, and `project_qa_critic_cutoff`),
the rejection was deferred to the web fact-check, which could **override** it if
the web confirmed the claim. After #355 that rescue path no longer existed:
`qa.programmatic` emitted a non-advisory `known_wrong_fact` veto that
**hard-rejected legit post-cutoff content with no web second opinion**. This is
a behavior regression, not just a dropped advisory rail.

The fix restores the rescue across two atoms:

1. `qa.programmatic` flags the condition — when every critical it found is a
   `known_wrong_fact`, it sets the `qa_known_wrong_fact_only` PipelineState
   channel.
2. `qa.aggregate` (which owns the veto decision) reads that flag + the
   `qa.web_factcheck` rail's verdict via
   `_qa_rail_common.known_wrong_fact_rescued`: when the _only_ non-advisory veto
   is `programmatic_validator`, the flag is set, AND `web_factcheck` approved,
   the validator veto is **suppressed** and the pass is approved — mirroring
   `review()`'s `_fact_only_rejection` carve-out. A missing/failed web check
   upholds the rejection (the genuinely-wrong-fact case).

This rescue only ever PREVENTS a wrong hard-reject; it never introduces a new
veto. `test_qa_web_factcheck_atom.py` (rail + rescue helper + end-to-end
aggregation), `test_qa_programmatic_atom.py` (the flag), and
`test_qa_aggregate_atom.py` (the state-read) pin it.

#### Named-source attributions: deterministic repair + advisory flag (#765)

The writer is told to cite the research corpus inline as markdown links but
does so **inconsistently** — naming a source in prose while dropping its URL
("as noted by M. Huzaifa Rizwan…", "GetMaxim points out…", "(Ai Insights)") even
as it links others correctly in the same post. Nothing in Layers 2–3 catches
this: `citation_verifier` (`qa.citations`) only HTTP-HEAD-checks URLs that
_already exist_ (a missing link is invisible to it), the `unlinked_citation`
validator rule is defanged + misses these phrasings, and the LLM critic reads
right past them. Because the corpus (name→URL) is still in `state['research_context']`
at draft time, the fix is a deterministic lookup, not a guess:

- **`content.reconcile_citations`** (repair, after the writer block, before
  `quality_evaluation`/`qa.*`): parses the corpus back out of `research_context`,
  then at every _attribution site_ whose named subject matches a corpus source by
  its distinctive **domain handle** (`getmaxim.ai` → "GetMaxim"), wraps the
  subject in a markdown link to that source's URL. Matching is tied to attribution
  grammar (a verb/preposition frame) — prose outside attribution sites is never
  touched, so a corpus domain like `python.org` can't turn every "python" into a
  link. High precision by construction; the inserted links then flow through
  `qa.citations`' dead-link check. Gated by `citation_reconcile_enabled` (default
  on). The pure matching core is `modules/content/atoms/_citation_match.py`.
  - **Verb frame (scan-1).** The subject-first site ("Keychron describes…",
    "GetMaxim points out…") recognises a **broader verb set for repair than for
    the advisory flag** below. "describes" & co. are common in plain prose
    ("Section 2 describes…", "Apple describes…"), so the advisory scan omits them
    to avoid over-flagging; the repair scan can afford them because it only ever
    links behind the **domain gate** — a broader verb widens the candidate set
    without risking a bad link ("Section" grounds to no corpus domain, so it's
    never linked). This mirrors the match-side asymmetry: repair is strict on
    MATCH, so it can be loose on FRAME; advisory is loose on match, so it stays
    strict on frame. The seam is `find_attributions(…, repair=True)`, broadening
    the conservative `_SUBJECT_VERBS` with `_REPAIR_EXTRA_VERBS`.
  - **Re-point pass (scan-3, same atom).** The writer also fabricates citations
    the other way: it wraps a brand in a markdown link to that brand's _own_
    domain but invents the **path** — a 404 the host-only `scrub_fabricated_links`
    keeps (the host is trusted) and that `qa.citations` then counts dead.
    `repoint_fabricated_citations` swaps the fabricated href for the corpus
    source's real URL when, and only when: (1) the link URL's registrable domain
    is **not** a multi-tenant platform, (2) exactly **one** corpus source sits on
    that domain (unambiguous target), (3) the link text names that brand by
    domain handle, and (4) the URL isn't already the corpus URL. The
    multi-tenant denylist (`DEFAULT_MULTITENANT_HOSTS`, override
    `citation_repoint_multitenant_hosts`) is load-bearing: on `github.com` /
    `arxiv.org` / `dev.to` a _different path is different content_ (a different
    repo/paper/article), so re-pointing there would silently mis-cite — the exact
    "a wrong auto-link is worse than a missing one" regression this machinery
    forbids. Gated by `citation_repoint_enabled` (default on). **Scope, measured:**
    real dead trusted-domain citations are dominated by fabricated links on those
    multi-tenant platforms _with no corpus source to re-point to_ (e.g. an
    invented `dev.to/<handle>/<slug>` article); those are out of reach of any
    deterministic same-domain repair and stay **advisory** under `qa.citations`.
    The durable lever for them is writer-prompt discipline (cite only
    corpus-provided URLs), not broader matching here.

- **`qa.unlinked_attribution`** (advisory rail, after `qa.citations`): sees the
  RESIDUAL — attribution subjects that match no corpus source and aren't already
  linked (author names / unknown brands a deterministic linker can't safely
  repair). It scores that density (gentle penalty, floored) and lists the
  offenders in its feedback (→ `qa_feedback` + the QA Rails dashboard, which
  groups by reviewer dynamically). **Advisory** via
  `qa_gates.unlinked_attribution.required_to_pass` (seeded `false`) — it surfaces
  feedback but neither vetoes nor feeds the gated score (advisory rails are
  excluded from the weighted mean); graduate it with the poindexter#454 lever. Returns nothing when there's no corpus (real-vs-fabricated is then the
  deferred grounded-LLM pass's job).

A grounded LLM citation pass is intentionally deferred — measure the
deterministic coverage first. `test_citation_match.py` (matching core),
`test_citation_atoms.py` (both atoms), and `test_canonical_blog_spec.py`
(wiring) pin it.

#### Placeholder citations: programmatic hard-gate + prompt tightening (#766)

A related but distinct failure: glm-4.7, told to "cite the SOURCES / internal
snippets **inline as markdown links**", invents a _placeholder_ citation when it
has a claim but no real URL — a bracketed label echoing the prompt's own
vocabulary (`[INTERNAL SNIPPET]`), a bare `` `source` `` tag, or a markdown link
whose href is a placeholder word (`[the analysis](internal_context_link)`,
`[x](url)`). These are draft artifacts, not real attributions, so the #765
reconciler (which links _named_ sources to corpus URLs) doesn't touch them, and
the advisory rails let them through: the `unlinked_citation` /
`citation_artifact` validator rules are `warning`-level (they only veto once a
per-category count threshold is exceeded), the dead-link check only validates
`http(s)://` hrefs so a `(internal_context_link)` target slips past, and the LLM
critic flags them in prose but scores above the gate. A 2026-06-11 niche rerun
shipped `[INTERNAL SNIPPET]` repeatedly plus a dead `(internal_context_link)` to
preview this way.

Two layers close it:

- **`placeholder_citation`** (programmatic, `content_validator.py`): a
  **critical** rule (so it bypasses the warning-promotion threshold and
  hard-rejects on the first match at `qa.programmatic`). Patterns:
  `[INTERNAL SNIPPET(S)]` / `[INTERNAL SOURCE(S)]` (the `internal` prefix is
  required so the bare word "snippet" as legitimate link text never fires),
  `[citation needed]`, and markdown links whose href is a placeholder word
  (`url` / `link` / `source` / `citation` / `internal[_ ]context[_ ]link` / …).
  Scanned over **code-span-blanked** text so a markdown tutorial showing
  `[text](url)` as an example doesn't false-positive. Zero-false-positive by
  construction — no reader-facing prose contains these tokens.

- **Writer-prompt tightening** (`modules/content/atoms/two_pass_writer.py::_draft_node`): the
  base + SOURCES instructions now state that `[EXTERNAL_NEEDED: …]` is the ONLY
  permitted placeholder and that an ungroundable claim must be written plainly
  **with no citation marker** — reducing the emission rate at the source while
  the hard-gate guarantees none reach preview.

`test_content_validator.py::TestPlaceholderCitation` pins the catches and the
false-positive guards.

## Layer 1 — Prompt-level guards

Files:

- `skills/content/blog-generation/SKILL.md` (migrated from
  `prompts/blog_generation.yaml`, #528)
- `src/cofounder_agent/skills/content/writer/SKILL.md`
- `src/cofounder_agent/modules/content/ai_content_generator.py:248-327`
  (`_load_prompts_for_generation` — fetches templates via
  `prompt_manager.get_prompt(...)`)

The public blog-generation skill and `system.yaml` files are
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

### Layer 1.5 — prompt-echo guard (writer-output sanitizer)

File: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py`
(`_strip_echoed_preamble`).

A weak/quantized writer model can _regurgitate its own prompt_ instead
of executing it — dumping the topic line, the angle, the niche
`writer_prompt_override`, the revise/expand instructions, the citation
rules, and its own planning notes as the OPENING of the "article".
Captured 2026-06-29 (task `ba4d627a`, `gemma-4-31B-it-qat`): the stored
draft opened with the topic, then `Technical/Professional.`, then the
niche descriptor, then `Expand from ~57 words to closer to 1500 words.
Add genuine substance...`, with the real body underneath. The #2009
keep-best expansion pass _compounded_ it — an echoed expansion is
"longer" than a thin original, so keep-best adopted the contaminated
version.

The guard deterministically strips a contiguous echoed/scaffolding
preamble off the front of the draft (no LLM call), both on the graph
draft and on the expansion output before the keep-best comparison. It is
**gated on a high-precision echo signature** with two independent
triggers: an _identity echo_ — the first lines restating ≥2 of {topic,
angle, niche-override} — or an _instruction echo_ — ≥2 of the first six
non-blank lines matching the instruction-imperative patterns (`Expand a
draft...`, `Do NOT pad...`, `Preserve all facts...`), or one of each.
The instruction trigger was added 2026-07-01 after tasks `e46b449c` /
`9921678f` / `ecaf0c01` leaked a **paraphrased** expand prompt (`Expand
a draft from ~416 words ... to closer to 651 words. Genuine added
substance...`) with zero identity lines, which the identity-only gate
could not see. One instruction-shaped line alone never triggers — a real
article can legitimately open with a single imperative. The guard is a
no-op on clean drafts and **never zeroes a draft** (if no substantial
body survives the strip it keeps the original and the contamination
becomes a human-review signal). Every strip emits a
`writer_prompt_echo_stripped` finding (`severity=warn` → Discord) so a
recurring echo surfaces the real fix: a writer model that can't follow
instructions on long prompts (memory: `feedback_writer_model_canary`,
`feedback_self_heal_not_suppress`). This is distinct from the off-topic
_research_ that can co-occur (free DuckDuckGo occasionally returns
unrelated results); the echo guard cleans the model's self-dump, not the
research corpus.

Downstream backstop: the Layer 2 validator's `prompt_leak` rule (10b)
pairs its exact-marker list with `detect_prompt_echo_paraphrase` —
regexes matching the _structure_ of the expand/revise instructions
("expand a … draft", "to approximately N words", "no padding", "no
preamble", "preserve all facts", …). ≥2 distinct shapes in the
code-span-stripped body is a CRITICAL `prompt_leak` issue, so a novel
echo shape the writer-side guard misses is still rejected at
`qa.programmatic` instead of reaching the approval queue (an FP scan
over all 302 published posts at introduction time found zero posts with
even one shape hit).

Second backstop — `planning_dump` (10c, 2026-07-01 follow-up): a
planning/outline dump with **no instruction lines at all** evades both
detectors above (task `e46b449c` persisted `*   Topic: Ellipses...`,
`*   Source Material provided:`, an inventory of source bullets, then
the article fused mid-line onto the last bullet — 0 markers, 0 shapes,
1 scaffold tell — and reached `awaiting_approval` at quality 85).
`detect_planning_dump_preamble` closes that residual STRUCTURALLY: a
finished article never _opens_ with a wall of outline bullets, so ≥6
bullet lines dominating (≥60%) the pre-heading opening PLUS ≥2 distinct
planning-vocabulary families ("\* Topic:", source-inventory phrases,
notes-to-self like "I should provide…", format-meta like "Check word
count") is a CRITICAL `planning_dump` issue. Vocabulary alone never
fires without the structure (an article ABOUT writing can discuss word
count in prose), and structure alone never fires without the vocabulary
(a legitimate opening list survives). FP scan at introduction time: 0
fires across all 301 `posts` bodies; the only fires in the last 200
`pipeline_versions` were the three 2026-07-01 incident drafts plus the
three prior known leak incidents (`ba4d627a`, `06715fb0`, `0f70f736`) —
the detector independently rediscovers every historical leak. The
`qa.review` critic prompt carries the same criterion LLM-side (see the
`ollama_critic` row in Layer 3): the 2026-07-01 incident showed the
judge anchoring its review on the TITLE — praising "the introduction
effectively defines meta-automation" over a body that was ellipsis
planning bullets — and landing at exactly 70, where the score-over-
boolean parse (`approved = score >= 70 or approved`) discards the
model's own `approved=false`.

## Layer 2 — Programmatic validator

File: `src/cofounder_agent/modules/content/content_validator.py`

Entry point: `validate_content(title, content, topic, tags)` at line
`686`. Runs synchronously, no LLM calls, returns a `ValidationResult`
with a list of `ValidationIssue` objects (each tagged `severity` =
`critical` | `warning` and a `category`).

A second async entry point, `verify_content_urls(content)` at line
`1086`, makes HTTP HEAD requests against every cited URL and is run
separately by the orchestrator (see Layer 3 below).

### Rule groups

| Category                 | Severity           | What it catches                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------ | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `fake_person`            | critical           | Common LLM-fabricated name + title combos (`FAKE_NAME_PATTERNS`, line 98). Examples: "Sarah Chen, CTO at Acme", "Dr. John Smith".                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `fake_stat`              | critical           | Suspiciously round percentage claims and "according to a 2024 study" patterns (`FAKE_STAT_PATTERNS`, line 105).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `glad_labs_claim`        | critical           | Impossible company claims — N years of operation, N-person team, named clients, specific revenue figures. Uses the configured company name from `GLAD_LABS_FACTS` (`GLAD_LABS_IMPOSSIBLE`, line 114).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `fake_quote`             | critical           | Quoted speech attributed to a name, with no link or citation (`FAKE_QUOTE_PATTERNS`, line 123).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `fabricated_experience`  | critical           | First-person anecdotes the AI made up — "I was on a call with...", "at my company...", "saved us $X" (`FABRICATED_EXPERIENCE_PATTERNS`, line 166).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `hallucinated_link`      | critical           | Phrases claiming an internal article exists when none does — "as we discussed in our guide on...", "check out our post" (`HALLUCINATED_LINK_PATTERNS`, line 129).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `unlinked_citation`      | warning (advisory) | Paper/study references with no URL — "introduced in <Title>", "et al.", bare `arXiv:` and `doi:` IDs (`UNLINKED_CITATION_PATTERNS`, line 139). **Demoted to a non-promotable warning 2026-06-09 (Glad-Labs/poindexter#692):** the pattern can't tell a fabricated external ref from a rhetorical phrase ("According to our analysis"), a self-reference ("Published in Glad Labs"), or a legitimate news citation, so accumulated false positives were vetoing high-quality posts. Count-promotion default is now `0` (never); the hard veto for genuinely fabricated external refs lives with the LLM critic + `qa.web_factcheck`. Re-arm via `content_validator_unlinked_citation_warning_threshold` > 0. The named-source-without-URL promoter is still available behind `content_validator_named_source_promote_enabled` (default off).                                                                                                                                                                                                                                                            |
| `brand_contradiction`    | warning            | Recommends paid cloud APIs in violation of the local-Ollama brand stance (`BRAND_CONTRADICTION_PATTERNS`, line 159).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `image_placeholder`      | critical           | LLM left literal `[IMAGE: ...]`, `[FIGURE: ...]`, etc. in the body (`IMAGE_PLACEHOLDER_PATTERNS`, line 297).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `leaked_image_prompt`    | warning            | Italic image-description text the writer was supposed to suppress (`LEAKED_IMAGE_PROMPT_PATTERNS`, line 182).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `known_wrong_fact`       | configurable       | Patterns loaded from the `fact_overrides` DB table at runtime, cached 5 min (`_load_fact_overrides_sync`, line 212). Each row carries its own severity and explanation, manageable via pgAdmin without a redeploy. Special handling: a fact-only rejection gets a second chance via web fact-check (see Layer 3).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `filler_phrase`          | warning            | LLM crutch phrases — "many organizations have found", "in today's fast-paced", "unlock the full potential of" (`FILLER_PHRASE_PATTERNS`, line 285).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `filler_intro`           | warning            | "In this post...", "In today's digital..." openers in the first 500 chars.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `banned_header`          | warning            | Generic section titles — `## Introduction`, `## Conclusion`, `## Summary`, `## Background`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `buzzword_density`       | warning            | LLM-tell vocabulary above the per-post distinct-trigger threshold (`buzzword_density_threshold`, default 2). Banned set: `delve` / `delves` / `delving` / `delved`, `testament`, `tapestry`, `multifaceted`, `at its core`, `at the heart of` (`LLM_TELL_BUZZWORDS`, line 403). Word-boundary anchored — won't false-positive on `delver` or `manifested`. Fires only when the _distinct_ trigger count exceeds the threshold so a single accidental "delve" doesn't reject.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `late_acronym_expansion` | warning            | An acronym was used 2+ times bare and only expanded later — "CRM (Customer Relationship Management)" after several uses.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `truncated_content`      | critical           | Content longer than 200 chars that doesn't end with terminal punctuation, code fence, list item, or heading. Indicates the LLM hit its token limit mid-sentence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `title_diversity`        | warning            | Title starts with an overused opener — "Beyond the", "Unlocking", "The Ultimate", "Mastering", etc.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `hallucinated_reference` | warning (advisory) | Library / API names that don't appear in the Python stdlib, top-500 PyPI packages, or known Ollama models (`_detect_hallucinated_references`, line 611). Pulls candidates from backtick-wrapped tokens and narrative prose ("explore CadQuery to see..."). Also fires when a known library is mentioned in a topic-mismatched post. Source lists live in `brain/hallucination-check/` (`stdlib-python-312.txt`, `pypi-top-500.txt`, `ollama-models.txt`, `library-topics.json`). **Internal project files / repo paths are exempt** (`_looks_like_file_or_path`): a backtick token ending in a source/config extension (`api_token_auth.py`, `Component.tsx`) or containing a path separator is the author referencing a repo file, not a library, and is skipped before any list lookup. **Demoted to a non-promotable warning 2026-06-09 (Glad-Labs/poindexter#692):** the pattern can't tell a fabricated lib from a brand-new post-cutoff product (`Claude Mythos 5`), so count-promotion default is now `0` (never); re-arm via `content_validator_hallucinated_reference_warning_threshold` > 0. |
| `citation_artifact`      | warning            | Bracketed numeric citations (`[12]`) and parenthetical academic citations (`(Smith, 2023)`, `(Smith et al., 2024)`) the LLM emits from training on papers/Wikipedia — real sources must be Markdown links, not dangling markers (`CITATION_ARTIFACT_PATTERNS`). poindexter#532.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `placeholder_citation`   | critical           | Placeholder citations the writer invents when it lacks a real URL — bracketed labels echoing the prompt vocabulary (`[INTERNAL SNIPPET]` / `[INTERNAL SOURCE]`), `[citation needed]`, and markdown links with a placeholder href (`(url)` / `(link)` / `(source)` / `(internal_context_link)`) (`PLACEHOLDER_CITATION_PATTERNS`). The `internal` prefix is required on the snippet/source labels so "snippet" as legitimate link text never fires; scanned over code-span-blanked text so a markdown tutorial showing `[text](url)` as an example doesn't false-positive. Zero-false-positive by construction → critical (hard-reject on first match). Closes the #765-adjacent gap that shipped `[INTERNAL SNIPPET]` + a dead `(internal_context_link)` past the advisory rails (a 2026-06-11 niche rerun). #766.                                                                                                                                                                                                                                                                                     |
| `leaked_path_token`      | warning            | poindexter's own source identifiers leaking into published niche content — `src/cofounder_agent`, `cofounder_agent`, `glad-labs-stack` (`LEAKED_PATH_TOKEN_PATTERNS`). Kept to unambiguous internal tokens so generic coding prose doesn't false-positive; dev_diary opts out via `applies_to_niches`. poindexter#532.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `orphaned_attribution`   | warning → critical | Orphaned attribution fragment — the writer dropped the named source, leaving a sentence that opens with a lowercase attribution verb ("... is enough. points out that this approach misses the mark.") (`ORPHANED_ATTRIBUTION_PATTERNS`). Case-sensitive (`(?-i:...)`) so well-formed "He points out that..." prose doesn't fire; requires a trailing "that". Distinct from `citation_artifact` above (numeric/parenthetical refs). Warning first per poindexter#532; the GH-91 per-category threshold promotes to critical once several fire in one post.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `internal_path_leak`     | warning            | Internal reference tokens like `[memory/...]` / `[brain/...]` bleeding into reader-facing prose (`INTERNAL_PATH_LEAK_PATTERNS`). Sibling of `unresolved_placeholder` (which owns `[posts/...]`); anchored on a known internal-store namespace + slash so genuine prose brackets don't fire, and skips real Markdown links via `(?!\()`. poindexter#532.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

### Severity promotion (GH-91)

Two passes after the main rule sweep, in `validate_content()` around
line 970:

1. **Per-category threshold** — if any single warning category fires
   more than `content_validator_warning_reject_threshold` times
   (default 3, DB-tunable), every warning in that category is promoted
   to critical. This catches "writer hallucinated 9 Medium articles"
   patterns that would otherwise pass. **Per-category overrides** in
   `_per_category_overrides` raise or lower the bar for a specific
   category (a threshold of `0` disables promotion entirely). As of
   2026-06-09 (Glad-Labs/poindexter#692), `unlinked_citation` and
   `hallucinated_reference` both default to `0` — they are advisory-only
   warnings that never single-handedly veto a post, because the regex
   can't distinguish a fabrication from a rhetorical phrase, a real
   post-cutoff product, or an internal file. Genuinely fabricated
   _external_ refs are caught by the LLM critic (`qa.critic`) + the
   `qa.web_factcheck` rescue (#661). Other warning categories still use
   the global threshold.
2. **Named-source-without-URL** — for every `unlinked_citation`
   warning whose matched text contains source-type keywords ("Medium",
   "article", "blog post", "documentation", "paper", "study"), if no
   URL appears within ~100 chars of the match, that warning is
   individually promoted to critical. Gated off by default
   (`content_validator_named_source_promote_enabled`, default false), so
   it does not override the advisory demotion above unless an operator
   explicitly re-enables it.

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

File: `src/cofounder_agent/modules/content/multi_model_qa.py`

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

| Reviewer                     | Provider tag       | Source line                          | Prompt / mechanism                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ---------------------------- | ------------------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `programmatic_validator`     | `programmatic`     | 309                                  | Calls Layer 2's `validate_content()`. Score = `100 - score_penalty`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `citation_verifier`          | `http_head`        | 946 (`_check_citations`)             | HTTP HEAD against every external URL via `services.citation_verifier`. Fails if dead-link ratio > `qa_citation_max_dead_ratio` (default 0.30) or count < `qa_citation_min_count`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `ollama_critic`              | `ollama`           | 652 (`_review_with_ollama`)          | Runs the `qa.review` prompt (in `skills/content/content-qa/SKILL.md`, sourced through `UnifiedPromptManager`) on local Ollama. Configurable model via `pipeline_critic_model` (default `gemma3:27b`). The prompt explicitly handles the training-cutoff case: "do NOT automatically reject just because you lack knowledge", and grounds factual claims against the optional `SOURCES` block built from `ResearchService.build_context()`. Criterion 5 (2026-07-01) vetoes UNFINISHED content — planning notes / outlines / drafting scaffolds cap `quality_score` at 25 with `approved=false`, below the parse-side `score >= 70` auto-approve that otherwise overrides the model's own boolean — and the "GROUND YOUR REVIEW IN THE CONTENT" clause forces feedback to quote the page, countering the judge's tendency to anchor on the TITLE and review the article it imagines instead (the 2026-07-01 planning-dump incident). Langfuse override note: `qa.review` is registered in Langfuse (production label wins over SKILL.md), so prompt fixes must also be pushed as a new Langfuse version. |
| `topic_delivery`             | `consistency_gate` | 1038 (`_check_topic_delivery`)       | Runs the `qa.topic_delivery` prompt (in `skills/content/content-qa/SKILL.md`) — checks numeric promises ("10 X" → does the body actually list 10?), named entities ("Llama 4" → not Llama 3), format promise (guide vs opinion), and angle/thesis. Hard binary veto when it fails — bait-and-switch can't be fixed by targeted edits.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `internal_consistency`       | `consistency_gate` | 1055 (`_check_internal_consistency`) | Runs the `qa.consistency` YAML prompt (in `prompts/content_qa.yaml`) — looks for recommendation contradictions ("don't use React" + "use Next.js"), factual contradictions, principle contradictions, and code-vs-prose contradictions. Soft veto: only fires a hard reject when its own score is unambiguously low (< `qa_consistency_veto_threshold`, default 50).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `image_relevance`            | `vision_gate`      | 1071 (`_check_image_relevance`)      | Opt-in via `qa_vision_check_enabled` (default false). Downloads up to `qa_vision_max_images` (default 3) inline images, base64-encodes them, sends to `qa_vision_model` (default `qwen3-vl:30b`) with a "rate 0-100 how well the image represents the article's subject" prompt. Catches stock-photo-for-a-FastAPI-post mismatches.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `web_factcheck`              | `web_factcheck`    | 1457 (`_web_fact_check`)             | Opt-in via `qa_web_factcheck_enabled` (default true). Extracts product / hardware / version claims via regex (RTX/Llama/Python version patterns), runs DuckDuckGo searches via `WebResearcher`, scores by fuzzy term-match ratio. The **fix** for the training-cutoff problem: local critics reject "RTX 5090 has 32GB VRAM" because they were trained before release; this gate confirms it on the live web. Special role: if the validator's only critical issue was `known_wrong_fact` and this gate confirms the claim, the validator's rejection is overridden.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `url_verifier`               | `programmatic`     | 416-465 (inline in `review()`)       | Calls Layer 2's `verify_content_urls()`. Dead links → score=`max(0, 100 - 20×dead_count)`, approved=False (hard veto). All URLs alive → score=`min(100, 80 + 5×external_citation_count)`, capped +15 bonus. Carrot-and-stick: dead links block, good citations are rewarded.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `rendered_preview`           | `vision_gate`      | 1276 (`_check_rendered_preview`)     | Opt-in via `qa_preview_screenshot_enabled` (default false) AND requires the orchestrator to pass a `preview_url`. Captures a full-page Playwright screenshot, sends to `qa_preview_vision_model` (default `qwen3-vl:30b`) for layout / readability / broken-image / mangled-HTML detection. The final "yup looks good" sanity check that no text-only QA can do.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `deepeval_brand_fabrication` | `deepeval`         | 1044 (`_check_deepeval_brand`)       | DeepEval-wrapped regex check — wraps Layer 2's `FAKE_*` / `HALLUCINATED_*` / `BRAND_CONTRADICTION` patterns as a `BaseMetric`. Pure-CPU, no LLM call. Score is binary (0.0 or 1.0). First production wire-in of DeepEval (#329 sub-issue 1, advisory by default).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `deepeval_g_eval`            | `deepeval`         | (`_check_deepeval_g_eval`)           | DeepEval `GEval` — chain-of-thought LLM-judge metric grading the post against `deepeval_g_eval_criterion` (default: groundedness + internal consistency + no invented facts). Threshold via `deepeval_threshold_g_eval` (default 0.7). Judge model resolved via `deepeval_rails._resolve_judge_model`: the `deepeval_judge_model` pin → `notify_operator(critical=True)` + raise (the `cost_tier.standard.model` and cross-step `pipeline_writer_model` fallbacks were removed in PR #1907; Glad-Labs/poindexter#455 Phase 1). Advisory by default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `deepeval_faithfulness`      | `deepeval`         | (`_check_deepeval_faithfulness`)     | DeepEval `FaithfulnessMetric` — every claim in the post must be attributable to a paragraph chunk of `research_sources` (the corpus the writer was given). Skips entirely without research. Threshold via `deepeval_threshold_faithfulness` (default 0.8). Advisory by default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `guardrails_brand`           | `guardrails`       | (`_check_guardrails_brand`)          | **Native** brand-fabrication rail (`services/guardrails_rails.run_brand_guard`) — runs the same `content_validator` regex pattern sets directly. Originally a `guardrails-ai` wrapper; reimplemented dependency-free after `guardrails-ai` was quarantined on PyPI (CVE-2026-45758 supply-chain compromise) and reinstated for #996. Cross-rail parallel signal (brand check now reports through three lenses: `programmatic_validator`, `deepeval_brand_fabrication`, and this rail; correlation drift = wrapper bug). Master switch `guardrails_enabled` (default true). Advisory.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `guardrails_competitor`      | `guardrails`       | (`_check_guardrails_competitor`)     | **Native** competitor-mention rail (`services/guardrails_rails.run_competitor_guard`) — `re` word-boundary, case-insensitive scan for any name in `app_settings.guardrails_competitor_list` (CSV). No third-party dependency (was a `guardrails-ai` `CompetitorMentionValidator` before the CVE-2026-45758 quarantine; reinstated for #996). Skipped entirely when the list is empty (no list = no enforcement). Fills a gap DeepEval doesn't cover. Advisory.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `ragas_eval`                 | `ragas`            | (`_check_ragas_eval`)                | Ragas RAG-quality reviewer — averages `faithfulness` + `answer_relevancy` + `context_precision` into one score; per-metric breakdown surfaces in the feedback string. Disabled by default (qa_gates row + `ragas_enabled` master switch both default off) because each call costs ~6K judge tokens. Skipped when `research_sources` is empty (Ragas needs grounding context). Judge model resolved via the `ragas_judge_model` pin (the `cost_tier='budget'` resolution was removed in PR #1907). Advisory when enabled.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

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
   the claim, the validator's rejection is reversed (line 568). **On the
   live graph_def path** this rescue is restored in `qa.aggregate` via
   `_qa_rail_common.known_wrong_fact_rescued` (driven by the
   `qa_known_wrong_fact_only` flag that `qa.programmatic` sets) — see the
   "`known_wrong_fact` web-rescue" subsection under Pipeline ordering
   (Glad-Labs/poindexter#661).
6. **Final decision** — `approved = all_passed and final_score >=
qa_final_score_threshold` (default 70).

> **Live-path note.** Items 1, 2, 4 (the non-advisory veto), 5, and 6
> are mirrored on the live graph_def path by
> `_qa_rail_common.aggregate_rail_reviews` (called from `qa.aggregate`).
> The provider→weight buckets are identical. Item 3 (the warning penalty)
> and the consistency low-score escape in item 4 live only in the legacy
> `review()` and are intentionally not ported (the rail aggregation is
> quality-canary-validated, not parity-checked — see `_qa_rail_common`).
> The restored `qa.consistency` rail is advisory on the live path until
> graduated; the `qa.topic_delivery` / `qa.citations` rails are seeded
> advisory-first and graduate to vetoes via `required_to_pass=true`.

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

### Rewrite loop (legacy — removed in atom-cutover #355)

> **Retired stage — live path has a bounded rescue cycle instead.** The
> unbounded rewrite loop described below was owned by the `cross_model_qa`
> stage, which #355 deleted. In its place the live `canonical_blog`
> graph*def has a `qa.rewrite` rescue node: `qa.aggregate` branches to
> `qa.rewrite` only for a \_rescuable* reject (soft critic veto or
> below-threshold score — never fabrication/gate/`missing_required`), which
> gets one targeted revision pass, then re-enters from `qa.programmatic`.
> Hard rejects halt immediately. The rescue cycle is bounded by
> `qa_rewrite_attempts` vs `qa_rewrite_max_attempts` (default 1). The
> description below is retained as the historical cross_model_qa reference.

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

**Rewrite-loop placeholder scrub (2026-05-25).** Every rewriter
output is run through `scrub_unresolved_placeholders()` from
`resolve_internal_link_placeholders.py` before the next QA pass.
Without it, the rewriter LLM can re-emit `[posts/<uuid>]` patterns
the template-stage-4 resolver already cleaned — the
`unresolved_placeholder` validator rule then fires `critical`,
which forces ANOTHER rewrite, which leaks again, until
`qa_max_rewrites` burns out and the post is rejected with score=0.
The scrub also runs once defensively before the first QA pass to
cover the case where the resolver stage bailed (no pool / DB error).
Strip-only is the right tool here: preserving a hypothetical
cross-link matters less than escaping the rewrite cycle, and the
resolver at template stage 4 already had its lookup-and-link shot
at the original draft.

**Reasoning-token strip (2026-06-09).** Mis-templated or
reasoning-channel models leak chat-template / reasoning control tokens
straight into prose. Two prod captures the same day — a mis-imported
`gemma-4-31B-it-qat` (broken Ollama `<|turn>` Modelfile template) and
`glm-4.7-5090` — both emitted article bodies that began, at char 1,
with a mangled-Harmony channel header
(`<|channel>thought\n<channel|>The release of …`), the whole article
living inside the `thought` channel. `strip_think_blocks` only knew
`<think>…</think>` and missed it. `strip_reasoning_artifacts`
(`services/llm_providers/thinking_models.py`) handles both forms —
`<think>` blocks (dropped when a real answer follows, **unwrapped** when
the answer is _inside_ the block, since some reasoning models put their
output there), mangled/proper channel headers, `<|turn>role` headers,
and standalone control markers (`<|message|>`, `<|end|>`,
`<|im_start|>`, …). It is **fence-aware**: a control token shown as an
example inside a fenced code block or inline code span (an AI/ML post
explaining the Harmony format) is left untouched, and the keyword
allowlist never touches semantic HTML such as `<article>` or `<section>`.
A control token must carry a pipe on at least one side, so a bare
`<user>` written as plain JSX is left alone; and the strip is **skipped
for JSON-mode calls** (`response_format=json_object`) — and, defensively,
on any payload that already parses as JSON — so a control-token literal
inside a JSON string value is never silently mutated. It runs at three
idempotent boundaries: the provider chokepoint (`litellm_provider.py`,
every dispatcher call), `ollama_chat_text` (beside `maybe_unwrap_json`,
for the httpx fallback), and `content.normalize_draft` (the body node
both writer paths converge on). Like `maybe_unwrap_json`, it is a no-op
on clean output.

**Planning-scaffold strip + gate (2026-06-28, #1963).** A sibling failure
to the reasoning-token leak, but **prose-shaped** rather than
token-shaped: the writer (`gemma-4-31B`) intermittently emits its
planning/outline scaffold as plain Markdown — a bulleted block of
meta-notes (`* Topic:`, `* Key elements from sources:`,
`* Models used/tested:`) and echoed prompt instructions (`Avoid "delve"`,
`Vary sentence length.`, `No placeholder brackets.`) — **before** the
article, gluing the article's first heading mid-line onto the last
bullet (prod task `0f70f736`: `…No placeholder brackets.## The Current
Ollama Model Stack`). Because there are no control tokens, the
reasoning-token strip above misses it; the draft reached
`awaiting_approval` at quality 82 and rendered as a wall of planning
bullets with the article buried below. Two defenses, both keyed on the
same echoed-instruction / planning-label tells:
`content.normalize_draft.strip_leaked_planning_scaffold` removes the
common case (re-anchoring the article from its first Markdown heading,
even when the writer glued it mid-line) and, as a QA-gate safety net for
any residual, the `leaked_planning_scaffold` rule in `content_validator`
hard-rejects (critical) so the post enters the QA rescue/rewrite cycle
instead of publishing. Both require **≥ 2 distinct tells** so a single
benign mention (a writing-tips post that says "vary sentence length")
never fires, and the gate is **fence-aware** — a post that shows these
rules as a code example is left alone. Like the reasoning-token strip,
both are no-ops on clean output and the gate is DB-toggleable via
`content_validator_rules` (`leaked_planning_scaffold`).

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

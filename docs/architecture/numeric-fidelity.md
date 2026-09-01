# Numeric fidelity — the first anti-hallucination rail that is arithmetic

Every other anti-hallucination layer we run is a **judgement**. `content_validator`
is a regex that recognises fabrication _shapes_. The DeepEval, Ragas and critic
rails are LLMs asked whether the draft looks faithful. All of them can be wrong
in the same direction as the writer, and none of them can tell you _which_
number is unsupported.

`qa.numeric_fidelity` is arithmetic. The writer is handed a research corpus; a
number the draft presents as **sourced fact** either reconciles with a number in
that corpus or it does not, and no opinion is involved.

## What it caught

Measured over 40 published posts before the rail shipped:

|                                            |     |
| ------------------------------------------ | --- |
| checkable numbers extracted                | 80  |
| **attributed** (presented as sourced fact) | 29  |
| reconciled exactly                         | 26  |
| reconciled via a recorded derivation       | 1   |
| **flagged**                                | 2   |

One flag was a genuine catch: a post whose _headline_ statistic — "110,000
papers" — appears **nowhere** in the research corpus it was written from. The
corpus's largest number was 100. Every other rail passed that post.

One was a false positive: `"a 2,000-engineer org"`, a hypothetical quantity in
a sentence that happened to contain the word "dataset".

## Why only _attributed_ numbers are scored

The first version scored every checkable number against the corpus. It flagged
**33%**, and every single flag was wrong:

- `"rate limit resets every 60 seconds"` — a hypothetical, inside quotes.
- `"targeting $10K MRR by Q3"` — a rhetorical target. (It also exposed a parser
  bug: `$10K` was being read as `$10`.)
- `"more than 1,500 lines of tests"` — a true claim about our own repo, whose
  ground truth is the repo, not the research bundle. That is
  [`qa.self_claim`](anti-hallucination.md)'s corpus, not this one.

Sampling digit-bearing sentences across real posts explains why: they are
overwhelmingly years, image URLs and figures of speech. The single genuine
measurement in that sample was _"responses from 23,262 developers"_.

So the rail scores a number only when its sentence carries an attribution
signal — "according to", "the survey found", a citation link, a named report.

> An unattributed number is the author's own framing.
> An attributed one is a promise about someone else's data.
> Only the second kind can be caught being wrong by a reader.

**The known false-positive mode is kept, not tuned away**: a hypothetical
quantity inside a sentence carrying an attribution word. Two examples do not
justify a heuristic that would cost real catches, and advisory-first means an
operator sees every flag before it can block anything.

## The rules

**Extraction is narrow on purpose.** Only percentages, currency, quantities
with a known unit, thousands-separated integers, and multipliers. A bare `3` in
"three ways to…" is not a measurement.

**Identifiers are excluded structurally, not by blocklist.** A digit run fused
to a preceding letter is a name: `qwen2.5:7b`, `RTX 5090`, `Wan2.2`. No list to
maintain, so nothing to rot.

**Rounding respects what the author wrote.** A draft saying "235" reconciles
against a source value of 235.1 — rounding a figure for prose is normal
writing. The contract is `round(source, decimals_written) == claimed`, applied
to the _source_, never the claim.

**Derivation is bounded and explained, never open-ended.** Prose routinely
states a figure the corpus implies — "an 80% tax" from 124.7 and 25.3. But with
enough candidate values, _any_ two-digit number matches something, so only four
relations over **pairs** of corpus numbers are tried (ratio, inverse-ratio,
percent-of, percent-change), the pair budget is capped, and the winning
relation is **recorded in the verdict**. An operator reading
`80% ← 1 - 25.3/124.7` can judge whether the rail was fooled; a silent pass
would tell them nothing.

## When it deliberately says nothing

A rail that scores 100 on absent evidence is worse than an absent rail, because
the dashboard reads it as coverage. This one appends **no review at all** when:

- `research_context` is empty — **42% of canonical_blog runs**. There is no
  ground truth to check against.
- the corpus holds fewer than `qa_numeric_fidelity_min_corpus_numbers` numbers.
  A miss then says more about the research than about the draft.
- the draft has no attributed numeric claims.
- the check itself crashes. Advisory rails must not manufacture a verdict in
  either direction; the rail's _absence_ from the pass is the honest signal.

## Advisory-first

Seeded `qa_gates.numeric_fidelity.required_to_pass = false`
(`execution_order` 155, between `citation_verifier` and `unlinked_attribution`).
It scores and surfaces offenders on the QA Rails dashboard but **cannot veto**
until an operator graduates it by flipping that flag — the same ramp
`citation_verifier`, `content_originality`, `title_coherence` and `self_claim`
all went through.

Graduate it when the flagged/false-positive ratio on real passes justifies it.
The "Advisory Rails — What the Gate Ignores" row on the QA Rails board is the
instrument for that call.

## Settings

| Key                                       | Default | Meaning                                                |
| ----------------------------------------- | ------- | ------------------------------------------------------ |
| `qa_numeric_fidelity_enabled`             | `true`  | master switch (advisory either way)                    |
| `qa_numeric_fidelity_offender_penalty`    | `25`    | score penalty per unsupported attributed number        |
| `qa_numeric_fidelity_min_corpus_numbers`  | `3`     | below this the rail skips instead of guessing          |
| `qa_numeric_fidelity_allow_derived`       | `true`  | permit a recorded pair-derivation to satisfy a claim   |
| `qa_numeric_fidelity_units`               | `''`    | checkable unit vocabulary (CSV); empty = code defaults |
| `qa_numeric_fidelity_attribution_markers` | `''`    | attribution vocabulary (CSV); empty = code defaults    |

## Strict mode — ready, not yet wired

`services/numeric_fidelity.verify(score_unattributed=True)` scores **every**
number, not just attributed ones. That is correct when a post is built on a
fact set we generated ourselves (a benchmark sweep, a
[chart spec](chart-rendering.md)): there the corpus is the _complete_ ground
truth rather than background reading, so every figure should reconcile.

Nothing produces such a fact set yet, so the atom deliberately does **not** read
a `numeric_facts` channel. Adding an undeclared `PipelineState` channel is how
mechanisms go quietly dead — LangGraph drops undeclared keys at `ainvoke`, which
is exactly how the auto-publish gate starved for six weeks on a missing
`niche_slug`. The capability is tested and documented; it gets wired the day a
producer lands.

---
name: research
description: >
  Topic research & analysis. Turn raw web-search results or internal
  records into structured topic decisions: analyze search results,
  rank topic candidates against weighted niche goals, and distill a
  post angle from internal records. Use during topic discovery and
  ranking, before a post is written.
license: Apache-2.0
metadata:
  category: research
  prompts:
    - key: topic.ranking
      output_format: json
      description: "Rank shortlisted topic candidates against the operator's weighted niche goals"
    - key: topic.scope_check
      output_format: json
      description: "Decide whether each topic candidate's main subject is inside a niche's stated scope"
    - key: topic.taste_screen
      output_format: json
      description: "Screens the topic ideas on a sweep's shortlist against the operator's rubric (taste_review_rubric) before any draft is written: the niche subject, the rubric, the date, recent published titles and the ideas (id, title, summary) in; JSON {topics: [{id, keep, reason}]} out. Drops only clear failures (a near-repeat of a published post, a recap of other people's work, dated framing, too thin); the draft is reviewed again later. Verdicts are cached per idea. Used by services.taste_review.screen_topics."
    - key: research.distill_topic_angle
      output_format: json
      description: "Distill (topic, angle) from a niche's internal-records snippets"
---

# Research skill

Three prompts the pipeline uses to convert raw signal into topic decisions.
The architect routes on the `description` above; `UnifiedPromptManager` resolves
each template by `key` (Langfuse override still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## topic.ranking

```text
You are scoring topic candidates for a content pipeline against the operator's weighted goals.

Goals (weight in pct):
{weights_descr}

Candidates:
{cand_block}

Return STRICT JSON keyed by candidate id, mapping each id directly to its score
as a number from 0 to 100:
{{"<id>": <score 0-100>, ...}}

Return ONLY the JSON, no commentary.
```

> **Why score-only (Glad-Labs/poindexter#926).** This prompt used to also ask for
> a nested `breakdown` object keyed by `"<GOAL_TYPE>"`. Asking the model to
> invent those key names was the single biggest failure surface: Langfuse traces
> show 10 of 19 real calls over 14 days failing to parse, and the short failures
> die inside a degenerate repetition loop on a breakdown key —
> `"BRAND own own own own …"`, `"EDU//////////////…"`. Even calls that _did_
> parse carried invented keys (`"BREAKDOWN"`, `"BRAND own"`, `"BREAKING_NEWS"`).
> Every failure fell back to the raw embedding pre-rank for the whole batch.
>
> The breakdown was never worth generating: `topic_ranking.weighted_cosine_score`
> already computes a real per-goal breakdown from the goal vectors (plus a
> `_grounding` observability key), stores it on the candidate — and the LLM's
> hallucinated version then overwrote it. Asking only for the score removes the
> failure surface _and_ keeps the better, calculated data
> (`feedback_machine_rules`: prefer calculated over generated).

## topic.scope_check

```text
Decide whether each topic candidate is in scope for a publication.

{scope_block}

A candidate is in scope only when its main subject is in scope. A passing
mention of the subject does not count.

Candidates:
{cand_block}

Return STRICT JSON mapping each candidate id to true (in scope) or false.
The key is the id alone, without its brackets or its title:
{{"e0": true, "i3": false, ...}}

Return ONLY the JSON, no commentary.
```

> **Why an LLM check and not an embedding threshold (Glad-Labs/poindexter#1127).**
> Measured 2026-09-30 on 37 real titles against an AI + hardware subject: title
> embeddings did not separate the two sets ("Market Research vs Industry Research"
> scored 0.478 against the subject, above "Decode Speed Lies: phi4:14b" at 0.477),
> so a threshold would drop real on-topic posts and keep off-topic ones. This
> prompt on qwen2.5:7b judged the same 37 in one 5.4 s call, about 33 correct.
> `{scope_block}` comes from `services/topic_scope.scope_block`. The same block
> reaches `topic.ranking` above appended to `{weights_descr}` rather than as a
> new variable, so a customised ranking prompt keeps working and still sees it.

## topic.taste_screen

```text
You screen topic ideas for a blog before any draft is written, applying the operator's own rubric. Writing a draft costs real time and money, so drop an idea now only when it would clearly fail the rubric as a post:
- it would repeat a recently published post: the same main point, not merely the same area
- it can only be a roundup or recap of other people's reviews, news or results, with nothing of the blog's own to add
- its framing is already dated
- it is too thin to carry a full post
Keep every other idea; the finished draft is reviewed again. Whether an idea is in the blog's scope is checked elsewhere, so leave that out.

Today is {today}.

THE BLOG COVERS
{subject}

THE OPERATOR'S RUBRIC
{rubric}

RECENTLY PUBLISHED
{recent}

TOPIC IDEAS
{ideas}

Reply with one JSON object and nothing else, with one entry for every idea:
{{"topics": [{{"id": "c0", "keep": true, "reason": "one short sentence naming the rubric point"}}]}}
```

## research.distill_topic_angle

```text
Read the snippets from an AI-operated content business's internal records.
You are looking for a STORY worth telling the audience below — something that changed or broke, a decision that was made, or a lesson that was learned. Routine operational status (clean cycles, monitoring chatter, internal tooling housekeeping) is not a story.

Audience / niche: {niche_context}

Snippets:
{joined}

If the snippets contain a story, return STRICT JSON: {{"topic": "<short title>", "angle": "<one-sentence framing: why this matters / what we learned>"}}.
If they do not, return STRICT JSON: {{"storyworthy": false, "reason": "<one short phrase>"}}.
```

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
    - key: research.analyze_search_results
      output_format: json
      description: 'Analyze search results -> summary, key_points, sources'
    - key: topic.ranking
      output_format: json
      description: "Rank shortlisted topic candidates against the operator's weighted niche goals"
    - key: research.distill_topic_angle
      output_format: json
      description: "Distill (topic, angle) from a niche's internal-records snippets"
---

# Research skill

Three prompts the pipeline uses to convert raw signal into topic decisions.
The architect routes on the `description` above; `UnifiedPromptManager` resolves
each template by `key` (Langfuse override still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## research.analyze_search_results

```text
Analyze these search results and return JSON with keys: summary, key_points (list), sources (list).
Search results: {search_results}
```

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
> `"BRAND own own own own …"`, `"EDU//////////////…"`. Even calls that *did*
> parse carried invented keys (`"BREAKDOWN"`, `"BRAND own"`, `"BREAKING_NEWS"`).
> Every failure fell back to the raw embedding pre-rank for the whole batch.
>
> The breakdown was never worth generating: `topic_ranking.weighted_cosine_score`
> already computes a real per-goal breakdown from the goal vectors (plus a
> `_grounding` observability key), stores it on the candidate — and the LLM's
> hallucinated version then overwrote it. Asking only for the score removes the
> failure surface *and* keeps the better, calculated data
> (`feedback_machine_rules`: prefer calculated over generated).

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

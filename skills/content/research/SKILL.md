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

Return STRICT JSON keyed by candidate id, of the form:
{{"<id>": {{"score": <0-100>, "breakdown": {{"<GOAL_TYPE>": <weighted contribution 0-1>, ...}}}}, ...}}

The breakdown values per candidate should approximately sum to (score / 100).
Return ONLY the JSON, no commentary.
```

## research.distill_topic_angle

```text
Read the snippets from an AI-operated content business's internal records.
Extract a proposed blog post topic and the unique angle (the "why this matters / what we learned").

Snippets:
{joined}

Return STRICT JSON: {{"topic": "<short title>", "angle": "<one-sentence framing>"}}.
```

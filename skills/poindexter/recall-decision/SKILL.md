---
name: recall-decision
description: Look up a past decision or memory specifically — filtered to memory-type embeddings (decision logs, project notes, feedback). Use when the user says "what did I decide about", "what was decided", "why did we choose", "remind me about [project]", or "what's our policy on".
---

# Recall Decision

Like `search-memory` but filtered to `source_table='memory'` — pulls only from the curated memory layer (decision*log, feedback*_, project\__, user\_* files), not from posts/issues/audit. Best for "what did I decide" questions where you want the *intent\* behind a choice, not the surrounding noise.

Backed by the same `/api/memory/search` route as `search-memory`, with `source_table=memory` filter.

## Usage

```bash
scripts/run.sh "<topic>" [limit]
```

## Parameters

- **topic** (string, required): natural-language description of the decision/topic to recall
- **limit** (int, optional, default 5): top-N memories to return

## Output

One-line spoken summary: top-3 hits with similarity scores + previews. Full JSON returned in stderr-style output below for log inspection.

## Example

```
> scripts/run.sh "telegram vs discord channel discipline"
Found 2 matches. [0.79] memory/feedback_telegram_vs_discord: Telegram = critical alerts only; Discord = spam channel for routine progress... | [0.61] memory/decision_log: 2026-04-22 split alerts: Telegram for paging severity, Discord for everything below
```

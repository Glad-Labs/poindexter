---
name: search-memory
description: Search semantic memory across all embedded content (memory, posts, issues, audit). Use when the user says "what do I know about", "search memory for", "recall", "find references to", or asks open-ended questions about prior context.
---

# Search Memory

Vector-search across the unified embeddings table (poindexter_brain.embeddings — ~25K rows spanning posts, memory files, issues, audit log, brain decisions, claude_sessions). Returns the top hits with similarity scores so the agent can decide how confidently to surface them.

Backed by the worker's `/api/memory/search` endpoint, which fronts `MemoryClient.search` (pgvector cosine similarity over nomic-embed-text:768 embeddings).

## Usage

```bash
scripts/run.sh "<query>" [limit] [min_similarity]
```

## Parameters

- **query** (string, required): natural-language search query
- **limit** (int, optional, default 5): top-N results to return
- **min_similarity** (float, optional, default 0.5): drop hits below this threshold (0..1)

## Output

Plain-text summary of hits, one per line, prefixed with `[similarity] [source_table] source_id`. Suitable for direct TTS readback in voice contexts.

## Example

```
> scripts/run.sh "backup strategy" 3 0.5
Memory search: "backup strategy" (3 results)

1. [0.7821] [memory] feedback_backup_strategy
   Tier 1 in-stack hourly+daily, Tier 2 off-machine via restic...
2. [0.6547] [post] post_142
   Why solo founders need ruthless backup discipline...
3. [0.5912] [memory] project_disaster_recovery
   2026-05-05 incident: postgres-local volume wiped during Docker Desktop reinstall...
```

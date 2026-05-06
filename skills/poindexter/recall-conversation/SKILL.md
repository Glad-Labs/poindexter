---
name: recall-conversation
description: Recall something said earlier in this voice conversation — semantic search over the voice_messages table. Use when the user says "what did I say about X earlier?", "remind me what we talked about", "did I mention X already?", "what was that thing I said about". Different from recall-decision (curated memory) — this is the in-conversation transcript.
---

# Recall Conversation

Slice 3 of the voice-agent rollout (Glad-Labs/poindexter#390). Wraps a
pgvector cosine search over the `voice_messages` table — the same query
the bot runs implicitly on every user turn — but exposed as an explicit
voice-invokable skill so the operator can also ask "what did I say
about X earlier?" without relying on the implicit recall surfacing it.

Backed by `scripts/_voice_memory.recall_similar_turns()` and direct
asyncpg access to `voice_messages` (no API hop — recall is local-only
data). Embedding via `nomic-embed-text:768` against Ollama at
`OLLAMA_URL` / `voice_agent_ollama_url`.

## Usage

```bash
scripts/run.sh "<topic>" [limit] [min_similarity]
```

## Parameters

- **topic** (string, required): natural-language description of what to
  recall from the conversation transcript
- **limit** (int, optional, default `voice_agent_recall_k` or 3): top-N
  prior turns to surface
- **min_similarity** (float, optional, default
  `voice_agent_recall_min_similarity` or 0.5): cosine-similarity floor

## Output

One-line spoken summary listing the top hits with role + similarity +
preview. Empty result is the no-match path (skill prints a friendly
"nothing came up" line).

## Example

```
> scripts/run.sh "the dashboard variety thing"
Found 2 prior turns. [0.78] user: I was talking about wanting more chart variety in the Grafana dashboards | [0.62] assistant: Mix of stats, gauges, time series, badges...
```

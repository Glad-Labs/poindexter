---
name: find-similar-posts
description: Find blog posts semantically similar to a topic — pulls from the posts embedding source. Use when the user says "have we written about", "any posts on", "find similar posts to", "what's our coverage of", or wants to avoid duplicating an existing post.
---

# Find Similar Posts

`source_table='post'`-filtered semantic search. Useful for "have I covered this already" questions before kicking off a new post, or for surfacing the related-reading list a fresh post should link to.

Backed by the same `/api/memory/search` route as `search-memory`, with `source_table=post` filter.

## Usage

```bash
scripts/run.sh "<topic>" [limit]
```

## Parameters

- **topic** (string, required): natural-language description of what you're looking for
- **limit** (int, optional, default 5): top-N posts to return

## Output

One-line spoken summary: top hits with similarity, post-id slug, and a snippet.

## Example

```
> scripts/run.sh "docker secrets management"
Found 4 matches. [0.78] post_91: 5 patterns for docker secret rotation in 2026... | [0.71] post_134: why every solo founder needs a secret manager...
```

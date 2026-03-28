---
name: create-post
description: Create a new blog post or content task about a given topic. Use when the user says "write a post about", "create content about", "draft an article on", or similar.
---

# Create Post

Creates a new content task in the Glad Labs pipeline. The task goes through the full 6-stage agent pipeline: Research, Creative Draft, QA Critique, Creative Refinement, Image Selection, and Publishing Prep.

## Usage

```bash
scripts/run.sh "topic" "category" "target_audience" "primary_keyword"
```

## Parameters

- topic (required): The subject of the blog post
- category (optional): Content category, e.g. "technology", "business", "marketing". Defaults to "general"
- target_audience (optional): Who the post is for, e.g. "developers", "founders". Defaults to "general"
- primary_keyword (optional): SEO keyword to target

## Output

Returns the created task object with its ID, status, and metadata. The task enters the pipeline queue immediately.

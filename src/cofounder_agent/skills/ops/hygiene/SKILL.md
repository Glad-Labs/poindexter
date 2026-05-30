---
name: hygiene
description: >
  Data-retention and memory-hygiene summarization. Compress one calendar
  day of source-table rows, or a cluster of older same-source memories,
  into a single dense paragraph. Use when bounding storage growth while
  preserving the gist for retrieval.
license: Apache-2.0
metadata:
  category: utility
  prompts:
    - key: ops.retention.summarize_to_table
      output_format: text
      description: 'Compress one calendar day of rows from a source table into a single paragraph summary. Replaces N rows with 1 summary row to bound storage growth while preserving the gist for retrieval.'
    - key: memory.collapse_old_embeddings.summary
      output_format: text
      description: 'Compress N older same-source memory excerpts into a single dense paragraph for re-embedding. Future semantic search reads the summary instead of the originals — favor facts/names/decisions over prose.'
---

# Hygiene skill

Operational summarization prompts that bound storage growth.
`ops.retention.summarize_to_table` is used by
`services/integrations/handlers/retention_summarize_to_table.py` to
compress one calendar day of rows (e.g. audit_log) into a single summary
row. `memory.collapse_old_embeddings.summary` is used by
`services/jobs/collapse_old_embeddings.py` (#242 migration from inline
`_DEFAULT_SUMMARY_PROMPT`) to compress a cluster of older same-source
memories into one paragraph that gets re-embedded; the original rows are
then deleted.

`UnifiedPromptManager` resolves each template by `key`; both callers
fetch the raw template and apply the `{...}` placeholders themselves. A
Langfuse production-label override still wins over the bodies below; the
inline fallbacks in the handlers stay as a bootstrap safety net per
`feedback_prompts_must_be_db_configurable`.

## ops.retention.summarize_to_table

```text
You are compressing one calendar day of {source_table} rows so the system remembers the gist without storing every row. Below are {n} representative rows from {bucket_start_iso}, each separated by '---'.

Write a single paragraph (3-6 sentences) summarizing what happened that day. Preserve specific event types, sources, severity, decisions, and outcomes. Drop boilerplate and repetition. The summary will be stored as a single row replacing all {row_count} raw rows for the day, so dense factual content beats prose.

Rows:
{joined}

Summary:
```

## memory.collapse_old_embeddings.summary

```text
You are compressing a cluster of older memories so the system remembers the gist without storing every detail. Below are {n} excerpts from the same source ({source_table}), each separated by '---'.

Write a single paragraph (3-6 sentences) summarizing what these excerpts collectively say. Preserve specific names, dates, decisions, errors, and outcomes. Drop boilerplate, repetition, and verbose phrasing. The summary will be embedded and used for future semantic search, so dense factual content beats prose.

Excerpts:
{joined}

Summary:
```

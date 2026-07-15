# Social Draft Identification — Design Spec

**Date:** 2026-07-14
**Status:** Approved — pending implementation plan
**Author:** brainstorming session (Matt + Claude)

---

## Context

Matt asked for a way to preview social posts in the console before approving
them. Investigation of `console/js/drawer.jsx` and `console/js/app.jsx` found
that a content-preview drawer already exists for social drafts (raw text,
`platform_config` dump, routing info), but it's only reachable from the
"NEEDS YOU" Action Inbox — and the Action Inbox deliberately filters to
drafts where `post_status === 'published'` (`app.jsx:492-494`), matching the
server-side gate in `approve_draft` that refuses to post a promo for a blog
post that isn't live yet.

Social drafts are generated speculatively at pipeline finalize
(`social.generate_drafts`), two steps before the operator approval gate
(`preview_gate`) even runs — so a freshly generated draft is almost always
sitting at `status='pending'` while its blog post is still `awaiting_approval`
(no `posts` row yet) or `approved`/`scheduled` (staged, not live). Both cases
report `post_status !== 'published'`, so the draft is invisible to "NEEDS
YOU" — it only shows up in the **SOCIAL DISTRIBUTION** panel
(`panels2.jsx::SocialPanel`), which lists every draft regardless of gate
status.

The actual problem: `SocialPanel`'s table has no way to identify _which_
article a pending row belongs to. Its one identifying column, "Post ID",
reads `d.post_id` — a column that stays `NULL` until the linked post is
backfilled at publish time (`publish_post_from_task`). So for exactly the
rows an operator needs to identify (drafts waiting on an article that hasn't
published yet), that column is always blank.

## Non-goals

- **Content preview / click-through drawer for not-yet-published drafts is
  out of scope.** Once a post publishes, the existing Action Inbox drawer
  already covers content preview. Matt confirmed preview isn't needed before
  that point — this spec is identification-only.
- No change to the `approve_draft` post-publish gate, to CLI (`poindexter
social list`) output formatting, or to the MCP `list_social_drafts` tool's
  presentation. They receive the new fields for free via the shared
  `SocialDraftRow` dataclass but aren't required to display them.

## Approach

Resolve the article's title and best-available id **server-side**, inside
the same query that already computes `resolved_post_status`
(`SocialDraftsService.list_drafts()`), rather than having the console
cross-reference other in-memory lists (e.g. an already-polled `/api/tasks`
page) client-side. Client-side joining was considered and rejected: it would
duplicate a join the backend can do once and correctly, it would silently
miss tasks outside whatever page/limit the console happened to have loaded,
and it pushes a data-shape decision into a transport adapter (the console),
which the project's transport-adapter contract (`docs/architecture/2026-06-10-transport-adapter-contract.md`)
reserves for the service layer.

Two sources back the identification, preferred in this order:

1. **`posts.title` / `posts.id`** — the real (possibly-revised) title and id,
   available as soon as a `posts` row exists. This can resolve _before_
   `social_post_drafts.post_id` itself gets backfilled, since that backfill
   only happens at actual publish (`publish_post_from_task`) — so it covers
   the "approved but not live" window too, not just "published."
2. **`pipeline_tasks.topic`** — always set the moment a task exists, so it's
   the fallback label while the article is still `awaiting_approval` and no
   `posts` row exists at all.

## Data model / query change

`SocialDraftsService.list_drafts()` (`services/social_drafts.py`) gains a
`LEFT JOIN pipeline_tasks` and two more columns on the existing posts
`LATERAL` join:

```sql
SELECT d.*,
       rp.status AS resolved_post_status,
       COALESCE(rp.title, pt.topic) AS article_title,
       COALESCE(d.post_id, rp.id) AS resolved_post_id
FROM social_post_drafts d
LEFT JOIN pipeline_tasks pt ON pt.task_id = d.pipeline_task_id
LEFT JOIN LATERAL (
    SELECT p.id, p.title, p.status
    FROM posts p
    WHERE (d.post_id IS NOT NULL AND p.id = d.post_id)
       OR (d.post_id IS NULL AND p.metadata->>'pipeline_task_id' = d.pipeline_task_id)
    ORDER BY p.created_at DESC
    LIMIT 1
) rp ON true
{where}
ORDER BY d.created_at DESC
```

`SocialDraftRow` gains two fields:

```python
title: str | None            # posts.title, else pipeline_tasks.topic
resolved_post_id: str | None # social_post_drafts.post_id, else posts.id
```

`pipeline_tasks.task_id`, `posts.title`, and `posts.id` are read-only lookups
here; no schema migration is needed since both tables and all three columns
already exist. `pipeline_tasks.topic` is a true `NOT NULL` column, so it's a
reliable fallback; `posts.title` has no `NOT NULL` constraint at the schema
level (application code always sets it, but the `COALESCE` doesn't depend on
that — if it were ever null, resolution falls through to `pipeline_tasks.topic`
the same as the no-posts-row case).

## Components touched

- **`services/social_drafts.py`** — `SocialDraftRow` dataclass + `list_drafts()`
  query, as above. `_row_to_dataclass()` maps the two new columns.
- **`routes/social_routes.py`** — `_serialize()` adds `"title"` and
  `"resolved_post_id"` to the response body.
- **`console/js/panels2.jsx`** — in `SocialPanel`'s table, replace the "Post
  ID" column with an "Article" column: the title as the primary text
  (truncated with ellipsis, full title in a `title=` tooltip — the same
  pattern the Error/Post ID cells already use), with a small dim mono id
  label stacked on the line below it — `POST <id8>` when `resolved_post_id`
  is set, else `TASK <id8>` using `pipeline_task_id`. Falls back to `—` if
  `title` is somehow null (defensive only; not expected in practice).

## Error handling

No new failure modes — this is read-only display data. The `COALESCE`s in
SQL guarantee a value whenever either source table has a matching row; the
console's existing `—` empty-state convention covers the remaining edge
case.

## Testing

- Unit tests in `tests/unit/services/test_social_drafts.py` covering both
  resolution paths: (1) draft whose task has no `posts` row yet → `title`
  falls back to `pipeline_tasks.topic`, `resolved_post_id` is `None`; (2)
  draft with a live `posts` row → `title`/`resolved_post_id` come from
  `posts` even when the draft's own `post_id` column is still `NULL`
  (pre-backfill).
- Console side: this codebase doesn't unit-test individual panel
  components (only the data/api layer and contract shapes under
  `console/js/__tests__/`), so verification is manual — run the dev console
  against live data and confirm the Article column renders both cases
  (task-only label, post-title label) correctly.

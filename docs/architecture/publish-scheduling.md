# Publish scheduling — where a slot actually lives

**Status:** current as of 2026-08-08.

One sentence to carry away: **a publish slot is
`posts.status='scheduled'` + `posts.published_at`, and nothing else.**
Every surface that schedules a post has to end up writing that pair, or
the post does not publish.

## The queue

`services/scheduled_publisher.py` runs a 60-second loop (interval:
`app_settings.scheduled_publisher_poll_seconds`) and promotes rows:

```sql
UPDATE posts
   SET status = 'published', ...
 WHERE status = 'scheduled' AND published_at <= NOW()
   AND awaiting_gate IS NULL
```

That query is the whole contract. `services/scheduling_service.py` owns
the write side of it — `assign_slot`, `assign_batch`, `shift`, `clear`,
`list_scheduled` — and `routes/scheduling_routes.py` is the HTTP mirror
(`/api/scheduling`). A post with a future `published_at` but
`status='approved'` is **not** scheduled; a post with
`status='scheduled'` and a NULL `published_at` is never picked up.

## How a task becomes a scheduled post

`pipeline_tasks` and `posts` are separate tables and the approval inbox
works on the former. The bridge is `stage_only`:

```
pipeline_tasks.status='awaiting_approval' | 'completed'
        │
        │  POST /api/tasks/{id}/approve
        ▼
pipeline_tasks.status='approved'
        │
        │  publish_post_from_task(stage_only=True)
        ▼
posts.status='approved', published_at=NULL          ← staged, not queued
        │
        │  scheduling_service.assign_slot(post_id, when)
        ▼
posts.status='scheduled', published_at=<slot>       ← in the queue
        │
        │  scheduled_publisher (≤60s after the slot)
        ▼
posts.status='published'  +  ISR revalidation
```

`approve` alone stops at the staged row — that is the
approve-≠-publish gate. `approve` with `publish_at` runs the same
staging call and then `assign_slot`, so the two paths differ only in the
final promotion.

The seam back from a post to its task is
`posts.metadata->>'pipeline_task_id'`; `scheduled_publisher` reads it to
sync `pipeline_tasks.status` in the same transaction it publishes.

## Entry points

| Surface        | Call                                          | Notes                                   |
| -------------- | --------------------------------------------- | --------------------------------------- |
| Console drawer | `POST /api/tasks/{id}/approve` + `publish_at` | Slot picker; approve + slot in one call |
| CLI            | `poindexter schedule ...`                     | Operates on already-staged `posts` rows |
| HTTP           | `POST /api/scheduling/{post_id}`              | Needs a `posts` row to exist first      |
| HTTP (batch)   | `POST /api/scheduling/batch`                  | Fills the next N approved posts         |

`publish_at` accepts anything `scheduling_service.parse_when` handles —
ISO 8601, `"tomorrow 9am"`, `"next monday 14:00"` — the same parser the
CLI uses. It is validated **before** any state change, so a typo 400s
with the task untouched rather than falling through to an immediate
publish.

`auto_publish` and `publish_at` are mutually exclusive (400). "Ship now"
and "ship Thursday" cannot both be true, and silently picking a winner is
how the old code turned a contradictory request into a surprise.

## Reading the result

`POST /api/tasks/{id}/approve` returns `scheduled_for` — the slot the
server **committed**, not the one requested. The approve itself commits
before staging and slot assignment, so a staging or slot failure still
returns 200; in that case `scheduled_for` is null and `message` carries
the reason.

Callers must branch on `scheduled_for`, never assume the requested
`publish_at` took effect. The console does this: a committed slot gets a
cyan "Scheduled — publishes <time>" toast, a null one gets an amber
"Approved but NOT scheduled — <reason>".

## Retraction

`POST /api/tasks/{id}/unapprove` deletes the staged `posts` row at
`status IN ('approved','scheduled')` and flips the task back. Both
statuses matter: a scheduled row left behind would sit in the queue and
publish itself on schedule despite the approval being retracted.

Retracting an already-**published** post is a different operation
(`unpublish_post`) — that one has external state to retire.

## Historical trap (do not reintroduce)

`pipeline_tasks.scheduled_at` used to exist and was written by the
`publish_at` branch of the approve route. Nothing ever read it. The
branch also skipped `stage_only`, so a scheduled approve created **no
`posts` row at all** — the post never entered the queue, never published,
and the API reported success. The console's Schedule button compounded
this by being a client-side stub that called nothing.

The column was dropped in
`20260808_035641_drop_orphan_pipeline_tasks_scheduled_at_column.py`. It
was deliberately **not** kept as a denormalised mirror of the real slot:
`PATCH /api/scheduling/shift` and `DELETE /api/scheduling` write `posts`
only, so a mirror would drift, and a schedule column that lies is worse
than no column.

If you find yourself wanting to record a slot on the task row, record the
`post_id` instead and read the slot from `posts`.

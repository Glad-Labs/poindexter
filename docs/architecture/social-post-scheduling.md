# Social post scheduling

Timing a social promo used to mean opening the Postiz UI. This is the local
queue that replaces it: a draft carries a fire time, and Poindexter posts it
when that time arrives.

Two ways in, both optional:

- **Manual** — `poindexter social schedule <draft> "tomorrow 9am"`.
- **Auto-drip** — per-platform prime-time hours (`twitter=09:00,12:30`) and/or
  an offset from the post going live, so a post published at 11pm still
  promotes at 9am and the same link doesn't hit five platforms in the same
  minute. Off by default.

## Why the queue is ours, not Postiz's

Postiz's API accepts `{"type": "schedule", "date": …}`, so we could hand it a
future-dated post and let it hold the queue. We deliberately don't.

`SocialDraftsService.approve_draft` gates on the promoted post actually being
live (`posts.status = 'published'`) and repairs the URL in the copy against the
live `posts` row before sending. A Postiz-side schedule runs that check when
the operator _picks the time_ — potentially hours or days early. If the post's
publish slipped, or an operator pulled it in the meantime, Postiz would still
fire the promo, at a URL that 404s.

Firing locally re-runs the gate at the moment of posting. The secondary reason
is the one that prompted the work: a queue held in Postiz has to be cancelled
and edited in Postiz. Holding it here keeps `schedule` / `unschedule` /
`queue` in the CLI, the API, MCP, and the console.

The tradeoff we accept: if the worker is down when a slot comes due, the promo
is late rather than early. See _Lateness_ below.

## Data model

`social_post_drafts.scheduled_at TIMESTAMPTZ` plus a `scheduled` status.

| Status                | Meaning                                                  |
| --------------------- | -------------------------------------------------------- |
| `pending`             | Needs an operator decision                               |
| `scheduled`           | Decided; waiting for `scheduled_at`                      |
| `posted` / `rejected` | Terminal                                                 |
| `failed`              | Postiz rejected it; `RetryFailedSocialDraftsJob` owns it |

**`scheduled` holds its `(pipeline_task_id, platform, subreddit)` key.** This
is the one invariant to not break. `create_draft`'s `NOT EXISTS` guard,
`existing_draft_keys`, and the `ux_social_post_drafts_active_key` partial
unique index all key off `_KEY_HELD_STATUSES`. A finalize re-run (preview_gate
regen loop, checkpoint restore, task retry) that saw a scheduled key as free
would insert a second draft, and both would post — poindexter#833 with extra
steps. The migration widens the index predicate for exactly this reason; the
service binds the shared constant rather than inlining a status list, and
`test_social_drafts_retention_policy.py` reads that constant so a newly-added
status is covered the moment it's added.

Corollary: `cancel_orphaned_for_rejected_tasks` reaps `scheduled` drafts too,
and clears `scheduled_at`. A scheduled orphan is worse than a pending one — it
has a timer on it.

## Time handling

Storage is UTC; operator-typed clock words are local
(`app_settings.operator_timezone`). `scheduling_service.parse_when` grew a
`tz` argument for this — it defaults to UTC, so the blog-post callers that
predate it are unchanged.

The subtle part is the calendar day: at 21:00 in `America/New_York` it is
already tomorrow in UTC, so a UTC-anchored "tomorrow 9am" lands a day early.
Relative specs resolve against the local date.

A naive ISO string (`2026-08-09 09:00`) is read as local too — someone writing
it with no offset means their own 9am. An explicit offset always wins.

## The sweep

`ScheduleSocialDraftsJob` (`every 1 minute` — the interval is the queue's
resolution) runs two passes, auto-slot first so a `publish+0m` draft fires on
the same sweep:

1. `auto_schedule_ready_drafts` — pending drafts whose post is published get a
   slot (see _Picking the slot_ below).
2. `fire_due_drafts` — due drafts go through `approve_draft`.

Only pass 1 is gated on `social_schedule_enabled`. A hand-scheduled draft
fires with auto-drip off, or manual scheduling would silently do nothing on a
default install.

### Outcomes

`fire_due_drafts` reports four buckets, and the two that aren't obvious matter
most:

- **blocked** — the publish gate refused (post not live yet). Not a failure:
  the draft keeps its slot and retries next sweep, without burning
  `RetryFailedSocialDraftsJob` retries.
- **overdue** — past the lateness grace period. **Not posted.** The draft stays
  queued and raises a `social_draft_overdue` finding (deduped per draft).

### Lateness

If the worker was down through a slot, firing whenever it happens to come back
silently converts a timed promo into an untimed one, hours off the slot that
was chosen for a reason. Past `social_schedule_max_lateness_minutes` (default 180) the sweep leaves it alone and tells the operator, who can reschedule,
post it anyway, or drop it. Raise the setting if you'd rather it always go.

### Backfill anchoring

Auto-slotting a post published longer ago than its offsets would put every
slot in the past and fire the whole drip in one burst. When the computed slot
is already due, the anchor moves to `now`, preserving the platform stagger.

## Picking the slot

Two ways to say when a promo fires. A platform opts in through **either**.

**Prime times** (`social_schedule_prime_times`) name the hours a channel is
worth posting to: `twitter=09:00,12:30,17:00`. The slot is the next listed
time at or after the floor. This is what a night publish needs — a post going
live at 11pm promotes at 09:00, not at midnight.

**Offsets** (`social_schedule_offsets`) are a delay from publish:
`linkedin=3h`. Alone, the offset _is_ the slot. Combined with prime times it
becomes the **floor** the scan starts from — "at least 3h after publish, then
the next good hour."

### Prime times beat quiet hours

For any platform declaring prime times, the quiet window is not applied. A
quiet window only says where _not_ to post, so it clamps every displaced promo
onto the window's edge. Measured, with `22:00-07:00` and an 11pm publish:

| Platform | Offset | Quiet hours only | With prime times |
| -------- | ------ | ---------------- | ---------------- |
| twitter  | 0m     | Mon 07:00        | Mon 09:00        |
| bluesky  | 15m    | Mon 07:00        | Mon 10:00        |
| linkedin | 3h     | Mon 07:00        | Mon 08:00        |
| reddit   | 1d     | Tue 07:00        | Tue 09:00        |

Three of four platforms collapsed onto the same minute — the exact
"same link everywhere at once" burst the stagger exists to prevent. Naming the
good hours is strictly better than naming the bad ones.

Note the side effect: prime times override relative ordering. LinkedIn above
fires at 08:00, _before_ twitter's 09:00, despite the longer offset. Each
channel independently hits its own best hour, so offsets stop controlling
sequencing once prime times exist.

### Collisions

Slots are de-duplicated per platform, against both drafts queued in earlier
sweeps and ones assigned in the current pass. Three posts published overnight
spread across `09:00 / 12:30 / 17:00` rather than firing together. When a day's
listed hours are all taken, the scan rolls to the next day (bounded by
`_PRIME_TIME_SCAN_DAYS`, 14).

Eligible drafts are ordered by `published_at`, so the assignment is stable —
the oldest post keeps the earliest slot across re-runs.

## Settings

| Key                                    | Default | What it does                                                                                            |
| -------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------- |
| `social_schedule_enabled`              | `false` | Auto-drip master switch                                                                                 |
| `social_schedule_prime_times`          | `''`    | `platform=HH:MM,HH:MM` groups split by `;` — **the setting for time-of-day posting**                    |
| `social_schedule_offsets`              | `''`    | `platform=duration` pairs. A floor when prime times are also set                                        |
| `social_schedule_quiet_hours`          | `''`    | `HH:MM-HH:MM` operator-local. Ignored for platforms with prime times; still applies to offset-only ones |
| `social_schedule_max_lateness_minutes` | `180`   | Grace period before a due draft is left overdue                                                         |
| `social_schedule_fire_batch_size`      | `10`    | Max promos one sweep sends                                                                              |

Both auto-drip gates default closed: the switch AND a per-platform entry in
`social_schedule_prime_times` or `social_schedule_offsets`. A platform named in
neither is never auto-slotted, so flipping the switch alone changes nothing —
enabling auto-drip is a deliberate two-step.

One malformed pair in `social_schedule_offsets` or
`social_schedule_prime_times` costs that platform its drip and is logged; the
rest still parse. A platform whose prime times all fail to parse falls back to
its offset rather than silently posting at midnight. A malformed `social_schedule_quiet_hours`
pauses auto-scheduling entirely instead — degrading to "no quiet hours" would
post inside exactly the window the operator carved out.

> **Auto-drip skips per-draft review.** Social copy is LLM-written, and with
> auto-drip on it ships on the strength of the _post's_ approval rather than
> its own. That's the point of it, and the reason it's off by default. If you
> want to read every promo first, leave it off and schedule by hand — the
> manual path is fully supported on its own.

## Surfaces

```bash
poindexter social schedule <draft-id> "tomorrow 9am"
```

```bash
poindexter social queue
```

- **CLI** — `social schedule` / `unschedule` / `queue`
- **REST** — `POST /api/social/drafts/{id}/schedule` `{when, force}`,
  `POST /api/social/drafts/{id}/unschedule`; `scheduled_at` on every draft
- **MCP** — `schedule_social_draft`, `unschedule_social_draft`
- **Console** — Social panel: a `queued` KPI, a Scheduled column and filter
  chip, and per-row schedule / reschedule / unschedule / post-now actions

A time in the past is refused unless forced — it's nearly always a typo'd year
or a missed am/pm, and posting immediately is the wrong recovery from a typo.

## See also

- `services/social_drafts.py` — the service every surface delegates to
- `services/jobs/schedule_social_drafts.py` — the sweep
- [`social_poster.md`](services/social_poster.md) — how the copy is generated
- `brain/postiz_queue_watch.py` — detects Postiz's own Temporal queue wedging
  after we hand a post off

# Distribution targets — why the own-site row is a sentinel

`pipeline_distributions` is the table that answers "where did this task's work
end up?" One row per `(task_id, target, medium)`:

| Column   | Means                                                                                                                                        |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `target` | **Where** it landed — a platform, or the site this install publishes                                                                         |
| `medium` | **Which artifact** landed there (`video` / `video_short` / `podcast`, or `default` for a target that receives one undifferentiated artifact) |
| `status` | `published`, or `deleted` once the platform says the upload is gone                                                                          |

`target` is a small closed vocabulary, and every value is a platform name —
`youtube` today, whatever comes next tomorrow — **with one exception**: the row
recording that the post itself went live on the site this install publishes.
That row carries the sentinel `'site'`, exported as
`services.pipeline_db.SITE_TARGET`.

## Why a sentinel and not the operator's domain

Until Glad-Labs/poindexter#1038 that row carried the literal `gladlabs.io` — the
source operator's own domain — hardcoded at three write sites and matched by
name at five read sites:

| Side  | Where                                                                                                                                                                  |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| write | `routes/task_publishing_routes.py` (approve + publish), `modules/content/auto_publish.py`                                                                              |
| read  | the `content_tasks` and `pipeline_tasks_view` view bodies (three correlated subqueries each), `services/distribution_yield.py`, two Grafana panels on Cost & Analytics |

**No fresh install was broken by this.** The literal matched on both the write
and the read side, so a fork's own-site rows resolved fine. That is precisely
why it survived for as long as it did — there was no failing behaviour to
notice, only the fact that every install labelled its own publishes with one
operator's brand, and that every "not the site itself" filter read as
`target <> '<one operator's domain>'`, which is true elsewhere only by
coincidence. It also slipped past `scripts/ci/check_public_mirror_safety.py`,
whose `gladlabs.io` rule is scoped to a SQL `VALUES` tuple on the theory that a
leaked operator domain looks like a seeded default; a kwarg and a `WHERE`
predicate are neither.

The obvious alternative — write `app_settings.site_domain` into the column — does
not work, for a structural reason worth remembering:

> **The read side is a VIEW.** `content_tasks` and `pipeline_tasks_view` resolve
> `post_id` / `post_slug` / `published_at` through correlated subqueries against
> this table, and a view cannot consult a settings row to build its own
> predicate. Whatever goes in `target` has to be knowable by SQL alone.

It is also the wrong model. The row records a _relationship_ — "this task
published to self" — not a hostname, and the hostname can change without any of
these rows meaning something different.

## Reading the column

Bind `services.pipeline_db.SITE_TARGETS` rather than inlining a list:

```python
from services.pipeline_db import SITE_TARGET, SITE_TARGETS

# writing: always the sentinel
await PipelineDB(pool).add_distribution(task_id, SITE_TARGET, post_slug=slug)

# reading: the sentinel AND the pre-cutover spelling
rows = await conn.fetch(
    "SELECT ... FROM pipeline_distributions WHERE target <> ALL ($1::text[])",
    list(SITE_TARGETS),
)
```

`SITE_TARGETS` is `(SITE_TARGET, *LEGACY_SITE_TARGETS)`. Readers accept the
legacy spelling on purpose: writers only ever produce the sentinel now, so a
legacy row means a DB restored from an older dump or a task published inside a
rollback window. Those rows must still resolve their own-site columns instead of
silently reading as an unpublished task — that is the difference between a
back-compat list and a cosmetic rename.

SQL that cannot import the constant (the view bodies, the Grafana panels) spells
the list out:

```sql
-- include the own-site rows
WHERE pd.target = ANY (ARRAY['site'::text, 'gladlabs.io'::text])

-- exclude them: the site itself is not an outbound placement
WHERE target NOT IN ('site', 'gladlabs.io')
```

## The migration

`20260901_183235_retarget_the_own_site_distribution_rows_onto_a_sentinel_*.py`
does two things, both idempotent and both safe on a fresh install where the
baseline already created the widened views:

1. **Rewrites the rows** with bound parameters, guarded by a `NOT EXISTS` so a
   task that somehow holds both spellings keeps its legacy row rather than
   violating `UNIQUE (task_id, target, medium)`. Prod carried 179 rows, all
   `medium='default'`, with no such conflict.
2. **Widens both view bodies** — read back with `pg_get_viewdef` and
   transformed, then re-issued through `CREATE OR REPLACE VIEW`. The definitions
   are deliberately **not pasted into the migration**: their real home is
   `0000_baseline.schema.sql`, and a pasted copy rots the first time that one
   changes. A transform adapts to whatever the view actually is.

Two things about that transform are worth copying if you ever write another one:

- **It matches the literal, never the left-hand side.**
  `pg_get_viewdef(..., pretty := true)` renders `pd.target::text` where the
  unpretty form renders `(pd.target)::text`. Pinning either spelling is how a
  transform quietly matches nothing and reports success — which is exactly what
  the first draft of this migration did.
- **It raises when it matches nothing.** A view that exists, is not already
  widened, and carries no legacy literal is a rendering change, not a no-op.
  A check that scanned nothing has not passed.

`CREATE OR REPLACE VIEW` preserves the three `INSTEAD OF` triggers on
`content_tasks`, because the column list is untouched.

## The guard

`check_public_mirror_safety.py` now carries an **operator domain used as a
distribution target** rule, matching a domain-shaped value on either side of a
`target` comparison. It is deliberately not `gladlabs.io`-specific — any
hostname in this position is the same bug — and deliberately does not fire on a
membership test (`NOT IN (...)`, `<> ALL ($n)`), which is how the widened
readers keep accepting the pre-cutover value.

Contract tests: `tests/unit/scripts/test_check_public_mirror_safety_distribution_target.py`
(the rule), `tests/unit/services/test_pipeline_db.py` (the constants),
`tests/integration_db/test_own_site_distribution_target.py` (both views against a
real Postgres, both spellings, plus the check that a platform row does **not**
fill the own-site columns).

# Dev.to Selective Syndication — Design

**Date:** 2026-07-12
**Status:** Approved (design), pending implementation plan
**Branch:** `claude/indiehackers-devto-posting-27bb1a`
**Related:** memory `feedback-amplify-operator-knowledge`, `project_postiz_social_live`;
precedent `services/devto_service.py` + `services/jobs/crosspost_to_devto.py`; the
`posts.metadata->>'pipeline_task_id' = pipeline_tasks.task_id` seam already load-bearing
in `publish_service` (status-sync). WS2 (IndieHackers draft-assistant) is a separate,
later spec.

## Problem

Dev.to cross-posting is **live in production** — `CrosspostToDevtoJob` runs every 4h,
and posts are visibly appearing on Dev.to (auto-published via `devto_publish_immediately=true`,
each carrying a `canonical_url` back to gladlabs.io). But the job syndicates **every**
published post indiscriminately — its candidate query filters only on
`status='published'` + Dev.to dedup flags ([crosspost_to_devto.py:81](../../../src/cofounder_agent/services/jobs/crosspost_to_devto.py)).

Two consequences:

1. **Fit/brand.** A developer audience gets your off-topic and weaker posts (gaming
   deals, consumer PC-hardware roundups, low-scoring drafts) under the Glad Labs name,
   next to your strong AI/ML deep-dives. On a platform with an active anti-AI-slop
   immune system, that's a brand-safety liability.
2. **Observed reality (2026-07-12):** the syndicated posts are live but show **no
   engagement**. See Goals/Non-goals — this spec does not claim to fix engagement;
   it fixes _fit_ and _brand safety_, and treats Dev.to as an SEO/backlink surface.

The volume is not the problem (~110 published posts, low cadence). The problem is
**which** posts represent you there.

## Goals / Non-goals

**Goals:** gate syndication so only posts in an operator-chosen **niche allowlist**
whose **`quality_score` ≥ a configurable floor** reach Dev.to. Hands-off (no per-post
flagging). DB-configurable, fail-closed, forward-only. Ship the metric + panel so gate
throughput is visible.

**Non-goals (this spec):**

- **Manufacturing Dev.to engagement.** Cross-posting canonical content does not earn
  reactions/comments — Dev.to's feed deprioritizes syndicated originals, and engagement
  there is a _participation_ game (native posts, commenting, following). The honest
  value of this channel is **SEO backlinks + referral clicks to gladlabs.io**, not
  Dev.to hearts. Native founder-voice participation is WS2's territory (IndieHackers),
  not this job.
- **Changing the publish posture.** `devto_publish_immediately` stays `true` (operator
  decision 2026-07-12: auto-publish live, because the gate now _is_ the curation and
  this is redistribution of already-approved content).
- **Retracting or re-posting** anything already on Dev.to.
- **Per-niche publish posture**, an explicit per-post opt-in flag, or stamping
  niche/quality onto `posts` (Approach B, rejected below).

## Approach A — gate in the candidate query (chosen)

Change only the candidate `SELECT` in `CrosspostToDevtoJob.run` to join the task graph
and filter on niche + quality. No schema change, no backfill, no publish-path change.

**Rejected — Approach B (stamp niche+quality onto `posts.metadata` at publish).** Would
make the gate query trivial but requires a `publish_service` change _and_ a backfill
migration for existing rows, and duplicates data already on `pipeline_tasks` /
`pipeline_versions`. More surface area than this earns (YAGNI). Only worth it if many
consumers needed niche/quality denormalized onto `posts`; none do.

## The gate (correctness core)

`niche_slug` lives on `pipeline_tasks`; the authoritative `quality_score` is the newest
`pipeline_versions` row for that task (`pipeline_tasks` has **no** score column). The
new candidate query:

```sql
SELECT p.id, p.title, p.slug
FROM posts p
JOIN pipeline_tasks pt
  ON p.metadata->>'pipeline_task_id' = pt.task_id
LEFT JOIN LATERAL (
    SELECT pv.quality_score
    FROM pipeline_versions pv
    WHERE pv.task_id = pt.task_id
    ORDER BY pv.version DESC
    LIMIT 1
) pv ON TRUE
WHERE p.status = 'published'
  AND (p.metadata IS NULL
       OR p.metadata->>'devto_url' IS NULL
       OR p.metadata->>'devto_url' = '')
  AND COALESCE(p.metadata->>'devto_status', '') NOT IN ('gave_up', 'already_exists')
  AND pt.niche_slug = ANY($2::text[])
  AND COALESCE(pv.quality_score, 0) >= $3
ORDER BY p.published_at DESC
LIMIT $1
```

- `$1` = `batch_size` (unchanged, default 3).
- `$2` = the parsed niche allowlist (text[]).
- `$3` = `min_quality`.

**Fail-closed by construction:** a post with no `pipeline_task_id` (manual/CLI posts)
fails the inner `JOIN` and is silently excluded — we never syndicate a post we can't
classify. A task with no niche or no scored version is likewise excluded. This matches
the curation ethos (`feedback-output-vs-curation`) and no-silent-defaults
(`feedback_no_silent_defaults`): the job only acts on posts it can affirmatively
qualify.

**Empty-allowlist short-circuit:** if the parsed niche list is empty, the job skips the
query entirely and returns `JobResult(ok=True, detail="no syndication niches configured
— skipping", changes_made=0)`. This is the OSS default state (opt-in), and it reads
clearly in logs instead of running a query guaranteed to return nothing.

The existing terminal-status dedup (`devto_url` / `already_exists` / `gave_up`) is
preserved verbatim — grandfathering falls out for free (see below).

## Settings (DB-configurable, seeded in `settings_defaults.py`)

Per `feedback_seed_data_in_baseline`, both go in `settings_defaults.py` (idempotent
boot seed), **not** a migration. Both are top-level `app_settings` keys in the existing
`devto_*` namespace (consistent with `devto_api_base` / `devto_publish_immediately`),
read through the `_site_config` DI seam the job already receives.

| Key                           | OSS default | Meaning                                                                |
| ----------------------------- | ----------- | ---------------------------------------------------------------------- |
| `devto_syndicate_niches`      | `""`        | CSV allowlist of niche slugs. **Empty = nothing syndicates** (opt-in). |
| `devto_syndicate_min_quality` | `"80"`      | Minimum `quality_score` (0–100 scale) to syndicate. Tunable.           |

- Parsing: `devto_syndicate_niches` → split on comma, strip, lowercase, dedupe →
  `text[]`. `devto_syndicate_min_quality` → float; on unparseable value, fail loud
  (`emit_finding` + treat as no-op) rather than silently defaulting.
- **OSS vs operator (`project_oss_vs_operator_model_defaults`):** the public seed stays
  `""` (a fresh operator opts in deliberately, and the `devto_api_key` gate already
  keeps the job dormant out-of-the-box). Matt's real allowlist — the **AI/ML** and
  **dev_diary** niche slugs — is set in the stripped operator overlay
  (`operator_overrides.py`) / via `poindexter settings set`, with the exact slugs
  confirmed against `niche_service.get_known_niche_slugs` at implementation.

## Behavior change (called out honestly)

Today every published post syndicates; after this change only allowlisted-niche posts
scoring ≥ floor do. This is the intended shift, not a regression — Matt is the only
operator, so nothing external depends on "syndicate everything." The change is
forward-only.

## Grandfathering

Anything already on Dev.to (`devto_url` set, or `devto_status` ∈ {`already_exists`,
`gave_up`}) is already excluded by the untouched dedup clause — no retraction, no
re-post. Posts already published that _don't_ pass the new gate simply won't be picked
up going forward. No backfill.

## Observability (`feedback_grafana_everything`)

The gate filters _inside_ the candidate query, so the job never sees the rejected rows.
To make gate throughput visible without guesswork, add **one cheap companion COUNT** per
tick for the pre-gate universe:

```sql
SELECT count(*) FROM posts p
JOIN pipeline_tasks pt ON p.metadata->>'pipeline_task_id' = pt.task_id
WHERE p.status='published'
  AND (p.metadata IS NULL OR p.metadata->>'devto_url' IS NULL OR p.metadata->>'devto_url' = '')
  AND COALESCE(p.metadata->>'devto_status','') NOT IN ('gave_up','already_exists')
```

- **Implemented as TWO counts** (`_UNIVERSE_COUNT_SQL` + `_ELIGIBLE_COUNT_SQL`, both
  unlimited) rather than the single count sketched above — because the candidate fetch is
  `LIMIT`-capped, `universe − len(rows)` would over-report "skipped" as "queued for a
  later tick" on the first-run backlog. `posts_skipped_by_gate = universe − eligible` is
  accurate. Metrics: `gate_universe`, `gate_eligible`, `posts_skipped_by_gate`, plus an
  INFO log (`"CrosspostToDevtoJob: N eligible, M skipped by niche/quality gate
(universe K)"`).
- **Panel = documented fast-follow (verified 2026-07-12).** Investigation of
  `plugins/scheduler.py` confirms `JobResult.metrics` is **not persisted anywhere
  Grafana can query** — the scheduler logs `ok`/`detail`/`changes_made` and records a
  last-run timestamp in `job_run_state`, but there is no job-metrics→Prometheus/DB sink
  for _any_ `_SAMPLES` job. So the counts are visible **via the Loki log line** (worker
  ships INFO+ → Grafana Explore → Loki), and a dedicated dashboard panel is deferred to a
  cross-cutting follow-up ("generic job-metrics→Grafana sink") rather than a fabricated
  per-job data source.

## Testing (`feedback_docs_and_tests_default`)

Extend `tests/unit/services/jobs/test_crosspost_to_devto_job.py`:

1. Post in an allowlisted niche, score ≥ floor → **selected**.
2. Post in a non-allowlisted niche → **excluded** (even at score 100).
3. Post in an allowlisted niche, score < floor → **excluded**.
4. Post with no `pipeline_task_id` → **excluded** (fail-closed).
5. Empty `devto_syndicate_niches` → job short-circuits, zero DB candidate query, clear
   `detail`.
6. Batch limit still honored (allowlist bigger than `batch_size`).
7. Regression: `devto_api_key` absent → still no-ops before touching the gate.
8. Unparseable `devto_syndicate_min_quality` → fails loud (finding), no-op.

Query-shape tests use the existing DB fixtures (the file already exercises the
candidate query). Update the job docstring (Config section) and the Dev.to notes in
`docs/architecture/services/publish_service.md`.

## Verification (before merge)

1. **Score-distribution dry-run.** With the DB reachable, run the new gate query
   read-only against prod to confirm how many of the ~110 published posts pass at
   `min_quality=80` for Matt's chosen niches. If it gates out _most_ good posts, tune
   the default down before merge. (This is the one real risk in the numeric default.)
2. **Live confirmation already have:** the key is set and auto-publish works — posts are
   visible on Dev.to — so the "is it configured" check is satisfied by observation.
3. Standard: `poetry run pytest tests/unit/services/jobs/test_crosspost_to_devto_job.py -q`
   green; the job runs clean one tick post-deploy (worker logs show the new eligible/skipped line).

## Follow-ups (out of scope here)

- **WS2 — IndieHackers draft-assistant:** the engagement/participation play. Draft-only,
  founder-voice, sourced from dev_diary, reviewed by Matt — no auto-posting. Its own
  brainstorm → spec → plan → build cycle after WS1 ships.
- Retiring the prior object at image regeneration, etc. — unrelated.

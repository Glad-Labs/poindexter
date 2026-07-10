# Media Quality Layer 2 — semantic scoring for video + podcast

**Date:** 2026-07-09
**Issue:** [Glad-Labs/glad-labs-stack#2129](https://github.com/Glad-Labs/glad-labs-stack/issues/2129) (the video + podcast half; text scoring is a separate spec)
**Status:** design — awaiting approval

## Problem

The operator-visible `media_approvals.quality_score` for video and podcast is a
binary `0.0 | 1.0`: `score = 0.0 if failures else 1.0`
(`services/media_quality_service.py:218` podcast, `:304` video). A corrupt or
off-topic clip that clears the file-size/duration floor scores a perfect `1.0`.
The module's own docstring already scoped a "Layer 2" (transcription → LLM
faithfulness for podcast; vision-frame assessment for video) that was never
built.

This spec adds that Layer 2 so the number reflects real semantic signal, and —
per the same issue's ask — unifies the media score onto the **0–100** scale the
text pipeline already uses (`QualityDimensions.average()`), instead of leaving
the operator to mentally rescale `0.85` on a video against `85` on an article.

## Scope

In scope: video (`video`, `video_short`) and podcast Layer 2 semantic scoring,
0–100 rescale of `media_approvals.quality_score`, advisory findings, settings,
Grafana, historical backfill.

Out of scope: text scoring (`quality_scorers.py` / `ai_content_generator._validate_content`)
— tracked separately under the same issue. No change to Layer 1 thresholds or
the Layer 1 auto-reject.

## Where this lives (the seam)

`media_quality_service.evaluate_video()` / `evaluate_podcast()` are the exact
functions that compute today's binary score and write `quality_score` +
`quality_signals` onto the `media_approvals` row. They're called from
`media_approval_service` (`~:349/:353`) when a medium goes `pending` with a
`file_path`, and that same `quality_score` column is read back by the
operator-review queries (`media_approval_service.py:413/:450/:696`) and the
Discord "awaiting approval" ping. So Layer 2 slots directly into these two
functions — no new surface to plumb to reach the operator.

`media_quality_service` is **substrate** (`services/`, not `modules/content/`),
so it may import `dispatch_complete`, `caption_providers`, and `emit_finding`
directly — no module-purity platform seam required.

### Relationship to the existing `media.qa` / `qa.audio` atoms

Those atoms (`modules/content/atoms/media_qa.py`, `qa_audio.py`) run _inside_ the
render graph and do **deterministic acoustic/visual** checks (silence, volume,
A/V-sync, duration-vs-word-count, a gated human-in-frame check) as advisory
findings. They explicitly **defer the semantic check** ("Qwen2-Audio … DEFERRED
until the model is available"). Layer 2 here is the complementary **semantic**
layer, post-render, using infra that already exists (Whisper + a text/vision LLM)
rather than waiting on Qwen2-Audio. No overlap: Layer 2 asks "does the content
mean the right thing," the atoms ask "is the signal physically intact."

## Score composition (0–100)

`evaluate_*` keeps its Layer 1 pass/fail logic, then:

| Situation                                  | `quality_score`   | `quality_signals.layer2_status` |
| ------------------------------------------ | ----------------- | ------------------------------- |
| Layer 1 hard-fail (garbage file)           | `0`               | not run — Layer 2 skipped       |
| Layer 1 pass, Layer 2 disabled/unavailable | `100` (bare pass) | `"disabled"` / `"unavailable"`  |
| Layer 1 pass, Layer 2 computed             | `0–100` composite | `"scored"`                      |

**Fall back to `100`, never lower, when Layer 2 can't compute** (model
unreachable, no transcript, master switch off). Rationale: a passing file must
not _regress_ to a worse-looking number just because the vision/judge model was
offline — but `layer2_status` records _why_ it's a bare pass so the operator (and
Grafana) can tell a real 100 from an un-assessed one. Every sub-score also lands
in `quality_signals` individually so a composite is always decomposable.

Layer 2 **only runs after Layer 1 passes** — no vision/transcription spend on a
file already being auto-rejected for being 3 s of black frames.

## Video Layer 2 — two signals

`file_path` handed to `evaluate_video` is the **final composed** MP4, so both
signals read it directly.

### Signal A — shot-fidelity aggregate (free, no new LLM call)

`shot_list_renderer` already vision-scores every rendered shot against its own
brief and writes `video_shot_rendered` rows to `audit_log` (with `qa_score`
0–100 and `qa_outcome`). Today those scores are computed and thrown away at the
video level. A new helper aggregates them:

```
SELECT (details->>'qa_score')::float AS qa_score, details->>'qa_outcome' AS outcome
FROM audit_log
WHERE event_type = 'video_shot_rendered' AND details->>'post_id' = $1
```

- `shot_fidelity = mean(qa_score)` over rows where `qa_score IS NOT NULL` and
  `qa_outcome NOT IN ('skipped_reused','unscored')`.
- `shot_coverage = scored_count / total_shot_count` — stored in `quality_signals`
  as an **informational confidence indicator only** (an average built from 2 of
  12 shots is visibly less trustworthy than one from 10 of 12). It is _not_ a
  weight on the composite — deliberately YAGNI; a coverage-weighting scheme can
  come later if the operator finds the raw mean misleading.
- `scored_count == 0` → Signal A is `unavailable` (relies on Signal B alone).

`qa_score` is already 0–100, no rescale.

### Signal B — topic-match on the composed video (one vision call per video)

Extract `media.video.topic_match_frames` frames (default 3) from the composed
MP4 at even intervals (`duration/(N+1)·i`), reusing the
`shot_vision_qa._extract_video_frame` ffmpeg `-ss … -frames:v 1` pattern. Send
each frame to `media.video.topic_match_model` (default = `qa_vision_model`) via
`dispatch_complete`, asking a 0–100 "does this frame belong in an article about
`<title/topic>`" score. `topic_match = mean(frame scores)`.

**`max_tokens ≥ 1024`** on the dispatch — `qa_vision_model` is `qwen3-vl`, whose
`<think>` trace shares the token budget and silently empties the answer at 300
(the bug fixed in `shot_vision_qa` on 2026-07-08, PR #2224). Reuse that lesson.

### Video composite

`video_layer2 = mean(available signals)`. Both available → average. One
available → that one. Neither → `unavailable` → score `100`, status stamped.

## Podcast Layer 2 — one signal: faithfulness

Full-episode transcription, then judge against the source article.

1. **Transcribe** the composed episode (`file_path`) via the _existing_ ASR
   interface — `services.caption_providers.get_caption_provider(site_config)` →
   `provider.transcribe(audio_path=file_path, task_id=...)` — the same call
   `media.transcribe_narration` already makes. No new STT plumbing.
2. **Fetch source body**: `SELECT content FROM posts WHERE id = $1::uuid`
   (`posts.content` is the article body, `text`).
3. **Judge**: transcript + source body → `media.podcast.faithfulness_model` via
   `dispatch_complete`, returning `{"score": 0-100, "reason": "..."}` — does the
   episode faithfully represent the article's key claims? `faithfulness` is that
   score; `podcast_layer2 = faithfulness`.

Full-episode (operator's explicit call): the whole transcript goes to the judge
so a mid-episode drift or a scaffold-dump (#2186/#2205, which land in the
opening) is in scope, not just a sampled window.

## Advisory-first (no new auto-reject)

Layer 2 informs the 0–100 score and emits **advisory** findings on low
sub-scores (via the established `emit_finding` convention) — it does **not**
auto-reject. Thresholds are uncalibrated, and per `feedback_stability_over_speed`

- `feedback_human_approval` a fresh judge shouldn't gate publish on day one.
  Auto-reject can graduate later once the score distribution is observed (the same
  graduation path the QA rails use).

Advisory finding kinds (all `severity="warn"`, dedup-keyed by post + signal):

- `video_topic_mismatch` — `topic_match < media.video.topic_match_min`
- `video_shot_fidelity_low` — `shot_fidelity < media.video.shot_fidelity_min`
- `podcast_faithfulness_low` — `faithfulness < media.podcast.faithfulness_min`

Every failure mode is **fail-soft** (the convention every sibling atom uses): a
missing model, unreachable infra, no transcript, or an unreadable frame records
`"unavailable"` for that signal and emits **no** finding — Layer 2 just doesn't
contribute, and the composite falls back per the table above.

## Settings (all `app_settings`, `media.*` prefix, seeded in `settings_defaults.py`)

| Key                                | Default                                                    | Meaning                                                              |
| ---------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------- |
| `media.layer2.enabled`             | `true`                                                     | master switch; off → status `"disabled"`, score `100` on pass        |
| `media.video.topic_match_model`    | `qa_vision_model`'s value                                  | vision model for Signal B (empty → skip B)                           |
| `media.video.topic_match_frames`   | `3`                                                        | frames sampled from the composed video                               |
| `media.video.topic_match_min`      | `50`                                                       | below → `video_topic_mismatch` finding                               |
| `media.video.shot_fidelity_min`    | `60`                                                       | matches `video_shot_qa_threshold`; below → `video_shot_fidelity_low` |
| `media.podcast.faithfulness_model` | `ragas_judge_model`'s value (already a faithfulness judge) | judge for podcast faithfulness (empty → skip)                        |
| `media.podcast.faithfulness_min`   | `60`                                                       | below → `podcast_faithfulness_low`                                   |

Per `feedback_no_silent_defaults`, an empty model key = "operator disabled this
signal" (skip + `unavailable`), not a crash.

## Grafana (`feedback_grafana_everything`)

New "Media Quality — Layer 2" row on the media-approval board:

- `quality_score` distribution for `media_approvals` (video / podcast), now 0–100.
- `layer2_status="unavailable"` **rate** — so a silently-degraded vision/judge
  model (bare-100 passes) is visible, not hidden behind a healthy-looking score.
- Advisory-finding counts by kind (`video_topic_mismatch` /
  `video_shot_fidelity_low` / `podcast_faithfulness_low`) from `audit_log`.

## Migration

No column-type change — `media_approvals.quality_score` is already `numeric`.
One data backfill rescales historical binary values so an old `1.0` ("passed")
doesn't read as `1/100` after the switch:

```sql
UPDATE media_approvals SET quality_score = quality_score * 100
WHERE quality_score IS NOT NULL AND quality_score <= 1.0;
```

All pre-migration rows are on the 0/1 scale, so `0→0`, `1→100`. The `<= 1.0`
guard makes a re-run a no-op (and the vanishingly rare genuine new-scale `≤1`
would only be a catastrophically-bad score bumped to `100` — a wash, and this
runs once before any new-scale scores exist). Per `feedback_seed_data_in_baseline`,
this is a DDL-adjacent data convergence step, so it belongs in a timestamped
migration file, not `settings_defaults.py`.

## Wiring detail (for the plan)

`evaluate_video` / `evaluate_podcast` currently take `(db, post_id, file_path)`.
Layer 2 needs `site_config` (model keys, ASR provider config) and a pool for
`dispatch_complete`. Thread `site_config` through from the
`media_approval_service` caller; `db` (asyncpg Pool/Connection) is the dispatch
pool. This signature change + caller update is a plan task, kept backward-safe
(`site_config` optional-with-skip so existing callers/tests that don't pass it
degrade to Layer-1-only rather than break).

## Testing

- **Unit, fail-soft matrix** (mirrors `test_shot_vision_qa` / `test_media_qa`):
  each signal returns `unavailable` on no-model / dispatch-raise / no-transcript /
  no-frames, and the composite falls back to `100` with `layer2_status` stamped —
  never raises.
- **Score math**: Layer-1 fail → `0`; pass + both video signals → mean; pass +
  one signal → that one; pass + none → `100`.
- **Shot-fidelity aggregate**: `audit_log` fixture rows with mixed
  `qa_outcome` — asserts `skipped_reused`/`unscored` excluded and `shot_coverage`
  correct.
- **`max_tokens ≥ 1024`** asserted on the topic-match dispatch (regression pin for
  the #2224 lesson).
- **Migration**: a row at `1.0` → `100`, a row at `0.0` → `0`, re-run no-op.
- Findings assert kind/severity/dedup-key/`extra` shape (dashboard-ready).

## Reuse callouts (build only the edge — `feedback_no_wheel_reinvention`)

- `shot_vision_qa._extract_video_frame` — the ffmpeg single-frame extract.
- `caption_providers.get_caption_provider` — the ASR interface (whisper sidecar).
- `dispatch_complete` — cost-logged + Langfuse-traced LLM path, with the
  `max_tokens ≥ 1024` qwen3-vl lesson.
- `emit_finding` — the advisory-finding convention.
- `media_quality_service._probe_duration` / `_run_argv` — existing ffprobe/argv
  helpers.

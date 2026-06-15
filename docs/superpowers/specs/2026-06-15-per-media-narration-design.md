# Per-media-type narration (script + CTA + audio) — design

- **Date:** 2026-06-15
- **Status:** Draft (awaiting review)
- **Epic:** Glad-Labs/poindexter#689 (video pipeline redesign)
- **Related:** #1445 (long shot-list reconcile), #1449 (ffmpeg in worker image), #1450 (tolerant SHORT: parse), #1233/#690 (Stage-1 audio persistence)

## Problem

The latest pipeline-rendered videos ship **silent**. Verified on prod:

| Video               | source         | mean_volume  | audio bitrate |
| ------------------- | -------------- | ------------ | ------------- |
| `433d67bd` (latest) | pipeline       | **−91.0 dB** | 2 kb/s        |
| `b14b8413` (latest) | pipeline       | **−91.0 dB** | 2 kb/s        |
| `f9eb1542` (legacy) | reconciliation | −27.3 dB     | 192 kb/s      |

−91.0 dB is ffmpeg's signature for pure digital silence — the AAC track is a
compatibility placeholder, not narration.

**Root cause:** The Stage-2 `media_pipeline` renders visuals from the shot list
but never feeds narration audio to the renderer. `media.load_scripts` does not
load any narration path, and no node in the graph _produces_ one — every node
(`transcribe_narration`, `qa_audio`, the render atoms) only _consumes_
`podcast_audio_path`. So `_media_render.render_from_state` calls the renderer
with `audio_path=""` → the ffmpeg compositor muxes a silent track. (Even the
Stage-1 `podcast_audio_path` it could have loaded is an ephemeral
`tempfile.NamedTemporaryFile` from a separate, earlier Prefect run, gone by
Stage-2 time.) The legacy `reconciliation` path generated narration inline,
which is why older videos have sound.

The podcast pipeline (`load_script → render → qa_audio → persist`) has a
`render` node (`podcast.render`, TTS from `podcast_script`). The video pipeline
never got the equivalent.

## Goals

1. Each media type owns an **independent script and CTA**:
   - Podcast: `podcast_script` + `media.cta.podcast` (already live)
   - Long video: **new `video_long_script`** + `media.cta.video`
   - Short video: `short_summary_script` (exists) + `media.cta.video_short`
2. Stage-2 **regenerates narration audio** from the durable scripts (Option A —
   reuse the pipeline-time _script_, regenerate the _audio_), so long and short
   videos render with their own narration.
3. **Prove it end-to-end**, including the first-ever short-video render (the
   short path is code-complete after #1450 but has never executed on a real
   task).

## Non-goals (YAGNI)

- Per-media-type **voices** (voice rotation stays as-is). Easy follow-up.
- **Short shot-list quality** tuning — if the 9:16 director output is weak,
  that's a separate follow-up. This work only proves the chain runs.
- Changes to the podcast pipeline flow beyond a shared-helper refactor.
- Gate-2 distribution / `media_approvals` changes (post-render concern).

## Design

### Pipeline-time anchor: the three settings already exist

`services/settings_defaults.py` already seeds all three CTAs:

```
media.cta.podcast      → "…rate and review the show…"   (LIVE)
media.cta.video        → "If this helped, like the video and subscribe for more."
media.cta.video_short  → "Follow for more — like and subscribe."
```

`media.cta.video` / `media.cta.video_short` were seeded "ahead of their reader"
and are wired by this work. No settings migration needed.

### 1. Stage-1 — generate the long-video script

`modules/content/stages/generate_media_scripts.py` adds one local LLM call
producing `video_long_script` — narration **paced to on-screen visuals**
(distinct from the podcast's audio-only conversational style). Emitted via
`context_updates` (the `make_stage_node` graph_def path drops direct
`context[...]` writes — #674). Fail-soft: a generation failure leaves it `""`.

`modules/content/task_metadata.py::build_task_metadata` persists one new key,
`video_long_script`, so it lands on `pipeline_versions.stage_data.task_metadata`
on both finalize paths (the parity test pins this).

_(The `short_summary_script` parser was fixed in #1450; this work depends on it
but does not change it.)_

### 2. Stage-2 — one new node + re-pointed wiring

New `media_pipeline` graph_def (7 → 8 nodes):

```
load_scripts
  → render_narration         NEW
  → transcribe_narration      MODIFIED
  → qa_audio                  MODIFIED
  → render_long_video         MODIFIED
  → render_short_video        MODIFIED
  → media_qa
  → persist_media
```

**`render_narration`** — new atom `media.render_narration` (new node):

- `requires=("task_id",)`; optional inputs `video_long_script`,
  `short_summary_script`, `podcast_script`, `site_config`.
- `produces=("long_narration_audio_path", "short_narration_audio_path")` —
  declares **both** statically so the build-time I/O contract checker
  (`pipeline_architect._validate_spec`) is satisfied. (This is why one combined
  node works; an output-key-parameterized single atom would fail the checker.)
- Renders long narration from `video_long_script` (falling back to
  `podcast_script` if empty — degrade to "has audio," never back to silent) +
  `media.cta.video`; renders short narration from `short_summary_script` +
  `media.cta.video_short`. Fail-soft per channel.
- Delegates to a new `_narration_render.py` helper (see §4).

**`transcribe_narration`** (`media.transcribe_narration`, modified): runs ASR
over **each** narration → `long_caption_srt_path` / `short_caption_srt_path`,
with fidelity checked against each narration's _own_ script. (A single shared
caption track is wrong once the two videos narrate different scripts.)

**`qa_audio`** (`qa.audio`, modified): runs the deterministic silence/volume/
duration checks over **both** narration tracks instead of `podcast_audio_path`.

**`render_long_video` / `render_short_video`** (modified): read their own
narration + caption channels. `_media_render.render_from_state` gains
`narration_key` + `caption_key` params; the long atom passes
`long_narration_audio_path`/`long_caption_srt_path`, the short atom passes
`short_narration_audio_path`/`short_caption_srt_path`.

**Considered & rejected:** splitting narration into separate long/short atoms
(to mirror the `render_long_video`/`render_short_video` split). TTS is light and
fail-soft per channel, so a single combined node keeps the graph leaner; noted
here so a future reviewer knows it was a deliberate call.

### 3. Plumbing

- **`PipelineState`** (`services/template_runner.py`, the `#674` channel block
  at ~447–474): add `video_long_script: str`, `long_narration_audio_path: str`,
  `short_narration_audio_path: str`, `long_caption_srt_path: str`,
  `short_caption_srt_path: str`. (Undeclared channels are silently dropped on
  the graph_def path — the #674 trap.)
- **`media.load_scripts`** (`modules/content/atoms/media_load_scripts.py`): load
  `video_long_script` from `task_metadata` into state (it already loads
  `podcast_script` / `short_summary_script` / both shot lists).
- **graph_def reseed migration** + update the source-of-truth dict
  `services/media_pipeline_spec.py::MEDIA_PIPELINE_GRAPH_DEF` to match. New
  timestamped migration reseeds the active `media_pipeline` row (mirrors the
  existing `20260608_*_reseed_media_pipeline_*` migrations).

### 4. Shared narration helper + podcast.render refactor

New `modules/content/atoms/_narration_render.py` (underscore → skipped by the
atom registry, mirroring `_media_render.py`):

```python
async def render_narration(
    script: str, *, cta_key: str, site_config, task_id, key,
) -> str:
    # empty script / no site_config → "" (fail-soft, never raises)
    # append site_config.get(cta_key) to the script, then
    # PodcastService(site_config=...).synthesize(script, key=key) → path
```

`media.render_narration` calls it twice (long + short). `podcast.render` is
refactored to delegate to it (`cta_key="media.cta.podcast"`) so there is **one**
TTS code path, not three copies.

## Data flow (long video)

```
Stage-1  generate_media_scripts → video_long_script (text)
         build_task_metadata    → pipeline_versions.task_metadata.video_long_script
         [persisted, durable]
                    │
Stage-2  load_scripts          → state.video_long_script
         render_narration      → TTS(video_long_script + media.cta.video)
                                  → state.long_narration_audio_path
         transcribe_narration  → ASR → state.long_caption_srt_path
         qa_audio              → silence/volume/duration check (advisory)
         render_long_video     → render_shot_list(audio_path=long_narration_audio_path,
                                                   caption_path=long_caption_srt_path)
                                  → MP4 with real narration
```

## Error handling

- Every narration/ASR/render step is **fail-soft** (returns `""` / no-op, never
  raises) — a media failure must not halt the graph (existing contract).
- Long narration falls back to `podcast_script` when `video_long_script` is
  empty. Short narration has **no** fallback (a "short" narrated by the full
  article script would be wrong) → empty short script means no short narration
  and the short render no-ops, same graceful behavior as today.
- Missing/empty CTA settings → narration renders without a CTA (the `.get`
  default is `""`).

## Testing

- **Contract tests** (one per new/changed unit):
  - `media.render_narration`: CTA-append, long→podcast fallback, per-channel
    fail-soft, both output keys populated.
  - `_narration_render` helper: empty-script/no-config → `""`; CTA appended.
  - `podcast.render`: unchanged behavior after the helper refactor (existing
    test stays green).
  - `transcribe_narration`: per-type SRT + per-type fidelity.
  - `qa.audio`: checks both tracks.
  - `media_render_long/short_video`: read the correct narration/caption channel.
- **Graph-level test** (`tests/integration/test_graphdef_pipeline.py` style):
  long/short narration channels reach the renders.
- **`build_task_metadata` parity test**: `video_long_script` present on both
  finalize paths (extends `test_task_metadata_parity_693.py`).

## Verification (gate before merge)

Run **one real task end-to-end** through the now-fixed chain (no shortcuts):

1. Stage-1 with the #1450 parser → `video_long_script` **and**
   `short_summary_script` populate → the long **and** short directors produce
   shot lists.
2. Stage-2 → `render_narration` → both renders.
3. `ffprobe -af volumedetect` **both** MP4s → assert `mean_volume` well above
   −91 dB (real narration) on the long form, and the **first-ever** short-form
   render exists with audio.

This single pass proves both the audio fix and the short-video path.

## Rollout / packaging

One coherent PR (the pieces are interdependent and the verification needs all of
them). The implementation plan may stage it internally: Stage-1 script + channels
→ helper + `render_narration` + graph reseed → transcribe/qa/render re-point →
tests → verification run.

## Open decisions (resolved; flag if you disagree)

- **(a)** One combined `render_narration` node — **chosen** over split long/short.
- **(b)** Per-type captions **included** (transcribe must change anyway since it
  can no longer read `podcast_audio_path`).
- **(c)** Long→podcast-script narration **fallback included**; short has none.

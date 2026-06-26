# Video Pipeline Redesign — `media_pipeline`

**Status:** Design approved 2026-06-07. Implementation phased (see Rollout).
**Scope:** High-quality long-form **and** short-form video, plus podcast, as a first-class composable media stage behind the content pipeline.
**Epic:** [Glad-Labs/poindexter#689](https://github.com/Glad-Labs/poindexter/issues/689).

> **Deviation (2026-06-11): podcast split into its own Stage-3 graph.** This doc
> keeps podcast as a branch inside the single `media_pipeline` graph. Per an
> operator decision, podcast was instead split into its **own** isolated
> `podcast_pipeline` graph_def with its own dispatcher + distribute lane, for hard
> process-level isolation (a video-render crash can never halt podcast production)
> and fully independent dispatch/approval/distribution lifecycles. The podcast lane
> shipped 2026-06-12 (dormant behind `podcast_pipeline_trigger_enabled`); see
> [`podcast-pipeline-stage3.md`](podcast-pipeline-stage3.md). The video-side
> `video_long`→`video` consolidation in this doc is deferred to that doc's §11.

---

## 1. Goal & principles

Produce **high-quality long-form (16:9) and short-form (9:16) video** — each purpose-built, not trimmed from the other — generated from the blog post's script, reviewed per-piece by a human, and distributed with platform-appropriate formatting.

Design principles (consistent with the rest of the stack):

- **Composable graph_def, not cron.** The media stage is a LangGraph `graph_def` template (`media_pipeline`) run by `TemplateRunner`, mirroring the `canonical_blog` cutover (#355/#362). Each render/QA/gate step is an **atom** with `atom_runs` observability.
- **Everything behind a seam is disposable.** Per-shot render is **source-agnostic** (Ken Burns slideshow / Pexels stock / generative). Wan2.1 (#669) becomes one more source atom later, not a re-plumb.
- **DB-first config.** Per-niche source policy, per-platform format profiles, voice pools, and gate tiers all live in `app_settings` — no hardcoded values.
- **Human approval, fine-grained.** Both gates approve **each piece independently**; rejecting one regenerates it in isolation without discarding the rest.
- **Prove before you broadcast.** YouTube (long + Shorts) is the proving ground; distribution to TikTok/Reels is gated on the YouTube quality bar.

---

## 2. Two-stage flow

```
STAGE 1 — canonical_blog (existing, extended)
  writer → … → generate scripts ──────────────────────────┐
  produces & PERSISTS (closes #674):                       │
    • blog post                                            │
    • long_script + long_shot_list       (16:9 explainer)  │
    • short_script[] + short_shot_list[]  (9:16, independent)
    • podcast_script                                       │
                                                           ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ GATE 1 — per-piece HITL (extends approval_service)            │
  │   items: blog | long_script | short_script[] | podcast_script│
  │   approve(blog) → publish blog to R2                          │
  │   approve(script) → that piece becomes render-eligible        │
  │   reject(piece) → regenerate that piece in isolation          │
  └───────────────────────────────┬──────────────────────────────┘
                                   │ trigger on gate-clear (per approved piece)
                                   ▼
STAGE 2 — media_pipeline (NEW graph_def template)
  load_scripts
     ├─► render_long_video    (director-composition, 16:9)   [if long_script approved]
     ├─► render_shorts         (director-composition, 9:16)   [per approved short_script]
     └─► generate_podcast      (podcast_service as atom)      [if podcast_script approved]
                                   │
                                   ▼
  media_qa  (frame human-detection · caption presence · A/V sync · qa.audio #1193)
                                   │
                                   ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ GATE 2 — media_gate atom (per-asset, tiered)                  │
  │   items: long_video | short[] | podcast                      │
  │   default routing: podcast/long → auto-eligible (dial),       │
  │                    shorts → ALWAYS human                       │
  │   (dials start OFF → everything human-approved today)         │
  │   reject(asset) → regenerate that asset in isolation          │
  └───────────────────────────────┬──────────────────────────────┘
                                   ▼
  distribute  (row-driven publishing_adapters; parallel + isolated)
     • podcast    → RSS (podcast-feed.xml)              [existing]
     • long_video → YouTube (16:9)                      [#682 fix]
     • short[]    → YouTube Shorts (9:16)               [reuses YT adapter]
     • video RSS  → video-feed.xml                      [existing]
     • (TikTok #685 / IG Reels #686 — deferred behind proving ground)

SAFETY NET — media_reconciliation watchdog (demoted backfill jobs)
  catches pipeline failures + DB↔R2↔platform drift; re-enqueues the failed piece;
  owns retry/backoff (#677).
```

**Scripts-in-Stage-1, render-in-Stage-2** keeps all creative/LLM work behind Gate 1 (approving the blog implicitly approves the plan) and makes Stage 2 deterministic rendering + distribution. A re-render never re-invents prompts — the root fix for #674 and #675.

**The sketch above is conceptual.** The authoritative Stage-2 `media_pipeline`
graph_def is a linear 8-node chain (`services/media_pipeline_spec.py`):

```
load_scripts → render_narration → transcribe_narration → qa_audio →
render_long_video → render_short_video → media_qa → persist_media → END
```

`render_narration` (#689) regenerates the long + short narration audio from
their own scripts + CTAs (§6); the podcast is produced by its own pipeline, not
this graph.

---

## 3. Gates

### Gate 1 — per-piece script/blog approval

Each item (`blog`, `long_script`, `short_script[i]`, `podcast_script`) is an independently-tracked approvable row. Surfaced on Telegram + MCP + CLI + a Grafana queue panel.

- **approve(blog)** → publish blog to R2 (existing behavior).
- **approve(script)** → that script becomes render-eligible in Stage 2.
- **reject(piece)** → regenerate that piece in isolation; others proceed.

### Gate 2 — per-asset media approval (tiered)

Each rendered asset (`long_video`, `short[i]`, `podcast`) is an independently-approvable row.

- **Granularity:** per-asset — a weak short is rejected/regenerated without touching the long video or the others.
- **Disposition policy (tiered):** `podcast` + `long_video` are _auto-eligible_ once they pass `media_qa` **and** the per-media-type earned-autonomy dial (#531) is on; `short[]` **always** requires explicit human sign-off.
- **Today:** earned-autonomy dials start **OFF**, so every asset is human-approved one-by-one. `podcast`/`long_video` can _graduate_ to auto-eligible later once their edit-distance track record earns it; **shorts never graduate.**

This preserves "nothing public without per-post approval" — just finer-grained.

---

## 4. Stage 1 — script & shot-list generation

Extends `canonical_blog` to produce and **persist** three independent creative artifacts (today they're generated then discarded — #674):

- **long**: `long_script` (VO paced for a ~3–8 min explainer) + `long_shot_list`.
- **short[]**: 1–N `short_script`s — each a self-contained **15–45s cold-open hook written for retention**, not a trim of the long — + `short_shot_list`s.
- **podcast_script** (existing).

The **director** (#517) produces the shot-lists. Each shot:

```
Shot {
  source_hint:      "slideshow" | "stock" | "generative"   # advisory; renderer resolves via policy
  visual_prompt:    str        # stylized, policy-compliant (no photoreal humans)
  on_screen_text:   str | null
  duration_s:       float
  narration_segment:str | null
  narration_offset_s: float    # for per-shot audio alignment (#517)
  aspect:           "16:9" | "9:16"
}
```

The existing `Shot` schema is extended with `source` and `aspect`.

---

## 5. Stage 2 — director-composition render engine

Per shot, the renderer:

1. **Selects a source** — `slideshow` (Ken Burns over a stylized image-gen still) · `stock` (Pexels B-roll) · `generative` (Wan2.1, future #669). Resolved from `shot.source_hint` + a **per-niche source policy in `app_settings`** (e.g. tech → stylized image-gen; real-world/human subjects → Pexels, honoring the no-photoreal-humans rule, #675).
2. **Produces the visual** — image-gen stylized render, or Pexels fetch.
3. **Composes** via `FFmpegLocalCompositor`: shots concat + transitions → narration layered at each shot's `narration_offset_s` (#517) → **ambient bed mixed under** (#679, finally consumed) → **captions burned in** (#676).
4. **Output profiles** (DB-config): long = 16:9 1080p; short = 9:16 1080×1920, ≤60s, punchier pacing + larger captions.

The legacy host `:9837` slideshow path is replaced by the compositor for the live path (the compositor already supports caption burn-in and per-shot audio).

---

## 6. Audio, captions & QA — per-lane narration + one ASR pass each

- **Per-lane TTS narration (#689)** — the `media.render_narration` atom
  regenerates the long AND short narration audio in Stage 2 from their OWN
  scripts (`video_long_script` / `short_summary_script`) with their OWN CTAs
  (`media.cta.video` / `media.cta.video_short`), into
  `long_narration_audio_path` / `short_narration_audio_path`. There is **no
  shared `podcast_audio_path` base** — the earlier "both renders narrate the
  same podcast audio" plan left every rendered video **silent**, because
  Stage 2 never carried the podcast audio across from Stage 1. The podcast and
  both video lanes all synthesize through the shared `_narration_render` helper
  (`podcast_service` / `tts_providers`). Voice is DB-configurable (#677) — see
  Voice Variety.
- **One ASR pass _per lane_** (`media.transcribe_narration`) over that lane's
  own narration audio does double duty:
  - **(a) Captions** — emits the lane's `.srt` (`long_caption_srt_path` /
    `short_caption_srt_path`) burned in by that lane's render (#676).
  - **(b) Fidelity QA** — diff the lane's ASR transcript against that lane's
    source script to catch TTS dropouts / mispronunciations / truncation.
- **`qa.audio` atom (#1193, dual-lane #689)** — runs the deterministic
  ffprobe/ffmpeg checks (silence / volume / duration-vs-script) on **each**
  narration lane, nesting the results under `audio_qa_result['long']` /
  `['short']`. (The audio-native model — Qwen2-Audio 7B-Instruct / Ola — that
  _listens_ for pacing / repetition / tone mismatch is deferred until the model
  is available.)
- **`media_qa` also runs:** frame **human-detection** (catches photoreal-human policy violations before ship), caption-presence, and A/V duration sync — replacing the audit-era check that only validated duration + file size.

---

## 7. Voice variety (deterministic rotation)

- `tts_voice_rotation_enabled` (bool, default off) + `tts_voice_pool` (list of voice IDs), DB-config, scopeable per-format / per-niche.
- **Deterministic round-robin** — the voice for a task is chosen by a task-keyed cursor (e.g. stable hash of `task_id` mod pool size), so each task gets a different voice with even coverage and no randomness ("calculated, not generated").
- Off → today's single configured voice. Pairs with slideshow-style variety (#688) for per-task audio+visual variety.

---

## 8. Distribution

Row-driven `publishing_adapters` — each platform is a row + handler; fan-out is **parallel and isolated** (a platform outage/401 never blocks another). Per-platform format profile (aspect, length cap, caption style) is `app_settings`.

| Asset             | Targets (Phase 1–2)                | Adapter                                                        |
| ----------------- | ---------------------------------- | -------------------------------------------------------------- |
| podcast           | RSS `podcast-feed.xml`             | existing                                                       |
| long_video (16:9) | YouTube                            | exists; #682 field-mismatch fix required                       |
| short[] (9:16)    | YouTube Shorts                     | reuses the YouTube adapter (≤60s 9:16 → auto-classified Short) |
| video RSS         | `video-feed.xml`                   | existing                                                       |
| short[] (9:16)    | **TikTok (#685), IG Reels (#686)** | **deferred** behind the YouTube proving ground                 |

**Proving ground:** ship long + Shorts to YouTube only until the quality bar is met (see Rollout), then enable TikTok/Reels.

---

## 9. Reliability & reconciliation

- `media_pipeline` atoms carry **enforced** retry policy (the runner must honor `ATOM_META.retry`; the same fix tracked in #681).
- Render failures retry with backoff (#677). **Per-shot partial failure emits a `finding`** — never silently ship a degraded video (e.g. an 8-shot plan that rendered 3).
- **`media_reconciliation` watchdog** (the demoted backfill jobs) catches pipeline failures and DB↔R2↔platform drift and re-enqueues the _specific_ failed piece, rather than being the primary producer.

---

## 10. Observability

- **Video-render Grafana panels (#678):** per-source render success rate, render outcomes, and gate-queue depth. Closes the audit blind spot where a silent render regression had no dashboard signal.
- `atom_runs` captures per-atom latency/outcome for every media step (free from the graph_def model).

---

## 11. Testing

Per "docs + tests default," contract tests pin:

- Shot-list schema (incl. `source`, `aspect`, `narration_offset_s`).
- Source-selection policy resolution from `app_settings`.
- Compositor output specs (aspect ratio, duration, caption presence) for both profiles — golden-output checks.
- ASR-fidelity threshold behavior (pass/flag).
- Per-piece Gate 1 and per-asset Gate 2 state machines (approve/reject/regenerate isolation).
- Adapter fan-out isolation (one platform failing doesn't block others).
- Voice rotation determinism (same `task_id` → same voice; coverage across the pool).

---

## 12. Issue map

| Issue                                  | Role in this design                                                       | Phase  |
| -------------------------------------- | ------------------------------------------------------------------------- | ------ |
| #674 reconnect scenes                  | Spine — persist scripts (S1) → `load_scripts` (S2)                        | 1      |
| #517 director multi-source composition | Shot-list + per-shot source selection + `narration_offset_s`              | 1      |
| #573 video_long stall                  | **Dissolved** — long_video is a first-class render; orphan flavor deleted | 1      |
| #668 video_short re-home               | **Subsumed** — shorts first-class; reconciliation already exists          | 1      |
| #675 photoreal prompts                 | Stylized image-gen + DB per-niche source policy                           | 1      |
| #676 captions                          | ASR-pass burn-in                                                          | 1      |
| #677 render retry + voice              | Render retry/backoff + DB voice + rotation                                | 1      |
| #679 ambient bed                       | Mixed under in the compositor                                             | 1      |
| #682 YouTube handler fix               | Distribution adapter correctness                                          | 1      |
| #1193 audio QA (stack)                 | `qa.audio` atom in `media_qa`                                             | 2      |
| #678 render observability              | Grafana video-render panels                                               | 2      |
| #531 earned autonomy                   | Per-media-type auto-eligibility dials at Gate 2                           | 2      |
| #669 Wan2.1                            | Future `generative` source atom                                           | Future |
| #685 TikTok / #686 IG Reels            | Deferred distribution (post-proving-ground)                               | Future |
| #687 SFX / #688 slideshow variety      | Future quality/variety levers                                             | Future |

---

## 13. Rollout

- **Phase 1 — Foundation & quality (YouTube long + Shorts):** `media_pipeline` template + per-piece Gate 1 + director-composition render (slideshow + Pexels) for long & short + one-ASR captions/fidelity + ambient + kill-photoreal + retry/voice/rotation + `media_qa` frame-detection + YouTube long + Shorts. _Resolves #573/#668/#674/#675/#676/#677/#679 and #517-core._
- **Phase 2 — Audio QA, observability & autonomy (still YouTube):** `qa.audio` (#1193) + render Grafana (#678) + earned-autonomy auto-eligibility for podcast/long (#531).
- **Quality bar (gates Phase 3):** YouTube long + Shorts judged "up to snuff." Default bar (DB-tunable): a **trailing 10 published YouTube videos with zero Gate-2 human rejections of media**, plus operator-acceptable retention/CTR signal from YouTube analytics. Meeting it unlocks TikTok/Reels. The operator can raise/lower the threshold; this is the same earned-autonomy pattern used for auto-publish.
- **Phase 3 — Reach & ceiling (post-proving-ground):** TikTok (#685) + IG Reels (#686) adapters; then Wan2.1 source (#669), slideshow-style variety (#688), SFX (#687).

---

## 14. Decisions made (2026-06-07)

- Long + short are **both first-class and independently generated** (not derived/trimmed).
- Render engine = **director-composition spine** (per-shot source selection), not a commitment to Wan2.1 or a slideshow-only polish.
- Media stage executes as a **graph_def `media_pipeline` template** (Approach A), with backfill jobs demoted to the reconciliation safety net.
- Both gates are **per-piece**; Gate 2 disposition is **tiered** (podcast/long auto-eligible via dials, shorts always human), with dials off today.
- **YouTube is the proving ground**; TikTok/Reels deferred behind a quality bar.
- Everything DB-configurable, including **voice rotation**.

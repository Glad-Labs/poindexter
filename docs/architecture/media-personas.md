# Media personas — a face bound to a voice

**Status:** shipped 2026-09-14 — persona records, voice seam, CLI, and the
`presenter` shot source that puts the persona on camera inside a video.

## Why

The 2026-09-14 talking-head spike proved Wan 2.2 S2V renders a lip-synced
presenter from a portrait plus narration. It also rendered a female narration
onto a male portrait, because the voice was a setting (`podcast_tts_voice`) and
the face was a file, chosen separately. A presenter has to be one object.

## Shape

A persona is the key family **`persona.<slug>.<field>`** in `app_settings` —
the same shape as the per-niche media policy (`niche.<slug>.media.*`), not a
new table. That means no migration, synchronous reads from the cached
`SiteConfig` wherever the TTS seam and prompt renders already live, and the
existing settings CLI / API / MCP edit it from a phone today.
`poindexter/services/persona_service.py` adds validation, listing by prefix,
niche resolution, voice inheritance and the portrait render.

| Field (`persona.<slug>.`) | Meaning                                                                                  |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| `display_name`, `description` | Who the presenter is; the description seeds the portrait prompt                      |
| `voice_provider`, `voice_id`  | `kokoro` or `chatterbox`; the voice id. **Empty `voice_id` inherits `podcast_tts_voice`** |
| `voice_ref_audio_url`     | Chatterbox clone reference (optional)                                                    |
| `portrait_url`, `portrait_prompt`, `portrait_seed` | The canonical reference still (R2) and how to regenerate it             |
| `style_policy`            | `photoreal` or `stylized`; must agree with the niche media policy                        |
| `enabled`                 | A disabled persona resolves to none, never to a different one                            |
| `render_prompt_suffix`    | Per-persona shaping for the S2V prompt (framing, mood)                                   |

**Selection:** `niche.<slug>.media.persona` → `media_default_persona` → none.
The seeded default is `presenter` (photoreal, voice inherited), so a fresh
install keeps exactly the narration voice it had.

**Voice seam:** `podcast_service._select_voice(site_config, key, niche_slug=)`
returns the persona's `voice_id` when one resolves and rotation is off; the
narration atoms pass the task's niche through `render_narration(niche_slug=)`.
Rotation (`tts_voice_rotation_enabled`) stays an explicit opt-in that wins over
the persona — an operator who turned rotation on asked for variety.

**Engine follows the persona.** `synthesize` resolves the persona once: a
`voice_provider=chatterbox` persona renders through the Chatterbox sidecar
with its own `voice_ref_audio_url` as the zero-shot clone reference (a path
inside the sidecar, e.g. `/app/voices/matt-voice.wav`), whatever
`podcast_tts_engine` says; a `kokoro` persona renders through Speaches. If the
clone fails, the ladder falls back to the install's normal voices *without*
the persona, so a broken reference degrades to the house voice, never to
silence.

## Operator surface

```bash
poindexter personas list
poindexter personas show presenter
poindexter personas create host --display-name "Host" --voice-id bm_george --style stylized \
    --description "a calm technology host in his forties"
poindexter personas set presenter voice_id bf_emma
poindexter personas portrait presenter --seed 21         # render + upload, stores portrait_url
poindexter personas set-portrait presenter ./face.png    # upload an existing still
poindexter settings set niche.glad-labs.media.persona presenter
```

`portrait` renders through `ImageService.generate_image_result` (GPU lock,
operator priority) and uploads to `personas/<slug>-<seed>.png` on the
configured storage; the previous portrait stays in the bucket.

## Presenter shots in a video

`source: "presenter"` in a shot list is a talking-head clip of the niche's
persona speaking that shot's narration window. The director learns about it
from the `{presenter_policy}` section of both director prompts (and both
review prompts), which `media_subject_policy.resolve_media_policy` fills:
"PRESENTER AVAILABLE — <name> …" when an enabled persona with a portrait is
allowed by the niche's media policy, otherwise "NEVER emit presenter". A
photoreal persona needs `human_subjects=allow` + `style_policy=any`; a
stylized one needs people allowed at all.

Render (`shot_list_renderer._render_presenter_clip`): fetch the portrait →
cut `[narration_offset_s, +duration_s]` of the narration to mono 16 kHz →
reclaim image-gen and check `video_presenter_min_free_vram_gb` → call the
ComfyUI provider with `audio_path` (speech path, provider pinned to
`comfyui`). The compositor maps only the narration track's audio, so the
clip's own muxed speech is never doubled. Every miss returns a failed shot
with a `presenter_render_fallback` finding and the substitution ladder fills
the slot.

Format (2026-09-23): the presenter opens the video (the hook, to camera),
returns at the midpoint (the turn) and closes it (the takeaway), and the
director adds as many further presenter shots as the script earns. The four
prompts carry the format through `{presenter_policy}`, and
`media_subject_policy.place_presenter_beats` enforces it on every director and
review output before the list is stored. It has to be the stored list: the
YouTube synthetic-media disclosure reads it, so a face added only at render
time would ship undisclosed. A missing beat adopts a presenter shot the
director put beside it (for the midpoint, anywhere in the middle third) or
else promotes the target shot, dropping its visual fields. A `holdover` or
`cli_demo` is never overwritten, and three presenter shots are never stacked,
because the schema rejects any source three times running.

Budget: `video_presenter_shots_max` defaults to `-1`, meaning no ceiling. The
operator's rule is no limit the render does not need in order to work. A
value of 0 or more is an optional GPU budget (each clip is a full S2V render,
about 12 minutes on the 5090): it trims the beats in priority order (opening,
midpoint, closing) and demotes extra presenter shots to `image_kenburns`
stills of their intent. Any presenter shot on a niche with no available
persona is downgraded to `image_kenburns` at render time
(`presenter_unavailable` finding). Presenter shots are not vision-QA
regenerable — a regen would cost a full render for a stochastic gain.
`video_presenter_render_prompt` is the S2V prompt template
(`{display_name}`), followed by the persona's `render_prompt_suffix` and the
shot's optional delivery note.

## Rules that follow

- A persona's `style_policy` must be allowed by the niche's media policy
  ([media-subject-policy](media-subject-policy.md)); the shot-list validator
  will refuse a photoreal presenter on a niche that forbids people.
- Publishing a realistic synthetic presenter to YouTube carries the
  altered/synthetic-content disclosure: `media_distribute` sets
  `status.containsSyntheticMedia` from the post's shot list + persona
  (`video_contains_synthetic_media`: a `presenter` shot with a photoreal
  persona → true; a stylized presenter is a character, not a likeness).
  `youtube_contains_synthetic_media` = `auto` | `true` | `false` overrides
  per channel.
- Speech-to-video cost: ~420 s and ~31.9 GB per 4.8 s chunk on a 5090
  ([comfyui-video-provider](comfyui-video-provider.md)).

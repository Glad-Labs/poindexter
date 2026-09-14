# Media personas — a face bound to a voice

**Status:** shipped 2026-09-14 (persona records, voice seam, CLI). The
`presenter` shot source that animates a persona inside a video lands next.

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

## Rules that follow

- A persona's `style_policy` must be allowed by the niche's media policy
  ([media-subject-policy](media-subject-policy.md)); the shot-list validator
  will refuse a photoreal presenter on a niche that forbids people.
- Publishing a realistic synthetic presenter to YouTube needs
  `status.containsSyntheticMedia`; it derives from `style_policy=photoreal`
  and lands before any such upload.
- Speech-to-video cost: ~420 s and ~31.9 GB per 4.8 s chunk on a 5090
  ([comfyui-video-provider](comfyui-video-provider.md)).

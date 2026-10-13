# Creative studio: characters, screenplays, dialogue, and architect-composed cartoons

Status: design, 2026-10-12. Epic: see the tracking issue linked from the PRs.

## Why

The media pipeline makes one kind of video: a blog post becomes a podcast
script, the script becomes one narration track, and a director turns that into
B-roll plus presenter shots. Everything downstream of the blog post is fixed.

On 2026-10-12 a hand-built test made an 18 s anime short with a recurring
character (Dex), lip-synced talking shots, two voices, music and captions
(`/data/comfyui-spike/cartoon-demo/`). It used only models already installed.
The operator wants the **architect** to make that kind of thing, and anything
else he prompts for, without a human building it step by step.

The architect composes graphs from atoms. It cannot invent a capability that
no atom provides. So the job is to add the missing atoms and make the existing
media atoms work without a blog post upstream.

## What exists and is reused

| Piece                                                                                                                           | Where                                                                                                                  | Reused as                                                         |
| ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Personas: face (`portrait_url`) + voice (`voice_provider`, `voice_id`, `voice_ref_audio_url`)                                   | `services/persona_service.py`, `persona.<slug>.*` settings, `poindexter personas`                                      | The cast. A character is a persona.                               |
| Per-persona TTS (Kokoro voice or Chatterbox clone, full speech normalization)                                                   | `PodcastService._generate_with_voice(..., persona=)`                                                                   | One call per dialogue line.                                       |
| Shot-list renderer: `presenter` (Wan S2V lip-sync), `generative` (Wan i2v), `image_kenburns`, `image_gen`, `pexels`, `cli_demo` | `services/video_renderers/shot_list_renderer.py`                                                                       | Unchanged except speaker resolution and the shot cap.             |
| State-driven render atoms                                                                                                       | `media.render_long_video` / `media.render_short_video` read `video_shot_list` + `long_narration_audio_path` from state | Unchanged.                                                        |
| Captions + fidelity                                                                                                             | `media.transcribe_narration` compares ASR with `video_long_script`                                                     | The dialogue atom writes the spoken lines to `video_long_script`. |
| `media.qa`, `media.persist`                                                                                                     | task-keyed, `post_id` optional                                                                                         | Unchanged.                                                        |
| House style per niche                                                                                                           | `niche.<slug>.media.house_style`, `media_subject_policy`                                                               | Style of every still.                                             |
| Architect node config                                                                                                           | spec node `config` is seeded onto the atom's input                                                                     | How a plan passes cast, format, length.                           |

## What is added

Four capabilities, each an atom (or a seam the atoms share), each its own PR.

### 1. Character sheets and cast-aware shots

- **Persona fields** `appearance` and `personality`. `appearance` is the
  canonical visual description that keeps a character on-model in every still
  ("a cheerful young fox engineer, orange fur, round amber goggles pushed up on
  his head, teal lab jacket over a black t-shirt"). It falls back to
  `description`. `personality` is how the character talks and behaves; only the
  writer reads it. Both are ordinary validated persona fields, set with
  `poindexter personas set <slug> appearance "..."`.
- **Shot fields** (optional, backward compatible with frozen lists):
  - `characters: list[str]` names the personas in frame. Their `appearance`
    text is joined into the still's prompt in code
    (`services/character_prompt.py`), so consistency does not depend on an LLM
    remembering to repeat the description.
  - `speaker: str | None` names who talks in a `presenter` shot. When set, the
    renderer lip-syncs that persona's portrait. When unset, it keeps today's
    behaviour (the niche's persona).
- **Shot cap**: `VideoShotList.shots` max goes from 30 to 120. A three-minute
  piece at 5 s per shot is about 36 shots. The blog director keeps its own
  30-shot cap.

Measured on 2026-10-12: prompt text plus a fixed seed kept Dex consistent
across three styles and two scenes in Qwen-Image. Reference-image
conditioning (an image-edit model, or a character LoRA) is a later upgrade
behind the same `characters` field.

### 2. Screenplay writer: `story.write_screenplay`

Input (state or node config): `topic` (always on state), optional `brief`,
`format` (`explainer` | `story` | `dialogue` | `sketch`), `cast` (persona
slugs), `narrator` (persona slug or empty), `target_seconds`, `tone`.

Output `screenplay`:

```json
{
  "title": "...",
  "logline": "...",
  "format": "dialogue",
  "cast": ["dex", "nova"],
  "narrator": "narrator",
  "scenes": [
    {
      "scene_id": "s1",
      "setting": "night-time server room, glowing racks",
      "characters": ["dex"],
      "beats": [
        {
          "kind": "line",
          "speaker": "dex",
          "text": "Hey! I'm Dex.",
          "delivery": "cheerful, to camera"
        },
        {
          "kind": "action",
          "text": "Dex walks the aisle sipping coffee",
          "characters": ["dex"]
        }
      ]
    }
  ]
}
```

The prompt (`story.screenplay`) lives in a new SKILL.md pack. It gets the cast
with each persona's `display_name`, `personality` and `appearance`, the target
length as a word budget (speech rate is a setting), and the format's rules. It
uses JSON-constrained decoding with thinking off. Code validates the result:
every speaker is in the cast or is the narrator, there is at least one line,
and the length is within tolerance. A failed validation feeds FIX messages
back for one repair attempt, the same pattern the architect uses. Model:
`story_writer_model`, else the local writer resolver.

### 3. Multi-voice dialogue audio: `media.render_dialogue`

Takes `screenplay`, renders every `line` beat with its speaker's persona voice
through `PodcastService.synthesize_line(text, persona=...)` (a public wrapper
over `_generate_with_voice`), and joins the lines with
`story_dialogue_gap_seconds` of silence. Lines inside a scene use that gap;
scene changes use `story_scene_gap_seconds`. It produces:

- `long_narration_audio_path`: the dialogue track, which the render atoms
  already read;
- `dialogue_timeline`: `[{"beat_idx", "scene_id", "speaker", "text",
"start_s", "end_s"}]`;
- `video_long_script`: the spoken lines in order, for the caption fidelity
  check.

The narrator is a persona too (`story_default_narrator_persona`). With no
narrator persona the line uses the house voice (`podcast_tts_voice`).

### 4. Screenplay director: `media.direct_screenplay`

Turns `screenplay` + `dialogue_timeline` into `video_shot_list`:

- Timing is code, not LLM. Shots tile the timeline exactly. A line spoken by
  an on-screen character becomes a `presenter` shot with `speaker` set,
  provided that persona has a portrait. Narrator lines and action beats become
  `generative` shots, or `image_kenburns` past the per-video hero budget
  `video_hero_shots_max`. Lines longer than `story_max_shot_seconds` split
  into several shots.
- Visuals are LLM. One call (`story.shot_visuals` prompt) writes a `prompt`
  and `motion` per non-presenter shot from the scene setting and beat text.
  Code then prefixes the house style and the `characters` appearance.
- Validated against `VideoShotList`. If the visuals call fails, the plain
  scene setting is used, so a render never stalls on it.

After this, the existing chain runs unchanged:
`media.transcribe_narration → media.render_long_video → media.qa → media.persist`.

### 5. Architect integration

- The new atoms register through the normal discovery path, so they appear in
  the catalog. Their `AtomMeta.description` states the contract in the
  catalog's terms ("INPUT … OUTPUT … USUALLY FOLLOWED BY …").
- The architect system prompt gains one composition pattern for creative video
  (writer → dialogue → director → transcribe → render → qa → persist).
  It is phrased as a pattern, not a fixed template, so the architect can drop
  steps (no captions), add steps (a short lane), or run the writer alone.
- An end-to-end acceptance run: in the console chat, "make a two-minute
  cartoon where Dex explains X to Nova" yields a plan card. Running it
  renders and persists the video as a task-keyed media asset. Reviewing and
  publishing a video with no post behind it is phase 6 (see Out of scope).

## Settings (all in `settings_defaults.py`)

As built. `story_writer_temperature` from the first draft was dropped: the
shared LLM helper has no temperature knob, and the model's own default suits
creative writing.

| Key                                                                                    | Default                   | Meaning                                         |
| -------------------------------------------------------------------------------------- | ------------------------- | ----------------------------------------------- |
| `story_writer_model`                                                                   | `""`                      | Screenplay model; empty = local writer resolver |
| `story_writer_timeout_seconds`                                                         | `300`                     | Per writer call                                 |
| `story_speech_words_per_second`                                                        | `2.5`                     | Converts `target_seconds` into a word budget    |
| `story_seconds_per_scene`                                                              | `40`                      | Scene count = target / this                     |
| `story_length_min_ratio` / `_max_ratio`                                                | `0.65` / `1.75`           | Soft length window around the budget            |
| `story_default_format` / `_cast` / `_target_seconds`                                   | `explainer` / `""` / `90` | Gaps a plan does not fill                       |
| `story_default_narrator_persona`                                                       | `""`                      | Narrator voice; empty = house voice             |
| `story_dialogue_gap_seconds` / `story_scene_gap_seconds` / `story_action_beat_seconds` | `0.35` / `0.8` / `1.5`    | Pauses in the dialogue track                    |
| `story_max_shot_seconds` / `story_min_shot_seconds`                                    | `6.0` / `0.8`             | Longest still; slivers merge below this         |
| `story_min_animated_shot_seconds`                                                      | `2.5`                     | Shortest still worth animating                  |
| `story_max_talking_shots`                                                              | `-1`                      | Cap on lip-synced shots (-1 = none)             |
| `story_visuals_model`                                                                  | `""`                      | Shot-visuals model; empty = writer model        |
| `video_cast_render_prompt` / `video_cast_negative_prompt`                              | see defaults              | S2V prompt + negative for cast shots            |

## As built (2026-10-12)

- **The writer is outline then scenes, not one call.** Asked for 300 words in
  one call, gemma-4-31B wrote 169 in a single scene, and its repair pass
  shrank that to 106. `story.outline`, then one `story.scene` call per scene,
  landed 307 words over three scenes, and the shape scales to multi-minute
  pieces. Length is a soft check: one retry, then a note.
- **`screenplay_narrator` names the narrating persona.** The architect
  naturally wrote a slug there, so `"none"` turns narration off and a slug
  makes that persona narrate. The screenplay records it, and the dialogue
  step reads it.
- **The architect gets a live cast roster** (`persona_service.cast_roster`) in
  its prompt, because it cannot cast a character it has never heard of. Live
  compose (qwen3-vl): a video intent produced the full seven-step chain, and a
  script-only intent produced the writer alone.
- **The architect validator treats every declared state channel as
  available**, so it cannot catch a director placed before the writer. The
  director's input check catches it at run time.

## Out of scope for this epic

- Review and distribution of standalone videos, which have no post
  (phase 6, poindexter#1140). `media.persist` saves them as task-keyed
  `media_assets` rows. Gate-2 approval (`media_approvals`, NOT NULL
  `post_id`) is created by `media_distribute` only once the task's post
  exists, so a post-less video sits unlinked: no approval card, no upload.
- Reference-image conditioning for stills (Qwen-Image-Edit or a character
  LoRA).
- Background music as its own atom. `video_ambient_audio_path` already flows
  into the render and can be produced by a later atom.
- Lip-synced shots of two characters at once. S2V drives one face per clip, so
  dialogue cuts between speakers (shot/reverse-shot), which is also how most
  animation does it.

## Order of work

1. Character sheets + cast-aware shots (schema, persona fields, renderer speaker, prompt composer, shot cap).
2. `story.write_screenplay` + pack prompt.
3. `media.render_dialogue` + `PodcastService.synthesize_line`.
4. `media.direct_screenplay` + pack prompt.
5. Architect prompt pattern + end-to-end acceptance run.
6. Review + publish standalone (post-less) videos (poindexter#1140).

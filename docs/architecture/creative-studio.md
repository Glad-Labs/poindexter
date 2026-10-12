# Creative studio — cartoons from a prompt

The creative studio (poindexter#1134) lets the pipeline architect compose
**creative video**: cartoons, explainers and sketches with recurring
characters, written and voiced from a topic and a cast. It sits beside the
blog → podcast → video path and reuses its renderer.

## The chain

```text
story.write_screenplay → media.render_dialogue → media.direct_screenplay
    → media.transcribe_narration → media.render_long_video → media.qa → media.persist
```

Every step is an atom, so the architect can compose part of the chain: a
screenplay alone, or dialogue audio without video. The node config keys below
are how a plan passes its creative choices; each falls back to a
`story_default_*` setting.

| Atom                      | Reads                                                          | Writes                                                                | Node config                                                                                                                                                                                                 |
| ------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `story.write_screenplay`  | `topic`                                                        | `screenplay`                                                          | `screenplay_format` (explainer, story, dialogue, sketch), `screenplay_cast` (persona slugs), `screenplay_target_seconds`, `screenplay_brief`, `screenplay_tone`, `screenplay_narrator` (`none` = cast only) |
| `media.render_dialogue`   | `screenplay`                                                   | `long_narration_audio_path`, `video_long_script`, `dialogue_timeline` | `dialogue_narrator_persona`                                                                                                                                                                                 |
| `media.direct_screenplay` | `screenplay`, `dialogue_timeline`, `long_narration_audio_path` | `video_shot_list`                                                     | `screenplay_visual_style` (art style for every still)                                                                                                                                                       |

## The cast is personas

A character is a persona ([media-personas](media-personas.md)): a face
(`portrait_url`), a voice (`voice_provider` + `voice_id`, or a Chatterbox clone
reference), and a character sheet. `appearance` is the canonical visual
description joined into every still the character appears in. `personality`
is how it talks; only the writer reads it. Set one up with
`poindexter personas` (example in the personas doc).

## Writing: outline, then scenes

`services/screenplay.py` writes in two steps, as a writers' room does:

1. `story.outline` plans the scenes: setting, summary and characters.
2. `story.scene` writes each scene's beats against its share of the word
   budget, given the outline and the last lines before it.

The word budget is `target_seconds × story_speech_words_per_second`. The scene
count is `target_seconds / story_seconds_per_scene`.

Asked for 300 words in one call, gemma-4-31B wrote 169 words in one scene, and
the repair pass shrank it to 106. Outline then scenes landed 307 words over
three scenes. Small calls hit their targets, and the shape scales to
multi-minute pieces.

Code checks every reply. A speaker outside the cast, a missing setting, or an
unknown beat kind is a hard problem. It gets one repair attempt
(`story.screenplay_fix`), then the step raises. Length is soft: it gets one
retry, then the scene is accepted and the shortfall is recorded in
`screenplay.notes`.

## Voicing: one track, measured

`services/dialogue_audio.py` speaks every line in its speaker's persona voice
through `PodcastService.synthesize_line`, which applies the same speech
normalization as narration. Clips are joined with pauses:

| Pause                        | Setting                      |
| ---------------------------- | ---------------------------- |
| Between two lines in a scene | `story_dialogue_gap_seconds` |
| At a scene change            | `story_scene_gap_seconds`    |
| For an action beat           | `story_action_beat_seconds`  |

The action-beat pause gives the shot that shows the action some screen time.

Durations are measured with ffprobe, never estimated, because the shot list
cuts lip-sync windows from this track. A line that fails twice raises.
Re-voicing it would put one character's words in another's mouth.

The narrator is a persona too (`story_default_narrator_persona`). Without one,
narration uses the house voice.

## Directing: shots that tile the track

`services/screenplay_director.py` builds the shot list. Timing is code, and the
shots tile the dialogue track exactly:

- **Cast lines** become talking shots: `presenter` with `speaker` set, so the
  speaker's portrait is lip-synced (Wan S2V). A talking shot is capped at one
  seamless S2V chunk (`presenter_window_budget_s`, about 4.7 s), because a
  chunk seam visibly jumps. The rest of a longer line becomes a cutaway with
  the voice over it.
- **Narration, action beats, and characters without a portrait** become scene
  stills. They are animated (`generative`) within `video_hero_shots_max`,
  longest first. The rest alternate between Ken Burns and static stills so the
  schema's pacing rule holds.
- **Repeated speakers:** a third talking shot in a row by one speaker becomes
  a cutaway.
- **Very short shots** (under `story_min_shot_seconds`) fold into a neighbouring
  still.

Visuals come from an LLM. One `story.shot_visuals` call writes a picture and a
motion line per non-talking shot. A failed or partial call falls back to the
scene setting plus beat text, per shot. Code then puts the visual style first,
and the renderer adds each character's appearance (`character_prompt`).

Talking cast shots act their line. They use `video_cast_render_prompt`
(expressive face, gestures) and a quality-only `video_cast_negative_prompt`,
not the blog presenter's still, gesture-free studio read.

## Cost

Measured 2026-10-12 on the RTX 5090:

| Step               | Time                                         |
| ------------------ | -------------------------------------------- |
| Screenplay         | 18 s (45 s sketch) to 171 s (3-minute story) |
| Dialogue track     | about 20 s for 2 minutes                     |
| Director           | about 30 s                                   |
| Each talking shot  | about 3½–4½ min                              |
| Each animated shot | 1–10 min, by quality tier                    |

A 2-minute two-character piece is about 13 talking shots, so roughly an hour of
GPU time.

## Not yet

- Uploading standalone videos (no post) to YouTube.
- Reference-image conditioning for stills.
- A music-bed atom.
- Two characters lip-synced in one shot. Dialogue cuts between speakers.

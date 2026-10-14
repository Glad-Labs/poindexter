# Creative studio — cartoons from a prompt

The creative studio (poindexter#1134) lets the pipeline architect compose
**creative video**: cartoons, explainers and sketches with recurring
characters, written and voiced from a topic and a cast. It sits beside the
blog → podcast → video path and reuses its renderer.

## The chain

```text
[story.cast_characters →] story.write_screenplay → media.render_dialogue → media.direct_screenplay
    → media.transcribe_narration → media.render_long_video → media.qa → media.persist
```

Every step is an atom, so the architect can compose part of the chain: a
screenplay alone, or dialogue audio without video. Its system prompt teaches
this order and carries a live roster of the characters it may cast
(`persona_service.cast_roster`). Ask the console chat for "a two-minute
cartoon where Dex explains VRAM to Nova, anime style" and it composes the
seven steps with the cast, length and style in config. The node config keys below
are how a plan passes its creative choices; each falls back to a
`story_default_*` setting.

| Atom                      | Reads                                                          | Writes                                                                            | Node config                                                                                                                                                                                                                                    |
| ------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `story.cast_characters`   | `topic`                                                        | `screenplay_cast`, `screenplay_visual_style`, `screenplay_narrator`, `cast_sheet` | `screenplay_format`, `screenplay_target_seconds`, `screenplay_brief`, `screenplay_cast` (slugs that must appear), `screenplay_visual_style`, `screenplay_narrator`                                                                             |
| `story.write_screenplay`  | `topic`                                                        | `screenplay`                                                                      | `screenplay_format` (explainer, story, dialogue, sketch), `screenplay_cast` (persona slugs), `screenplay_target_seconds`, `screenplay_brief`, `screenplay_tone`, `screenplay_narrator` (`none` = cast only, or the persona slug that narrates) |
| `media.render_dialogue`   | `screenplay`                                                   | `long_narration_audio_path`, `video_long_script`, `dialogue_timeline`             | `dialogue_narrator_persona`                                                                                                                                                                                                                    |
| `media.direct_screenplay` | `screenplay`, `dialogue_timeline`, `long_narration_audio_path` | `video_shot_list`                                                                 | `screenplay_visual_style` (art style for every still)                                                                                                                                                                                          |

A plan can also change creative settings for itself or one step, such as a
calmer voice for one character or a different coat this time, without
touching the saved settings. See
[plan setting overrides](plan-setting-overrides.md).

## The cast is personas

A character is a persona ([media-personas](media-personas.md)): a face
(`portrait_url`), a voice (`voice_provider` + `voice_id`, or a Chatterbox clone
reference), and a character sheet. `appearance` is the canonical visual
description joined into every still the character appears in. `personality`
is how it talks; only the writer reads it. Set one up with
`poindexter personas` (example in the personas doc).

### Casting: characters the request brings with it

`story.cast_characters` (`services/casting.py`) runs first when a request
needs characters nobody has made: a fairytale, a fable, any story with its
own people. Before it, those had to be created by hand as personas, so "make
The Boy Who Cried Wolf" could not be asked for at all. One LLM call
(`story.casting` in the storytelling pack) reads the request and the roster,
reuses the personas that fit, and designs the rest: name, role, appearance,
personality, a voice from `story_voice_catalog`, an energy and a sample line.
It also decides on a narrator. The art style comes from
`media_subject_policy.story_visual_style`, which `media.direct_screenplay`
uses too. A look the request names ("claymation", "watercolour storybook")
is kept, and casting writes it out as a phrase a painter could follow.
With no look named, the piece takes the company's house style
(`niche.<slug>.media.house_style`, then `media_house_style`). A style that
only names the medium ("cartoon", "animated", listed in
`story_style_medium_words`) counts as none, because the architect passes
the medium as the style even when told not to. Code checks the reply (reused ids exist, voices come from the
catalog, every new character has looks, at most `story_cast_max_new`) and
sends the FIX lines back once.

Each new character becomes a persona, so it is reusable and shows up in the
architect's roster afterwards:

- **Portrait.** `story_portrait_template` with the cast's style. The first
  one is drawn from text by the image service; each later one by the
  image-edit model with the cast's portraits as style-only references, so
  the whole cast shares one look. With no style asked for, reused characters'
  portraits anchor too. Every draw is checked by `qa.cast_frame` for exactly
  that one character and redrawn up to `story_portrait_attempts` times. The
  portrait matters past the cast sheet: every still and talking shot of the
  piece is drawn from it ([One look](#one-look-every-frame-drawn-from-the-portraits)).
  A portrait that cannot be drawn or uploaded leaves the character voice-only.
- **One look per cast.** Casting offers for reuse only characters who suit
  this piece's look: a voice-only character, one whose portrait was drawn in
  that look (the look's words are in its `portrait_prompt`), and anyone the
  request names. Everyone else is designed fresh, and the new portraits are
  drawn as one set, each anchored on those before it. The first version
  reused every character and redrew them in the asked-for look, for that
  piece only. A neon retelling (2026-10-14) restyled the watercolour Ruby,
  Wolf and Marta, and the three came out in three different styles. Matt:
  "they should really be new characters". A named character in another look
  is still redrawn for the piece from their own portrait. The redraw travels
  in the shot list as `cast_portraits`, and the persona keeps its own
  portrait.
- **Voice.** With `story_cast_voice_engine=chatterbox`, Kokoro reads the
  sample line in the chosen voice and the clip becomes the character's
  Chatterbox clone reference, with an emotion strength from its energy
  (`story_cast_energy_exaggeration`, low 0.65 / medium 0.85 / high 1.0).
  Kai's and Mira's voices won their 2026-10-13 auditions this way, at 0.9
  and 0.85. Animation takes a bigger delivery than a realistic talking head:
  the louder take beat the restrained one for a cartoon. The worker writes the clip to
  `story_cast_voice_dir`, which compose mounts from `~/.poindexter/tts-voices/cast`,
  and chatterbox reads it under `story_cast_voice_ref_prefix`. The worker
  gets only that `cast/` subfolder, never `tts-voices` itself, because the
  hand-made references there must stay out of reach of LLM-driven code. A
  clip that cannot be written leaves the character on plain Kokoro.

Its outputs are state, and state wins over node config, so a plan that
casts puts the cast, narrator and style on `story.cast_characters` and on no
later node. All four outputs are declared `PipelineState` channels. An
undeclared one would be dropped before the writer ran.

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

### A story, not a lecture with pictures

Matt on the first two-character cartoon: he wants "coherent stories and not
just random shots stitched together without emotion or meaning". So:

- The outline names a **spine**: who wants what, what is in the way (often a
  wrong belief), the turn, and the payoff that answers the opening. Each scene
  names its **turn**, what is different by its end. Missing either is a hard
  problem that goes back to the writer once.
- Each scene is written knowing the spine, its turn, and its place: what the
  next scene needs, or, for the last scene, the payoff and the opening lines
  to call back to.
- Characters answer each other, the emotion follows the story, and action
  beats appear only where the audience must see something (one to three a
  scene, not one between every line). The characters stay in one main place
  unless the story gives them a reason to move.
- Every line carries an **intensity** (low / medium / high), which the
  voicing step turns into emotion (below).

Re-written with the same topic, cast and length, the first cartoon's script
went from four scenes of explanation with 11 action beats to a spine (Kai
thinks "RAM is RAM", Mira's desk-and-bookshelf analogy turns him, he ends on
"I just need a bigger desk!") with 4 action beats and an arc from frustrated
to triumphant.

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

**Per-line emotion.** A Chatterbox voice reads each line at its own emotion
strength: the character's level (`persona.<slug>.voice_exaggeration`, else
the shared `plugin.tts_provider.chatterbox.exaggeration`) moved by the line's
intensity (`story_line_intensity_offsets`, default low −0.2 / high +0.2) and
clamped to `story_line_exaggeration_min` / `_max` (0.25–1.2; past about 1.2
Chatterbox garbles words). Kai sits at 0.9, so his outbursts read at 1.1 and
his quiet lines at 0.7; a Whisper round trip of a voiced scene came back word
for word. Kokoro has no emotion control, so a Kokoro character's lines are
unchanged.

## Directing: shots that tile the track

`services/screenplay_director.py` builds the shot list. Timing is code, and the
shots tile the dialogue track exactly:

- **Cast lines** become talking shots: `presenter` with `speaker` set, so the
  speaker's portrait is lip-synced (Wan S2V). A talking shot is capped at one
  seamless S2V chunk (`presenter_window_budget_s`, about 4.7 s), because a
  chunk seam visibly jumps. The rest of a longer line becomes a **reaction
  shot**: the scene's other characters listening, with the voice over it.
- **Narration, action beats, and characters without a portrait** become scene
  stills. They are animated (`generative`) within `video_hero_shots_max`,
  longest first. The rest alternate between Ken Burns and static stills so the
  schema's pacing rule holds.
- **Repeated speakers:** a third talking shot in a row by one speaker becomes
  a reaction shot of the listener.
- **Short beats** (under `story_min_insert_seconds`, 2.5 s) are the scene's
  **master shot**: one wide picture of the scene's characters in its setting
  (`story_scene_master_template`), drawn once per scene and reused.
- **Very short shots** (under `story_min_shot_seconds`) fold into a neighbouring
  still.

Visuals come from an LLM. One `story.shot_visuals` call writes a picture and a
motion line per reaction and longer insert (master shots are fixed). It is told
that each picture is the next moment of the same scene, in the same place with
the same people, and that a spoken analogy stays spoken: the picture shows the
characters talking it through. A failed or partial call falls back to the scene
setting plus beat text, per shot. Code then puts the visual style first, and
the renderer adds each character's appearance (`character_prompt`).

This is coverage, the way a filmed conversation is cut, rather than
illustration. The first two-character cartoon cut talk, a new 1.5 s picture,
talk, another new picture: 16 of its 19 non-talking shots were action-beat
pauses, each drawn fresh, and the illustrator was handed the spoken line as
"what is happening", so "think of the model as a pot of soup" became a picture
of soup. Re-directed with the same screenplay, 15 of the 20 stills became the
scene's own master shot and the other 5 reactions.

Every still with characters is checked before use (`qa.cast_frame`): the
characters it needs are there, and nobody outside the cartoon's cast is. A
still that fails is redrawn, up to `video_cast_talking_frame_attempts`; the
last draw stands if none passes. On the re-directed cartoon's 20 stills it
rejected the one with an invented man in a white coat and passed the rest,
twice each, including reaction shots with the speaker in the background.

Talking cast shots use `video_cast_render_prompt` and
`video_cast_negative_prompt`, not the blog presenter's studio read. They
first asked for an "expressive face" and "hand gestures". In the Red Riding
Hood render (2026-10-13) every visible hand melted mid-gesture and the mouth
opened far wider than the line needed. They now ask for a natural, lively
expression with still hands and small mouth movements, and the negative
names the failures. The talking frame (`story_talking_frame_template`) is a
head-and-shoulders close-up with the hands out of frame, since S2V animates
any hand it can see. The voice carries the emotion.

## One look: every frame drawn from the portraits

The first cartoon renders switched art style whenever a character spoke.
Stills, and the frames animated shots start from, came from the production
image model, drawn from text. Talking shots lip-synced a fixed portrait made
by another model, with its own style and a baked-in background.

For a screenplay video (`timing="exact"`), every frame now comes from one
source (`services/reference_frames.py`, poindexter#1141). An image-edit model,
Qwen-Image-Edit-2511 in ComfyUI with a 4-step Lightning add-on, draws each
still with the cast's portraits as references, which keeps both identity and
art style:

- **Stills and animated-shot first frames** use the portraits of the
  characters in the shot as references. A shot that shows nobody passes the
  cast portraits as style-only references, so backgrounds match too.
- **Talking shots** first draw a per-scene talking frame: the speaker in
  this scene's setting and style, from their portrait. The director writes
  its prompt as `Shot.frame_prompt` (`story_talking_frame_template`), and the
  lip-sync animates that frame instead of the fixed portrait. One frame is
  drawn per speaker per scene and reused for each of their lines there.

  Before the lip-sync spends ~6 minutes on it, the vision judge checks the
  frame (`qa.talking_frame`): exactly one person, and that person is the
  speaker as their sheet describes them. A frame that fails is redrawn with a
  fresh seed, up to `video_cast_talking_frame_attempts` (3) draws, and then the
  portrait is used. On the first two-character render the edit model put an
  unwritten stranger beside Mira in the closing scene, and he stayed there
  through every one of her lines. On that render's six frames the check
  rejected that one and passed the other five, twice each.

Each frame takes about 6–22 s. The wording that tells the model which picture
is whom is `video_reference_*_template`, and the model files and sampler
settings are also `video_reference_*` settings. Any failure falls back to the
old path (text still, fixed portrait), and `video_reference_frames_enabled`
turns the whole thing off. Blog videos never take this path.

A line that fails to voice now runs the shared VRAM reclaim ladder, keeping
Chatterbox, before its one retry. On 2026-10-12 a narrator line OOM'd because
ComfyUI still held the edit model; the reclaim freed 24 GB and the retry
voiced it.

## Review: a video with no post

A creative video has no blog post. It is the whole piece. Gate 2 used to key
every review row on a post, so these videos could not be reviewed
(poindexter#1140).

`media.persist` marks a video **standalone** when the run has a `screenplay` and
no post: `media_assets.metadata.standalone = true`, with the screenplay's title
and logline as the asset's title and description. `media_distribute` seeds
its review row under the task on the first cycle it sees the asset, instead of
waiting for a post.

A `media_approvals` row is keyed on its **subject**. `subject_id` is a stored
generated column: the post id, or the task id when there is no post. Every
review surface takes that one id:

```bash
poindexter media pending --medium video   # a standalone item prints its task id
poindexter media open <id> video
poindexter media approve <id> video
```

The console's media queue lists the video with its screenplay title, and the
drawer previews it the same way. The quality eval runs as it does for any
video. Shot fidelity comes from the render's own audit rows, and topic match
is scored against the screenplay title.

The plan's own task finishes as `completed`, not `awaiting_approval`: the
video's review is the decision, and the article review queue has nothing to
publish for a video with no post (`chat_plans._landing_status`).

A standalone video always waits for a human. The niche auto-approve and
earned-autonomy tiers vouch for an article's media, and that track record says
nothing about a cartoon. The RSS feeds join posts, so a standalone video never
appears in one.

Once approved, `media_distribute` uploads it to YouTube in the same pass as
any other video. The title is the screenplay's title, the description opens
with its logline, and there is no "Read the full post" link because there is
no post. The synthetic-media disclosure reads the shot list that
`media.persist` kept on the asset, so a cast member drawn photoreal is
disclosed exactly as on a post's video. The upload is stamped on the task's
review row, and its `pipeline_distributions` row carries the task with no
post.

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

- A music-bed atom.
- Two characters lip-synced in one shot. Dialogue cuts between speakers.

## Drafting in the chat

The console chat can draft a piece before anything renders
(`services/creative_drafts.py`, phase 1 of chat drafting). It has four
tools:

- `draft_cast` designs the characters, draws their portraits and voices a
  sample line for each, without saving anything.
- `draft_screenplay` writes a script for existing characters.
- `revise_draft` makes the next version from a note ("make the wolf
  scarier", "cut scene 2").
- `show_draft` reads a draft back to the model.

Each draft is a row in `creative_drafts`, one row per version (`parent_id`
points at the version it revises). It is built in the background, because
drawing a cast outlasts a chat turn's deadline. The chat card polls
`GET /api/chat/drafts/{id}` until the draft is `ready` or `failed`. A ready
card shows the portraits with playable voices, or the script, and has Keep
and Discard buttons.

**Keep** (`POST /api/chat/drafts/{id}/keep`) saves the cast's characters as
personas, using the portraits already drawn (`casting.keep_cast`). Nothing
before that touches `persona.*`. A script draft is marked kept for the render
step (phase 3). A draft card adds a `[cast draft <id> v<n>]` line to the
model's context, so a note in a later turn can name it.

Casting is split so a draft can stop short of saving:

1. `design_cast` decides who is in the piece and how it looks.
2. `draw_cast` draws the portraits through an upload callback.
3. `preview_cast` stores a draft under `drafts/<id>/`.
4. `keep_cast` and `cast_characters` save.

A revision (`revise_cast_design`, prompt `story.casting_revise`) keeps every
character the note does not touch under the same id, and `preview_cast`
redraws only those whose looks or style changed. It re-voices only those
whose voice, energy or line changed. Scripts revise in one call
(`screenplay.revise_screenplay`, prompt `story.screenplay_revise`) and are
checked exactly like a new script.

Two GPU rules came from the first live drafts:

- A cast draft waits while a pipeline task runs a GPU stage
  (`creative_draft_gpu_busy_stages`, up to `creative_draft_gpu_wait_minutes`).
- The voice previews are made before the portraits, with ComfyUI reclaimed
  first when the render card has under `story_cast_preview_min_free_gb` free.
  ComfyUI keeps the ~27 GB image-edit model resident after drawing, and its
  `/free` often releases nothing, so a Chatterbox preview right after the
  portraits ran out of card.

`draw_cast` also soft-frees ComfyUI after it draws.

### Storyboards

Phase 2 adds `storyboard_draft`, which turns scenes of a script draft
(`scenes`: ids or numbers, or all) into the pictures and sound the render
would start from, before any animation (`services/storyboard.py`). It runs
the render's own steps and stops short of animating:

1. `render_dialogue` voices the scenes into one track.
2. `direct_screenplay` plans the shots against that track, with the cast
   draft's style and per-piece portraits.
3. Each shot is drawn with the renderer's functions:
   - a talking shot gets its per-scene talking frame (`_draw_talking_frame`,
     checked for exactly the speaker);
   - a still is drawn from the cast's portraits (`_render_reference_still`).

   Stock footage is listed, not drawn.

So the preview is the frame the video will animate, not a look-alike. A
talking shot costs about six minutes of GPU to animate and a still about ten
seconds, so a still is the cheap place to fix a shot.

The card plays an animatic: the voiced track, with the picture of whichever
shot is on screen at that moment. Clicking any thumbnail seeks to that shot.

A note on a storyboard goes to `revise_draft` (prompt
`story.storyboard_revise`). It returns new picture prompts for only the
shots the note is about, and only those are redrawn (into `-v<n>` files);
every other shot keeps its picture. The payload keeps the shot list and the
timeline, which phase 3 renders from.

Storyboards wait for a running render like cast drafts, and voice before
they draw (`_ensure_voice_headroom`).

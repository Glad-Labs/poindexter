---
name: storytelling
description: >
  Creative studio storytelling — writes short animated screenplays (explainers,
  stories, two-character dialogues, sketches) for a cast of personas, as JSON
  scenes of spoken lines and visible actions. An outline call plans the scenes,
  then one call per scene writes its beats. Used by the story.write_screenplay
  atom (services/screenplay.py); the dialogue and shot-list steps read its
  output. Operator brand is templated via {site_name}.
license: Apache-2.0
metadata:
  category: story
  prompts:
    - key: story.outline
      output_format: json
      description: 'Screenplay outline — topic + brief + format + cast (slug, personality, looks) + length + scene count in; JSON title, logline and scenes (scene_id, setting, summary, characters) out. Validated in code (settings, summaries, cast-only characters). Used by services.screenplay.write_screenplay.'
    - key: story.scene
      output_format: json
      description: 'Writes ONE scene of an outlined screenplay — the outline, this scene, its word budget and the last lines before it in; JSON "beats" out: "line" beats (speaker, text, delivery) and "action" beats (text, characters). Validated in code (cast-only speakers, beat kinds, soft length). Used by services.screenplay.write_screenplay.'
    - key: story.shot_visuals
      output_format: json
      description: 'Shot visuals for a screenplay video — the piece''s title/logline and one row per non-talking shot (scene setting, what happens, who is visible, animated or still) in; JSON {"shots": [{id, prompt, motion}]} out. Style and character looks are added in code, so prompts describe composition and action only. Used by services.screenplay_director.write_visuals.'
    - key: story.screenplay_fix
      output_format: json
      description: 'The one repair retry for story.outline and story.scene: carries the rejected reply ({previous}) and the FIX list code produced ({errors}). Used by services.screenplay.write_screenplay.'
---

# Storytelling skill

Screenplays for the creative studio (poindexter#1134), written in two
steps: `story.outline` plans the scenes, then `story.scene` writes each one
against its share of the word budget. The cast is personas; code formats each
one as `slug: name; personality; looks` in `{cast_block}`. The word budget comes
from `story_speech_words_per_second` × the requested length, and the scene count
from `story_seconds_per_scene`. Code validates every reply and sends problems
back once through `story.screenplay_fix`.

## story.outline

```text
You are the head writer at {site_name}, outlining a short animated screenplay.

ASSIGNMENT
Topic: {topic}
Brief: {brief}
Format: {format_name}
Tone: {tone}
Length: about {target_seconds} seconds of audio across about {scene_count} scene(s).

CAST — the only characters who may appear. Use the id before the colon exactly.
{cast_block}

Narrator available: {narrator_available}.

FORMATS — follow the one named above.
- explainer: one idea taught clearly. Open with a hook question, build the idea in small steps with a concrete example, end on the one thing to remember.
- story: a character wants something, meets an obstacle, and changes or learns by the end. The topic is the world the story happens in.
- dialogue: two or more cast members talk it through. One is curious and asks; the other knows and explains; both have personality.
- sketch: a short comedic scene built on one funny premise drawn from the topic, escalating to a punchline.

PLAN THE SCENES
- Give the piece about {scene_count} scene(s). Each scene moves the piece forward and happens in its own place or moment, so the picture changes from scene to scene.
- "setting": a place, time of day and mood in one sentence a background artist could paint.
- "summary": what happens in the scene and what the audience learns or feels, in one or two sentences.
- "characters": the cast ids visible in the scene.
- The first scene hooks the audience; the last one lands the point or the punchline.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"title": "a short title", "logline": "the whole piece in one sentence", "scenes": [{{"scene_id": "s1", "setting": "a cosy workshop at night, warm lamps, rain on the window", "summary": "what happens here", "characters": ["id"]}}]}}
```

## story.scene

```text
You are the head writer at {site_name}, writing one scene of an animated screenplay.

THE PIECE
Topic: {topic}
Brief: {brief}
Format: {format_name}
Tone: {tone}

CAST — the only characters who may speak or appear. Use the id before the colon exactly.
{cast_block}

Narrator available: {narrator_available}. When yes, a line may use the speaker "narrator" for scene-setting, transitions and wrap-ups; the narrator's text is spoken aloud like any line, so sounds and visuals belong in action beats. When no, only cast members speak.

THE OUTLINE
{outline_block}

WRITE THIS SCENE: {scene_id}
Setting: {scene_setting}
What happens: {scene_summary}
Characters in the scene: {scene_characters}
Length: about {scene_word_budget} spoken words.

The lines just before this scene:
{previous_lines}

HOW TO WRITE IT
- The scene is an ordered list of beats. A "line" beat is one speaker saying one to three short sentences that sound natural out loud. An "action" beat is something the audience sees while nobody speaks, in one visual sentence, with the cast ids it shows in its "characters".
- Give every line a "delivery" note an actor could perform: an emotion and where the speaker looks.
- Write each character in their own voice, from their personality, and pick up naturally from the lines before.
- Keep facts accurate. Where a fact is uncertain, let a character ask or wonder rather than state it.
- The "text" of a line is read aloud by a speech synthesizer: write words exactly as a person would say them, spell out symbols and abbreviations, and keep stage directions in "delivery".
- Alternate lines with action beats so the picture keeps moving, and spend the full length on this scene's part of the story.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"beats": [{{"kind": "line", "speaker": "id or narrator", "text": "what they say", "delivery": "excited, to camera"}}, {{"kind": "action", "text": "what we see", "characters": ["id"]}}]}}
```

## story.shot_visuals

```text
You are the storyboard artist for an animated short.

THE PIECE
Title: {title}
Logline: {logline}

Each row below is one shot. The shots cut between the characters' talking
close-ups, so each picture is the next moment of the same scene: the same
place, the same people, the same light. Write the picture an illustrator
should paint for it.

SHOTS
{shot_rows}

FOR EVERY SHOT
- "prompt": one or two sentences describing the picture: camera framing (wide, medium or close-up), where the visible characters are and what they are doing, the key objects, and the lighting and mood of the scene.
- Set it in the row's scene, as that scene is described. The characters stay where the scene puts them.
- A reaction row shows the listed characters listening to the speaker: their face and body answering what was just said. The speaker's words stay spoken; the picture shows the people in the room.
- When a line uses a comparison or an analogy, picture the characters talking it through in the scene, with at most one prop from it in their hands or on a surface.
- Name visible characters by their id; their looks and the art style are added later, so describe only composition and action.
- Vary the framing from shot to shot (wide, medium, close-up, over the shoulder) while the place stays the same.
- Keep pictures free of written words, signs and screens with text.
- "motion": for an animated shot, one sentence on how the camera moves and what moves in the picture. For a still, an empty string.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"shots": [{{"id": 3, "prompt": "medium shot of dex at a cluttered workbench, holding a glowing graphics card up to the light, warm lamp glow", "motion": "slow push-in as dex turns the card in his paws"}}]}}
```

## story.screenplay_fix

```text
YOUR PREVIOUS REPLY
{previous}

It was rejected. Fix every problem below, keep everything that was fine, and reply with the complete corrected JSON object only:
{errors}
```

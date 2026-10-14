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
      description: "Screenplay outline — topic + brief + format + cast (slug, personality, looks) + length + scene count in; JSON title, logline, the story's locations (id, description: each place drawn once and reused) and scenes (scene_id, location, setting, summary, characters) out. Validated in code (settings, summaries, cast-only characters, every scene at a listed location). Used by services.screenplay.write_screenplay."
    - key: story.scene
      output_format: json
      description: 'Writes ONE scene of an outlined screenplay — the outline, this scene, its word budget and the last lines before it in; JSON "beats" out: "line" beats (speaker, text, delivery) and "action" beats (text, characters). Validated in code (cast-only speakers, beat kinds, soft length). Used by services.screenplay.write_screenplay.'
    - key: story.shot_visuals
      output_format: json
      description: 'Shot visuals for a screenplay video — the piece''s title/logline and one row per non-talking shot (scene setting, what happens, who is visible, animated or still) in; JSON {"shots": [{id, prompt, motion}]} out. Style and character looks are added in code, so prompts describe composition and action only. Used by services.screenplay_director.write_visuals.'
    - key: story.casting
      output_format: json
      description: "Casting for an animated piece: the request (topic, brief, format, length), the existing characters, a required cast, a style hint and the voice catalog in; JSON art style, narrator and characters out, each one either a reused character id or a new character with name, role, appearance, personality, voice, energy and a sample line. Validated in code (reused ids exist, voices are in the catalog, every new character has looks). Used by services.casting.cast_characters, which then draws each new character's portrait and builds its voice."
    - key: story.screenplay_revise
      output_format: json
      description: 'Revises a whole screenplay to address one note from the operator (chat drafting): the current screenplay JSON, the note, the cast and the word budget in; the whole revised screenplay out, in the same shape, with untouched scenes and lines kept. Validated in code exactly like a new screenplay. Used by services.screenplay.revise_screenplay.'
    - key: story.casting_revise
      output_format: json
      description: 'Revises a cast design to address one note from the operator (chat drafting): the current design JSON (style, characters, narrator), the note, the art style and the voice catalog in; the whole revised design out in the story.casting shape, with untouched characters kept under their ids. Validated in code like a new cast. Used by services.casting.revise_cast_design.'
    - key: story.storyboard_revise
      output_format: json
      description: 'Revises the pictures of a storyboard to address one note from the operator (chat drafting): a table of the drawable shots (number, talking or still, who, the words over it, the current picture prompt), the note and the art style in; JSON {"shots": [{id, prompt}]} out for only the shots the note changes. Validated in code (known shot numbers, non-empty prompts). Used by services.storyboard.revise_storyboard, which redraws just those shots.'
    - key: story.score
      output_format: json
      description: 'Scores a voiced cartoon: its scenes (id, length, setting, what happens, the turn), its action beats (number, scene, what happens) and the art style in; JSON {"music": [{scene_id, prompt}], "sounds": [{beat, prompt}]} out, a music brief per scene and a sound for the actions worth hearing. Validated in code (real scene ids and beat numbers, a prompt each). Used by services.score.plan_score; Stable Audio makes each piece.'
    - key: story.youtube_package
      output_format: json
      description: 'Packages a finished cartoon for YouTube (chat drafting phase 7): the title, logline, art style, cast (id and name), the story (scene summaries and turns, then the spoken lines) and a table of the drawn shots in; JSON {title, description, tags, thumbnail_text, thumbnail_shot} out. Validated in code (title fits whole, numbers only from the script, no links, short comma-free tags, the long-video thumbnail checks on thumbnail_text, a real shot number). Used by services.video_package.draft_package, which composes the thumbnail from a frame of that shot.'
    - key: story.youtube_package_revise
      output_format: json
      description: "Revises a cartoon's YouTube package to address one note from the operator: the same inputs as story.youtube_package plus the current package JSON and the note; the whole revised package out in the same shape, untouched fields kept. Validated in code like a new package. Used by services.video_package.draft_package."
    - key: story.director_notes
      output_format: json
      description: "Director's notes on a finished cartoon: the title, logline, art style, the story, the length and a table of every shot (start, length, drawn or not, who, the line, the picture, and the flaws the vision judge saw in it) in; JSON {verdict, strengths, notes: [{kind: redo|story|pacing|sound, shots, problem, fix}]} out, ranked by how much each note would improve the piece. Validated in code (real shot numbers; a redo names only drawn shots and says how to redraw them). Used by services.director_notes.write_notes; redo notes feed a render revision."
    - key: story.screenplay_fix
      output_format: json
      description: 'The one repair retry for story.casting, story.casting_revise, story.outline, story.scene, story.screenplay_revise, story.storyboard_revise, story.score, story.youtube_package, story.youtube_package_revise and story.director_notes: carries the rejected reply ({previous}) and the FIX list code produced ({errors}). Used by services.screenplay.write_screenplay.'
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

FIND THE STORY FIRST
Every format, an explainer included, is a story about a character. Decide the spine before the scenes:
- "want": which cast member wants what, in their own words and as something concrete.
- "obstacle": what stands in the way, often a belief the character holds that is wrong.
- "turn": the moment the character sees it differently, and what makes it happen.
- "payoff": how the last scene answers the first: the opening problem solved, a line called back, the character changed.
The topic is what the character learns or runs into; the audience learns it by watching them.
When the topic is a well-known tale, tell that tale: keep its best-known moments, lines and ending, retold in fresh words, and let its plot be the spine.

PLAN THE PLACES
- List the story's locations once under "locations": each has a short "id" (lowercase words joined by hyphens) and a "description" of the place itself that a background artist could paint: its architecture, objects, scale and look. Leave the time of day and mood to the scenes.
- A place the story returns to is the same location, so it is drawn the same way every time.

PLAN THE COLOUR
- Paint a colour script: each scene gets a "palette", the colours and light it is painted in (the main hues, the time of day, where the light comes from and how warm or cold it is), in one short phrase.
- A palette describes only colours and light. It is painted over every picture of the scene, the characters included, so the characters and what happens stay in the summary.
- Plan the palettes together, as one strip across the whole story. Colour carries the feeling: let it shift when the story turns, contrast the low point with the payoff, and come back to a palette when the story comes back to a feeling.
- Keep every palette inside the piece's art style; the style is the same throughout and only the colours and light change.

PLAN THE SCENES
- Give the piece about {scene_count} scene(s). Each scene is one step of the spine, and something is different at its end: a new understanding, a decision, a feeling.
- Keep the characters in one main place unless the story has a reason to move. When it moves, the summary says why (they go to try something, or a demonstration takes them there).
- "location": the id of the location the scene happens in.
- "setting": that place at this moment, with its time of day and mood, in one sentence a background artist could paint.
- "palette": the scene's entry in the colour script.
- "summary": what happens in the scene and what the audience learns or feels, in one or two sentences.
- "turn": what is different by the end of this scene, in one sentence.
- "characters": the cast ids visible in the scene.
- The first scene sets up the want and hooks the audience; the last one delivers the payoff.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"title": "a short title", "logline": "the whole piece in one sentence", "spine": {{"want": "...", "obstacle": "...", "turn": "...", "payoff": "..."}}, "locations": [{{"id": "workshop", "description": "a cluttered workshop with a long workbench, hanging tools and a tall rain-streaked window"}}], "scenes": [{{"scene_id": "s1", "location": "workshop", "setting": "the workshop at night, warm lamps, rain on the window", "palette": "deep blue night with warm amber lamplight from the bench", "summary": "what happens here", "turn": "what is different by the end", "characters": ["id"]}}]}}
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

THE STORY
{spine_block}

THE OUTLINE
{outline_block}

WRITE THIS SCENE: {scene_id}
Setting: {scene_setting}
What happens: {scene_summary}
By the end of the scene: {scene_turn}
Characters in the scene: {scene_characters}
Length: about {scene_word_budget} spoken words.
{scene_position}

The lines just before this scene:
{previous_lines}

HOW TO WRITE IT
- The scene is an ordered list of beats. A "line" beat is one speaker saying one to three short sentences that sound natural out loud. An "action" beat is something the audience sees while nobody speaks, in one visual sentence, with the cast ids it shows in its "characters".
- Build the scene toward its turn: every line moves the character closer to it, and the turn lands on screen, in a line or a reaction.
- Characters answer each other. Each line reacts to the one before it (agreeing, pushing back, joking, realizing) so the scene is a conversation with feeling, not turns at a microphone.
- Give every line a "delivery" note an actor could perform: an emotion and where the speaker looks. The emotion follows the story: frustration builds, curiosity sharpens, the turn brings surprise or relief.
- Give every line an "intensity": "low" for quiet, thoughtful or deadpan lines, "medium" for ordinary conversation, "high" for outbursts, big surprises and the turn itself. Vary it across the scene.
- Write each character in their own voice, from their personality.
- Use an action beat only where something happens that the audience must see to follow the story, such as a demonstration, a reaction that replaces a line, or an entrance. Most scenes need one to three.
- A character who arrives partway through the scene arrives in an action beat that lists them in "enters"; one who leaves goes in an action beat that lists them in "exits". Everyone else is in the scene from its first beat to its last. A character speaks only while they are in the scene.
- Keep facts accurate. Where a fact is uncertain, let a character ask or wonder rather than state it.
- The "text" of a line is read aloud by a speech synthesizer: write words exactly as a person would say them, spell out symbols and abbreviations, and keep stage directions in "delivery".
- Spend the full length on this scene's part of the story.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"beats": [{{"kind": "line", "speaker": "id or narrator", "text": "what they say", "delivery": "excited, to camera", "intensity": "high"}}, {{"kind": "action", "text": "what we see", "characters": ["id"], "enters": [], "exits": []}}]}}
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
- Draw only the characters a row lists as visible. Anyone else the row names, such as a narrator, is a voice heard over the picture.
- Vary the framing from shot to shot (wide, medium, close-up, over the shoulder) while the place stays the same.
- Keep pictures free of written words, signs and screens with text.
- "motion": for an animated shot, one sentence on how the camera moves and what moves in the picture. For a still, an empty string.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"shots": [{{"id": 3, "prompt": "medium shot of dex at a cluttered workbench, holding a glowing graphics card up to the light, warm lamp glow", "motion": "slow push-in as dex turns the card in his paws"}}]}}
```

## story.casting

```text
You are the casting director for a short animated piece by {site_name}. Decide who is in it and how it looks.

THE REQUEST
Topic: {topic}
Brief: {brief}
Format: {format_name}
Length: about {target_seconds} seconds

CHARACTERS WHO ALREADY EXIST - reuse one when the request names them or when they fit the part:
{roster_block}

MUST BE IN THE CAST: {required}

ART STYLE: {style_hint}

VOICES - choose each new character's voice from this list, by id:
{voice_catalog}

HOW TO CAST
- Cast the characters the story needs to be told, usually two or three and at most {max_new} new ones. A character the story only mentions, or one who never speaks, stays out of the cast and is drawn in the scenes from the screenplay.
- For a well-known tale, cast its own characters, including every one its best-known scenes need: a character who has a famous line speaks it. Give each one a short first name that suits the tale.
- "appearance": one sentence a character designer could draw the same way every time: age, build, hair, face, and the clothes they always wear. Make the cast easy to tell apart by colour and shape.
- "personality": how they talk and behave, in one sentence.
- "voice": an id from the list that fits their age and temperament. Give different characters different voices.
- "energy": "low" for calm or dry characters, "medium" for most, "high" for lively, excitable or loud ones.
- "sample_line": one or two sentences the character might say, in their voice, used to set up their voice.
- "style": the ART STYLE above written out as one short phrase a painter would follow. Keep everything it says and add the concrete details it implies: line, colour, texture, lighting. Design every character to look at home in it.
- "narrator": set "use" to true when the piece needs a storyteller's voice, false otherwise. A narrator is heard and never seen. Put an existing character's id in "reuse" when one fits the part; otherwise leave "reuse" empty and describe a new voice with "name", "voice", "energy" and "sample_line".

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"style": "...", "characters": [{{"reuse": "existing_id"}}, {{"id": "lowercase-id", "name": "...", "role": "...", "appearance": "...", "personality": "...", "voice": "voice_id", "energy": "medium", "sample_line": "..."}}], "narrator": {{"use": true, "reuse": "existing_id or empty", "name": "...", "voice": "voice_id", "energy": "low", "sample_line": "..."}}}}
```

## story.screenplay_revise

```text
You are the head writer at {site_name}, revising a short animated screenplay with the operator.

THE SCREENPLAY NOW
{current}

THE NOTE TO ADDRESS
{instruction}

CAST - the only characters who may appear or speak. Use the id before the colon exactly.
{cast_block}

Narrator available: {narrator_available}.
Length: about {word_budget} spoken words in total.

HOW TO REVISE
- Make the change the note asks for, and whatever that change needs elsewhere to stay consistent: a renamed thing, a moved beat, a line that calls back to it.
- Keep everything the note does not touch: the same scenes, lines and order, word for word.
- Keep the story spine (want, obstacle, turn, payoff). When the note changes one of them, update the spine to match.
- Keep the "locations" and each scene's "location". A scene the note moves somewhere new uses a listed location, or a new one added to "locations" with an id and a description of the place.
- Keep each action's "enters" and "exits", so a character still arrives and leaves where they did; a note that moves an entrance moves its action.
- Keep each scene's "palette" unless the note changes the scene's feeling; a scene whose feeling changes gets a palette that fits the colour script around it.
- Every "line" beat has "speaker", "text", "delivery" and "intensity" ("low", "medium" or "high"). Every "action" beat has "text" and "characters".

OUTPUT
Reply with the whole revised screenplay as one JSON object and nothing else, in the same shape as THE SCREENPLAY NOW:
{{"title": "...", "logline": "...", "spine": {{"want": "...", "obstacle": "...", "turn": "...", "payoff": "..."}}, "locations": [{{"id": "...", "description": "..."}}], "scenes": [{{"scene_id": "s1", "location": "...", "setting": "...", "palette": "...", "summary": "...", "turn": "...", "characters": ["id"], "beats": [{{"kind": "line", "speaker": "id", "text": "...", "delivery": "...", "intensity": "medium"}}, {{"kind": "action", "text": "...", "characters": ["id"], "enters": [], "exits": []}}]}}]}}
```

## story.casting_revise

```text
You are the casting director for a short animated piece by {site_name}, revising a cast design with the operator.

THE CAST NOW
{current}

THE NOTE TO ADDRESS
{instruction}

ART STYLE: {style_hint}

CHARACTERS WHO ALREADY EXIST AND MAY BE REUSED:
{roster_block}

VOICES - choose each new character's voice from this list, by id:
{voice_catalog}

HOW TO REVISE
- Make the change the note asks for: a character's looks, personality, voice, energy or sample line; a character added or dropped; the narrator.
- Keep every character the note does not touch exactly as it is, under the same "id", or as the same {{"reuse": id}} entry.
- "appearance": one sentence a character designer could draw the same way every time: age, build, hair, face, and the clothes they always wear.
- "voice": an id from the list that fits the character. Different characters get different voices.
- "energy": "low", "medium" or "high".
- At most {max_new} new characters.

OUTPUT
Reply with the whole revised cast as one JSON object and nothing else, in the same shape as THE CAST NOW:
{{"style": "...", "characters": [{{"reuse": "existing_id"}}, {{"id": "lowercase-id", "name": "...", "role": "...", "appearance": "...", "personality": "...", "voice": "voice_id", "energy": "medium", "sample_line": "..."}}], "narrator": {{"use": true, "reuse": "existing_id or empty", "name": "...", "voice": "voice_id", "energy": "low", "sample_line": "..."}}}}
```

## story.storyboard_revise

```text
You are the storyboard artist for a short animated piece. The operator has looked at the storyboard and left a note.

THE NOTE
{instruction}

THE SHOTS - number | talking (a character speaking to camera) or still | who is in it | the words heard over it | the picture prompt now
{shot_table}

ART STYLE: {style}

HOW TO REVISE
- Change only the shots the note is about, and a shot next to them when the note needs it to stay consistent (the same place, light or prop).
- Write each changed shot's whole new picture prompt: framing, where the characters are and what they do, the key objects, and the light and mood.
- Keep the art style words at the start of a prompt that has them.
- A talking shot shows the speaker alone in the frame with the hands out of it: change the place, framing or expression, never add people.
- Keep pictures free of written words, signs and screens with text.

OUTPUT
Reply with one JSON object and nothing else, listing only the changed shots:
{{"shots": [{{"id": 3, "prompt": "..."}}]}}
```

## story.score

```text
You are the composer and sound designer for a short animated piece, "{title}": {logline}

ART STYLE: {style}

THE SCENES - id | length | setting | what happens | the turn
{scene_rows}

THE ACTIONS - number | scene | what happens
{action_rows}

MUSIC
For every scene, write the music under it as one prompt a music generator follows: the instruments, the tempo, and the feeling. Follow the scene's mood and its turn, and let the pieces belong together as one score: the same instruments carry through, and the music grows where the story does. The music sits under spoken dialogue, so keep it light enough to talk over: no vocals, no lead melody fighting the voices.

SOUNDS
Pick at most {max_sounds} of the actions whose sound the audience should hear (a door slamming, rain starting, a robot whirring) and describe each sound in a few words: what makes it and how it sounds. Leave out actions that make no clear sound.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"music": [{{"scene_id": "s1", "prompt": "..."}}], "sounds": [{{"beat": 4, "prompt": "..."}}]}}
```

## story.youtube_package

```text
You are publishing a finished short animated piece on YouTube, "{title}": {logline}

ART STYLE: {style}

THE CAST - id: name
{cast_block}

THE STORY
{story}

THE DRAWN SHOTS - number | who is in it | the picture
{shot_table}

WRITE
- "title": what makes someone browsing click, and what someone searching would type. Name the thing people know: the tale it retells, a character, or the subject. Make it fit in {title_max} characters as written.
- "description": two or three short paragraphs, under {description_max} characters: the first line hooks (it shows before "more"), then what happens without giving away the ending, then who it is for. Plain text.
- "tags": up to {max_tags} search phrases of one to four words each: the tale, the characters, the genre, the art style, the audience.
- "thumbnail_text": the words on the thumbnail, at most {hook_max} characters: a few words that add a question or a stake to the title rather than repeating it.
- "thumbnail_shot": the number of the shot whose picture makes the best thumbnail: a character's face showing a clear feeling, at the moment the story turns.
- Every number you write comes from the story above.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"title": "...", "description": "...", "tags": ["..."], "thumbnail_text": "...", "thumbnail_shot": 0}}
```

## story.youtube_package_revise

```text
You are publishing a finished short animated piece on YouTube, "{title}": {logline}. The operator has read its package and left a note.

THE NOTE
{instruction}

THE PACKAGE NOW
{current}

ART STYLE: {style}

THE CAST - id: name
{cast_block}

THE STORY
{story}

THE DRAWN SHOTS - number | who is in it | the picture
{shot_table}

HOW TO REVISE
- Change what the note is about and keep every other field as it is.
- When the note gives exact words, use those words.
- The title fits in {title_max} characters as written; the description stays under {description_max} characters, plain text; up to {max_tags} tags of one to four words; thumbnail_text at most {hook_max} characters; thumbnail_shot is a number from the shot table.
- Every number you write comes from the story above.

OUTPUT
Reply with the whole package as one JSON object and nothing else, shaped like this:
{{"title": "...", "description": "...", "tags": ["..."], "thumbnail_text": "...", "thumbnail_shot": 0}}
```

## story.director_notes

```text
You are the director of a short animated piece, "{title}": {logline}. You have just screened {cut} ({total_seconds} seconds) and are giving notes to the team.

ART STYLE: {style}

THE STORY
{story}

THE CUT - shot | starts | seconds | drawn or not | who | heard over it | the picture | what the screening judge saw
{shot_table}

GIVE NOTES
- "verdict": two or three sentences on how the piece plays as a whole: does the story land, does it feel alive, what holds it back most.
- "strengths": the few things that work and should stay.
- "notes": at most {max_notes}, the most important first. Each has:
  - "kind": "redo" for shots to redraw (a visible flaw, a picture that misses its moment, a frame that is dull where the story peaks); "story" for the script; "pacing" for shots or scenes that drag or rush; "sound" for voices, music or effects.
  - "shots": the shot numbers it is about. A redo names drawn shots only.
  - "problem": what the audience notices, in one sentence.
  - "fix": for a redo, the new picture in one or two sentences a shot artist can draw: framing, who is where doing what, the expression, the light. For the other kinds, the change to make.
- Base picture notes on what the judge saw. A shot the judge could not read gets no picture note.
- "Not known" in the heard-over-it column means the words were not kept for this review, not that the shot is silent; judge sound only from what the table does show.
- Give a note only where fixing it makes the piece clearly better; an empty list is a fine answer for a clean cut.

OUTPUT
Reply with one JSON object and nothing else, shaped like this:
{{"verdict": "...", "strengths": ["..."], "notes": [{{"kind": "redo", "shots": [0], "problem": "...", "fix": "..."}}]}}
```

## story.screenplay_fix

```text
YOUR PREVIOUS REPLY
{previous}

It was rejected. Fix every problem below, keep everything that was fine, and reply with the complete corrected JSON object only:
{errors}
```

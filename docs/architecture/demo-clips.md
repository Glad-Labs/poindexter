# Demo clips — real CLI footage as a video shot source

Records the `poindexter` CLI with [VHS](https://github.com/charmbracelet/vhs)
and bakes the result to MP4, so the video pipeline can cut to footage of the
system actually running instead of synthetic stills or generic stock video.

Tracking issue: [Glad-Labs/poindexter#937](https://github.com/Glad-Labs/poindexter/issues/937).

## Why it exists

The video lane's shot sources — `image_gen`, `image_kenburns`, `pexels`,
`generative` — are all either diffusion output or stock footage. For a blog
documenting the building of an autonomous content pipeline, none of it is
footage _of the thing being described_. A demo clip is the one visual nobody
else can produce.

It also costs no VRAM. Recording is CPU-only, so every demo shot is a
`generative` (image-to-video) shot not rendered — which relieves the
render-lane VRAM pressure tracked in
[poindexter#907](https://github.com/Glad-Labs/poindexter/issues/907) rather
than adding to it.

## Shape

```
demo_tapes/_preamble.tape     shared Set directives (theme, font, geometry)
demo_tapes/<slug>.tape        metadata header + Type/Enter/Wait content
services/demo_clips.py        parse → validate → compose → bake
poindexter media demos …      list / bake
```

A bake composes `Output "<path>"` + rendered preamble + tape body, runs `vhs`,
then ffprobes the result. Both the file and a non-zero duration must exist —
VHS exits 0 on some parse-error paths, so the artefact is the evidence, not
the exit code.

## Three decisions worth not re-litigating

**Tapes are code, not config.** Every other declarative surface here
(`external_taps`, `publishing_adapters`, `qa_gates`) is a DB table an operator
edits at runtime. Tapes deliberately are not: a tape is a shell script, so a
DB-editable tape turns any `app_settings` write into arbitrary code execution
inside the worker. Tapes change through code review. The DB's role is the bake
ledger, not the script.

**Bake ahead of time, not at render time.** Recording during a video render
would make every render depend on a live database, a warm API, and a working
terminal emulator — three new ways to fail _late_, after the expensive LLM
work is already paid for. Baking on a schedule means a broken demo fails in
the bake, alertable and out of band, while the renderer only consumes finished
MP4s.

**The director selects; it never authors.** An LLM asked to write CLI
invocations produces plausible flags that do not exist, and the clip would
faithfully record the error message. The director picks a `slug`.
`assert_read_only` is the second line of defence — an allowlist of read-only
leaf verbs, checked at load time, so a mutating tape fails in CI rather than
first on camera. Demos run against **live production data**, so a tape typing
`poindexter tasks approve` would not merely record badly; it would publish
something.

## Writing a tape

```
# title: Recent posts with publish status
# category: content
# description: Scrolling list of real published posts with status and slug.
#   Use for beats about the content library or publishing cadence.

Type "poindexter posts list --limit 5"
Sleep 400ms
Enter
Wait+Screen@45s /total/
Sleep 2500ms
```

`description` is what the video director reads when choosing a shot, so say
what the clip **shows a viewer**, not what the command does.

### Compose for video, not for docs

A 27-line `--help` dump is unreadable as a five-second shot. Large font, one
idea per tape, compact output.

### The `Wait` regex is the health check

`Wait+Screen@45s /token/` is what makes a renamed flag, a broken API, or an
empty table fail the bake instead of shipping a clip of a blank terminal.
Choose the token carefully:

- **Match a left-of-centre token.** Terminal width is finite, and a wide table
  wraps _mid-word_. `publishers list` renders its rightmost header as `FAI` /
  `L` across a line break, so `/FAIL/` can never match even though the word is
  visibly on screen. Match `/PLATFORM/`, not `/FAIL/`.
- **Prefer a header over install-specific data.** `/youtube/` matches on this
  install and nowhere else.

### Wide tables need a smaller face

Font size drives terminal columns (`COLUMNS_AT_FONT_SIZE` in
`services/demo_clips.py`): ~88 columns at size 34, ~115 at 26, ~136 at 22. A
table wider than the terminal wraps and looks broken. Set `# font_size` on
wide-table tapes — most need 26; `taps list` needs 22, because its name column
is variable-width and the longest row runs past 115.

Going smaller trades legibility for width, so a table that still wraps at 22
is a sign the command is a poor fit for video rather than a font problem.

### Inventory shots vs process shots

The catalog has two kinds of tape, and they are not interchangeable.

**Inventory** tapes run one command and show a table: `posts list`, `taps
list`, `qa-gates`. Honest and useful, but structurally the same shot every
time — a table appears. Good B-roll, weak highlight reel.

**Process** tapes run several commands in sequence and show something
happening over time: `logs-tail`, `ops-sweep`, `pipeline-review-story`,
`content-inventory-story`. They have motion and a beat, which is what a
longer shot wants. Chain commands with a `clear` between blocks when the
screen would otherwise overflow.

`poindexter logs --follow` is the only genuine _tail_ — it polls the Loki
proxy, so lines arrive during the recording rather than all at once. It is
read-only by construction (Loki is queried, never written), which is what
makes it safe to allowlist.

Level colouring depends on Loki carrying a `level` label, and not every
stream does. The structured `worker` / `brain-daemon` / `prefect-worker` logs
carry `INFO` / `WARNING`; uvicorn's access-log stream carries none, so a share
of lines in any worker tail render with an empty level column. Write tape
descriptions against what the clip actually shows — claiming "colour-coded by
level" oversells a tail that is mostly access logs.

Process tapes want a smaller font (24–26) because they accumulate output
across several commands, and longer `Sleep` beats so a viewer can read each
result before the next command types.

**Length changes how the renderer must use them.** An inventory tape bakes to
~5–7s; a four-command process tape bakes to ~25s. Trimming a 25s narrative
down to a 6s shot keeps only the first command and throws away the story that
justified the tape. So the clip's real duration has to reach the director —
that is what the bake ledger's recorded duration is for. A process tape is a
candidate for a _long_ shot, not a drop-in replacement for a short one.

### Not every command makes a good clip

`alerts list`, `schedule list`, `skills list`, and `media pending` all render
empty on a typical install. An empty table is not footage; those have no tape.

## The `cli_demo` shot source

A shot picks a clip by catalog slug:

```json
{
  "idx": 3,
  "source": "cli_demo",
  "demo_id": "ops-sweep",
  "duration_s": 20.0,
  "intent": "show the operator health sweep",
  "narration_offset_s": 42.0
}
```

`demo_id` and **no** `prompt`, **no** `query` — the clip already exists, so a
prompt would mean the model tried to generate rather than choose. The schema
rejects it.

**`demo_id` is constrained to a bare slug** at both the schema boundary and
the render site. It is LLM-authored and becomes a filename, so an
unconstrained string is a path-traversal seam. Two checks rather than one
because a shot list frozen into `pipeline_versions` can reach the renderer by
routes that skip re-validation.

**Duration is clamped to the clip, never looped.** The compositor
`-stream_loop`s any non-still shorter than its scene duration, and a looped
terminal recording re-types the command mid-shot — visibly broken in a way a
looped abstract clip is not. If the director asks for 30s of a 20s clip, the
shot renders 20s and the timeline shortens. The director avoids the situation
in the first place because the prompt lists each clip's baked length.

**A missing clip is not fatal.** It falls through the same `_backfill_pass`
ladder as any other failed source (→ branded card) and emits a `warn`
`demo_clip_missing` finding naming the slug. Note it does _not_ get a Pexels
substitute: swapping stock footage in for a missing recording of your own
product would be misleading, so the honest card is the right rung.

**The pacing guard applies.** `cli_demo` is subject to the existing
max-2-consecutive-shots-per-source rule, so demo footage can never take over
a video.

`cli_demo` is **not** in `_REGENERABLE_SOURCES` — re-rolling a deterministic
recording replays identical frames and only burns a QA pass.

### Not offered to the short (9:16) director

Clips bake 16:9 landscape. Letterboxing terminal text into a vertical frame
makes it unreadable on a phone — the same trap that produced letterboxed
landscape hero shots in shorts (#2774). The short director prompt says so
explicitly rather than omitting the source, because an unexplained absence
invites the model to try it anyway.

### How the director learns what is available

`_build_demo_catalog_block` renders only clips **actually on disk**, with each
one's baked duration from `manifest.json`. Offering an unbaked demo would
guarantee a ladder fill and a finding, so availability is filtered before the
model sees it.

When nothing is baked the block says `NONE AVAILABLE ... do NOT emit
source="cli_demo"` explicitly. An omitted section invites a plausible invented
`demo_id`; a stated absence does not.

## Running it

```bash
poindexter media demos list
```

```bash
poindexter media demos bake --slug posts-list --out /tmp/clips
```

`vhs` and `ttyd` are baked into the worker image. Outside the compose stack,
VHS drives headless Chromium and needs `--security-opt seccomp=unconfined` —
without it the zygote aborts with a stack trace that never mentions seccomp.

The CLI is an API client ([#198](https://github.com/Glad-Labs/poindexter/issues/198)
/ [#249](https://github.com/Glad-Labs/poindexter/issues/249)), so the recorder
needs `POINDEXTER_API_URL` plus OAuth credentials — ideally a dedicated
least-privilege client rather than database access.

## Theming

The palette lives in `app_settings` under `demo_clip_*`, mirroring the CLI's
colourblind-safe status roles (`poindexter/cli/_status_style.py`) so a
recording reads the same way the live terminal does. ANSI green is mapped onto
the amber "active" colour deliberately: nothing in the CLI's semantic maps
emits green any more, and third-party green that slips through should not land
on the red-green confusion pair on camera.

Restyle by changing settings, not tapes.

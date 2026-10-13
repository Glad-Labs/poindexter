# TTS text normalization — the render boundary

Stored narration scripts are **written English**. The spoken form is produced
at the TTS render boundary by `services/podcast_service.py::_normalize_for_speech`,
which is the ONE seam covering every audio path: podcast episodes, long/short
video narration, spoken titles, and the caption-fidelity reference. A fix here
reaches the frozen backlog on re-render — that is the whole point of the
2026-08-01 generation/speech split.

Order inside the pass (order is load-bearing):

1. `_normalize_model_names` — collapse `gemma-4-31B-it-qat:latest` → "gemma 4 31B"
2. `_normalize_dashes` — give every dash its spoken meaning (below)
3. `tts_pronunciations` replacements
4. `_normalize_for_script` — the structural pass (URLs, filenames, versions,
   parentheticals, emoji, em/en dash → comma, whitespace)
5. `tts_acronym_replacements`
6. `_space_compound_hyphens` — word-word hyphen → space

**Step 6 must stay last.** Every pass above it matches on the WRITTEN form, so
spacing compounds earlier pulls those matches apart: `https?://\S+` stops at
the injected space and strands half a URL, and any hyphenated key an operator
adds to `tts_pronunciations` / `tts_acronym_replacements` silently stops
matching. That is why the compound rule is its own function rather than another
line in `_normalize_dashes` (which must run _before_ the replacement passes, so
a spaced digit range is not turned into a comma first).

While fixing the ordering, a pre-existing bug surfaced: the filename-strip
character class `[\w/\\]` omitted `-`, so `my-notes.md` only ever matched the
`notes.md` tail and left a dangling `my-` for the engine to read aloud
(likewise `src/my-module/thing.py` → `src/my-`). The class now includes `-`;
the stem must still contain a letter, so it still cannot eat the `1.65` in
`$1.65 trillion`.

## The dash rules

Chatterbox has no text-normalization front end, so a dash whose meaning lives
in its context arrives raw and is voiced wrong. `_normalize_dashes` handles it:

| written                            | spoken              | why                                                          |
| ---------------------------------- | ------------------- | ------------------------------------------------------------ |
| `2026-05-04`                       | "May 4, 2026"       | ISO dates came out garbled                                   |
| `9-5`, `8-16`                      | "9 to 5", "8 to 16" | ranges MERGED into one wrong number ("ninety-five", "816")   |
| `-5`, `-$3M`                       | "negative 5"        | the minus was silently DROPPED — meaning inverted            |
| `state-of-the-art`                 | "state of the art"  | the engine breathes at a compound hyphen                     |
| `COVID-19`, `top-10`, `5090-class` | unchanged           | engines speak letter-digit hyphens acceptably                |
| `in 2024 — 12 people`              | stays a pause       | a SPACED em/en dash between numbers is an aside, not a range |

Switches: `tts_dash_normalization_enabled` is the master (default true);
`tts_number_range_word` ("to"), `tts_negative_number_word` ("negative") and
`tts_compound_hyphen_to_space_enabled` (true) tune the individual rules.

A compound becomes a **space, never a deletion** — joining it would turn
"re-sign" into "resign".

Known accepted misread: a model pin whose family is not in
`tts_model_name_families` (e.g. `wan2.1-14B`) reaches the range rule and is
read as a range. The fix is adding the family to that CSV, not touching the
dash pass.

## The number, unit and quote rules (2026-09-16)

Measured on the first presenter video, running whisper over the **rendered** narration
(the instrument for "which words came out"):

| written                       | heard before                    | spoken now                              |
| ----------------------------- | ------------------------------- | --------------------------------------- |
| `decodes at 236.7 tok/s`      | "decodes at 2036.7 talks"       | "236.7 tokens per second"               |
| `105.5 tok/s, 55.4% gone`     | "105 talks by 5, 4% gone"       | "105.5 tokens per second, 55.4 percent" |
| `a 2,068 ms overhead`         | "a 2068 misses overhead"        | "a 2068 milliseconds overhead"          |
| `from 2,218 production calls` | "from 2000 to 2008 production…" | "from 2218 production calls"            |
| `calls "evalduration."`       | quote voiced as a break         | "calls evalduration."                   |
| `Ms. Smith`, `it's`, `$1.65`  | unchanged                       | unchanged                               |

Every raw form reads correctly in a short isolated sentence; the damage only appears in the
real utterance where they cluster (a slash unit next to a percent, a thousands comma two
words from a decimal). Rather than hope the engine's number parser gets the context right,
the boundary hands it unambiguous forms. Decimals stay as digits: spelling "236 point 7"
read identically on the prod voice, so it buys nothing and would cost the money/version
edge cases.

Rules, all speech-boundary only (stored scripts keep the written forms):

- **Thousands commas** between digit groups are dropped (`2,218` → `2218`). List commas
  (`1, 2, 3`) have a space and are untouched.
- **Units after a digit** expand via `tts_unit_expansions` (JSON, written → spoken; default
  `tok/s`, `t/s`, `ms`). A bare abbreviation fires only after a number — `Ms. Smith` and
  "the ms server" are not units — while a slash unit fires anywhere as a whole token
  (`km/h` is not in the map and is left alone).
- **Percent after a digit** is spoken ("55.4 percent").
- **Quotes**: straight double quotes are dropped; single quotes that _wrap_ a word are
  unwrapped; apostrophes inside words stay. Timing measured at most 0.08 s of extra gap in
  isolation — the quote characters carry nothing the engine can voice, so they go.

Switches: `tts_number_normalization_enabled`, `tts_strip_quotes` (both default on).

Verification (prod voice `bf_emma`, Kokoro via speaches, then `Systran/faster-whisper-medium`):
the rewritten qwen sentence came back as _"236.7 tokens per second, but delivers just 105.5
tokens per second. 55.4% gone with a 2068 milliseconds overhead"_, the calls sentence as
_"2218 production calls"_, and the quoted phrase as _"calls eval duration."_ Gaps ≥ 0.3 s
fell only at commas and sentence ends.

Model names: the collapse now puts a comma between a version and the size that follows it
(`qwen2.5:7b` → "qwen 2.5, 7b"). Spoken as "qwen 2.5 7b" the engine fused the numbers
("QN2.57b", "PHY 414b" in the rendered narration); with the comma the same voice separated them
("QN2.5-7B"). Spacing the size ("7 B") did not help.

## Measuring a pronunciation fix — pick the right instrument

**This is the part that is easy to get wrong.** There are two questions, and
each has exactly one instrument that can answer it:

| question             | instrument                                                                               | why the other one lies                                                   |
| -------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Are the WORDS right? | TTS→STT round-trip (chatterbox `/v1/audio/speech` → speaches `/v1/audio/transcriptions`) | audio timing tells you nothing about which word was said                 |
| Is the TIMING right? | render the audio and run `ffmpeg -af silencedetect` over it                              | **a transcript cannot see a pause** — whisper writes one back as nothing |

The 2026-08-24 round-trip that found the digit-dash failures also read
word-word compounds back verbatim, and they were recorded as fine. They were
not. The probe was structurally incapable of detecting the failure, because
the failure was a pause and a pause transcribes as nothing. An audio pass on
2026-09-01 found the engine does breathe at a compound hyphen:

- On real stored-script sentences, de-hyphenating cut **0.10–0.86s** of
  internal silence and **0.33–1.25s** of duration, and roughly **halved** the
  number of internal pause runs.
- The effect is real but modest — a comma adds ~0.5s of silence per instance,
  a hyphen ~0.05s. It reads as choppiness, not as a comma-length pause.
- It is pervasive rather than dramatic: **9,406 word-word hyphens across 953
  stored scripts**, ~10 per script.
- Bonus observation (n small, whisper imperfect): `coherence-shift` was heard
  as "coheren**t** shift" hyphenated and correctly as "coherence shift"
  de-hyphenated — the hyphen was degrading the word itself, not just the pacing.

**The compound rule is engine-specific.** It exists because Chatterbox has no
text-normalization front end. Kokoro (the `speaches` fallback) has a real G2P
and already handles hyphens: measured the same way it is deterministic
(identical durations across runs) and shows no benefit — flat-to-0.09s more
detected silence, flat-or-shorter duration. So the rule is a win on the prod
engine and roughly neutral on the fallback; an install running Kokoro can set
`tts_compound_hyphen_to_space_enabled=false` without losing anything.

Note the round-trip's other caveat: whisper's inverse normalization can write a
correctly-spoken "ten to twenty" back as "10-20", so an identical round-trip is
inconclusive — only _changed_ text is proof.

Caption fidelity is unaffected by the compound rule in either direction: the
`_fidelity_ratio` tokenizer strips all punctuation before diffing, so
"state-of-the-art" and "state of the art" compare equal.

## Caption fidelity is measured in WORDS, and that is load-bearing

`difflib.SequenceMatcher` compares **characters** when you hand it strings,
and past 200 elements its `autojunk` heuristic silently discards every element
occurring in more than 1% of the sequence. Across a 3,800-character narration
that is every common letter, so the ratio collapses.

Measured on the 2026-09-22 "Skip NCCL" long form:

| comparison                               | ratio     |
| ---------------------------------------- | --------- |
| **as shipped** — characters, autojunk on | **0.400** |
| characters, `autojunk=False`             | 0.980     |
| **words, `autojunk=False`** (now)        | **0.941** |

548 of its 591 words matched. The check was not detecting a TTS fault; it was
reporting one that did not exist, and its finding text asserted "likely a TTS
dropout or truncation", which sent the next reader looking for a problem in
the wrong component. The `asr_len` / `script_len` in that finding (3794 vs 3836) were the tell: a truncation does not preserve length.

The old tests could not catch this — their fixtures were a few dozen
characters, under the autojunk floor. **A guard for a length-dependent bug has
to use a realistic length**, and a repetitive fixture will not do either: a
script that repeats one sentence gives the character matcher huge identical
blocks to lock onto and scores 0.65 where varied prose scores 0.04.

### What the threshold means now

For a transcript holding fraction _k_ of the script, the word-level ratio is
`2k / (k + 1)`, so the default `0.80` sits at **k = 2/3** — it fires when
roughly a third or more of the narration is missing from the audio. That is a
dropout alarm, not a transcription-accuracy alarm.

A perfect round trip does **not** score 1.0 and should not be expected to. The
reference is the text TTS received, so acronyms and numbers are spelled out —
`v l l m` against ASR's `vllm`, `one point one four` against `1 14`. On the
NCCL narration that accounted for most of the 43 unmatched words.

## A chunk that stops early is caught in the sidecar (2026-10-07)

The fidelity alarm above fires when about a third of the narration is
missing, so a lost sentence or two is invisible to it by design. That is the
failure Chatterbox actually has. The sidecar splits text into chunks of
about 240 characters (`text_chunking.py`) and generates each one separately,
and now and then a generation emits its stop token after the chunk's first
sentence. The audio sounds natural and nothing errors. The 2026-10-06
"Unsealed OpenAI Briefs" long video lost "The contents? A highlight reel no
one at OpenAI wanted public. Two elements stand out starkly from this
filing." that way: about 4% of the script, a 0.95 word ratio, no finding. The
same chunk rendered in full the next day, so the stop is intermittent.

`scripts/tts_sidecars/truncation_guard.py` checks each chunk's speaking rate
(characters per second of edge-trimmed audio) against the median of its own
request, so no per-voice rate needs tuning. Across 41 production chunks every
rate fell within 0.73-1.23x of that median. A chunk faster than
`plugin.tts_provider.chatterbox.truncation_max_rate_ratio` (default 1.5x)
had too little audio for its text. It is generated again, one sentence at a
time, up to `plugin.tts_provider.chatterbox.truncation_retries` (default 2)
times, and the longest take is kept. A repair logs a WARNING with the before
and after durations. A chunk still short after the retries logs a WARNING
that quotes the text the audio is missing. Chunks under 40 characters are not
judged, and a request with fewer than three judgeable chunks is measured
against the production median (16.4 chars/s). Either setting at `0` turns the
guard off. The sidecar returns only audio, so the warnings live in the
chatterbox container's log, not in a finding.

## Dead air inside a chunk is clamped in the sidecar (2026-10-09)

The chunk seams are fixed at 0.40s (+2×30ms margins) by `join_segments`. The
pauses Chatterbox makes **inside** a chunk were not bounded, and they are where
the remaining "weird gaps" came from. Rendering the same five paragraphs three
times with production settings (`exaggeration` 0.65, `cfg_weight` 0.30, the
cloned voice) gave:

| spot                             | take 1 | take 2 | take 3 |
| -------------------------------- | ------ | ------ | ------ |
| "per gigabyte." → next sentence  | 0.86s  | 3.33s  | 0.85s  |
| "a rare number." → next sentence | 2.20s  | 1.26s  | 1.87s  |
| "MSI Claw" → "eight AI Plus"     | 0.29s  | 2.76s  | 0.53s  |

So the long ones are a per-generation lottery, mostly at sentence ends and
sometimes in the middle of a product name. No text rewrite reaches them, and
the 2026-10-08 episode had 7 pauses over 0.9s (max 3.08s). Silence was
25-30% of the runtime.

`audio_join.clamp_internal_silence` shortens every pause inside a generated
chunk to at most `plugin.tts_provider.chatterbox.max_internal_silence_seconds`
(default **0.55**, `0` disables). A pause over the cap keeps its first and last
half-cap, so the decay of the word before and the onset of the word after are
untouched; only the dead middle goes, with a 10ms crossfade at the splice.
"Silence" is a 10ms frame more than 40 dB under the chunk's own peak, quieter
than a breath, so a breath inside a long pause splits it instead of being cut.
0.55 sits just above the median natural sentence pause (0.46s) and the seam,
so ordinary pauses keep their variation.

Applied offline to the three takes above: every pause over 0.6s went (8, 10
and 13 of them before), duration fell 7-11% (102.9/110.9/102.4s →
95.1/99.6/96.0s), and the take-to-take spread roughly halved. A whisper
round-trip of each take before and after differed only by transcription noise,
so no word was clipped.

The clamp runs inside the sidecar's `generate()`, **before** the truncation
guard measures a chunk. That is deliberate: a 3s dead-air pause pads a chunk's
duration, which can hide an early stop from a rate check. One side effect is
that chunk speaking rates rise ~8-10%, so `FALLBACK_CHARS_PER_SECOND` (16.4,
used only when a request has fewer than three judgeable chunks) now sits a bit
below the true rate, and the guard is that much less eager on short requests.
Normal chunks measured 0.73-1.23x of the median, still well under 1.5x.

**The steadier hesitations are the next section.** A shorter 0.3-0.5s pause
recurs at the same place on every take, next to names and numbers. Those sit
under the cap, and the audio alone cannot tell them from a comma pause, so
they are fixed in the text.

**`cfg_weight` is not the fix.** Three takes at Chatterbox's default 0.5 cut
silence to 20-24s from 25-31s and the median sentence pause to ~0.5s, but
still had 6-9 pauses over 0.7s (max 1.6-2.1s). It also changes the delivery
pace, so it is a voice decision, not a gap fix.

## Hesitations at names and numbers (2026-10-09)

To find where the engine hesitates every time, each pause has to be located
against the **script's** words, not whisper's. Whisper drops the word next to a
pause and stretches word edges into the silence, so "Steam ‖ ROG" was really
"Steam Deck, ‖ ROG" (a comma). The measurement splits whisper's words around a
pause by their midpoints, aligns them to the script with `difflib` (digits
expanded to words so "$259.99" lines up with "two hundred fifty nine dollars"),
and keeps only pauses whose neighbours are adjacent on both sides. Five episode
openings were measured, with three takes of one and two of each of the others.

Rate of a pause of 0.25s or more at an unpunctuated word boundary:

| boundary                                          | rate  | vs plain words |
| ------------------------------------------------- | ----- | -------------- |
| plain word → plain word                           | 3.5%  | 1.0×           |
| after a spelled-out number word                   | 2.6%  | 0.7×           |
| after a mixed-case token (NVMe, OpenAI, DeepSeek) | 19.4% | 5.5×           |
| after a digit token (SN770M, oss-120b)            | 11.1% | 3.2×           |
| after an ALL-CAPS token (ROG, ASUS, PC)           | 10.0% | 2.9×           |
| **before a mid-sentence capitalized name**        | 15.0% | 4.3×           |

At 0.4s or more, the before-a-name rate was **11×** that of plain words. Spelled
numbers are fine. The number trouble came from digits the spelling pass missed
and from names sitting next to numbers ("eight ‖ Max", "eight ‖ AI Plus").

### Why: the vocabulary has no capital pieces

Chatterbox's text tokenizer is a 704-token BPE, and every multi-character piece
in it is lowercase. A capital exists only as a single-character token, so
"deepseek" is `de e p se e k` while "DeepSeek" is `D e e p S e e k`, and
TEAMGROUP is nine separate capitals. In training, a lone capital after a space
mostly opened a sentence, and the model pauses as if one had.

### Fix 1: fold mid-sentence names (sidecar, `text_casing.py`)

The chatterbox sidecar lowercases a Title-case word that does not start a
sentence ("score Alibaba" → "score alibaba"). It splits a word made only of
Title-case pieces into its words ("DeepSeek" → "deep seek", "SanDisk" → "san
disk"), and keeps "Mc"/"Mac" names whole. It leaves alone ALL-CAPS tokens and
anything with an uppercase run (NVMe, OpenAI, GPU), since single capitals are
how the model spells letters out. It also leaves digit tokens, sentence starts,
"I", and `plugin.tts_provider.chatterbox.keep_case_words`, whose capital changes
the word (default `August,Polish,Job,Nice,Reading,Mobile`; a single `,` means no
exceptions). `plugin.tts_provider.chatterbox.fold_name_case` turns it off.

It lives in the sidecar rather than in `_normalize_for_speech` because it is a
workaround for this engine's tokenizer. Kokoro has a real G2P and was not
measured. Stored scripts and the caption-fidelity reference keep their casing.
The fidelity tokenizer is case-insensitive anyway.

Same five openings, two more renders each with the fold applied (the module
reproduces the measured text exactly):

| pause at a boundary  | ≥0.25s before → after | ≥0.4s before → after |
| -------------------- | --------------------- | -------------------- |
| right before a name  | 19.6% → **6.4%**      | 15.7% → **5.3%**     |
| right after a name   | 9.0% → 12.5%          | 6.4% → 4.2%          |
| every other boundary | 3.1% → 3.5%           | 0.6% → 0.7%          |

The unchanged "other" row is the control: the improvement is at the names, not
a calmer batch of takes. The after-name row was never the problem and is a
small sample. A whisper round-trip still heard every folded name. Its few
misses were its own artifacts: "8AI+" written for "eight AI Plus", and
"DeepSeq" written for a sentence-initial "DeepSeek" the fold had not touched.

### Fix 2: a number ending a sentence is spelled

`_NUMBER_RE` refused a number followed by any `.`, meant for versions and
addresses ("1.2.3", "10.0.0.1"), so a sentence's own full stop blocked it too.
"PCIe 4.0." and "then 92." reached the engine as digits, in 132 of 564 stored
scripts (172 numbers). A trailing dot now blocks the match only when a digit
follows it.

### Still open

ALL-CAPS brand words (TEAMGROUP) and digit-bearing product codes (SN770M,
MP44S) still hesitate about 3× as often as plain words. Folding them is not
safe without knowing which are spelled out (MSI) and which are words (ASUS).
That is an operator call per name, through `tts_pronunciations`.

### A lowercase acronym is read as a word (2026-10-13)

The same lowercase vocabulary runs the other way. Scripts copy a model's
literal name, and "gpt oss" reached the engine as written. Whisper heard
"GPT-OS" on 3 of 3 renders, because "oss" has word-like pieces to read.
Spelled "O S S", it heard "GPT OSS" on 3 of 3. The fix is the existing
`tts_pronunciations` map (`"OSS": "O S S"`). It matches whole words in any
casing, so "oss", "OSS" and "gpt-oss" all get it, while "boss" and "moss"
don't. Any acronym a script may write in lowercase needs an entry there. The
name fold cannot help: it never uppercases.

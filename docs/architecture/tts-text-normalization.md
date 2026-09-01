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

---
name: tts
description: >
  TTS pronunciation configuration. Controls how written text is normalized
  to natural spoken English before being passed to the Speaches/Kokoro TTS
  engine. Two DB-configurable app_settings keys let operators add or override
  pronunciation rules without code changes.
license: Apache-2.0
metadata:
  category: tts
---

# TTS pronunciation skill

Controls how the pipeline converts written text to natural spoken audio.
Two `app_settings` keys are the operator interface — both accept JSON and
are merged on top of the built-in defaults at render time.

## Keys

### `tts_pronunciations`

JSON object mapping a written form to its spoken replacement. Applied as
plain string substitutions (case-insensitive). Pure-letter tokens (no
punctuation) are matched with word boundaries so short abbreviations like
`"GB"` do not fire inside longer words like `"RGB"`.

DB entries **add to** (or override) the built-in defaults. To change an
existing default, set the same key with a new value. To add a new entry,
include only the new key — existing defaults are unaffected.

**Format:**

```json
{ "written": "spoken", "written2": "spoken2" }
```

**Example — add a custom pronunciation and override an existing one:**

```bash
poindexter settings set tts_pronunciations \
  '{"NVMe": "N V Me", "PostgreSQL": "post gres"}'
```

**Built-in defaults** (seeded at first boot; edit via `poindexter settings set`):

| Written    | Spoken              |
| ---------- | ------------------- |
| VRAM       | Vee RAM             |
| SRAM       | Ess RAM             |
| DRAM       | Dee RAM             |
| PB         | petabyte            |
| TB         | terabyte            |
| GB         | gigabyte            |
| MB         | megabyte            |
| KB         | kilobyte            |
| GHz        | gigahertz           |
| MHz        | megahertz           |
| kHz        | kilohertz           |
| Gbps       | gigabits per second |
| Mbps       | megabits per second |
| Kbps       | kilobits per second |
| fps        | frames per second   |
| GitFlow    | git flow            |
| GitHub     | git hub             |
| GitLab     | git lab             |
| DevSecOps  | dev sec ops         |
| DevOps     | dev ops             |
| FastAPI    | fast A P I          |
| PostgreSQL | postgres            |
| GraphQL    | graph Q L           |
| TypeScript | type script         |
| JavaScript | java script         |

In addition to the table above, the code always applies these
**structural** replacements (hardcoded, not DB-configurable — they are
formatting transforms, not pronunciation opinions):

- Medium adaptation: "in this post" → "in this episode", "read on" → "stay tuned", etc.
- Symbols: `&` → "and", em dash → pause, `->` → "to", `==` → "equals", etc.
- Abbreviations with punctuation: `e.g.` → "for example", `vs.` → "versus", `CI/CD` → "CI CD", etc.

---

### `tts_acronym_replacements`

JSON object of uppercase acronym → plain-English expansion. Applied with
`\b` word boundaries (so `SOC` does not fire inside `SOCK`). Replaces the
entire built-in acronym list when set — supply all desired entries, not
just the new ones.

**Format:**

```json
{ "ACRONYM": "plain english expansion" }
```

**Example:**

```bash
poindexter settings set tts_acronym_replacements \
  '{"SOC": "security operations", "API": "A P I", "SDK": "S D K"}'
```

**Built-in defaults:**

| Acronym | Expansion                        |
| ------- | -------------------------------- |
| SOC     | security operations              |
| CRM     | customer relationship management |
| SLA     | service level agreement          |
| KPI     | key performance indicator        |
| ROI     | return on investment             |
| MVP     | minimum viable product           |
| POC     | proof of concept                 |
| EOL     | end of life                      |

---

## Word-boundary behaviour

Pure-letter tokens from `tts_pronunciations` are matched with `\b` on both
sides, so:

- `"GB"` fires on `"256 GB SSD"` → `"256 gigabyte SSD"` ✓
- `"GB"` does **not** fire inside `"RGB"` or `"16GB"` (no space before) ✓
- `"VRAM"` fires on `"16 VRAM"` → `"16 Vee RAM"` ✓

Tokens that contain punctuation (`"vs."`, `"e.g."`, `"->"`) use plain
string matching without `\b`, which is correct for their role as
punctuation-delimited abbreviations.

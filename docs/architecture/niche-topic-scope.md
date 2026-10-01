# Niche topic scope

A niche can state what it covers. When it does, topic discovery keeps to that
subject instead of taking whatever its sources happen to fetch.
([Glad-Labs/poindexter#1127](https://github.com/Glad-Labs/poindexter/issues/1127))

## Why it exists

The topic ranker scores candidates against a niche's **goals**: `AUTHORITY`,
`EDUCATION`, `TRAFFIC` and so on. Goals say _why_ to write something. Nothing
said _what about_. The subject a niche ended up writing about was decided
entirely by which sources fed its pool, so a niche meant to cover one field
drifted into business, marketing and posts about itself whenever those came
in. The only way to push back was editing `niche_goal_descriptions`, a single
setting shared by every niche on the install.

## What you set

Three columns on `niches`, set with `poindexter topics niche set-scope`:

| Column               | Meaning                                                                                                                |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `topic_subject`      | What the niche covers, in plain prose. NULL means no scope: sweeps behave exactly as they did before scope existed.    |
| `topic_exclusions`   | Subjects that are out of scope even when they sit next to the subject, such as "video game news" beside "PC hardware". |
| `topic_scope_filter` | On (the default): out-of-scope candidates are dropped before ranking. Off: the subject only steers ranking.            |

```bash
poindexter topics niche set-scope my-niche \
  --subject "AI and computer hardware: models, training, inference, GPUs, CPUs, builds, servers" \
  --exclude "video games and game news" \
  --exclude "business strategy, marketing and startups"
poindexter topics niche check-scope my-niche --show out   # what would be dropped
poindexter topics niche show my-niche                     # goals, sources, scope
```

`set-scope` edits only what its flags name, so adding one exclusion keeps the
subject and the other exclusions. `--remove-exclude`, `--clear-exclusions`,
`--filter/--no-filter` and `--clear` do what they say.

## What reads it

- **The scope check (hard filter).** After the sanity filter and before
  embedding, the sweep sends each candidate's title and summary to
  `niche_topic_scope_check_model` (empty means the structured-extraction
  model) in batches of `niche_topic_scope_check_chunk_size` (15) and drops the ones whose main subject is out of scope. Carried-forward
  candidates are re-judged, so a scope edit applies to them on the next sweep.
  The prompt is `topic.scope_check` in `skills/content/research/SKILL.md`.
- **The ranking prompt.** The scope is appended to the goal list the
  `topic.ranking` prompt receives, so the ranker scores off-subject candidates
  low even when the filter is off. It rides on the existing `weights_descr`
  variable rather than a new one, so a customised ranking prompt keeps
  working and still sees the scope.
- **The `NICHE_DEPTH` goal.** With a subject set, `NICHE_DEPTH` is anchored on
  the subject text instead of the install-wide description, so it means "deep
  on this niche's subject". Weight it with `set-goals`.
- **Internal story selection.** `internal_rag` passes the scope to its
  distiller and uses the same niche-aware goal vectors to pick snippets.

## Why an LLM check and not an embedding threshold

Measured before building it, on 37 real titles against an AI + hardware
subject with `nomic-embed-text`: title embeddings do not separate the two
sets. "Market Research vs Industry Research" scored 0.478 against the subject,
above "Decode Speed Lies: phi4:14b" at 0.477, and "Qwen 3.8 27B Needs 22,000
Tokens to Draw a Pelican" scored 0.450. Any threshold would drop real on-topic
posts and keep off-topic ones. The same 37 titles in one batched call to
qwen2.5:7b came back about 33 correct in 5.4 s.

The embedding anchor is still used for `NICHE_DEPTH`, where it is one weighted
signal among several rather than a gate.

## Which model, and how many per call

Measured on 107 live candidates with 16 hand-labelled on-topic and 14
off-topic titles:

| Model                     | Per call | On-topic wrongly dropped | Off-topic wrongly kept |
| ------------------------- | -------- | ------------------------ | ---------------------- |
| qwen2.5:7b                | 40       | 8 of 16                  | 1 of 14                |
| qwen2.5:7b                | 15       | 4 of 16                  | 1 of 14                |
| qwen3-vl:30b-a3b-instruct | 15       | 0 and 1 of 16 (two runs) | 0 of 14                |

Smaller models drop real on-topic candidates, often terse search-query titles
such as "Gguf Quantization Types". Point `niche_topic_scope_check_model` at
the strongest model you can afford to run on every sweep, and check the result
with `check-scope` before relying on the filter.

## Failure behaviour

The check **fails open and loud**. A model error, an unparseable reply or an id
the model skipped keeps the candidate, and the sweep emits
`topic_scope_check_failed` (routed to Discord, 6 h cooldown), so an off filter
never looks like a working one. Each sweep that drops candidates emits one
`topic_scope_filtered` finding listing them (logged and on the Findings board,
not sent). If the filter drops everything, the existing empty-batch guard
records the run and does not open an empty batch.

`check-scope` prints the check's first error when anything is unjudged. A
common one is running it from a host shell on an install whose model URL only
resolves inside the containers (`host.docker.internal`). Run it in the worker
instead: `docker exec poindexter-worker python -m poindexter topics niche
check-scope <slug>`.

## Other niche controls

```bash
poindexter topics niche set-goals my-niche NICHE_DEPTH=35 AUTHORITY=25 EDUCATION=20 TRAFFIC=15 REVENUE=5
poindexter topics niche set-sources my-niche internal_rag=25 web_search=25 hackernews=20 devto=15 knowledge=5
poindexter topics niche set-writer-prompt my-niche --file writer-prompt.md
```

Goals must sum to 100. `set-sources` replaces the list; `NAME=WEIGHT:off`
keeps a source listed but disabled. What each source fetches (queries, feeds,
tags) lives on its `external_taps` row: `poindexter taps set-config`.

# A/B writer harness — "dream vs reality"

One question, answered honestly: **on the same topic and the same production
prompt, how much better is a cloud model than the local model you ship today —
and does the local model clear the bar of "publishable" at all?**

It swaps _only the model_. Same prompt (pulled straight from
`skills/content/two-pass-writer/SKILL.md`), same topic, angle, snippets, and
length for every contender. You score the writing **blind** — the model
identity is hidden until you run `reveal` — so your hope for the local model
can't tip the result.

It deliberately tests the **draft stage** only. That's the clean signal for
model quality: downstream revise/expand/QA are the same regardless of which
model wrote the draft. If the raw draft is junk, nothing downstream saves it;
if it's good, any remaining junk is a _pipeline_ problem, not a _model_ one.

## Setup

- Ollama running locally (default `http://localhost:11434`) for the local
  contenders. Make sure the models are pulled (`ollama pull gemma3:27b`, etc.).
- `export ANTHROPIC_API_KEY=...` for the cloud contender.
- Zero Python dependencies — stdlib only. Any `python3` works.

## The three contenders

Edit `DEFAULT_MODELS` at the top of `ab_writer_test.py` (or pass your own with
`--models a_models.json`):

| label       | why it's here                                                                                                                           |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `local-27b` | what you ship today (`gemma3:27b`, ~5090-class hardware)                                                                                |
| `local-8b`  | the **honest** everyday-hardware tier (~8GB VRAM). The real mission test: can the model a poor person can _actually_ run clear the bar? |
| `cloud`     | the ceiling. **Set the exact Anthropic model id your account accepts** before running.                                                  |

The gap between `local-8b` and `cloud` is the mission question. The gap between
`local-27b` and `cloud` is the "how much am I leaving on the table on my own
hardware" question.

## Run it

```bash
# 1. Generate blinded candidates for every topic
python3 scripts/experiments/ab_writer_test.py run \
    --topics scripts/experiments/topics.example.json

# (sanity-check the exact prompt first, without calling models:)
python3 scripts/experiments/ab_writer_test.py run \
    --topics scripts/experiments/topics.example.json --dry-run

# 2. Read runs/<timestamp>/topic_*.md and fill the score table at the bottom
#    of each. DO THIS BEFORE STEP 3. Do not open answer_key.json yet.

# 3. Reveal which candidate was which model (+ speed / word count)
python3 scripts/experiments/ab_writer_test.py reveal runs/<timestamp>
```

## How to read the result

Don't just ask "which won" — cloud will probably win today. Ask:

1. **Does `local-8b` (or `local-27b`) clear "publishable" on its own?** If yes,
   the mission is alive on today's hardware — ship local-first and keep cloud
   as an optional tier. If no, build cloud-first _now_ to earn credibility and
   revenue, and keep the local tier warming up as models improve.
2. **How big is the gap?** A small gap means local is a genuine choice. A large
   gap means "$0 infra" is currently costing you the whole business.
3. **Score blind, then reveal.** If you can't reliably tell the cloud output
   from the local one while blind, that itself is the answer.

## Add your own topics

Copy `topics.example.json`. Each entry: `topic`, `angle`, optional
`target_length` (default 1200), optional `extra_instructions`, optional
`snippets` (`[{ "source": "...", "text": "..." }]` — the background/RAG context
the writer grounds itself in). For the fairest test, use topics your pipeline
_actually_ produced junk on, with the real snippets it had.

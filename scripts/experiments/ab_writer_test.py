#!/usr/bin/env python3
"""
A/B writer harness — "dream vs reality" for the Poindexter writer.

Question this answers: on the SAME topic and the SAME production prompt,
how much better is a cloud model than the local model you ship today —
and does the local model clear the bar of "publishable" at all?

Design principles (so the answer is trustworthy, not wishful):
  1. Same prompt. It parses the REAL production draft prompt straight out
     of skills/content/two-pass-writer/SKILL.md — the exact text the
     canonical_blog pipeline feeds the writer. No cleaned-up demo prompt.
  2. Only the model changes. Every contender gets identical topic, angle,
     background snippets, and target length. The only variable is which
     model writes.
  3. Blind scoring. Outputs are shuffled and labelled "Candidate 1/2/3"
     with the model identity hidden in answer_key.json. You score the
     writing before you learn which one is the expensive one.
  4. It isolates the DRAFT stage on purpose. Downstream revise/expand/QA
     are model-independent, so the draft is the clean signal for model
     quality. If the raw draft is junk, nothing downstream saves it; if
     it's good, any remaining junk is a pipeline problem, not a model one.

Zero dependencies — stdlib only. Runs with plain `python3`, no venv needed.

USAGE
  # 1. Generate blinded candidates for every topic:
  python3 scripts/experiments/ab_writer_test.py run \
      --topics scripts/experiments/topics.example.json

  # 2. Open runs/<timestamp>/topic_*.md, read each candidate, fill the
  #    score table at the bottom. Do this BEFORE step 3.

  # 3. Reveal which candidate was which model (+ speed / length):
  python3 scripts/experiments/ab_writer_test.py reveal runs/<timestamp>

  # Sanity-check the exact filled prompt without calling any model:
  python3 scripts/experiments/ab_writer_test.py run --topics ... --dry-run

REQUIREMENTS
  - Ollama running locally (default http://localhost:11434) for local models.
  - ANTHROPIC_API_KEY in the environment for any cloud (anthropic) contender.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILL = REPO_ROOT / "src/cofounder_agent/skills/content/two-pass-writer/SKILL.md"
PROMPT_KEY = "atoms.two_pass_writer.generate_with_context"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# The three contenders. Edit these to match YOUR hardware and account.
#   - "local-27b"  : what you ship today (your 5090-class writer).
#   - "local-8b"   : the HONEST "everyday-hardware" tier — runs on ~8GB VRAM.
#                    This is the real question for the mission: can the model
#                    a poor person can actually run clear the bar?
#   - "cloud"      : the ceiling. NOTE: set the exact model id your Anthropic
#                    account accepts — confirm the current id before running.
DEFAULT_MODELS = [
    {"label": "local-27b", "kind": "ollama", "model": "gemma3:27b"},
    {"label": "local-8b", "kind": "ollama", "model": "qwen3:8b", "think": False},
    {"label": "cloud", "kind": "anthropic", "model": "claude-sonnet-5"},
]

# Generation params (kept modest + fixed across contenders for fairness).
OLLAMA_NUM_PREDICT = 2048
ANTHROPIC_MAX_TOKENS = 4096
TEMPERATURE = 0.7
OLLAMA_TIMEOUT = 600  # local 27B can be slow
ANTHROPIC_TIMEOUT = 180

# Fallback prompt if the SKILL.md can't be parsed (kept short; the real one
# is parsed from disk in the normal path so it always matches production).
_FALLBACK_PROMPT = (
    'Write a blog post on the topic: "{topic}" with this angle: "{angle}".\n\n'
    "{instructions}\n\n{snippet_block}\n\n"
    "Aim for roughly {target_length} words. Use real names and numbers, take a "
    "position, vary sentence length, and never invent a statistic or a source. "
    "Return the full post body once, in Markdown."
)


# --------------------------------------------------------------------------
# Prompt loading / filling
# --------------------------------------------------------------------------

def load_prompt_body(skill_path: Path, key: str) -> str:
    """Parse the fenced prompt body under `## <key>` from a SKILL.md pack."""
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_PROMPT
    header = re.search(r"^##\s+" + re.escape(key) + r"\s*$", text, re.M)
    if not header:
        print(f"  [warn] key '{key}' not found in {skill_path}; using fallback prompt")
        return _FALLBACK_PROMPT
    block = re.search(r"```(?:text)?\n(.*?)```", text[header.end():], re.S)
    if not block:
        print(f"  [warn] no fenced block after '{key}'; using fallback prompt")
        return _FALLBACK_PROMPT
    return block.group(1).rstrip("\n")


def format_snippets(snippets, cap: int = 500) -> str:
    blocks = []
    for s in snippets or []:
        src = s.get("source", "source")
        txt = (s.get("text", "") or "")[:cap]
        blocks.append(f"From {src}:\n{txt}")
    return "\n\n".join(blocks)


def fill_prompt(body: str, *, topic, angle, instructions, snippet_block, target_length) -> str:
    """Targeted placeholder replacement (safer than str.format against stray braces)."""
    repl = {
        "{topic}": topic,
        "{angle}": angle,
        "{instructions}": instructions,
        "{snippet_block}": snippet_block,
        "{target_length}": str(target_length),
    }
    out = body
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


# --------------------------------------------------------------------------
# Model calls (stdlib urllib)
# --------------------------------------------------------------------------

def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_ollama(model_cfg: dict, prompt: str) -> str:
    payload = {
        "model": model_cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_predict": OLLAMA_NUM_PREDICT},
    }
    # Reasoning models waste their budget "thinking" and return empty content
    # (this is exactly the bug the pipeline hit — see ai_content_generator note).
    if "think" in model_cfg:
        payload["think"] = model_cfg["think"]
    result = _post_json(f"{OLLAMA_URL}/api/chat", payload, {}, OLLAMA_TIMEOUT)
    msg = result.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        raise RuntimeError("empty content from Ollama (thinking model? try 'think': false)")
    return content


def call_anthropic(model_cfg: dict, prompt: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    payload = {
        "model": model_cfg["model"],
        "max_tokens": ANTHROPIC_MAX_TOKENS,
        "temperature": TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    result = _post_json(ANTHROPIC_URL, payload, headers, ANTHROPIC_TIMEOUT)
    parts = [b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"]
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError(f"empty content from Anthropic: {json.dumps(result)[:300]}")
    return text


def generate(model_cfg: dict, prompt: str) -> dict:
    """Return {ok, text, words, latency_s, error}."""
    t0 = time.time()
    try:
        if model_cfg["kind"] == "ollama":
            text = call_ollama(model_cfg, prompt)
        elif model_cfg["kind"] == "anthropic":
            text = call_anthropic(model_cfg, prompt)
        else:
            raise RuntimeError(f"unknown kind {model_cfg['kind']}")
        return {
            "ok": True, "text": text, "words": len(text.split()),
            "latency_s": round(time.time() - t0, 1), "error": None,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as e:
        detail = e.read().decode("utf-8", "replace")[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
        return {
            "ok": False, "text": "", "words": 0,
            "latency_s": round(time.time() - t0, 1), "error": detail,
        }


# --------------------------------------------------------------------------
# Blind-scoring output
# --------------------------------------------------------------------------

RUBRIC = """\
Score each candidate 1-5 (5 = best). Score the WRITING, not your guess about
which model wrote it.

  worth_reading   would a real reader finish it? (1 skim-and-leave .. 5 gripping)
  specific        real names/numbers/claims vs generic mush (1 vague .. 5 concrete)
  has_a_view      takes a clear position vs both-sides waffle (1 fence .. 5 stance)
  prose           rhythm & voice vs LLM-tell slop (1 robotic .. 5 human)
  publishable     would you publish it? (no / needs-edit / yes)

Fill this table (edit the blanks), then run the `reveal` command:
"""


def write_topic_file(run_dir: Path, idx: int, topic: dict, candidates: list[dict]) -> dict:
    """Write a blinded markdown file; return the answer-key entry."""
    order = list(range(len(candidates)))
    random.shuffle(order)

    lines = [
        f"# Blind scoring — Topic {idx}",
        "",
        f"**Topic:** {topic['topic']}",
        f"**Angle:** {topic.get('angle', '')}",
        "",
        "---",
        "",
    ]
    key_candidates = {}
    for pos, orig_i in enumerate(order, start=1):
        c = candidates[orig_i]
        lines.append(f"## Candidate {pos}")
        lines.append("")
        if c["result"]["ok"]:
            lines.append(c["result"]["text"])
        else:
            lines.append(f"_[generation failed: {c['result']['error']}]_")
        lines.append("")
        lines.append("---")
        lines.append("")
        key_candidates[str(pos)] = {
            "label": c["cfg"]["label"],
            "model": c["cfg"]["model"],
            "kind": c["cfg"]["kind"],
            "ok": c["result"]["ok"],
            "words": c["result"]["words"],
            "latency_s": c["result"]["latency_s"],
            "error": c["result"]["error"],
        }

    lines.append("## Your scores")
    lines.append("")
    lines.append(RUBRIC)
    lines.append("| candidate | worth_reading | specific | has_a_view | prose | publishable | notes |")
    lines.append("|-----------|:---:|:---:|:---:|:---:|:---:|-------|")
    for pos in range(1, len(candidates) + 1):
        lines.append(f"| {pos} | _ | _ | _ | _ | _ | |")
    lines.append("")

    path = run_dir / f"topic_{idx}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"topic": topic["topic"], "angle": topic.get("angle", ""), "candidates": key_candidates}


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_run(args) -> int:
    topics = json.loads(Path(args.topics).read_text(encoding="utf-8"))
    if not isinstance(topics, list) or not topics:
        print("topics file must be a non-empty JSON list", file=sys.stderr)
        return 2

    models = DEFAULT_MODELS
    if args.models:
        models = json.loads(Path(args.models).read_text(encoding="utf-8"))

    prompt_body = load_prompt_body(Path(args.skill), PROMPT_KEY)
    print(f"Prompt: {PROMPT_KEY} ({len(prompt_body)} chars) from {args.skill}")
    print(f"Contenders: {', '.join(m['label'] + '=' + m['model'] for m in models)}")

    if args.dry_run:
        t = topics[0]
        filled = fill_prompt(
            prompt_body, topic=t["topic"], angle=t.get("angle", ""),
            instructions=t.get("extra_instructions", ""),
            snippet_block=format_snippets(t.get("snippets")),
            target_length=t.get("target_length", 1200),
        )
        print("\n" + "=" * 70 + "\nFILLED PROMPT (topic 1)\n" + "=" * 70)
        print(filled)
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.out) / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    answer_key = {"generated_at": ts, "prompt_key": PROMPT_KEY, "topics": {}}
    for idx, t in enumerate(topics, start=1):
        filled = fill_prompt(
            prompt_body, topic=t["topic"], angle=t.get("angle", ""),
            instructions=t.get("extra_instructions", ""),
            snippet_block=format_snippets(t.get("snippets")),
            target_length=t.get("target_length", 1200),
        )
        print(f"\n[topic {idx}/{len(topics)}] {t['topic']}")
        candidates = []
        for m in models:
            print(f"  - {m['label']} ({m['model']}) ...", end="", flush=True)
            res = generate(m, filled)
            status = f"{res['words']}w in {res['latency_s']}s" if res["ok"] else f"FAILED: {res['error']}"
            print(f" {status}")
            candidates.append({"cfg": m, "result": res})
        answer_key["topics"][f"topic_{idx}"] = write_topic_file(run_dir, idx, t, candidates)

    (run_dir / "answer_key.json").write_text(json.dumps(answer_key, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"Done. Blinded candidates in: {run_dir}")
    print("  1. Read topic_*.md and fill the score tables (DON'T peek at answer_key.json).")
    print(f"  2. Then: python3 {sys.argv[0]} reveal {run_dir}")
    return 0


def cmd_reveal(args) -> int:
    run_dir = Path(args.run_dir)
    key = json.loads((run_dir / "answer_key.json").read_text(encoding="utf-8"))
    print(f"Run {key['generated_at']} — prompt {key['prompt_key']}\n")
    for tkey, tval in key["topics"].items():
        print(f"{tkey}: {tval['topic']}")
        for pos, c in sorted(tval["candidates"].items()):
            flag = "" if c["ok"] else "  <FAILED>"
            print(f"  Candidate {pos}  ->  {c['label']:<12} {c['model']:<22} "
                  f"{c['words']:>4}w  {c['latency_s']:>6}s{flag}")
        print()
    print("Now put your blind scores next to the revealed labels and look at the GAP,\n"
          "not just the winner: does local clear 'publishable' on its own?")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="A/B writer harness: local vs cloud on the real prompt.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="generate blinded candidates")
    r.add_argument("--topics", required=True, help="JSON list of topics")
    r.add_argument("--models", help="JSON list of model configs (defaults to the 3 built-in contenders)")
    r.add_argument("--skill", default=str(DEFAULT_SKILL), help="SKILL.md to pull the production prompt from")
    r.add_argument("--out", default=str(Path(__file__).resolve().parent / "runs"), help="output root dir")
    r.add_argument("--dry-run", action="store_true", help="print the filled prompt for topic 1 and exit")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("reveal", help="reveal which candidate was which model")
    v.add_argument("run_dir", help="a runs/<timestamp> directory")
    v.set_defaults(func=cmd_reveal)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

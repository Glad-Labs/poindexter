#!/usr/bin/env python3
"""Golden-conversation evals for the Cofounder chat brain (poindexter#947).

Local-model tool-calling is the joint that breaks silently on a model swap:
the pipeline stays green while the chat agent turns into a polite
paperweight. Run this whenever ``console_chat_model`` changes (and after
Ollama upgrades). It sends each golden intent through the REAL system
prompt + tool schemas and PARSER-verifies the model's first move — which
tool it called and that required args are present — never an LLM judging an
LLM (reference_convergence_watchdog_pattern).

Usage (host — talks straight to Ollama's host port):

    cd src/cofounder_agent
    poetry run python ../../scripts/chat_brain_evals.py --model qwen2.5:7b
    poetry run python ../../scripts/chat_brain_evals.py \
        --model qwen2.5:32b --api-base http://localhost:11434

Exit code 0 when the pass rate meets ``--min-pass`` (default 0.8), 1
otherwise — wire-able into a model-swap checklist. Deliberately NOT a
pytest suite: it needs live Ollama + a pulled model, and its pass rate is a
model property, not a code property.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "cofounder_agent"))

GOLDEN = [
    # (intent, expected tool or None, required arg keys)
    ("How many tasks are waiting for my approval?", "list_tasks", []),
    ("Show me the last 5 pipeline tasks", "list_tasks", []),
    ("List all failed tasks", "list_tasks", []),
    ("What's the status of task 3f2a?", "get_task", ["task_id"]),
    ("Give me the details on task 9b1c8d2e", "get_task", ["task_id"]),
    ("How much have we spent this month?", "get_budget", []),
    ("Are we close to the AI budget cap?", "get_budget", []),
    ("What do we know about the Pop OS migration?", "search_memory", ["query"]),
    ("Search memory for the auto-publish gate decision", "search_memory", ["query"]),
    ("Have we already written about local-first analytics?",
     "find_similar_posts", ["topic"]),
    ("Do we have coverage of RAG retrieval stacks?",
     "find_similar_posts", ["topic"]),
    ("What happened in the system in the last 24 hours?",
     "get_audit_summary", []),
    ("What is the value of the default_template_slug setting?",
     "get_setting", ["key"]),
    ("Write a blog post about self-hosted analytics dashboards",
     "create_post", ["topic"]),
    ("Draft an article on VRAM contention in shared GPU rigs",
     "create_post", ["topic"]),
    # Negative controls — a model that tool-calls on chitchat fails these.
    ("Thanks, that's all for now!", None, []),
    ("Good morning! How are you today?", None, []),
]


async def _run_case(
    model: str, api_base: str, intent: str,
) -> tuple[str | None, dict[str, Any]]:
    import litellm

    from services.chat_prompts import _CHAT_SYSTEM_FALLBACK
    from services.chat_tools import to_openai_tools, tool_names_csv

    system = _CHAT_SYSTEM_FALLBACK.format(
        persona_name="Poindexter", tool_names=tool_names_csv(),
    )
    response = await litellm.acompletion(
        model=f"ollama/{model.removeprefix('ollama/')}",
        api_base=api_base,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": intent},
        ],
        tools=to_openai_tools(),
        tool_choice="auto",
        temperature=0.2,
        timeout=120,
    )
    msg = response.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        return None, {}
    fn = tool_calls[0].function
    try:
        args = json.loads(fn.arguments or "{}")
    except ValueError:
        args = {"__unparseable__": fn.arguments}
    return fn.name, args if isinstance(args, dict) else {}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--api-base", default="http://localhost:11434")
    parser.add_argument("--min-pass", type=float, default=0.8)
    args = parser.parse_args()

    passed = 0
    for intent, expected_tool, required_args in GOLDEN:
        try:
            tool, call_args = await _run_case(args.model, args.api_base, intent)
        except Exception as exc:  # noqa: BLE001 — report + count as fail
            print(f"FAIL  {intent!r}: call error {type(exc).__name__}: {exc}")
            continue
        ok = tool == expected_tool
        missing = [k for k in required_args if k not in call_args]
        if ok and missing:
            ok = False
        mark = "pass " if ok else "FAIL "
        detail = f"tool={tool!r}"
        if missing:
            detail += f" missing_args={missing}"
        if not ok:
            detail += f" expected={expected_tool!r}"
        print(f"{mark} {intent!r}: {detail}")
        passed += ok

    rate = passed / len(GOLDEN)
    print(
        f"\n{passed}/{len(GOLDEN)} passed ({rate:.0%}) — "
        f"model={args.model} threshold={args.min_pass:.0%}"
    )
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

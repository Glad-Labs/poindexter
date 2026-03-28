#!/usr/bin/env python3
"""
Agent Runner - Submit a task to the Cofounder Agent and monitor it to completion.

Usage:
    python scripts/run_agent.py <topic> [--type blog_post|social_media|email|newsletter] [--model balanced]

Examples:
    python scripts/run_agent.py "AI in Healthcare 2026"
    python scripts/run_agent.py "Best Python Tips" --type blog_post --model cheap
    python scripts/run_agent.py "AI trends" --type social_media

Task types: blog_post, social_media, email, newsletter, business_analytics
Models: ultra_cheap, cheap, balanced, premium
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")

API_URL = os.getenv("API_URL", "http://localhost:8000")
HEADERS = {"Authorization": "Bearer dev-token", "Content-Type": "application/json"}
POLL_INTERVAL = 5  # seconds


async def submit_task(topic: str, task_type: str, model: str) -> str:
    """Submit a new task and return the task_id."""
    payload = {
        "task_name": f"CLI: {topic[:60]}",
        "topic": topic,
        "category": "general",
        "task_type": task_type,
        "target_audience": "general audience",
        "primary_keyword": topic.split()[0].lower(),
        "model": model,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/api/tasks", headers=HEADERS, json=payload, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        raise RuntimeError(f"No task_id in response: {data}")
    return task_id


async def poll_task(task_id: str) -> dict:
    """Poll until task reaches a terminal state, printing status updates."""
    terminal = {"completed", "failed", "awaiting_approval", "approved", "rejected"}
    last_status = None
    start = time.time()

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{API_URL}/api/tasks/{task_id}", headers=HEADERS, timeout=30
            )
            resp.raise_for_status()
            task = resp.json()

            status = task.get("status", "unknown")
            elapsed = time.time() - start

            if status != last_status:
                print(f"  [{elapsed:5.1f}s] {status}", flush=True)
                last_status = status

            if status in terminal:
                return task

            await asyncio.sleep(POLL_INTERVAL)


def print_result(task: dict):
    status = task.get("status")
    print()
    if status in ("completed", "awaiting_approval", "approved"):
        print("✅ Task succeeded!")
        result = task.get("result") or {}
        content = result.get("content") or task.get("content") or ""
        if content:
            word_count = len(content.split())
            print(f"   Word count: {word_count}")
            print(f"   Preview:    {content[:200].strip()}...")
        print(f"   Model used: {task.get('model_used') or 'unknown'}")
    else:
        print("❌ Task failed!")
        error = task.get("error_message") or task.get("error") or "no error message"
        print(f"   Error: {error}")

    # Optionally save full result
    out = Path(__file__).parent / f"agent_result_{task['id'][:8]}.json"
    out.write_text(json.dumps(task, indent=2, default=str))
    print(f"   Full result saved to: {out}")


async def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    topic = args[0]
    task_type = "blog_post"
    model = "balanced"

    i = 1
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            task_type = args[i + 1]
            i += 2
        elif args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        else:
            i += 1

    print(f"🚀 Submitting {task_type} task: '{topic}' (model: {model})")
    print(f"   API: {API_URL}")
    print()

    try:
        task_id = await submit_task(topic, task_type, model)
        print(f"   Task ID: {task_id}")
        print(f"   Polling every {POLL_INTERVAL}s...")
        print()

        task = await poll_task(task_id)
        print_result(task)

    except httpx.ConnectError:
        print(f"❌ Cannot connect to {API_URL}")
        print("   Make sure the backend is running: npm run dev:cofounder")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

#!/bin/bash
# scripts/run.sh — Recall something said earlier in this voice conversation.
#
# Slice 3 of the voice-agent rollout (Glad-Labs/poindexter#390). Wraps a
# pgvector cosine search over voice_messages — the same query the bot
# fires implicitly on every user turn — but exposed as an explicit
# voice-invokable skill.
#
# Direct DB access (no API hop) because:
#   1. recall is local-only conversation data, not shared content
#   2. the embedder Ollama call has to happen anyway and we already
#      have a python helper at scripts/_voice_memory.py that does it
#
# Resolution chain for DATABASE_URL:
#   1. $DATABASE_URL env var
#   2. ~/.poindexter/bootstrap.toml (resolved by brain.bootstrap)

set -e

TOPIC="$1"
LIMIT="${2:-}"
MIN_SIM="${3:-}"

if [ -z "$TOPIC" ]; then
  echo "Error: topic is required"
  echo "Usage: run.sh \"<topic>\" [limit] [min_similarity]"
  exit 1
fi

# Resolve repo root so we can import scripts/_voice_memory + brain
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

cd "${REPO_ROOT}"

LIMIT="${LIMIT}" MIN_SIM="${MIN_SIM}" TOPIC="${TOPIC}" \
  python3 - <<'PY'
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path.cwd()
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Best-effort tunable defaults — match settings_defaults.py / migration
LIMIT = int(os.environ.get("LIMIT") or "3") or 3
MIN_SIM = float(os.environ.get("MIN_SIM") or "0.5")
TOPIC = os.environ.get("TOPIC") or ""


async def _main():
    import asyncpg

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            from brain.bootstrap import resolve_database_url
            db_url = resolve_database_url()
        except Exception:
            db_url = None
    if not db_url:
        print("recall-conversation: no DATABASE_URL configured")
        return 2

    ollama_url = (
        os.environ.get("OLLAMA_URL")
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://host.docker.internal:11434"
    )

    from _voice_memory import recall_similar_turns  # noqa: E402

    try:
        conn = await asyncpg.connect(db_url)
    except Exception as exc:
        print(f"recall-conversation: DB connect failed: {exc}")
        return 2

    try:
        hits = await recall_similar_turns(
            conn,
            query_text=TOPIC,
            ollama_url=ollama_url,
            k=LIMIT,
            min_similarity=MIN_SIM,
        )
    finally:
        await conn.close()

    if not hits:
        print(f"Nothing came up for: {TOPIC}")
        return 0

    parts = []
    for h in hits[:3]:
        sim = round(h.get("similarity", 0.0), 2)
        role = h.get("role") or "?"
        preview = (h.get("content") or "").replace("\n", " ")[:90]
        parts.append(f"[{sim}] {role}: {preview}")
    print(f"Found {len(hits)} prior turn(s). " + " | ".join(parts))
    return 0


sys.exit(asyncio.run(_main()))
PY

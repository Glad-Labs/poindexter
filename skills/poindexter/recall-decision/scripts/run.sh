#!/bin/bash
# scripts/run.sh — Recall a past decision/memory.
#
# Same endpoint as search-memory, but filtered to source_table=memory so the
# results are only from the curated decision_log + feedback_* + project_*
# files (not posts, audit, or issues). Better signal-to-noise for "what did
# we decide" questions.

set -e

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "${SCRIPT_DIR}/../../_lib/get_token.sh"
POINDEXTER_TOKEN="$(get_poindexter_token)" || exit 1

TOPIC="$1"
LIMIT="${2:-5}"

if [ -z "$TOPIC" ]; then
  echo "Error: topic is required"
  echo "Usage: run.sh \"<topic>\" [limit]"
  exit 1
fi

QUERY_ENC=$(echo -n "$TOPIC" | sed 's/ /%20/g')

RESPONSE=$(curl -sS -w "\n%{http_code}" -X GET \
  "${FASTAPI_URL}/api/memory/search?q=${QUERY_ENC}&limit=${LIMIT}&source_table=memory&min_similarity=0.4" \
  -H "Authorization: Bearer ${POINDEXTER_TOKEN}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ne 200 ]; then
  echo "Decision recall failed (HTTP $HTTP_CODE)"
  echo "$BODY"
  exit 1
fi

echo "$BODY" | python3 -c '
import json, sys
d = json.load(sys.stdin)
hits = d.get("hits", [])
if not hits:
    print(f"No prior decision found for: {sys.argv[1] if len(sys.argv) > 1 else \"that topic\"}")
    sys.exit(0)
parts = []
for h in hits[:3]:
    sim = round(h["similarity"], 2)
    sid = (h["source_id"] or "")[:30]
    preview = (h["text_preview"] or "").replace("\n", " ")[:90]
    parts.append(f"[{sim}] {sid}: {preview}")
print(f"Found {len(hits)} memories. " + " | ".join(parts))
'

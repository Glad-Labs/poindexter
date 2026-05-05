#!/bin/bash
# scripts/run.sh — Vector-search the unified poindexter memory.
#
# Wraps the worker's /api/memory/search route (pgvector cosine search over
# the embeddings table). Sorted output by descending similarity. Voice-bot
# uses the first non-JSON line as the spoken response, so we lead with a
# human-readable summary.

set -e

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "${SCRIPT_DIR}/../../_lib/get_token.sh"
POINDEXTER_TOKEN="$(get_poindexter_token)" || exit 1

QUERY="$1"
LIMIT="${2:-5}"
MIN_SIM="${3:-0.5}"

if [ -z "$QUERY" ]; then
  echo "Error: query is required"
  echo "Usage: run.sh \"<query>\" [limit] [min_similarity]"
  exit 1
fi

# URL-encode the query (just spaces — the worker handles the rest).
QUERY_ENC=$(echo -n "$QUERY" | sed 's/ /%20/g')

RESPONSE=$(curl -sS -w "\n%{http_code}" -X GET \
  "${FASTAPI_URL}/api/memory/search?q=${QUERY_ENC}&limit=${LIMIT}&min_similarity=${MIN_SIM}" \
  -H "Authorization: Bearer ${POINDEXTER_TOKEN}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ne 200 ]; then
  echo "Memory search failed (HTTP $HTTP_CODE)"
  echo "$BODY"
  exit 1
fi

# Pull the first hit's source + similarity for a one-line spoken summary,
# then dump the full JSON for log/debug context.
COUNT=$(echo "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("count",0))')
if [ "$COUNT" = "0" ]; then
  echo "No memory matches for: $QUERY"
  exit 0
fi

SUMMARY=$(echo "$BODY" | python3 -c '
import json, sys
d = json.load(sys.stdin)
hits = d["hits"][:3]
parts = []
for h in hits:
    sim = round(h["similarity"], 2)
    src = h["source_table"]
    sid = (h["source_id"] or "")[:30]
    preview = (h["text_preview"] or "").replace("\n", " ")[:80]
    parts.append(f"[{sim}] {src}/{sid}: {preview}")
print(f"Found {d[\"count\"]} matches. " + " | ".join(parts))
')
echo "$SUMMARY"

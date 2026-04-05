#!/usr/bin/env bash
# Build the public site locally and deploy to Vercel as static.
# No cloud API needed — builds from local Postgres.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SITE_DIR="$PROJECT_DIR/web/public-site"
API_PORT=8099

echo "=== Glad Labs: Local Build & Deploy ==="

# 1. Start a temporary local coordinator API
echo "[1/4] Starting temporary API server on port $API_PORT..."
cd "$PROJECT_DIR/src/cofounder_agent"

# Use the local venv if available, otherwise system python
PYTHON="${PROJECT_DIR}/src/cofounder_agent/.venv/Scripts/python.exe"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python"
fi

DATABASE_URL="postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain" \
DEPLOYMENT_MODE=coordinator \
ENVIRONMENT=production \
PORT=$API_PORT \
DEVELOPMENT_MODE=true \
"$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port $API_PORT &
API_PID=$!

# Wait for API to be ready
echo "    Waiting for API..."
for i in $(seq 1 30); do
    if curl -s "http://localhost:$API_PORT/api/health" > /dev/null 2>&1; then
        echo "    API ready!"
        break
    fi
    sleep 1
done

# Verify posts are served
POST_COUNT=$(curl -s "http://localhost:$API_PORT/api/posts?limit=1" 2>/dev/null | python -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('posts',d.get('items',[]))))" 2>/dev/null || echo "0")
echo "    Posts available: $POST_COUNT"

if [ "$POST_COUNT" = "0" ]; then
    echo "ERROR: No posts served. Aborting."
    kill $API_PID 2>/dev/null
    exit 1
fi

# 2. Build the Next.js site
echo "[2/4] Building Next.js site..."
cd "$SITE_DIR"
NEXT_PUBLIC_API_BASE_URL="http://localhost:$API_PORT" \
NEXT_PUBLIC_FASTAPI_URL="http://localhost:$API_PORT" \
NEXT_PUBLIC_SITE_URL="https://www.gladlabs.io" \
npm run build

# 3. Deploy to Vercel
echo "[3/4] Deploying to Vercel..."
npx vercel deploy --prebuilt --prod

# 4. Cleanup
echo "[4/4] Stopping temporary API server..."
kill $API_PID 2>/dev/null || true

echo "=== Deploy complete! ==="

#!/usr/bin/env bash
# Build the public site and deploy to Vercel.
#
# Vercel rebuilds on its servers using NEXT_PUBLIC_API_BASE_URL from Vercel env vars.
# The API must be publicly reachable during the build (currently Railway).
#
# TODO: Switch to local prebuilt deploy once vercel build works on Windows
#       (currently fails with "Unable to find lambda" trace errors).
#
# Start all:  docker compose -f docker-compose.local.yml up -d
# Deploy:     bash scripts/build-and-deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SITE_DIR="$PROJECT_DIR/web/public-site"
COORDINATOR_CONTAINER=""

# Cleanup on exit
cleanup() {
    if [ -n "$COORDINATOR_CONTAINER" ]; then
        echo "[cleanup] Removing temporary coordinator container..."
        docker rm -f "$COORDINATOR_CONTAINER" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "=== Glad Labs: Build & Deploy ==="

# 1. Verify API is reachable (Vercel needs this during build)
echo "[1/4] Checking API availability..."

# Get the Vercel env var value (what Vercel will use during build)
API_URL=$(cd "$PROJECT_DIR" && npx vercel env pull --yes --environment production /dev/stdout 2>/dev/null | grep NEXT_PUBLIC_API_BASE_URL | cut -d= -f2- || echo "")

if [ -z "$API_URL" ]; then
    echo "WARNING: NEXT_PUBLIC_API_BASE_URL not set in Vercel env vars."
    echo "         Vercel build will fail if it can't fetch posts."
    echo "         Set it with: npx vercel env add NEXT_PUBLIC_API_BASE_URL production"
    exit 1
fi

echo "    API URL: $API_URL"
if ! curl -s "$API_URL/api/health" > /dev/null 2>&1; then
    echo "WARNING: API at $API_URL is not responding."
    echo "         Vercel build may fail."
    read -p "    Continue anyway? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
fi

# Verify posts are served
POST_COUNT=$(curl -s "$API_URL/api/posts?limit=1" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('posts',d.get('items',[]))))" 2>/dev/null || echo "0")
echo "    Posts available: $POST_COUNT"

if [ "$POST_COUNT" = "0" ]; then
    echo "ERROR: No posts served from $API_URL"
    exit 1
fi

# 2. Generate static feeds (RSS + sitemap) from local DB
echo "[2/6] Generating static feed.xml and sitemap.xml..."
DATABASE_URL="postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain" \
python3 "$PROJECT_DIR/scripts/generate-static-feeds.py"

# 3. Copy media files (podcast + video) into public/ for static serving
echo "[3/6] Syncing media files to public/media/..."
MEDIA_DIR="$SITE_DIR/public/media"
PODCAST_SRC="$HOME/.gladlabs/podcast"
VIDEO_SRC="$HOME/.gladlabs/video"

mkdir -p "$MEDIA_DIR/podcast" "$MEDIA_DIR/video"

# Copy only real episode files (UUID-named, skip test files)
PODCAST_COUNT=0
if [ -d "$PODCAST_SRC" ]; then
    for f in "$PODCAST_SRC"/*.mp3; do
        [ -f "$f" ] || continue
        base=$(basename "$f")
        # Skip test/jingle files, only copy UUID-named episodes
        if echo "$base" | grep -qE '^[0-9a-f]{8}-'; then
            cp "$f" "$MEDIA_DIR/podcast/$base"
            PODCAST_COUNT=$((PODCAST_COUNT + 1))
        fi
    done
fi
echo "    Podcast episodes: $PODCAST_COUNT"

VIDEO_COUNT=0
if [ -d "$VIDEO_SRC" ]; then
    for f in "$VIDEO_SRC"/*.mp4; do
        [ -f "$f" ] || continue
        cp "$f" "$MEDIA_DIR/video/$(basename "$f")"
        VIDEO_COUNT=$((VIDEO_COUNT + 1))
    done
fi
echo "    Video episodes: $VIDEO_COUNT"

MEDIA_SIZE=$(du -sh "$MEDIA_DIR" 2>/dev/null | cut -f1)
echo "    Total media: $MEDIA_SIZE"

# 4. Deploy to Vercel (builds on Vercel's servers using their env vars)
echo "[4/6] Deploying to Vercel (server-side build)..."
cd "$PROJECT_DIR"
npx vercel deploy --prod --archive=tgz

# 5. Clean up media files (not committed to git)
echo "[5/6] Cleaning up local media copy..."
rm -rf "$MEDIA_DIR"

# 6. Verify deployment
echo "[6/6] Verifying deployment..."
sleep 5
if curl -sL "https://www.gladlabs.io" | grep -q "href=\"/posts/"; then
    echo "    Homepage verified — posts are rendering!"
else
    echo "WARNING: Homepage may not be rendering posts correctly."
    echo "         Check https://www.gladlabs.io manually."
fi

echo "=== Deploy complete! ==="

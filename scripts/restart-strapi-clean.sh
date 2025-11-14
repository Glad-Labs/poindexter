#!/bin/bash

# Comprehensive Strapi Restart Script
# This script properly shuts down and restarts Strapi with all caches cleared

set -e

STRAPI_DIR="/c/Users/mattm/glad-labs-website/cms/strapi-main"
MAIN_DIR="/c/Users/mattm/glad-labs-website"

echo "=== Strapi Clean Restart Script ==="
echo ""

# Step 1: Kill any existing node processes
echo "Step 1: Killing any existing Strapi/Node processes..."
ps aux | grep "npm run develop\|strapi" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null || true
sleep 2
echo "✓ Processes terminated"
echo ""

# Step 2: Verify port is free
echo "Step 2: Verifying port 1337 is free..."
if netstat -ano | grep -q ":1337.*LISTENING"; then
    echo "⚠️  Port 1337 still in use, waiting..."
    sleep 3
else
    echo "✓ Port 1337 is free"
fi
echo ""

# Step 3: Clear Strapi caches
echo "Step 3: Clearing Strapi caches..."
cd "$STRAPI_DIR"
rm -rf .strapi .cache build node_modules/.cache .next dist __pycache__ 2>/dev/null || true
echo "✓ Caches cleared"
echo ""

# Step 4: Build Strapi
echo "Step 4: Building Strapi..."
cd "$STRAPI_DIR"
npm run build
echo "✓ Build complete"
echo ""

# Step 5: Start Strapi
echo "Step 5: Starting Strapi in develop mode..."
cd "$STRAPI_DIR"
npm run develop &
STRAPI_PID=$!
echo "✓ Strapi starting (PID: $STRAPI_PID)"
echo ""

# Step 6: Wait for Strapi to be ready
echo "Step 6: Waiting for Strapi to initialize..."
sleep 10
echo ""

# Step 7: Check if Strapi is responsive
echo "Step 7: Checking Strapi status..."
if curl -s http://localhost:1337/admin > /dev/null 2>&1; then
    echo "✓ Strapi is responding at http://localhost:1337/admin"
else
    echo "⚠️  Strapi is still initializing, please wait a few more seconds..."
fi
echo ""

echo "=== Strapi Restart Complete ==="
echo "Access Strapi admin at: http://localhost:1337/admin"
echo ""

# Keep script running so process stays alive
wait

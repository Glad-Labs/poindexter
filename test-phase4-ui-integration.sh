#!/bin/bash
# Quick Test Script for Phase 4 UI Integration
# Run this in your terminal to verify the implementation

set -e

echo "═══════════════════════════════════════════════════════════"
echo "  Phase 4 UI Integration - Quick Test Suite"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status
print_status() {
  if [ $1 -eq 0 ]; then
    echo -e "${GREEN}✓ $2${NC}"
  else
    echo -e "${RED}✗ $2${NC}"
  fi
}

# Check if services are running
echo "Checking service status..."
echo ""

# Test Backend API
echo "1. Testing Backend (port 8000)..."
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
  print_status 0 "Backend is running on port 8000"
else
  print_status 1 "Backend is NOT running. Start with: npm run dev:cofounder"
  echo "   If already running, verify port 8000 is accessible"
fi
echo ""

# Test Phase 4 Endpoints
echo "2. Testing Phase 4 API Endpoints..."
echo ""

echo "   Testing /api/agents/list..."
if curl -s http://localhost:8000/api/agents/list | grep -q "content_service"; then
  print_status 0 "/api/agents/list returns agents"
else
  print_status 1 "/api/agents/list failed or no agents"
fi

echo "   Testing /api/services..."
if curl -s http://localhost:8000/api/services | grep -q '"name"'; then
  print_status 0 "/api/services endpoint working"
else
  print_status 1 "/api/services endpoint failed"
fi

echo "   Testing /api/workflows/templates..."
if curl -s http://localhost:8000/api/workflows/templates 2>/dev/null > /dev/null; then
  print_status 0 "/api/workflows/templates endpoint responding"
else
  print_status 1 "/api/workflows/templates endpoint failed"
fi

echo ""

# Check Frontend Files
echo "3. Checking Frontend Implementation Files..."
echo ""

FRONTEND_PATH="web/oversight-hub/src"

if [ -f "$FRONTEND_PATH/services/phase4Client.js" ]; then
  print_status 0 "phase4Client.js exists"
else
  print_status 1 "phase4Client.js NOT FOUND"
fi

if [ -f "$FRONTEND_PATH/services/orchestratorAdapter.js" ]; then
  print_status 0 "orchestratorAdapter.js exists"
else
  print_status 1 "orchestratorAdapter.js NOT FOUND"
fi

if [ -f "$FRONTEND_PATH/components/pages/UnifiedServicesPanel.jsx" ]; then
  print_status 0 "UnifiedServicesPanel.jsx exists"
else
  print_status 1 "UnifiedServicesPanel.jsx NOT FOUND"
fi

if [ -f "$FRONTEND_PATH/styles/UnifiedServicesPanel.css" ]; then
  print_status 0 "UnifiedServicesPanel.css exists"
else
  print_status 1 "UnifiedServicesPanel.css NOT FOUND"
fi

echo ""

# Check if routes updated
echo "4. Checking Route Updates..."
echo ""

if grep -q "UnifiedServicesPanel" "$FRONTEND_PATH/routes/AppRoutes.jsx" 2>/dev/null; then
  print_status 0 "UnifiedServicesPanel imported in AppRoutes.jsx"
else
  print_status 1 "UnifiedServicesPanel NOT imported in AppRoutes.jsx"
fi

if grep -q "path=\"/services\"" "$FRONTEND_PATH/routes/AppRoutes.jsx" 2>/dev/null; then
  print_status 0 "/services route registered"
else
  print_status 1 "/services route NOT registered"
fi

if grep -q "{ label: 'Services'" "$FRONTEND_PATH/components/LayoutWrapper.jsx" 2>/dev/null; then
  print_status 0 "'Services' added to navigation"
else
  print_status 1 "'Services' NOT added to navigation"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Test Summary"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "Next Steps:"
echo "1. Start all services: npm run dev"
echo "2. Open browser: http://localhost:3001"
echo "3. Login if needed"
echo "4. Click 'Services' in navigation menu"
echo "5. Verify all 4 services load (Content, Financial, Market, Compliance)"
echo ""
echo "Manual Testing Checklist:"
echo "  ☐ Services load without errors"
echo "  ☐ Service cards display correctly"
echo "  ☐ Capability filter works"
echo "  ☐ Phase filter works"
echo "  ☐ Search filter works"
echo "  ☐ Click to expand service details"
echo "  ☐ Health status shows green (operational)"
echo "  ☐ No console errors"
echo ""
echo "═══════════════════════════════════════════════════════════"

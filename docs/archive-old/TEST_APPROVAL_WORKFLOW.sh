#!/bin/bash

# Complete Approval Workflow Testing Script
# This script helps verify the entire flow from UI approval to database persistence

echo "════════════════════════════════════════════════════════════════"
echo "APPROVAL WORKFLOW TESTING GUIDE"
echo "════════════════════════════════════════════════════════════════"
echo ""

echo "✅ ENVIRONMENT CHECK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check services
echo "Checking FastAPI Backend (port 8000)..."
if curl -s http://localhost:8000/health > /dev/null; then
    echo "  ✅ Backend is running"
else
    echo "  ❌ Backend NOT running - Start with: npm run dev:cofounder"
    exit 1
fi

echo "Checking Oversight Hub UI (port 3001)..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:3001 | grep -q "200"; then
    echo "  ✅ Oversight Hub is running"
else
    echo "  ❌ UI NOT running - Start with: npm start (from web/oversight-hub)"
    exit 1
fi

echo ""
echo "📋 TESTING STEPS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "STEP 1: Open Oversight Hub"
echo "  URL: http://localhost:3001/tasks"
echo "  Look for: Task with status 'awaiting_approval'"
echo "  ACTION: Open this URL in browser NOW"
echo ""

echo "STEP 2: View Task Details"
echo "  ACTION: Click 'View Details' on any awaiting_approval task"
echo "  You should see the Content Preview panel"
echo ""

echo "STEP 3: Generate Featured Image (CRITICAL TEST)"
echo "  OPTION A (Fast - Recommended):"
echo "    - Keep 'Pexels' selected in dropdown"
echo "    - Click '🎨 Generate' button"
echo "    - Wait ~1-2 seconds"
echo "    - Image should appear with URL below it"
echo ""
echo "  OPTION B (Tests full pipeline):"
echo "    - Select 'SDXL (GPU-based)' from dropdown"
echo "    - Click '🎨 Generate' button"
echo "    - Wait 20-30 seconds"
echo "    - AI-generated image should appear"
echo ""
echo "  ACTION: Choose one and generate an image"
echo ""

echo "STEP 4: Fill Approval Form"
echo "  - Reviewer ID: dev-user"
echo "  - Feedback: 'Excellent content and image quality. Approved for publication.' (≥10 chars)"
echo "  ACTION: Fill in the form fields"
echo ""

echo "STEP 5: Click Approve"
echo "  ACTION: Click green '✅ Approve' button"
echo "  ACTION: Watch for success message (should appear immediately)"
echo ""

echo "STEP 6: Check Backend Logs (IMPORTANT)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Look in the terminal where backend is running for:"
echo ""
echo "  MESSAGE 1 - 'COMPLETE POST DATA BEFORE INSERT'"
echo "  Should show:"
echo "    - featured_image_url: https://images.pexels.com/... (NOT EMPTY)"
echo "    - seo_title: [Your post title]"
echo "    - seo_description: [description text]"
echo "    - seo_keywords: [keywords]"
echo ""
echo "  MESSAGE 2 - 'INSERTING POST WITH THESE VALUES'"
echo "  Should show same non-empty values"
echo ""
echo "  ACTION: Copy these log messages and paste them below"
echo ""

echo "STEP 7: Verify Database"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Run this command to verify the database:"
echo ""
cat << 'EOF'
psql -U postgres -d glad_labs_dev -c "
SELECT 
  title,
  featured_image_url,
  seo_title,
  seo_description,
  seo_keywords,
  status,
  created_at
FROM posts
ORDER BY created_at DESC
LIMIT 5;"
EOF
echo ""
echo "Expected results:"
echo "  ✅ featured_image_url = Pexels/SDXL URL (NOT NULL)"
echo "  ✅ seo_title = Your post title (NOT NULL)"
echo "  ✅ seo_description = Description (NOT NULL)"
echo "  ✅ seo_keywords = Keywords (NOT NULL)"
echo "  ✅ status = published"
echo ""

echo "════════════════════════════════════════════════════════════════"
echo "READY TO TEST?"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "1. Open http://localhost:3001/tasks in your browser"
echo "2. Follow the steps above"
echo "3. Report back with:"
echo "   - Backend log messages"
echo "   - Database query results"
echo "   - Any error messages"
echo ""

#!/bin/bash
# Test Task Metadata Fix - End to End Verification
# This script helps verify that task metadata (style, tone, models) is properly saved

echo "=================================================="
echo "Task Metadata Fix - Testing Guide"
echo "=================================================="
echo ""

# Step 1: Check if services are running
echo "Step 1: Checking service status..."
echo ""

if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Backend (port 8000): RUNNING"
else
    echo "❌ Backend (port 8000): NOT RUNNING"
    echo "   Start with: npm run dev:cofounder"
fi

if curl -s http://localhost:3001 > /dev/null 2>&1; then
    echo "✅ Oversight Hub (port 3001): RUNNING"
else
    echo "❌ Oversight Hub (port 3001): NOT RUNNING"
    echo "   Start with: npm run dev:oversight"
fi

echo ""
echo "=================================================="
echo "Step 2: Create a Test Task"
echo "=================================================="
echo ""
echo "1. Open: http://localhost:3001"
echo "2. Click 'Create Task' button"
echo "3. Select 'Blog Post'"
echo "4. Fill in the form:"
echo "   - Topic: 'Testing Task Metadata Fix - March 7 2026'"
echo "   - Word Count: 1500"
echo "   - Writing Style: Select 'narrative' (NOT technical)"
echo "   - Tone: Select 'casual' (NOT professional)"
echo ""
echo "5. In Model Selection Panel:"
echo "   - Select 'Quality' preference OR"
echo "   - Manually configure phase models"
echo ""
echo "6. Click 'Create Task'"
echo ""
echo "=================================================="
echo "Step 3: Monitor Logs"
echo "=================================================="
echo ""
echo "Backend Logs to Watch For:"
echo "📥 [BLOG_POST] Incoming request:"
echo "   style: narrative"
echo "   tone: casual"
echo "   models_by_phase: {...}"
echo ""
echo "Browser Console to Watch For:"
echo "📤 [CreateTaskModal] Final payload: {...}"
echo "   style should NOT be 'technical'"
echo "   tone should NOT be 'professional'"
echo ""
echo "=================================================="
echo "Step 4: Run Diagnostic"
echo "=================================================="
echo ""
echo "After creating the task, run:"
echo "  python scripts/diagnose_metadata_flow.py"
echo ""
echo "Expected to see:"
echo "  ✅ Style: narrative (not 'technical')"
echo "  ✅ Tone: casual (not 'professional')"
echo "  ✅ Model Selections: {...} (not empty)"
echo ""
echo "=================================================="
echo "Press Enter to run diagnostic now..."
read

python scripts/diagnose_metadata_flow.py

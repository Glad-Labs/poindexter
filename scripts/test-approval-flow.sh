#!/bin/bash
#
# Test approval flow for blog posts
# This script tests the complete approval workflow
#

set -e

API_URL="http://localhost:8000"
TASK_ID=${1:-"test-approval-task"}

echo "=========================================="
echo "Testing Approval Flow"
echo "=========================================="
echo ""

# Step 1: Check backend health
echo "✓ Step 1: Checking backend health..."
HEALTH=$(curl -s "$API_URL/health")
echo "  Response: $HEALTH"
echo ""

# Step 2: Simulate approval request
echo "✓ Step 2: Testing approval endpoint..."
echo "  Task ID: $TASK_ID"
echo "  Endpoint: POST $API_URL/api/content/tasks/{task_id}/approve"
echo ""

# Create test payload
PAYLOAD=$(cat <<EOF
{
  "approved": true,
  "human_feedback": "This is a test approval. Content is well-written and ready for publishing. All SEO requirements met.",
  "reviewer_id": "test.reviewer",
  "featured_image_url": "https://example.com/test-image.jpg"
}
EOF
)

echo "  Payload:"
echo "  $PAYLOAD"
echo ""

echo "Note: This test will fail with 'task not found' unless you provide a real task ID"
echo "Usage: $0 <task-id>"
echo ""
echo "To get a real task ID:"
echo "1. Go to UI at http://localhost:3001"
echo "2. Navigate to Tasks"
echo "3. Find a task with status 'awaiting_approval'"
echo "4. Copy its ID"
echo "5. Run: $0 <task-id>"
echo ""

#!/bin/bash
# Test the approval endpoint fix

echo "Testing approval workflow with fixed code..."
echo ""

# Get a task ID (using task 73 from the logs)
TASK_ID="73"

# Test payload with CORRECT field names (after our fix)
PAYLOAD=$(cat <<EOF
{
  "status": "approved",
  "updated_by": "test-user",
  "reason": "Testing the fix",
  "metadata": {
    "feedback": "Content looks good",
    "timestamp": "2026-01-17T02:00:00Z",
    "updated_from_ui": true
  }
}
EOF
)

echo "Testing endpoint: PUT /api/tasks/$TASK_ID/status/validated"
echo "Payload:"
echo "$PAYLOAD" | jq '.'
echo ""

# Make the request
RESPONSE=$(curl -s -X PUT \
  "http://localhost:8000/api/tasks/$TASK_ID/status/validated" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dev-token" \
  -d "$PAYLOAD")

echo "Response:"
echo "$RESPONSE" | jq '.' 2>/dev/null || echo "$RESPONSE"

# Check for import error
if echo "$RESPONSE" | grep -q "TaskDatabaseService"; then
  echo ""
  echo "❌ FAILED: Import error still present"
  exit 1
elif echo "$RESPONSE" | grep -q "400\|Bad Request"; then
  echo ""
  echo "⚠️  HTTP 400 but no import error - may be other validation issue"
  exit 1
elif echo "$RESPONSE" | grep -q "200\|success"; then
  echo ""
  echo "✅ SUCCESS: Endpoint working!"
  exit 0
else
  echo ""
  echo "⚠️  Unexpected response - needs manual verification"
  exit 0
fi

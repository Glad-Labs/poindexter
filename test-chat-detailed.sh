#!/bin/bash
# Detailed Chat API Testing Script
# Tests different chat scenarios and providers

API_BASE="${API_BASE:-http://localhost:8000}"
CONVERSATION_ID="chat-test-$(date +%s%N)"

echo "=== Chat API Testing ==="
echo "API Base: $API_BASE"
echo "Conversation ID: $CONVERSATION_ID"
echo ""

# Test function
test_chat() {
    local message=$1
    local model=$2
    local temp=$3
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Message: \"$message\""
    echo "Model: $model"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    RESPONSE=$(curl -s -X POST "$API_BASE/api/chat" \
      -H "Content-Type: application/json" \
      -d "{
        \"message\": \"$message\",
        \"model\": \"$model\",
        \"conversationId\": \"$CONVERSATION_ID\",
        \"temperature\": ${temp:-0.7},
        \"max_tokens\": 500
      }")
    
    # Pretty print response
    if echo "$RESPONSE" | jq . >/dev/null 2>&1; then
        echo "$RESPONSE" | jq '.'
    else
        echo "Response (raw): $RESPONSE"
    fi
    
    echo ""
}

# Test 1: Simple fact question (Ollama)
test_chat "What is the capital of France?" "ollama-llama2" "0.3"

# Test 2: Creative response (higher temperature)
test_chat "Tell me a short story about a robot learning to cook" "ollama-llama2" "0.9"

# Test 3: Code generation attempt
test_chat "Write a Python function that adds two numbers" "ollama-llama2" "0.5"

# Test 4: Multi-turn context
test_chat "I mentioned France earlier. What is its official language?" "ollama-llama2" "0.7"

echo "=== Test Complete ==="
echo ""
echo "To test other providers, ensure API keys are set in .env.local:"
echo "  OPENAI_API_KEY=sk-..."
echo "  ANTHROPIC_API_KEY=sk-ant-..."
echo "  GOOGLE_API_KEY=AIza-..."
echo ""
echo "Then try:"
echo "  test_chat 'Your question' 'openai'"
echo "  test_chat 'Your question' 'claude'"
echo "  test_chat 'Your question' 'gemini'"

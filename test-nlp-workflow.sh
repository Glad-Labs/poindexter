#!/bin/bash
# Quick NLP Agent Workflow Test Script
# Tests the chat and NLP composition endpoints

set -e

API_BASE="http://localhost:8000"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🧪 NLP Agent Workflow Test Suite${NC}\n"

# Test 1: Health Check
echo -e "${YELLOW}[1/5] Testing Backend Health...${NC}"
HEALTH=$(curl -s http://localhost:8000/health)
if echo "$HEALTH" | grep -q "ok"; then
    echo -e "${GREEN}✅ Backend is healthy${NC}"
else
    echo -e "${RED}❌ Backend health check failed${NC}"
    echo "Response: $HEALTH"
    exit 1
fi

# Test 2: Model Health
echo -e "\n${YELLOW}[2/5] Testing Model Router...${NC}"
MODELS=$(curl -s http://localhost:8000/api/models/health)
if echo "$MODELS" | grep -q "status"; then
    echo -e "${GREEN}✅ Model router responding${NC}"
    echo "Available providers:"
    echo "$MODELS" | jq '.components // {}' 2>/dev/null || echo "$MODELS"
else
    echo -e "${YELLOW}⚠️  Could not get model details${NC}"
fi

# Test 3: Chat Endpoint (Simple)
echo -e "\n${YELLOW}[3/5] Testing Chat Endpoint (Ollama)...${NC}"
CHAT_RESPONSE=$(curl -s -X POST "$API_BASE/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is 2+2?",
    "model": "ollama-llama2",
    "conversationId": "test-1",
    "temperature": 0.7,
    "max_tokens": 100
  }')

if echo "$CHAT_RESPONSE" | grep -q "response"; then
    echo -e "${GREEN}✅ Chat endpoint working${NC}"
    RESPONSE_TEXT=$(echo "$CHAT_RESPONSE" | jq -r '.response // .error' 2>/dev/null || echo "$CHAT_RESPONSE")
    echo "Response preview: ${RESPONSE_TEXT:0:80}..."
else
    echo -e "${RED}❌ Chat endpoint failed${NC}"
    echo "Response: $CHAT_RESPONSE"
fi

# Test 4: Multi-turn Conversation
echo -e "\n${YELLOW}[4/5] Testing Conversation History...${NC}"
CONV_ID="nlp-test-$(date +%s)"
MSG1=$(curl -s -X POST "$API_BASE/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Hello, what is your name?\",
    \"model\": \"ollama-llama2\",
    \"conversationId\": \"$CONV_ID\"
  }")

MSG2=$(curl -s -X POST "$API_BASE/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Tell me about AI\",
    \"model\": \"ollama-llama2\",
    \"conversationId\": \"$CONV_ID\"
  }")

if echo "$MSG1" | grep -q "response" && echo "$MSG2" | grep -q "response"; then
    echo -e "${GREEN}✅ Multi-turn conversation working${NC}"
else
    echo -e "${YELLOW}⚠️  Could not verify conversation history${NC}"
fi

# Test 5: Check Frontend Connection
echo -e "\n${YELLOW}[5/5] Testing Frontend Connectivity...${NC}"
if curl -s http://localhost:3001 > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Oversight Hub UI is accessible${NC}"
else
    echo -e "${YELLOW}⚠️  Oversight Hub not responding on port 3001${NC}"
    echo "   Ensure: npm run dev:oversight is running"
fi

echo -e "\n${GREEN}✅ Test suite complete!${NC}\n"

echo "Next steps:"
echo "1. Open http://localhost:3001 in your browser"
echo "2. Navigate to the Orchestrator or Natural Language Composer"
echo "3. Try: 'Write a blog post about AI safety'"
echo "4. Monitor backend logs: npm run dev:cofounder"

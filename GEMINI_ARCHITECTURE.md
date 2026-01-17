# Gemini Testing Architecture & Flow

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Oversight Hub (React)                        │
│                    http://localhost:3001                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Model Selector Dropdown                                 │   │
│  │  ┌─────────────────────────────────────┐                 │   │
│  │  │ ☁️ gemini-1.5-pro                    │ ← SELECT HERE  │   │
│  │  │ ☁️ gemini-1.5-flash                  │                │   │
│  │  │ 🖥️ mistral:latest (ollama)           │                │   │
│  │  │ 🧠 claude-3-opus (anthropic)         │                │   │
│  │  │ ⚡ gpt-4-turbo (openai)              │                │   │
│  │  └─────────────────────────────────────┘                 │   │
│  │                    ↓                                       │   │
│  │  Chat Input: "What is your model name?"                  │   │
│  │                    ↓                                       │   │
│  │  [SEND] Button                                            │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────┬──────────────────────────────────────────┘
                      │
                      │ POST /api/chat
                      │ model: "gemini-1.5-pro"
                      │ message: "What is your model name?"
                      │
                      ↓
┌─────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (Python)                        │
│                 http://localhost:8000                            │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Chat Route Handler                                      │   │
│  │  POST /api/chat                                          │   │
│  │                                                           │   │
│  │  1. Parse request (model, message, conversationId)       │   │
│  │                    ↓                                      │   │
│  │  2. Model Router: route_request()                        │   │
│  │     Check: provider = "google"                           │   │
│  │                    ↓                                      │   │
│  │  3. Initialize: GoogleGenerativeAI(api_key)              │   │
│  │                    ↓                                      │   │
│  │  4. Send to Gemini API:                                  │   │
│  │     model.generate_content(message)                      │   │
│  │                    ↓                                      │   │
│  │  5. Parse response                                       │   │
│  │     Extract: response_text, tokens_used, timestamp       │   │
│  │                    ↓                                      │   │
│  │  6. Store in PostgreSQL:                                 │   │
│  │     - Conversation history                               │   │
│  │     - Model metadata                                     │   │
│  │     - Tokens used                                        │   │
│  │                    ↓                                      │   │
│  │  7. Return ChatResponse(...)                             │   │
│  │     provider: "google"                                   │   │
│  │     model: "gemini-1.5-pro"                              │   │
│  │     response: "I'm Gemini..."                            │   │
│  │     tokens_used: 42                                      │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────┬──────────────────────────────────────────┘
                      │
                      │ HTTPS to Google API
                      │ https://generativelanguage.googleapis.com
                      │
                      ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Google Gemini API                             │
│              (Cloud Endpoints)                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Models Available:                                       │   │
│  │  • gemini-1.5-pro (recommended)                         │   │
│  │  • gemini-1.5-flash (faster)                            │   │
│  │  • gemini-pro (legacy)                                  │   │
│  │  • gemini-pro-vision (multimodal)                       │   │
│  │                                                           │   │
│  │  Request:                                                │   │
│  │  {                                                        │   │
│  │    "contents": [{                                         │   │
│  │      "parts": [{"text": "What is your model name?"}]     │   │
│  │    }]                                                     │   │
│  │  }                                                        │   │
│  │                                                           │   │
│  │  Response:                                               │   │
│  │  {                                                        │   │
│  │    "candidates": [{                                       │   │
│  │      "content": {                                         │   │
│  │        "parts": [{"text": "I'm Gemini..."}]              │   │
│  │      },                                                   │   │
│  │      "finishReason": "STOP"                              │   │
│  │    }],                                                    │   │
│  │    "usageMetadata": {                                     │   │
│  │      "promptTokens": 10,                                 │   │
│  │      "candidatesTokens": 32                              │   │
│  │    }                                                      │   │
│  │  }                                                        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Request Flow Sequence

```
┌─────────────────┐
│  Oversight Hub  │
│  (UI)           │
└────────┬────────┘
         │
         │ 1. User selects "gemini-1.5-pro"
         │ 2. User types message
         │ 3. User clicks [SEND]
         │
         ↓
    JSON Request
    POST /api/chat
    ────────────────────────────────────────
    {
      "conversationId": "unique-id-123",
      "model": "gemini-1.5-pro",
      "message": "What is your model name?"
    }
         │
         ↓
┌─────────────────────────────────────────┐
│    FastAPI Backend Routes               │
│    /routes/chat_routes.py                │
└──────────┬────────────────────────────────┘
           │
           │ Route: POST /api/chat
           │ Handler: send_message()
           │
           ↓
┌─────────────────────────────────────────┐
│  1. VALIDATE REQUEST                    │
│  - Check model name valid               │
│  - Check conversationId provided        │
│  - Check message not empty              │
└──────────┬────────────────────────────────┘
           │
           ↓
┌─────────────────────────────────────────┐
│  2. SELECT PROVIDER                     │
│  ModelRouter.route_request()            │
│  - Provider detection: google           │
│  - Complexity analysis: auto            │
│  - Load model settings                  │
└──────────┬────────────────────────────────┘
           │
           ↓
┌─────────────────────────────────────────┐
│  3. INITIALIZE CLIENT                   │
│  google.generativeai.GenerativeModel()  │
│  - Load API key: GOOGLE_API_KEY         │
│  - Set model: gemini-1.5-pro            │
│  - Configure safety settings            │
└──────────┬────────────────────────────────┘
           │
           ↓
┌─────────────────────────────────────────┐
│  4. PREPARE MESSAGE                     │
│  - Build full prompt                    │
│  - Load conversation history            │
│  - Add system context                   │
│  - Set max_tokens (if applicable)       │
└──────────┬────────────────────────────────┘
           │
           ↓
    HTTPS Request
    POST https://generativelanguage.googleapis.com
    /v1beta/models/gemini-1.5-pro:generateContent
    ────────────────────────────────────────
    Headers:
      x-goog-api-key: AIzaSy...
      Content-Type: application/json

    Body:
      {
        "contents": [{
          "parts": [{"text": "What is your model name?"}]
        }],
        "generationConfig": {
          "maxOutputTokens": 1000,
          "temperature": 0.7
        },
        "safetySettings": [...]
      }
           │
           ↓
┌─────────────────────────────────────────┐
│  GOOGLE GEMINI SERVER                   │
│  - Process request                      │
│  - Generate response tokens             │
│  - Apply safety filters                 │
│  - Calculate token usage                │
└──────────┬────────────────────────────────┘
           │
           ↓
    HTTPS Response
    200 OK
    ────────────────────────────────────────
    {
      "candidates": [{
        "content": {
          "parts": [{"text": "I'm Gemini..."}]
        },
        "finishReason": "STOP"
      }],
      "usageMetadata": {
        "promptTokens": 10,
        "candidatesTokens": 32,
        "totalTokens": 42
      }
    }
           │
           ↓
┌─────────────────────────────────────────┐
│  5. PARSE RESPONSE                      │
│  - Extract response text                │
│  - Extract token count: 42              │
│  - Extract finish reason: STOP          │
│  - Generate timestamp                   │
└──────────┬────────────────────────────────┘
           │
           ↓
┌─────────────────────────────────────────┐
│  6. STORE IN DATABASE                   │
│  PostgreSQL                             │
│  - INSERT into chat_history table       │
│  - conversationId: unique-id-123        │
│  - model_used: gemini-1.5-pro           │
│  - provider: google                     │
│  - message: "What is your model..."     │
│  - response: "I'm Gemini..."            │
│  - tokens_used: 42                      │
│  - timestamp: 2026-01-16T...            │
└──────────┬────────────────────────────────┘
           │
           ↓
    JSON Response
    200 OK
    ────────────────────────────────────────
    ChatResponse {
      "response": "I'm Gemini...",
      "model": "gemini-1.5-pro",
      "provider": "google",
      "conversationId": "unique-id-123",
      "timestamp": "2026-01-16T12:34:56Z",
      "tokens_used": 42
    }
           │
           ↓
┌─────────────────┐
│  Oversight Hub  │
│  (UI)           │
│  - Display      │
│    response     │
│  - Show metadata│
│  - Update UI    │
└─────────────────┘
```

---

## Model Selection Fallback Chain

```
User selects "gemini-1.5-pro"
          │
          ↓
    Is Gemini available?
          │
      YES │ NO
          │  └─────────────────────────────────┐
          ↓                                     ↓
    Use Gemini                           Try next provider
    "provider": "google"                 in fallback chain
                                                │
                                                ↓
                                         Is HuggingFace available?
                                                │
                                            YES │ NO
                                                │  └────────────┐
                                                ↓               ↓
                                         Use HuggingFace   Try Claude
                                         "provider":       (Anthropic)
                                         "huggingface"     "provider":
                                                           "anthropic"
                                                                │
                                                            YES │ NO
                                                                │  └─────┐
                                                                ↓        ↓
                                                           Use Claude Try GPT-4
                                                                       (OpenAI)
                                                                       "provider":
                                                                       "openai"


FALLBACK CHAIN (Priority Order):
────────────────────────────────
1. 🖥️  Ollama        (Local, instant, FREE)
2. 🌐 HuggingFace   (Cheap, but slower)
3. ☁️  Gemini        (Good balance: cost/quality)
4. 🧠 Claude        (Premium: excellent)
5. ⚡ GPT-4         (Expensive: best)


What Triggers Fallback:
───────────────────────
✗ API key not configured
✗ API key invalid/expired
✗ Rate limit exceeded (429)
✗ API temporarily down (503)
✗ Network error
✗ Timeout (30+ seconds)
```

---

## Testing Workflow

```
┌──────────────────────────────────────────────────────────────┐
│                    START: Test Gemini                        │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ↓
        ┌──────────────────────────────────┐
        │  TEST 1: Environment Check       │
        │  curl $GOOGLE_API_KEY            │
        │  echo ${GOOGLE_API_KEY:0:10}     │
        └──────────┬───────────────────────┘
                   │
        PASS? │ │ FAIL?
              │ └─────→ ✗ Error: API key not set
              │                  Solution: Add to .env.local
              │
              ↓
        ┌──────────────────────────────────┐
        │  TEST 2: Backend Running         │
        │  curl http://localhost:8000      │
        │  /api/health                     │
        └──────────┬───────────────────────┘
                   │
        PASS? │ │ FAIL?
              │ └─────→ ✗ Error: Backend down
              │                  Solution: npm run dev:cofounder
              │
              ↓
        ┌──────────────────────────────────┐
        │  TEST 3: Models Available        │
        │  curl /api/v1/models/available   │
        │  | jq '.models | length'         │
        └──────────┬───────────────────────┘
                   │
        PASS? │ │ FAIL?
              │ └─────→ ✗ Error: No models
              │                  Solution: Check backend logs
              │
              ↓
        ┌──────────────────────────────────┐
        │  TEST 4: Gemini in List          │
        │  curl /api/v1/models/available   │
        │  | jq '.models[] | select       │
        │   (.provider=="google")'         │
        └──────────┬───────────────────────┘
                   │
        PASS? │ │ FAIL?
              │ └─────→ ✗ Error: No Gemini models
              │                  Solution: API key invalid
              │
              ↓
        ┌──────────────────────────────────┐
        │  TEST 5: Send Chat Message       │
        │  curl -X POST /api/chat          │
        │  -d '{"model":"gemini-1.5...'   │
        └──────────┬───────────────────────┘
                   │
        PASS? │ │ FAIL?
              │ └─────→ ✗ Error: Check jq '.provider'
              │                  (Should be "google")
              │
              ↓
        ┌──────────────────────────────────┐
        │  TEST 6: UI Test                 │
        │  1. Open http://localhost:3001   │
        │  2. Select gemini-1.5-pro        │
        │  3. Send message                 │
        │  4. Verify response              │
        └──────────┬───────────────────────┘
                   │
        PASS? │ │ FAIL?
              │ └─────→ ✗ Error: Check browser console
              │                  (F12 → Console tab)
              │
              ↓
    ┌──────────────────────────────────────────┐
    │  ✓ ALL TESTS PASSED                     │
    │                                          │
    │  Gemini is working!                     │
    │                                          │
    │  You can now:                            │
    │  - Use Gemini in Oversight Hub          │
    │  - Send messages                         │
    │  - View conversation history             │
    │  - Monitor response metadata             │
    └──────────────────────────────────────────┘
```

---

## API Endpoint Hierarchy

```
Backend: http://localhost:8000
│
├── /api/health
│   └── Status of entire system
│
├── /api/v1/models/
│   ├── /available
│   │   ├── List all models (Ollama, HuggingFace, Gemini, Claude, GPT)
│   │   └── Example: gemini-1.5-pro (☁️ provider: google)
│   │
│   ├── /status
│   │   ├── Provider availability check
│   │   └── Example: google: {available: true, models: 4}
│   │
│   └── /recommended
│       ├── Best models by cost/tier
│       └── Example: [gemini-1.5-pro, claude-opus, gpt-4]
│
├── /api/chat
│   ├── POST (send message)
│   │   ├── Input: model, conversationId, message
│   │   └── Output: response, provider, tokens_used
│   │
│   ├── GET /history/{id} (view conversation)
│   │   └── Output: messages array with history
│   │
│   └── DELETE /history/{id} (clear conversation)
│       └── Output: success confirmation
│
└── /api/docs
    └── Swagger UI (interactive API documentation)
```

---

## Storage & Persistence

```
┌────────────────────────────────────────────────────┐
│           PostgreSQL Database                      │
│         glad_labs_dev (development)                │
└────────────────────────────────────────────────────┘
            │
            ├─ TABLE: chat_history
            │  ├─ conversation_id (UUID)
            │  ├─ user_message (text)
            │  ├─ assistant_response (text)
            │  ├─ model_used (varchar)
            │  │  └─ Example: "gemini-1.5-pro"
            │  ├─ provider (varchar)
            │  │  └─ Example: "google"
            │  ├─ tokens_used (int)
            │  │  └─ Example: 42
            │  ├─ cost_estimate (float)
            │  │  └─ Calculated based on provider/model
            │  ├─ timestamp (datetime)
            │  └─ metadata (json)
            │     └─ {finish_reason, safety_ratings, etc}
            │
            ├─ TABLE: tasks
            │  ├─ task_id (UUID)
            │  ├─ model_used (varchar)
            │  ├─ status (enum)
            │  └─ result (json)
            │
            └─ TABLE: conversations
               ├─ conversation_id (UUID)
               ├─ title (varchar)
               ├─ created_at (datetime)
               ├─ updated_at (datetime)
               └─ messages_count (int)
```

---

## Monitoring & Debugging Signals

```
HEALTHY STATE:
───────────────
✓ Backend logs show: "[Chat] Using provider: google"
✓ Response includes: "provider": "google"
✓ Response time: 1-3 seconds
✓ UI shows Gemini in dropdown with ☁️ icon
✓ No CORS errors in browser console
✓ Database stores conversation history


WARNING STATE:
──────────────
⚠️ Response time: 5-10 seconds (slow network or API lag)
⚠️ Some fallback happening (not using primary model)
⚠️ Rate limit warnings in logs
⚠️ Some API keys not configured (using reduced model set)


ERROR STATE:
────────────
✗ Response shows wrong provider (not "google")
✗ "provider": "anthropic" or "openai" (fallback engaged)
✗ Error in chat response (timeout, 429, 503)
✗ Model not in dropdown
✗ CORS errors in browser console
✗ Backend logs: "[ERROR] Gemini API failed"


DEBUG SIGNALS:
──────────────
🔍 Check: Backend logs for provider selection
🔍 Check: Browser DevTools → Network tab → /api/chat response
🔍 Check: Database with: SELECT * FROM chat_history WHERE provider='google'
🔍 Check: API key validity at https://aistudio.google.com/app/apikey
```

---

## Performance Expectations

```
Model: gemini-1.5-pro
─────────────────────

Response Time by Task:
  Simple greeting:        1-2 seconds
  3-sentence summary:     2-3 seconds
  Code generation:        3-4 seconds
  Analysis task:          3-5 seconds
  Complex reasoning:      4-6 seconds
  Network latency only:   + 1-2 seconds

Token Usage by Task:
  Simple greeting:        20-50 tokens
  3-sentence summary:     100-150 tokens
  Code generation:        200-500 tokens
  Full page analysis:     500-1000+ tokens

Cost (Gemini 1.5 Pro):
  Input:  $0.075 per 1M tokens  (~$0.00000075 per token)
  Output: $0.30 per 1M tokens   (~$0.0000030 per token)

Example Cost Calculation:
  Message 1: 10 input tokens + 32 output tokens
  Cost = (10 × $0.00000075) + (32 × $0.0000030)
  Cost ≈ $0.000105 (0.0001 cents)

Monthly Estimate (100 messages/day):
  ≈ $0.31/month


Comparison to Other Models:
─────────────────────────────
Ollama (local):    FREE    ✓ (instant, no internet)
HuggingFace:       FREE    ✓ (free tier, rate limited)
Gemini 1.5 Pro:    $0.31   ✓ (monthly estimate)
Claude 3 Opus:     $3.00   (monthly estimate)
GPT-4 Turbo:       $5.00   (monthly estimate)
```

---

**Status:** ✅ Ready for Testing  
**Last Updated:** January 16, 2026  
**Backend:** http://localhost:8000  
**Frontend:** http://localhost:3001

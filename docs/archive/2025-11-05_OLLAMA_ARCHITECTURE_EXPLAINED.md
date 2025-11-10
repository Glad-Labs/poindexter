# 🧠 Ollama Architecture: Chat & Blog Generation

**Last Updated:** November 2, 2025  
**Overview:** How Glad Labs uses Ollama for both real-time chat and async blog post generation

---

## 📊 High-Level Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                    OVERSIGHT HUB (Frontend)                     │
│                                                                 │
│  Chat Interface                    Blog Generator UI             │
│  (Real-time messages)              (Create blog posts)           │
│  └─ Send message                   └─ Enter topic, style, etc   │
└─────────────────────┬───────────────────────────────┬───────────┘
                      │                               │
                      │ HTTP POST                     │ HTTP POST
                      ▼                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   COFOUNDER AGENT (FastAPI Backend)             │
│                                                                 │
│  POST /api/chat              POST /api/content/blog-posts       │
│  └─ Immediate response       └─ Returns task ID immediately    │
│     (sync)                      (async in background)           │
└─────────────────┬───────────────────────────────┬───────────────┘
                  │                               │
                  └────────────┬──────────────────┘
                               │
                               ▼
                  ┌────────────────────────────┐
                  │   OLLAMA (Local, Free)     │
                  │                            │
                  │ http://localhost:11434     │
                  │                            │
                  │ Available Models:          │
                  │ - mistral:latest (default) │
                  │ - llama2:latest            │
                  │ - neural-chat:latest       │
                  │ - qwen2.5:14b              │
                  │ - ... 12 more models       │
                  └────────────────────────────┘
```

---

## 🔄 Comparison: Chat vs Blog Generation

### Chat (Real-Time, Synchronous)

```
FLOW:
Frontend → POST /api/chat {message, model: "ollama"} → Backend

Backend:
1. Receives request immediately
2. Calls OllamaClient.chat()
3. Waits for response (BLOCKS request)
4. Returns response to frontend

Characteristics:
├─ Synchronous: User waits for response
├─ Fast: Models run instantly on local GPU (RTX 5070)
├─ Typical latency: 1-5 seconds (depends on model size)
├─ Model used: llama2 (default, good balance)
├─ Response size: 500 tokens max
└─ UI: Shows spinner, then response appears
```

**Code Location:** `src/cofounder_agent/routes/chat_routes.py`

```python
@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    # Model selection
    if request.model == "ollama":
        actual_ollama_model = "llama2"  # Sync with Ollama

        # Direct call - BLOCKS until response
        chat_result = await ollama_client.chat(
            messages=conversations[request.conversationId],
            model=actual_ollama_model,
            temperature=request.temperature or 0.7,
            max_tokens=request.max_tokens or 500
        )
        response_text = chat_result.get("content", "")

    return ChatResponse(response=response_text, model=request.model, ...)
```

---

### Blog Generation (Async, Background Task)

```
FLOW:
Frontend → POST /api/content/blog-posts {topic, style, etc} → Backend

Backend:
1. Validates request
2. Creates task ID (UUID)
3. Returns task ID IMMEDIATELY to frontend
4. Spawns BACKGROUND task (non-blocking)
5. Frontend polls for status

Background Task:
1. Generates detailed prompt (1500+ words)
2. Calls Ollama (BLOCKS in background)
3. Stores result in task_store
4. Frontend displays "Complete!" when done

Characteristics:
├─ Asynchronous: Returns immediately with task_id
├─ Long-running: Can take 1-10 minutes (depends on topic length)
├─ Model used: mistral:latest (default)
├─ Response size: 1500-5000 tokens
├─ Status tracking: Frontend polls /api/content/blog-posts/tasks/{task_id}
└─ UI: Shows "Generating..." with progress spinner until complete
```

**Code Location:** `src/cofounder_agent/routes/content_generation.py`

```python
@router.post("/blog-posts", response_model=GenerateBlogPostResponse)
async def create_blog_post(
    request: GenerateBlogPostRequest,
    background_tasks: BackgroundTasks
):
    # Create task ID
    task_id = str(uuid.uuid4())

    # Store initial task status
    task_store[task_id] = {
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }

    # IMPORTANT: Add background task and return IMMEDIATELY
    # This doesn't block the frontend
    background_tasks.add_task(generate_post_background, task_id, request)

    return GenerateBlogPostResponse(
        task_id=task_id,
        status="pending",
        message="Blog generation started..."
    )

# Background task runs independently
async def generate_post_background(task_id: str, request: GenerateBlogPostRequest):
    """
    This runs in the BACKGROUND while frontend polls for status.
    The request/response cycle completes immediately.
    This function can take minutes without blocking the frontend.
    """
    try:
        task_store[task_id]["status"] = "processing"

        # Generate detailed prompt
        prompt = generate_blog_post_prompt(...)

        # Call Ollama (this BLOCKS but it's in background thread)
        content = await call_ollama(prompt)  # Calls mistral:latest

        # Store result
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {...}

    except Exception as e:
        task_store[task_id]["status"] = "error"
        task_store[task_id]["error"] = str(e)
```

---

## 🎯 Key Design Decisions

### 1. **Why Synchronous Chat?**

✅ **Pro:** Immediate user feedback, conversational flow
✅ **Pro:** Low latency on local GPU
❌ **Con:** Blocks request thread during LLM call

**Solution:** Works fine because:

- Ollama is LOCAL (fast GPU access, no network latency)
- Default model (llama2) is relatively small (7B)
- FastAPI uses async, so other requests aren't blocked
- Chat responses limited to 500 tokens (shorter = faster)

**Typical Performance:**

```
Small prompt (< 100 tokens) → 1-2 seconds
Medium prompt (100-300 tokens) → 2-5 seconds
Large prompt (> 300 tokens) → 5-10 seconds
```

---

### 2. **Why Asynchronous Blog Generation?**

✅ **Pro:** Returns immediately (no waiting)
✅ **Pro:** Can handle long-running tasks
✅ **Pro:** Multiple posts can generate in parallel
❌ **Con:** Requires frontend polling

**Solution:** Works great because:

- Blog posts are 1500+ words (takes minutes)
- User doesn't wait for response
- Frontend polls status endpoint every 2-5 seconds
- Multiple blog posts can queue and process sequentially

**Typical Performance:**

```
1500-word blog post → 2-5 minutes (depends on topic complexity)
2000-word blog post → 3-8 minutes
Multiple posts → Sequential (one at a time)
```

---

### 3. **Why Different Models?**

| Task     | Model        | Reason                                    |
| -------- | ------------ | ----------------------------------------- |
| **Chat** | llama2 (7B)  | ✅ Fast ✅ Good quality ✅ Conversational |
| **Blog** | mistral (7B) | ✅ Excellent writing ✅ Creative ✅ Fast  |

**Could we use the same model?** Yes! But mistral is slightly slower for chat but better for longer content.

---

## 🚀 Concurrent Handling

### Can Ollama Handle Simultaneous Requests?

```
Scenario 1: User A chats while User B generates blog
├─ Chat request (sync, 2s) → Ollama
└─ Blog request (async background) → Ollama (queued)

Result: ✅ Both work!
- Chat response: 2 seconds (smaller request)
- Blog generation: Starts immediately but Ollama processes sequentially

Scenario 2: Multiple concurrent chat requests
├─ User A: POST /api/chat → Waiting
├─ User B: POST /api/chat → Waiting
└─ Ollama processes one at a time in queue

Result: ✅ Works but slightly slower
- Each request waits for previous one to finish
- Total time: ~4 seconds each
```

### Current Limitation: No Concurrency Control

**Current State:**

- ✅ Ollama can handle 1-2 concurrent requests
- ❌ No queue management (requests just wait)
- ❌ No semaphore limiting (potential overload)
- ⚠️ High load could cause timeouts

**Example Problem:**

```
10 users send blog requests simultaneously
├─ Request 1: Starts immediately
├─ Request 2: Waits (Ollama busy)
├─ Request 3: Waits
├─ ...
├─ Request 10: Waits in backend queue
└─ Result: Request 10 could timeout after 5 minutes!

Why? Ollama can't process 10 requests in parallel.
It can run ONE at a time (or maybe 1-2 with smaller models).
```

---

## 🔧 How They Share Ollama

### Both Routes Use Same Ollama Instance

**Chat Route Flow:**

```python
# routes/chat_routes.py
ollama_client = OllamaClient()  # Shared instance

@router.post("/api/chat")
async def chat(request: ChatRequest):
    if request.model == "ollama":
        chat_result = await ollama_client.chat(
            model="llama2",  # Hardcoded to llama2
            ...
        )
```

**Blog Route Flow:**

```python
# routes/content_generation.py
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral:latest")

async def call_ollama(prompt: str) -> str:
    # Direct HTTP call to Ollama
    response = await client.post(
        f"{OLLAMA_HOST}/api/generate",
        json={"model": OLLAMA_MODEL, "prompt": prompt}
    )
```

### Connection Method

**Option A: Via OllamaClient (Chat)**

```
Chat → OllamaClient class → HTTP POST to localhost:11434
```

**Option B: Direct HTTP (Blog)**

```
Blog → httpx.AsyncClient → HTTP POST to localhost:11434
```

**Both end up here:**

```
Ollama API Endpoint: http://localhost:11434/api/generate
or: http://localhost:11434/api/chat
```

---

## 📈 Scaling Considerations

### Current Bottleneck: Single Ollama Process

```
Ollama Process (PID: ???)
├─ Can load 1-3 models in VRAM at once
├─ Can run 1-2 concurrent requests (default)
├─ Uses GPU acceleration (RTX 5070)
└─ Single point of failure
```

### How to Handle High Load

**Option 1: Multiple Ollama Instances (No Parallelism)**

```
✅ More stable
❌ Each instance uses VRAM
❌ Frontend needs routing logic
❌ Complex setup

Example: 2 Ollama instances
- Instance 1: localhost:11434 (llama2)
- Instance 2: localhost:11435 (mistral)
- Chat → Instance 1
- Blog → Instance 2
```

**Option 2: Task Queue (Recommended for Production)**

```
✅ Professional solution
✅ Single Ollama still works
✅ Better resource management
❌ Requires Redis/RabbitMQ

Example: Celery + Redis
- Frontend → FastAPI (returns immediately)
- FastAPI → Redis (enqueue task)
- Worker → Ollama (process sequentially)
- Frontend polls for status (same as now)
```

**Option 3: Ollama Server Configuration**

```
Edit Ollama settings to handle more concurrent requests:
- Set OLLAMA_NUM_PARALLEL=2 (or higher)
- Set OLLAMA_NUM_GPU=1 (or more GPUs)
- Requires restart
```

---

## 🔌 API Endpoints

### Chat (Synchronous)

```bash
POST /api/chat
{
  "message": "What is AI?",
  "model": "ollama",              # Required: "ollama" for Ollama
  "conversationId": "default",    # For multi-turn tracking
  "temperature": 0.7,
  "max_tokens": 500
}

Response (immediate):
{
  "response": "AI is artificial intelligence...",
  "model": "ollama",
  "conversationId": "default",
  "timestamp": "2025-11-02T06:00:00",
  "tokens_used": 45
}
```

### Blog Generation (Asynchronous)

```bash
# Step 1: Create task
POST /api/content/blog-posts
{
  "topic": "How to use Ollama",
  "style": "technical",
  "tone": "professional",
  "target_length": 1500,
  "tags": ["ai", "ollama"]
}

Response (immediate, < 100ms):
{
  "task_id": "abc-123-def",
  "status": "pending",
  "message": "Blog generation started..."
}

# Step 2: Poll for status
GET /api/content/blog-posts/tasks/{task_id}

Polling response (1st check - still generating):
{
  "task_id": "abc-123-def",
  "status": "processing",
  "created_at": "2025-11-02T06:00:00"
}

Polling response (final - complete):
{
  "task_id": "abc-123-def",
  "status": "completed",
  "created_at": "2025-11-02T06:00:00",
  "result": {
    "title": "How to Use Ollama Locally",
    "slug": "how-to-use-ollama-locally",
    "content": "# How to Use Ollama Locally\n\n...",
    "topic": "How to use Ollama",
    "tags": ["ai", "ollama"],
    "generated_at": "2025-11-02T06:05:00"
  }
}
```

---

## 💡 Key Insights

### 1. **Ollama is Shared Resource**

- ✅ Both chat and blog use same Ollama instance
- ✅ They talk to `http://localhost:11434` (same process)
- ⚠️ Can cause contention under high load

### 2. **Design Trade-off**

- Chat: **Sacrifices concurrency** for **immediate response**
- Blog: **Accepts async** to **allow long-running tasks**
- Overall: ✅ Great UX, ⚠️ Limited scalability

### 3. **Current Limitations**

- No concurrency control (requests queue up)
- Single Ollama process (single point of failure)
- No horizontal scaling (can't add more instances easily)
- Task storage in-memory (lost if backend restarts)

### 4. **Perfect for Development**

- ✅ Zero cost (Ollama is free)
- ✅ No API rate limits
- ✅ No internet required
- ✅ Full GPU access (fast on RTX 5070)
- ✅ Great for testing and demos

### 5. **Would Need Enhancement for Production**

- Add task queue (Redis/Celery)
- Add concurrency semaphore
- Add persistent task storage (PostgreSQL)
- Add load balancing across multiple Ollama instances
- Add health checks and auto-restart

---

## 📊 Current Flow Diagram

```
FRONTEND (http://localhost:3001)
├─ Chat Interface
│  └─ User types message
│     └─ POST /api/chat
│        └─ Backend (sync, waits)
│           └─ Ollama chat (1-2s)
│              └─ Response appears immediately ✅
│
└─ Blog Generator
   └─ User clicks "Generate"
      └─ POST /api/content/blog-posts
         └─ Backend (async, returns immediately) ✅
            └─ task_id: "abc-123"
            └─ Frontend polls /api/content/blog-posts/tasks/abc-123
               └─ Every 2-5 seconds: "Loading..." → "Loading..." → "Complete!" ✅
                  └─ In background: Ollama generating (2-5 min)

OLLAMA (http://localhost:11434) - Shared by both
├─ Process 1: Chat request (llama2)
├─ Process 2: Blog request (mistral) - waits if chat is running
└─ Sequential execution (one at a time by default)
```

---

## ✅ Summary

| Aspect               | Chat      | Blog          | Notes                                             |
| -------------------- | --------- | ------------- | ------------------------------------------------- |
| **Sync/Async**       | Sync      | Async         | Chat waits for response, Blog returns immediately |
| **Model**            | llama2    | mistral       | Different models for different tasks              |
| **Response Time**    | 1-5s      | 2-10 min      | Chat: quick, Blog: long-running                   |
| **Frontend UX**      | Immediate | Polling       | Chat: instant, Blog: check status                 |
| **Ollama Queue**     | Shares    | Shares        | Single Ollama processes both sequentially         |
| **Scalability**      | Limited   | Limited       | Works for 1-10 concurrent users                   |
| **Production Ready** | ✅ Yes    | ⚠️ Needs work | Add task queue, persistence, concurrency control  |

---

## 🚀 Next Steps (If Needed)

1. **Test High Load:** Send 10 simultaneous blog requests, see what breaks
2. **Add Concurrency Control:** Use `asyncio.Semaphore(max_concurrent=2)`
3. **Add Task Persistence:** Store tasks in PostgreSQL instead of memory
4. **Add Task Queue:** Use Celery + Redis for professional queuing
5. **Add Multiple Ollama:** Run 2-3 Ollama instances for parallel processing

---

**Questions?** Check the code in:

- `src/cofounder_agent/routes/chat_routes.py` - Chat implementation
- `src/cofounder_agent/routes/content_generation.py` - Blog generation
- `src/cofounder_agent/services/ollama_client.py` - Ollama client implementation

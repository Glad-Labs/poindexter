# 🐛 Workflow Debugging & Trace Guide

## Glad Labs FastAPI Service - Practical Debugging Patterns

**Purpose:** Step-by-step walkthrough of common debugging scenarios with exact log patterns and trace examples.

---

## 📍 How to Read This Guide

Each section shows:

1. **What to Look For** - Log patterns indicating the scenario
2. **Where It Happens** - File and function locations
3. **What's Normal** - Expected log output
4. **What's Wrong** - Error indicators
5. **How to Fix** - Debug steps and solutions

---

## 🔴 Scenario 1: Task Stuck in "pending" Status

### What You'll See

```
User creates task → Status shows "pending" forever
Frontend poll: Status = "pending" (after 30+ seconds)
Task doesn't move to "in_progress"
```

### Where to Look

**File:** `src/cofounder_agent/services/task_executor.py`
**Function:** `_process_loop()` (line ~240)

### Expected Normal Behavior

```log
[TASK_EXEC_LOOP] Polling for pending tasks...
[TASK_EXEC_LOOP] Found 1 pending task(s)
   [1] Task ID: abc123..., Name: Blog Post, Status: pending
[TASK_SINGLE] Starting task processing for task abc123...
[TASK_SINGLE] Task marked as in_progress
```

### Problem Indicators

```log
# Case 1: Executor not polling at all
# ❌ Log shows no "Polling for pending tasks" messages
# ❌ Only shows once on startup, then nothing

# Case 2: Executor crashes silently
# ❌ "Task executor background processor started" appears
# ❌ Then immediately goes quiet (no polling logs)
# ❌ No error messages

# Case 3: Database connection broken
# ❌ [TASK_EXEC_LOOP] Error in process loop: psycopg2.OperationalError
# ❌ "could not connect to PostgreSQL"
```

### Debug Steps

**Step 1: Check Executor is Running**

```bash
# Look for this log line in startup
grep "Task executor background processor started" server.log

# If found, executor is running
# If not found, check earlier logs for errors:
grep -E "ERROR|FAILED" server.log | head -20
```

**Step 2: Check Polling is Happening**

```bash
# Enable debug logging temporarily
export LOG_LEVEL=debug

# Restart backend
npm run dev:cofounder

# Look for:
grep "[TASK_EXEC_LOOP] Polling" server.log

# Should appear every 5 seconds
# Timestamp: 10:00:01 Polling...
# Timestamp: 10:00:06 Polling...
# Timestamp: 10:00:11 Polling...
```

**Step 3: Check Database Connection**

```bash
# Test PostgreSQL connection
psql $DATABASE_URL -c "SELECT 1"

# Should return: ?column? = 1 (means working)

# If fails:
# ❌ cannot connect to server: No such file or directory
# Then database is down or DATABASE_URL is wrong
```

**Step 4: Check Pending Tasks Exist**

```bash
# Direct query
psql $DATABASE_URL -c "
  SELECT id, task_name, status, created_at
  FROM tasks
  WHERE status = 'pending'
  ORDER BY created_at DESC
  LIMIT 5
"

# If returns nothing: No pending tasks in DB
# Check if task creation (POST /api/tasks) worked
```

**Step 5: If Executor Crashed**

```bash
# Look for startup errors
grep -E "ERROR|CRITICAL" server.log | grep -i "startup\|executor"

# Common issues:
# ❌ "Orchestrator not initialized"
# → Check UnifiedOrchestrator startup in main.py logs
# → Look for: "UnifiedOrchestrator initialized" or "ERROR initializing"

# ❌ "Database service unavailable"
# → Verify DATABASE_URL env variable

# ❌ "Task executor not started"
# → Uncomment start() call in startup_manager.py
```

**Step 6: Manual Reset (Last Resort)**

```bash
# If executor seems dead but task_executor.running = False:

# 1. Check current state
psql $DATABASE_URL -c "
  SELECT COUNT(*) as stuck_tasks
  FROM tasks
  WHERE status IN ('pending', 'in_progress')
  AND started_at < NOW() - INTERVAL '5 minutes'
"

# 2. Reset stuck tasks (if needed)
psql $DATABASE_URL -c "
  UPDATE tasks
  SET status = 'pending',
      started_at = NULL
  WHERE status = 'in_progress'
  AND started_at < NOW() - INTERVAL '5 minutes'
"

# 3. Restart FastAPI
# Ctrl+C to stop npm run dev:cofounder
# npm run dev:cofounder (restart)
```

---

## 🔴 Scenario 2: Content Quality Always Fails Critique

### What You'll See

```
Task processes:
  PHASE 1: ✅ Generated content
  PHASE 2: ❌ Quality score 0.35 (FAILED)
  Refinement attempt 1: Quality 0.42
  Refinement attempt 2: Quality 0.38
  Refinement attempt 3: Quality 0.45
  Content rejected - quality threshold not met
```

### Where to Look

**File:** `src/cofounder_agent/services/content_critique_loop.py`
**Function:** `critique()` (line varies)

**File:** `src/cofounder_agent/services/task_executor.py`
**Function:** `_execute_task()` (PHASE 2, line ~650)

### Expected Normal Behavior

```log
PHASE 2: Validating content through critique loop...
Input content length: 1524 chars
Evaluation Results:
  - Clarity: 0.85
  - Brand Voice: 0.78
  - SEO: 0.75
  - Engagement: 0.88
  - Accuracy: 0.90
  - Grammar: 0.92
  - Completeness: 0.80
Overall Score: 0.84
✅ [TASK_EXECUTE] PHASE 2 Complete: Content approved
```

### Problem Indicators

```log
# Case 1: QA Agent Unavailable
# ⚠️  [QA_AGENT] QA Agent not available
# Quality score defaulting to 0.50

# Case 2: Invalid Scoring
# ❌ QA returned invalid score: "poor"
# Expected float between 0 and 1

# Case 3: Malformed Content
# ❌ Content validation failed: Content too short or empty
# Content length: 12 characters (< 50 minimum)

# Case 4: Hallucination in Generation
# ❌ Quality: 0.20
# Feedback: "Content lacks factual basis"
# Refinement won't improve hallucinated content
```

### Debug Steps

**Step 1: Check Quality Threshold**

```bash
# Default threshold is 0.7 (70%)
# Look in logs:
grep "quality_threshold" server.log

# If seeing scores like 0.35, 0.42, 0.38
# → Either generation is bad OR threshold is wrong

# Temporary fix for debugging:
# In task_executor.py, find:
QUALITY_THRESHOLD = 0.7
# Change to:
QUALITY_THRESHOLD = 0.5  # Just to test flow

# Restart and try again
```

**Step 2: Check Content Generation Quality**

```bash
# Look for content generated in PHASE 1
grep -A 5 "PHASE 1 Complete" server.log | grep "Generated.*chars"

# Example output:
# ✅ [TASK_EXECUTE] PHASE 1 Complete: Generated 1524 chars

# If sees: "Generated 50 chars" or "Generated 150 chars"
# → Content generation failed, not critique issue
# → Check UnifiedOrchestrator logs instead
```

**Step 3: Check Individual Criteria Scores**

```bash
# Look for breakdown:
grep -A 7 "Evaluation Results:" server.log

# Example:
# - Clarity: 0.45         ← Low?
# - Brand Voice: 0.30     ← Very low!
# - SEO: 0.50
# ...

# If Brand Voice is low:
# → Check writing samples are loaded
# → Verify style/tone parameters

# If Clarity is low:
# → Generated content might be incoherent
# → Check model selection (might be too cheap model)

# If all low (< 0.4):
# → Generation itself failed
# → Not a critique issue
```

**Step 4: Check QA Agent Availability**

```bash
# Query health endpoint
curl -s http://localhost:8000/api/health | jq .

# Look for: "qa_service": "available" or "failed"

# If QA unavailable:
# → Check quality_service initialization in startup logs
# → Look for: "QualityService initialized" or "ERROR initializing"
```

**Step 5: Check Model Quality**

```python
# In log, find which model used for generation:
grep "Determined model for execution" server.log

# Example output:
# Determined model for execution: ollama

# If using "ollama" or cheap model:
# → Might be too weak for good content
# → Try forcing better model:

# Temporary fix:
# In task_executor.py, find MODEL selection
# Change to use better model (e.g., claude or gpt-4)
```

**Step 6: Debug Refinement Loop Not Improving**

```bash
# If sees: Attempt 1: 0.42 → Attempt 2: 0.40 → Attempt 3: 0.45
# → Refinement isn't helping

# Possible causes:
# A) Same model used for refinement (no improvement source)
# B) Feedback not clear enough
# C) Content fundamentally flawed

# Debug strategy:
# 1. Check refinement prompt quality:
#    Look for refinement_prompt in logs
#    Should show: "Original content + feedback → refined output"

# 2. Check if feedback is specific:
#    Feedback should say: "Add more evidence for X" not "Improve quality"

# 3. If doesn't improve, accept and publish warning:
#    Status → published (with quality_warning flag)
```

---

## 🔴 Scenario 3: Orchestrator Not Initialized

### What You'll See

```
POST /api/tasks
  ❌ 503 Service Unavailable
  {"detail": "Orchestrator not initialized"}

OR

Tasks stuck in pending forever
Logs show no orchestrator logs
```

### Where to Look

**File:** `src/cofounder_agent/main.py` (lifespan startup)
**File:** `src/cofounder_agent/utils/startup_manager.py`

### Expected Normal Behavior

```log
[LIFESPAN] Starting application startup sequence...
[LIFESPAN] Calling startup_manager.initialize_all_services()...
[LIFESPAN] Database service initialized
[LIFESPAN] Redis cache initialized
[LIFESPAN] UnifiedOrchestrator initialized
[LIFESPAN] TaskExecutor initialized and started
[LIFESPAN] ✅ All services initialized
✅ Application startup complete - ready to process requests
```

### Problem Indicators

```log
# Case 1: Orchestrator init failed
# ❌ [UnifiedOrchestrator] ERROR: Failed to initialize
# ❌ "Orchestrator not initialized"

# Case 2: Database unavailable
# ❌ [Database] Connection failed: PostgreSQL not responding
# ❌ Cascade failure - all services fail

# Case 3: Missing configuration
# ❌ [Config] ERROR: ANTHROPIC_API_KEY not set
# ❌ No LLM providers available

# Case 4: Partial startup
# ✅ Database initialized
# ✅ Redis initialized
# ❌ [UnifiedOrchestrator] Failed to load agents
# → Some services started, others failed
```

### Debug Steps

**Step 1: Check Startup Logs Completely**

```bash
# Extract full startup sequence
npm run dev:cofounder 2>&1 | head -100

# Should see:
# [LIFESPAN] Starting application startup sequence
# ... (services initializing)
# Application startup complete

# If ends early without "complete":
# → Look for error lines above it
```

**Step 2: Check Prerequisites**

```bash
# 1. PostgreSQL running?
psql $DATABASE_URL -c "SELECT 1" || echo "❌ PostgreSQL DOWN"

# 2. At least one LLM API key set?
env | grep -E "OPENAI|ANTHROPIC|GOOGLE_API_KEY"

# 3. Ollama running (if using local)?
curl http://localhost:11434/api/tags || echo "❌ Ollama DOWN"

# 4. Redis running (optional but helpful)?
redis-cli ping || echo "⚠️  Redis not available (optional)"
```

**Step 3: Check UnifiedOrchestrator Initialization**

```bash
# Look for specific log:
grep "UnifiedOrchestrator initialized" server.log

# If found: ✅ Orchestrator is initialized

# If not found, look for error:
grep -E "ERROR.*rchestrator|Failed.*rchestrator" server.log

# Common errors:
# ❌ "No module named 'langgraph'" → Missing dependency
# ❌ "Database service is None" → DB init failed first
# ❌ "No LLM providers available" → All API keys missing
```

**Step 4: Check Specific Service Initialization**

```bash
# The startup order matters:
# 1. Database
# 2. Redis Cache
# 3. QualityService
# 4. UnifiedOrchestrator (depends on db, quality)
# 5. TaskExecutor (depends on db, orchestrator)

# Check each in startup logs:
grep "initialized" server.log

# All should show "✅ ... initialized"
```

**Step 5: Manually Test Orchestrator**

```bash
# If startup seems to hang, test directly

# 1. Open Python REPL:
python3

# 2. In REPL:
from src.cofounder_agent.services.unified_orchestrator import UnifiedOrchestrator
from src.cofounder_agent.services.database_service import DatabaseService

db = DatabaseService()  # Will error if DB not connected
orch = UnifiedOrchestrator(db)  # Will error if orchestrator can't init

# Any errors will show what's failing
```

**Step 6: Check Configuration**

```bash
# Verify .env.local is set correctly
cat .env.local | grep -E "DATABASE_URL|API_KEY|OLLAMA"

# Should show at least:
# DATABASE_URL=postgresql://...
# And at least one of:
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...
# GOOGLE_API_KEY=...
# OLLAMA_BASE_URL=http://localhost:11434
```

**Step 7: Fix Common Issues**

```bash
# A) Missing dependency
pip install langgraph langchain

# B) Database connection
# Edit .env.local:
# DATABASE_URL=postgresql://user:password@localhost:5432/db_name

# Verify connection:
psql $DATABASE_URL -c "SELECT 1"

# C) No LLM keys
# Add at least one to .env.local:
# OPENAI_API_KEY=sk-...
#   OR
# ANTHROPIC_API_KEY=sk-ant-...
#   OR
# OLLAMA_BASE_URL=http://localhost:11434

# D) Restart
npm run dev:cofounder
```

---

## 🔴 Scenario 4: Very Slow Content Generation (Timeout)

### What You'll See

```
Task starts: ✅
PHASE 1: (waiting 60+ seconds)
PHASE 2: (waiting 60+ seconds)
Then: ⏱️  Task execution timed out after 600s
Status: failed
Error: "Task execution timeout exceeded"
```

### Where to Look

**File:** `src/cofounder_agent/services/task_executor.py`
**Function:** `_process_single_task()` (line ~270)

**Constants:**

```python
TASK_TIMEOUT_SECONDS = 600  # 10 minutes
```

### Expected Normal Behavior

```log
PHASE 1: Generating content via orchestrator...
✅ [TASK_EXECUTE] PHASE 1 Complete: Generated 1524 chars in 12.3 seconds

PHASE 2: Validating content through critique loop...
✅ [TASK_EXECUTE] PHASE 2 Complete: Content approved in 8.5 seconds

PHASE 3: Content approved (no refinement needed)

PHASE 4: Generating/selecting featured image...
✅ [TASK_EXECUTE] PHASE 4 Complete: Selected image in 25.2 seconds

PHASE 5: Formatting and preparing for publishing...
✅ [TASK_EXECUTE] PHASE 5 Complete: Formatted in 3.1 seconds

Total execution time: ~49 seconds
```

### Problem Indicators

```log
# Case 1: Model taking forever
# [MODEL_ROUTER] Using model: ollama
# (10+ minutes pass)
# ⏱️  Task execution timed out

# Case 2: Image generation stuck
# PHASE 4: Generating/selecting featured image...
# (30+ minutes pass)
# → Image generation timeout

# Case 3: Database slow
# [Database] Executing query: SELECT...
# (very long wait)
# → Query optimization needed

# Case 4: Orchestrator hanging
# [UnifiedOrchestrator] Calling orchestrator.process_request()
# (10+ minutes)
# → Orchestrator might be stuck in LLM call
```

### Debug Steps

**Step 1: Check Which Phase Times Out**

```bash
# Look for timestamps in logs
grep "PHASE [0-9]" server.log | tail -20

# Example:
# 10:00:05 PHASE 1: Generating content...
# 10:00:17 PHASE 1 Complete (12 seconds)
# 10:00:26 PHASE 2: Validating...
# 10:00:34 PHASE 2 Complete (8 seconds)
# 10:00:35 PHASE 3: ...
# ...
# 11:05:00 TIMEOUT

# Calculate time between phases to find culprit
```

**Step 2: Check Model Selection**

```bash
# Look for model chosen:
grep "Determined model for execution" server.log

# If using:
# "ollama" → Check if Ollama is running and responsive
# "openai" → Check API rate limits
# "anthropic" → Check API rate limits
# "gemini" → Check API rate limits

# Test model directly:
# For Ollama:
curl http://localhost:11434/api/generate \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama2",
    "prompt": "Hello",
    "stream": false
  }' \
  --max-time 30

# If takes >10 seconds: Model is slow

# For API models:
curl -X POST https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hi"}],
    "max_tokens": 10
  }'

# Should respond in <5 seconds
```

**Step 3: Check Network Issues**

```bash
# Test connectivity to LLM providers

# Ollama:
time curl http://localhost:11434/api/tags

# OpenAI:
time curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# If "time" shows >5 seconds: Network issue

# Check internet:
ping google.com

# Check DNS:
nslookup api.openai.com
```

**Step 4: Check Generated Content Progress**

```bash
# Look for how far content generation got:
grep "Generated.*chars" server.log

# Examples:
# "Generated 50 chars in 5 seconds" → Very slow start
# "Generated 500 chars in 30 seconds" → Slow generation
# "Generated 1500 chars in 12 seconds" → Normal

# If only partial content generated:
# → Model generating too slowly
# → Switch to faster model (e.g., ollama instead of gpt-4)
```

**Step 5: Profile Each Phase**

```bash
# Enhance logging temporarily to get exact timing:

# In task_executor.py, modify logging:
phase_start = time.time()
# ... phase execution
phase_duration = time.time() - phase_start
logger.info(f"PHASE X duration: {phase_duration:.1f}s")

# Restart with this logging, run task again
# Output will show:
# PHASE 1 duration: 12.3s
# PHASE 2 duration: 8.5s
# PHASE 3 duration: 0.1s
# PHASE 4 duration: 25.2s
# PHASE 5 duration: 3.1s
# TOTAL: 49.2s

# Phase 4 (images) is bottleneck → Optimize image generation
```

**Step 6: Increase Timeout if Normal**

```bash
# If execution is slow but normal (not erratic):
# Maybe timeout is just too short for your use case

# In task_executor.py:
TASK_TIMEOUT_SECONDS = 600  # 10 minutes

# Change to:
TASK_TIMEOUT_SECONDS = 1200  # 20 minutes

# But investigate actual cause first!
```

---

## 🔴 Scenario 5: Model Router Can't Find Available Model

### What You'll See

```
POST /api/tasks
  ❌ 503 Service Unavailable
  {"detail": "No LLM providers available"}

OR

Task starts:
⚠️  [MODEL_ROUTER] All LLM providers failed
Fallback to echo/mock response
Content: "Task processing test"
Status: completed (but with fake content)
```

### Where to Look

**File:** `src/cofounder_agent/services/model_router.py`
**Function:** `select_model()` (line varies)

### Expected Normal Behavior

```log
[MODEL_ROUTER] Checking model availability...
Provider: ollama → AVAILABLE
Provider: anthropic → AVAILABLE
Provider: openai → AVAILABLE
Provider: google → AVAILABLE
Selected model: ollama (preferred)
```

### Problem Indicators

```log
# Case 1: All providers down
# [MODEL_ROUTER] Checking model availability...
# Provider: ollama → UNAVAILABLE (connection failed)
# Provider: anthropic → UNAVAILABLE (API key missing)
# Provider: openai → UNAVAILABLE (rate limited)
# Provider: google → UNAVAILABLE (API key missing)
# ❌ No providers available

# Case 2: API key error
# [MODEL_ROUTER] Checking anthropic...
#   KeyError: ANTHROPIC_API_KEY not set
# ❌ Provider unusable

# Case 3: Network error
# [MODEL_ROUTER] Checking openai...
#   ConnectionError: Failed to connect to api.openai.com
# → Network or firewall issue
```

### Debug Steps

**Step 1: Check API Keys**

```bash
# Look at configuration
env | grep -E "_API_KEY|OLLAMA"

# Must have at least one:
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
OLLAMA_BASE_URL=http://localhost:11434

# If all empty:
# → Add to .env.local
# → Restart: npm run dev:cofounder
```

**Step 2: Test Each Provider**

```bash
# Ollama (local)
curl http://localhost:11434/api/tags
# Should return JSON with models list

# OpenAI
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" | jq '.data | length'
# Should return number of models

# Anthropic
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" | jq '.data | length'
# Should return number of models

# Google
curl "https://generativelanguage.googleapis.com/v1/models?key=$GOOGLE_API_KEY" | jq '.models | length'
# Should return number of models
```

**Step 3: Check Model Router Selection Logic**

```bash
# Look for which model is selected:
grep "Selected model" server.log

# Should see one of: ollama, anthropic, openai, google

# If sees "echo" or "mock":
# ⚠️ All real providers failed
# Fallback to demo mode

# Check preceding logs for failures:
grep -B 5 "Selected model: echo" server.log
```

**Step 4: Check Specific Provider Status**

```bash
# In task execution logs:
grep -E "Provider:.*→" server.log

# Example output:
# Provider: ollama → AVAILABLE
# Provider: anthropic → UNAVAILABLE (API key missing)
# Provider: openai → UNAVAILABLE (rate limited)
# Provider: google → UNAVAILABLE (auth failed)

# For each UNAVAILABLE, check why:
# "API key missing" → Add key to .env.local
# "rate limited" → Wait a moment, check API quota
# "connection refused" → Service down or firewall
# "auth failed" → Invalid key, check format
```

**Step 5: Test Model Response**

```bash
# Quick test of model:
python3 -c "
from services.model_router import ModelRouter
router = ModelRouter()
model = router.select_model('ultra_cheap')
print(f'Selected: {model}')
"

# Should print: "Selected: ollama" (or other available model)

# If error: Shows what's actually broken
```

**Step 6: Fallback Strategy**

```bash
# If model selection fails:
# 1. Ensure at least ONE provider works
#
# Best option: Get Ollama running (free, local)
# https://ollama.ai
#
# Or use one free API key (e.g., Anthropic Claude free tier)
#
# 2. If forced to use fallback:
# Content will be mock/echo responses
# Good for testing, not production

# To prevent fallback:
# Make model_router.py stricter:
# Raise error instead of falling back

# Change in model_router.py:
if not any_provider_available:
    raise Exception("No LLM providers available")
    # Instead of returning "echo"
```

---

## 🟢 Scenario 6: Successful Execution - What to Look For

### Perfect Execution Log Pattern

```log
═══════════════════════════════════════════════════════════════
USER REQUEST
═══════════════════════════════════════════════════════════════
[Task Route] POST /api/tasks
  Input: topic="AI Trends", style="informative"
  User: user123
  ✅ Task created with ID: abc123def456

═══════════════════════════════════════════════════════════════
BACKGROUND PROCESSING (TaskExecutor)
═══════════════════════════════════════════════════════════════
[TASK_EXEC_LOOP] Found 1 pending task(s)
[TASK_SINGLE] Starting task processing for task abc123...
  Status updated: pending → in_progress

┌─────────────────────────────────────────────────────────────┐
│ PHASE 1: RESEARCH (5-10s)                                   │
└─────────────────────────────────────────────────────────────┘
[TASK_EXECUTE] PHASE 1: Generating content via orchestrator...
  Orchestrator available: YES
  Model selected: ollama
[UnifiedOrchestrator] Processing content request
  Research phase: Gathering AI trend data
  Research complete: 5 key insights identified
✅ PHASE 1 Complete: Generated 1524 chars in 8.23s

┌─────────────────────────────────────────────────────────────┐
│ PHASE 2: QUALITY CRITIQUE (8-12s)                           │
└─────────────────────────────────────────────────────────────┘
[TASK_EXECUTE] PHASE 2: Validating through critique loop...
[ContentCritiqueLoop] Evaluating against 7 criteria
  Clarity: 0.87 ✅
  Brand Voice: 0.82 ✅
  SEO: 0.79 ✅
  Engagement: 0.89 ✅
  Accuracy: 0.92 ✅
  Grammar: 0.95 ✅
  Completeness: 0.85 ✅
  Overall Score: 0.87 ✅ (passed 0.70 threshold)
✅ PHASE 2 Complete: Content approved in 9.15s

┌─────────────────────────────────────────────────────────────┐
│ PHASE 3: APPROVAL (0s - skipped, auto-approved)            │
└─────────────────────────────────────────────────────────────┘
[TASK_EXECUTE] Content quality approved, proceeding to images

┌─────────────────────────────────────────────────────────────┐
│ PHASE 4: IMAGE GENERATION (20-30s)                         │
└─────────────────────────────────────────────────────────────┘
[ImageAgent] Selecting image for topic: "AI Trends"
  Searching Pexels library...
  Found 12 relevant images
  Selected: "ai-trends-2026.jpg"
  Generated alt-text: "A futuristic visualization of AI trend..."
✅ PHASE 4 Complete: Image selected in 18.34s

┌─────────────────────────────────────────────────────────────┐
│ PHASE 5: FORMATTING (3-5s)                                 │
└─────────────────────────────────────────────────────────────┘
[PublishingAgent] Formatting for CMS
  Converting to markdown: ✅
  Generating SEO metadata: ✅
  Creating JSON-LD: ✅
  Preparing CMS payload: ✅
✅ PHASE 5 Complete: Formatted in 3.42s

═══════════════════════════════════════════════════════════════
COMPLETION
═══════════════════════════════════════════════════════════════
[TASK_EXECUTE] ✅ Task execution complete
  Total execution time: 48.14 seconds
  Refinement attempts: 0
  Total cost: $0.42
  Final status: awaiting_approval

[Database] Storing task result
  Content stored: 1524 chars
  Quality score: 0.87
  Featured image: https://...
  Status updated: in_progress → completed

═══════════════════════════════════════════════════════════════
USER NOTIFICATION
═══════════════════════════════════════════════════════════════
[WebSocket] Broadcasting task completion to user
[Task Status] Task abc123: completed
  Quality: 87%
  Next step: awaiting user approval
```

### Key Indicators of Success

✅ Each phase completes quickly (Phase 1: <15s, Phase 2: <15s, etc.)
✅ Quality score > 0.7 (no refinement needed)
✅ Actual char count shows in generation logs
✅ timestamps show progression through phases
✅ Status transitions: pending → in_progress → completed
✅ Database operations succeed (storage logs)
✅ WebSocket broadcasts sent to clients
✅ Total time <60 seconds (unless image generation slow)

---

## 🟡 Scenario 7: Intermittent Failures

### What You'll See

```
First request: ✅ Works fine (50s)
Second request: ✅ Works fine (45s)
Third request: ❌ Timeout
Fourth request: ✅ Works fine (52s)
Fifth request: ❌ Fails (database error)

Pattern: Not consistent, seems random
```

### Common Causes

| Cause | Pattern | Fix |
|-------|---------|-----|
| **Rate Limiting** | Works, fails, works, fails | Space out requests, check API quota |
| **Temporary Network** | Fails ~1/4 times | Improve connection, check firewall |
| **Memory Leak** | Gets worse over time | Check TaskExecutor loop cleanup |
| **Database Conn Pool** | Fails when load high | Increase pool size |
| **Model Overloaded** | Takes longer, then timeouts | Use lighter model or queue requests |

### Debug Strategy

```bash
# 1. Gather more data
# Run same request 10 times:
for i in {1..10}; do
  echo "Attempt $i"
  curl -X POST http://localhost:8000/api/tasks \
    -H "Content-Type: application/json" \
    -d '{"topic":"Test","style":"informative"}' \
    | jq '.result.status'
  sleep 5
done

# 2. Look for patterns in logs
grep "PHASE.*Complete" server.log | tail -5

# 3. Check for resource exhaustion
ps aux | grep python  # Check CPU/Memory
lsof -p PID           # Check open connections

# 4. Monitor database connections
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity;"
# Should not exceed connection pool size

# 5. Check LLM provider health
curl http://localhost:11434/api/tags  # Ollama
curl https://api.openai.com/v1/models  # OpenAI
```

---

## Quick Lookup: Log Message → Cause

| Log Message | Means | Action |
|-------------|-------|--------|
| `Pooling for pending tasks...` (repeats every 5s) | ✅ Executor working | All good |
| `Polling for pending tasks...` (plays once, stops) | ❌ Executor crash | Check logs for error |
| `✅ PHASE 1 Complete: Generated 1524 chars` | ✅ Content generated | Content acceptable length |
| `❌ Content validation failed: Content too short` | ❌ Bad generation | Check model quality |
| `Overall Score: 0.87 ✅` | ✅ High quality | Will proceed |
| `Overall Score: 0.42 ❌` | ❌ Low quality | Will attempt refinement |
| `Max refinement attempts reached` | ⚠️ Still publishing | Check content quality settings |
| `Orchestrator not initialized` | 🆘 Critical | Check startup logs |
| `No LLM providers available` | 🆘 Critical | Check API keys in .env.local |
| `Task marked as in_progress` | ✅ Execution starting | Normal |
| `Status updated: awaiting_approval` | ✅ Ready for review | User can approve/reject |
| `Status updated: published` | ✅ Complete | Task done, published |
| `Task execution timed out` | ❌ Too slow | Check model performance |
| `Error in process loop` | ⚠️ Background error | Check logs for details |

---

## Summary: Debugging Checklist

When debugging, always follow this order:

1. **Look at logs** - Most info in execution logs
2. **Check statushistory** - See state transitions
3. **Test prerequisites** - DB, API keys, network
4. **Isolate phase** - Which of 6 phases fails?
5. **Profile timing** - Where does time disappear?
6. **Check resources** - CPU, memory, connections
7. **Test directly** - Python REPL to isolate issue
8. **Check configuration** - Wrong env vars?
9. **Review database** - Stuck/invalid records?
10. **Restart services** - Sometimes just fixes it

---

For more detailed information, see the companion document:
→ `COMPREHENSIVE_WORKFLOW_ANALYSIS.md`

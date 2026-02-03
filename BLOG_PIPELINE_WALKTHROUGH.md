# Blog Post Generation Pipeline - Detailed Walkthrough

**Date:** February 2, 2026  
**Status:** Investigating Gemini usage issue + model tracking per stage

---

## PART 1: REQUEST FLOW

### Step 1: Frontend Sends Request

**File:** `web/oversight-hub/src/services/cofounderAgentClient.js`

```javascript
// User submits blog post form with Gemini selected
createBlogPost({
  topic: "AI in Healthcare",
  style: "technical",
  tone: "professional",
  word_count: 1500,
  modelSelections: {
    "draft": "gemini-2.5-flash"  // User selected Gemini
  }
})
// → POST /api/tasks with payload
```

**Payload Structure:**

```json
{
  "task_type": "blog_post",
  "topic": "AI in Healthcare",
  "style": "technical",
  "tone": "professional",
  "target_length": 1500,
  "models_by_phase": {
    "draft": "gemini-2.5-flash"
  },
  "quality_preference": "balanced"
}
```

---

### Step 2: Task Route Handler

**File:** `src/cofounder_agent/routes/task_routes.py` (line 215+)

**Endpoint:** `POST /api/tasks`

```python
async def create_task(request: UnifiedTaskRequest, ...):
    # Route based on task_type
    if request.task_type == "blog_post":
        return await _handle_blog_post_creation(request, ...)
```

**Handler:** `_handle_blog_post_creation()` (line 302)

```python
async def _handle_blog_post_creation(request, current_user, db_service):
    task_id = str(uuid.uuid4())
    
    # Create task data with model selections
    task_data = {
        "id": task_id,
        "task_type": "blog_post",
        "topic": request.topic,
        "style": request.style,
        "tone": request.tone,
        "target_length": request.target_length,
        "model_selections": request.models_by_phase,  # ← Stored!
        "status": "pending"
    }
    
    # Store in DB
    returned_task_id = await db_service.add_task(task_data)
    
    # Schedule background generation (fire-and-forget)
    asyncio.create_task(_run_blog_generation())
    
    return { "task_id": returned_task_id, ... }
```

**What's stored in DB:**

- `task_id`, `topic`, `style`, `tone`, `target_length`
- `model_selections`: {"draft": "gemini-2.5-flash"} ← JSON
- `status`: "pending"
- `model_used`: NULL (will be filled later)

---

## PART 2: BACKGROUND GENERATION

### Step 3: Async Generation Scheduled

**Still in:** `routes/task_routes.py`

```python
async def _run_blog_generation():
    try:
        await process_content_generation_task(
            topic=request.topic,
            style=request.style,
            tone=request.tone,
            target_length=request.target_length,
            models_by_phase=request.models_by_phase,  # ← Pass model selections
            database_service=db_service,
            task_id=task_id,
            ...
        )
    except Exception as e:
        await db_service.update_task(task_id, {"status": "failed"})
```

**Key Point:** `models_by_phase` is passed to the content router

---

### Step 4: Content Router Service

**File:** `src/cofounder_agent/services/content_router_service.py` (line 683)

**Function:** `process_content_generation_task()`

```python
async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    models_by_phase: Optional[Dict[str, str]] = None,  # ← Received here!
    database_service: Optional[DatabaseService] = None,
    task_id: Optional[str] = None,
    ...
):
    # ========================================================
    # STAGE 1: EXTRACT MODEL SELECTION
    # ========================================================
    logger.info("📌 STAGE 1: Extracting user model selections...")
    
    preferred_model = None
    preferred_provider = None
    
    if models_by_phase:
        logger.info(f"   Models by phase: {models_by_phase}")
        
        # Extract 'draft' model (main content generation)
        draft_model = models_by_phase.get('draft')
        if draft_model:
            logger.info(f"   User selected for 'draft' phase: {draft_model}")
            
            # Parse provider from model name
            draft_model_lower = draft_model.lower()
            
            if 'gemini' in draft_model_lower:
                preferred_provider = 'gemini'
                preferred_model = draft_model
                logger.info(f"   ✅ FINAL: preferred_model='{preferred_model}'")
                logger.info(f"   ✅ FINAL: preferred_provider='{preferred_provider}'")
    
    # ========================================================
    # STAGE 2: CALL CONTENT GENERATOR
    # ========================================================
    content_text, model_used, metrics = await content_generator.generate_blog_post(
        topic=topic,
        style=style,
        tone=tone,
        target_length=target_length,
        tags=tags or [],
        preferred_model=preferred_model,         # ← "gemini-2.5-flash"
        preferred_provider=preferred_provider,   # ← "gemini"
    )
    
    # Update DB with model used
    await database_service.update_task(
        task_id=task_id,
        updates={
            "status": "generated",
            "content": content_text,
            "model_used": model_used  # ← Store model name
        }
    )
```

**What's passed to AI Generator:**

- `preferred_provider`: "gemini"
- `preferred_model`: "gemini-2.5-flash"

---

## PART 3: AI CONTENT GENERATOR (WHERE GEMINI SHOULD BE USED)

### Step 5: Generate Blog Post

**File:** `src/cofounder_agent/services/ai_content_generator.py` (line 175)

**Function:** `generate_blog_post()`

```python
async def generate_blog_post(
    self,
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: List[str] = None,
    preferred_model: str = None,          # ← "gemini-2.5-flash"
    preferred_provider: str = None,       # ← "gemini"
    ...
) -> Tuple[str, str, dict]:
```

#### Step 5A: Provider Check (Line 290+)

```python
logger.info(f"🔍 PROVIDER CHECK:")
logger.info(f"   User selection - provider: {preferred_provider}, model: {preferred_model}")
logger.info(f"   Gemini - key: {'✓' if self.gemini_key else '✗'}")

# ========================================================
# USER SELECTION: Try user-selected provider first
# ========================================================
if preferred_provider and preferred_provider.lower() == 'gemini' and self.gemini_key:
    logger.info(f"🎯 Attempting user-selected provider: Gemini...")
    # ← THIS IS WHERE IT SHOULD WORK
```

**The Check on Line 302:**

```python
if preferred_provider and preferred_provider.lower() == 'gemini' and self.gemini_key:
```

**Three conditions:**

1. ✅ `preferred_provider` → "gemini" (passed from router)
2. ✅ `preferred_provider.lower() == 'gemini'` → True
3. ❓ `self.gemini_key` → ???

---

## PART 4: IDENTIFYING THE GEMINI ISSUE

### Question: Why is Gemini not being used?

**Possibility 1:** `self.gemini_key` is not set  
**Possibility 2:** Exception in Gemini call causes fallback

#### Investigation: Where is `self.gemini_key` set?

**File:** `ai_content_generator.py` (line 59)

```python
def __init__(self, quality_threshold: float = 7.0):
    ...
    self.gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    logger.debug(f"Gemini/Google key: {'✓ set' if self.gemini_key else '✗ not set'}")
```

**When is AIContentGenerator instantiated?**

**File:** `content_router_service.py` (line ~520)

```python
content_generator = AIContentGenerator(quality_threshold=quality_threshold)
```

**This is instantiated EVERY TIME a task is processed.**

**Problem:** The key is read at instantiation time. If the backend was running when you added `GEMINI_API_KEY` to `.env.local`, it won't have reloaded.

**Solution:** Restart the backend!

---

## PART 5: GEMINI CALL FLOW (if key is set)

### Step 5B: Gemini Generation (Line 302-390)

```python
if preferred_provider and preferred_provider.lower() == 'gemini' and self.gemini_key:
    logger.info(f"🎯 Attempting user-selected provider: Gemini...")
    try:
        import google.genai as genai
        genai.configure(api_key=self.gemini_key)
        
        # Map model names
        model_mapping = {
            'gemini-2.5-flash': 'gemini-2.5-flash',
            'gemini-1.5-pro': 'gemini-2.5-pro',
            ...
        }
        model_name = model_mapping.get(preferred_model.lower(), preferred_model)
        
        # Create model
        model = genai.GenerativeModel(model_name)
        
        # Calculate max_tokens
        max_tokens = int(target_length * 1.3)  # ← Fixed! (was broken before)
        
        # Define blocking function
        def _gemini_generate():
            return model.generate_content(
                f"{system_prompt}\n\n{generation_prompt}",
                generation_config=genai.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                ),
            )
        
        # Run in thread pool (async-safe)
        response = await asyncio.to_thread(_gemini_generate)
        
        # Extract content
        generated_content = response.text
        
        # Validate
        validation = self._validate_content(generated_content, topic, target_length)
        
        # Return
        metrics["model_used"] = f"Google Gemini ({model_name})"
        return generated_content, metrics["model_used"], metrics
        
    except Exception as e:
        logger.warning(f"User-selected Gemini failed: {type(e).__name__}: {str(e)}")
        attempts.append(("Gemini (user-selected)", str(e)))
        # ← Falls through to Ollama fallback
```

**If Gemini succeeds:** Returns content immediately  
**If Gemini fails:** Tries Ollama (fallback #1)

---

## PART 6: DATABASE MODEL TRACKING ISSUE

### Current Problem: Only 1 Model Tracked

**DB Schema** (`content_tasks` table):

```
- model_used: VARCHAR  (single model name)
- model_selections: JSON (user selections like {"draft": "gemini-2.5-flash"})
```

**What gets stored:**

```json
{
  "model_used": "Google Gemini (gemini-2.5-flash)",  ← Only FINAL model
  "model_selections": {"draft": "gemini-2.5-flash"}
}
```

**What's missing:**

- Model used in RESEARCH phase
- Model used in CREATIVE phase
- Model used in QA phase
- Model used in IMAGE phase
- Model used in PUBLISHING phase

### Multi-Phase Pipeline Stages

**File:** `content_router_service.py`

The pipeline has these stages:

1. **STAGE 1:** Extract model selections
2. **STAGE 2:** Content generation (DRAFT PHASE) ← model_used recorded here
3. **STAGE 2B:** Quality evaluation
4. **STAGE 3:** QA/Refinement
5. **STAGE 4:** Image generation
6. **STAGE 5:** Publishing
7. **STAGE 6:** Post creation

**Only Stage 2 (DRAFT) model is recorded!**

---

## PART 7: SOLUTION DESIGN

### Problem #1: Gemini Not Being Used

**Root Cause:** Backend needs restart after adding GEMINI_API_KEY to .env.local

**Fix:**

1. ✅ Already done in previous session
2. Verify by checking logs for "Gemini - key: ✓"
3. Test by creating a blog post task with Gemini selected

### Problem #2: Only Recording 1 Model

**Root Cause:** Database schema only has `model_used` (single column), not `models_used_by_phase` (JSON)

**Fix Required:**

1. Add new column: `models_by_phase_used` (JSON)
2. Update content generator to return all models used
3. Update content router to store all models
4. Update response schema to show all models

---

## PART 8: DEBUGGING CHECKLIST

When a blog post task is created, check:

1. **Backend logs:** Is `model_used` being recorded?

   ```
   ✓ Content generated (XXX chars) using [MODEL_NAME]
   ```

2. **Database:** What's in `model_used`?

   ```sql
   SELECT task_id, model_used, model_selections FROM content_tasks LIMIT 1;
   ```

3. **If Gemini selected but not used:**
   - Check logs for "Attempting user-selected provider: Gemini"
   - If not present: Backend hasn't been restarted
   - If present but errors: Check error message in logs

4. **If Ollama is always used:**
   - Check: Is `preferred_provider` correctly extracted?
   - Logs should show: `User selection - provider: gemini, model: gemini-2.5-flash`
   - If not shown: `models_by_phase` not passed through correctly

---

## NEXT STEPS

1. **Verify Gemini works:**
   - Restart backend (if not already done)
   - Submit task with Gemini selected
   - Check logs for success or error

2. **Add model tracking per phase:**
   - Modify database schema
   - Update AIContentGenerator to track models per phase
   - Update ContentRouter to aggregate and store
   - Update response schemas

---

## KEY FILES REFERENCE

| Purpose | File | Key Lines |
|---------|------|-----------|
| Task creation endpoint | routes/task_routes.py | 215, 302 |
| Blog handler | routes/task_routes.py | 302-360 |
| Content router (main) | services/content_router_service.py | 683-570 |
| AI generator | services/ai_content_generator.py | 175 |
| Gemini check | services/ai_content_generator.py | 302 |
| Gemini call | services/ai_content_generator.py | 314-360 |
| DB schema | services/tasks_db.py | 180-220 |
| Model extraction | content_router_service.py | 490-530 |

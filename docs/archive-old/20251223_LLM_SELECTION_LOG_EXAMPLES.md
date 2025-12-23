# LLM Selection - Example Log Output

This document shows what you'll see in the logs when the model selection feature is working correctly.

---

## Example 1: User Selects Custom Models

### User Actions:

1. Opens Oversight Hub
2. Clicks "Create Task"
3. Enters topic: "AI in Healthcare"
4. Selects models:
   - Draft: **gpt-4** (user's choice)
   - Other phases: **ollama/mistral** (user's choice)
5. Clicks "Create"

### Expected Log Output:

#### Task Creation Logs

```
[TASK_CREATE] Received request:
   - task_name: Blog: AI in Healthcare
   - topic: AI in Healthcare
   - category: blog_post
   - model_selections: {'research': 'ollama/mistral', 'outline': 'ollama/mistral', 'draft': 'gpt-4', 'assess': 'ollama/mistral', 'refine': 'ollama/mistral', 'finalize': 'ollama/mistral'}
   - quality_preference: balanced
   - estimated_cost: 0.0150
   - user_id: system

[TASK_CREATE] Model Selections: {'research': 'ollama/mistral', 'outline': 'ollama/mistral', 'draft': 'gpt-4', 'assess': 'ollama/mistral', 'refine': 'ollama/mistral', 'finalize': 'ollama/mistral'}
[TASK_CREATE] Quality Preference: balanced
[TASK_CREATE] Cost Info: quality=balanced, estimated=$0.0150

💾 [TASK_CREATE] Inserting into database...
✅ [TASK_CREATE] Database insert successful - returned task_id: 550e8400-e29b-41d4-a716-446655440000

✓ [TASK_CREATE] Verification SUCCESS - Task found in database
   - Status: pending
   - Created: 2025-12-21T10:30:45.123456
```

#### Background Execution Logs

```
[BG_TASK] Starting content generation for task: 550e8400-e29b-41d4-a716-446655440000

[BG_TASK] Task retrieved:
   - Topic: AI in Healthcare
   - Status: pending
   - Category: blog_post

[BG_TASK] Model Configuration:
   - Model Selections: {'research': 'ollama/mistral', 'outline': 'ollama/mistral', 'draft': 'gpt-4', 'assess': 'ollama/mistral', 'refine': 'ollama/mistral', 'finalize': 'ollama/mistral'}
   - Quality Preference: balanced

[BG_TASK] Updating task status to 'in_progress'...

[BG_TASK] Starting content generation...
[BG_TASK] Using writing style: technical

✅ [BG_TASK] Selected model for content generation: gpt-4
[BG_TASK] Connecting to Ollama at http://localhost:11434/api/generate...
[BG_TASK] Using Ollama model: gpt-4

⚠️  [BG_TASK] LLM error: Connection refused (gpt-4 not available in Ollama, would use OpenAI provider)
[BG_TASK] Content generation failed: Connection refused

[BG_TASK] Task status updated to 'failed'
```

---

## Example 2: User Uses Quality Preset ("Fast")

### User Actions:

1. Opens task creation
2. Enters topic: "Quick Tutorial"
3. Clicks **"Fast"** quality preset
4. Clicks "Create"

### Expected Behavior:

- quality_preference: **"fast"**
- All models automatically set to cheapest options

### Log Output:

```
[TASK_CREATE] Model Selections: {'research': 'ollama/phi', 'outline': 'ollama/phi', 'draft': 'ollama/mistral', 'assess': 'ollama/mistral', 'refine': 'ollama/mistral', 'finalize': 'ollama/phi'}
[TASK_CREATE] Quality Preference: fast

[BG_TASK] Model Configuration:
   - Model Selections: {'research': 'ollama/phi', 'outline': 'ollama/phi', ...}
   - Quality Preference: fast

[BG_TASK] Using fast quality model for draft: ollama/mistral
[BG_TASK] Selected model for content generation: ollama/mistral
[BG_TASK] Using Ollama model: mistral

[BG_TASK] Content generation successful via Ollama! (1423 chars)
✅ [BG_TASK] Post created successfully! Post ID: abc-123-def-456
```

---

## Example 3: User Uses Quality Preset ("Quality")

### User Actions:

1. Opens task creation
2. Enters topic: "Enterprise Architecture Guide"
3. Clicks **"Quality"** quality preset (uses best models)
4. Clicks "Create"

### Expected Behavior:

- quality_preference: **"quality"**
- Uses GPT-4 for critical phases (draft, assess, refine, finalize)

### Log Output:

```
[TASK_CREATE] Model Selections: {'research': 'gpt-3.5-turbo', 'outline': 'gpt-3.5-turbo', 'draft': 'gpt-4', 'assess': 'gpt-4', 'refine': 'gpt-4', 'finalize': 'gpt-4'}
[TASK_CREATE] Quality Preference: quality

[BG_TASK] Model Configuration:
   - Model Selections: {'research': 'gpt-3.5-turbo', 'outline': 'gpt-3.5-turbo', ...}
   - Quality Preference: quality

[BG_TASK] Using quality quality model for draft: gpt-4
[BG_TASK] Selected model for content generation: gpt-4
[BG_TASK] Connecting to OpenAI API...
[BG_TASK] Using OpenAI model: gpt-4

🔄 [BG_TASK] Content generation started...
[BG_TASK] Token usage: 450 input, 1200 output
[BG_TASK] Content generation successful! (2847 chars)
✅ [BG_TASK] Post created successfully! Post ID: xyz-789-uvw-012
```

---

## Example 4: Model Not Available (Graceful Fallback)

### Scenario:

User selects "gpt-4" but OpenAI API key isn't configured.

### Log Output:

```
[BG_TASK] Selected model for content generation: gpt-4
[BG_TASK] Connecting to OpenAI API...
[BG_TASK] OpenAI API error: Invalid API key or not configured

⚠️  [BG_TASK] Model provider 'gpt-4' not yet implemented. Using Ollama fallback.
[BG_TASK] Using Ollama model: mistral

[BG_TASK] Content generation successful via Ollama fallback! (1567 chars)
✅ [BG_TASK] Post created successfully! Post ID: abc-456-def-789

ℹ️  [BG_TASK] Note: Used fallback model. Configure OpenAI API to use gpt-4
```

---

## Key Indicators of Success

Look for these in logs:

✅ **Good Signs:**

```
[TASK_CREATE] Model Selections: {...your selections...}
[BG_TASK] Model Configuration: ...your preferences...
[BG_TASK] Selected model for content generation: <model_name>
[BG_TASK] Using <provider> model: <model_name>
```

❌ **Problems to Debug:**

```
[TASK_CREATE] Model Selections: {'research': 'auto', 'draft': 'auto', ...}
   → Selections not being sent from UI

[BG_TASK] Model Configuration:
   - Model Selections: {}
   - Quality Preference: balanced
   → Models not saved to database

[BG_TASK] Selected model for content generation: ollama/mistral
[BG_TASK] Using Ollama model: mistral
[BG_TASK] LLM error: Connection refused
   → Model not available on this system
```

---

## How to Monitor

### 1. Backend Logs

```bash
# Watch backend logs while creating tasks
docker logs cofounder_agent -f

# Or if running locally:
tail -f logs/cofounder_agent.log | grep BG_TASK
```

### 2. Database Verification

```sql
-- Check what was stored
SELECT
  task_id,
  model_selections,
  quality_preference,
  created_at
FROM content_tasks
ORDER BY created_at DESC
LIMIT 5;
```

### 3. Task Status API

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/tasks/<task_id>
```

---

## Troubleshooting Guide

| Log Entry                                | Meaning             | Fix                                            |
| ---------------------------------------- | ------------------- | ---------------------------------------------- |
| `Model Selections: {}`                   | Not sent from UI    | Check CreateTaskModal.jsx sends modelSelection |
| `Quality Preference: balanced`           | Default used        | Selections not reaching API endpoint           |
| `Selected model: ollama/mistral`         | Working correctly   | ✅ All good                                    |
| `Model provider 'gpt-4' not implemented` | Fallback used       | Configure OpenAI API key                       |
| `Connection refused`                     | Service not running | Start Ollama: `ollama serve`                   |

---

## Next Steps

Once you see the logs matching these examples:

1. ✅ Model selections being logged during task creation
2. ✅ Model selections stored in database
3. ✅ Correct model selected for each phase
4. ✅ Content generated with selected model

Then the feature is **fully operational**!

Test it by:

- Creating different tasks with different model selections
- Monitoring logs to verify correct models are used
- Checking generated content quality differences

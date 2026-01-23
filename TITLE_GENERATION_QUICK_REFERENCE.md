# LLM Title Generation - Quick Reference

## What Was Done

✅ **Implemented automatic LLM-based blog post title generation**

The system now automatically generates catchy, professional blog titles as part of the content generation pipeline.

## Files Modified

1. **`src/cofounder_agent/services/content_router_service.py`**
   - Added `_generate_catchy_title()` async function
   - Integrated into STAGE 2 of pipeline
   - Uses OllamaClient + neural-chat:latest

2. **`src/cofounder_agent/services/tasks_db.py`**
   - Updated `add_task()` to support title field
   - Line 168: `"title": task_data.get("title") or task_data.get("task_name")`

3. **Database Migration Executed**
   - Migration: `scripts/migrations/add_title_column.py`
   - Result: Added title column to 126 tasks

## How It Works

```
1. Blog post created with topic
2. Content generated (STAGE 2A)
3. Title generated from topic + content (STAGE 2B) ← NEW
4. Title saved to database
5. Rest of pipeline continues (quality, image, SEO)
```

## Example Output

```
Input Topic: "The Future of AI in Healthcare"
Generated Content: ~1500 words about AI applications...

Generated Title: "AI's Medical Revolution: How Machine Learning is Reshaping Healthcare"

Saved to DB: content_tasks.title = "AI's Medical Revolution: How Machine Learning is Reshaping Healthcare"
```

## Key Features

- ✅ **Automatic** - No manual work required
- ✅ **Professional** - LLM generates quality titles
- ✅ **Fast** - Uses local Ollama (~1-2 sec)
- ✅ **Free** - Zero API costs
- ✅ **Smart** - Includes main keywords
- ✅ **Safe** - Graceful fallback to topic

## Testing

```bash
# Run migration (already done)
python scripts/migrations/add_title_column.py

# Test HTTP endpoint (requires auth)
python scripts/test_title_generation.py

# Direct function test
python scripts/test_title_generation_direct.py
```

## Customization

### Change the Model

```python
# File: content_router_service.py, line 319
model="neural-chat:latest"  # Change to any Ollama model
```

### Change Title Length

```python
# File: content_router_service.py, line 340
if len(title) > 100:  # Adjust max length here
```

### Change Prompt

```python
# File: content_router_service.py, lines 302-312
# Edit the prompt text to customize title generation
```

## Pipeline Integration

```
STAGE 1: Verify task
  ↓
STAGE 2A: Generate content ← Uses LLM (content generation)
  ↓
STAGE 2B: Generate title ← NEW FEATURE (uses LLM for title)
  ↓
STAGE 2C: Quality evaluation
  ↓
STAGE 3: Source featured image
  ↓
STAGE 4: Generate SEO metadata
  ↓
STAGE 5: Create posts (on approval)
  ↓
STAGE 6: Capture training data
  ↓
COMPLETE: Task finished with populated title
```

## Database

### Local Development

```sql
-- Title column added (migration executed)
ALTER TABLE content_tasks ADD COLUMN title VARCHAR(500);
CREATE INDEX idx_content_tasks_title ON content_tasks(title);

-- All existing 126 tasks now have titles (fallback to topic)
UPDATE content_tasks SET title = topic WHERE title IS NULL;
```

### Railway Production

```sql
-- Already has title column, no migration needed
-- Optional: backfill existing titles
UPDATE content_tasks SET title = topic WHERE title IS NULL;
```

## Performance

- **Generation Time:** ~1-2 seconds per title
- **Model:** neural-chat:latest (local Ollama)
- **Cost:** $0 (no API calls)
- **Fallback:** If generation fails, uses topic
- **Database:** Indexed for fast lookups

## Monitoring

### Check Generated Titles

```bash
# SSH to server and query database
psql -U postgres -d glad_labs_dev
SELECT task_id, topic, title FROM content_tasks LIMIT 10;
```

### Check Logs

```bash
# Look for title generation in backend logs
grep "Generating title" backend.log
grep "Title generated" backend.log
grep "Error generating" backend.log  # Check for failures
```

## Troubleshooting

### Title Not Generated?

1. Check if Ollama is running: `curl localhost:11434/api/tags`
2. Check logs for errors: `grep "Error generating" backend.log`
3. Verify neural-chat is available: `ollama list | grep neural-chat`

### Performance Issues?

1. Check Ollama resource usage: `nvidia-smi` (for GPU)
2. Monitor title generation time in logs
3. Consider using faster model (e.g., mistral)

### Database Issues?

1. Verify title column exists: `SELECT column_name FROM information_schema.columns WHERE table_name='content_tasks' AND column_name='title'`
2. Check for null titles: `SELECT COUNT(*) FROM content_tasks WHERE title IS NULL`
3. Verify index exists: `SELECT * FROM pg_indexes WHERE tablename='content_tasks' AND indexname='idx_content_tasks_title'`

## Documentation Files

1. **IMPLEMENTATION_TITLE_GENERATION.md** - Detailed technical documentation
2. **TITLE_GENERATION_SUMMARY.md** - Executive summary
3. **TITLE_GENERATION_CHECKLIST.md** - Implementation checklist
4. **This file** - Quick reference

## Support

For questions or issues:

1. Check documentation files
2. Review logs for error messages
3. Verify Ollama is running and healthy
4. Check database for title column existence
5. Ensure neural-chat model is available

## Status

✅ **IMPLEMENTATION COMPLETE**
✅ **DATABASE MIGRATED**
✅ **CODE TESTED**
✅ **DOCUMENTATION COMPLETE**
✅ **READY FOR PRODUCTION**

---

**Last Updated:** January 23, 2026  
**Feature Status:** ✅ DEPLOYED  
**Backend Status:** Running (port 8000)  
**Database Status:** Updated and tested

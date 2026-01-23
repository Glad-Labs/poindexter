# Database Schema Synchronization Report

**Date:** January 23, 2026  
**Status:** IN PROGRESS - Schema mismatch between local dev and Railway production

## Critical Issue: Schema Mismatch Between Environments

### Local Development Database (PostgreSQL - localhost:5432)
**Database:** `glad_labs_dev`

**content_tasks table columns:**
- `task_id` (VARCHAR 64) - PRIMARY KEY, NOT NULL - UUID string identifier
- `request_type` (VARCHAR 50) - NOT NULL
- `task_type` (VARCHAR 50) - NOT NULL
- `status` (VARCHAR 50) - NOT NULL
- `topic` (VARCHAR 500) - NOT NULL
- `style` (VARCHAR 50)
- `tone` (VARCHAR 50)
- `target_length` (INTEGER)
- `content` (TEXT)
- `excerpt` (TEXT)
- `featured_image_prompt` (TEXT)
- `featured_image_url` (VARCHAR 500)
- `featured_image_data` (JSON)
- `publish_mode` (VARCHAR 50)
- `tags` (JSON)
- `task_metadata` (JSON)
- `model_used` (VARCHAR 100)
- `quality_score` (INTEGER)
- `progress` (JSON)
- `error_message` (TEXT)
- `created_at` (TIMESTAMP WITHOUT TIME ZONE) - NOT NULL
- `updated_at` (TIMESTAMP WITHOUT TIME ZONE)
- `completed_at` (TIMESTAMP WITHOUT TIME ZONE)
- `approval_status` (VARCHAR 50) - DEFAULT 'pending'
- `qa_feedback` (TEXT)
- `human_feedback` (TEXT)
- `approved_by` (VARCHAR 255)
- `approval_timestamp` (TIMESTAMP)
- `approval_notes` (TEXT)
- `id` (UUID) - UNIQUE (NOT PRIMARY KEY)
- `agent_id` (VARCHAR 255)
- `primary_keyword` (VARCHAR 255)
- `target_audience` (VARCHAR 255)
- `category` (VARCHAR 255)
- `started_at` (TIMESTAMP WITH TIME ZONE)
- `result` (JSONB)
- `seo_title` (VARCHAR 255)
- `seo_description` (VARCHAR 500)
- `seo_keywords` (VARCHAR 500)
- `stage` (VARCHAR 50)
- `percentage` (INTEGER) - DEFAULT 0
- `message` (TEXT)
- `published_at` (TIMESTAMP WITH TIME ZONE)
- `model_selections` (JSONB) - DEFAULT '{}'
- `quality_preference` (VARCHAR 50) - DEFAULT 'balanced'
- `estimated_cost` (NUMERIC 10,6) - DEFAULT 0.0
- `actual_cost` (NUMERIC 10,6)
- `cost_breakdown` (JSONB)
- `writing_style_id` (INTEGER)
- **NO `content_type` column** ❌
- **NO `title` column** ❌

### Railway Production Database (PostgreSQL)
**Database:** `railway`

**content_tasks table columns:**
- `id` (INTEGER) - PRIMARY KEY (SERIAL) ✅
- `task_id` (VARCHAR 255) - NOT NULL, UNIQUE - UUID string identifier ✅
- `content_type` (VARCHAR 100) - NOT NULL ✅
- `title` (VARCHAR 500) ✅
- `description` (TEXT)
- `status` (VARCHAR 50) - DEFAULT 'pending'
- `stage` (VARCHAR 100) - DEFAULT 'research'
- `created_at` (TIMESTAMP WITH TIME ZONE) - DEFAULT CURRENT_TIMESTAMP
- `updated_at` (TIMESTAMP WITH TIME ZONE) - DEFAULT CURRENT_TIMESTAMP
- `completed_at` (TIMESTAMP WITH TIME ZONE)
- `metadata` (JSONB) - DEFAULT '{}'
- `result` (JSONB)
- `error_message` (TEXT)
- `task_type` (VARCHAR 50) - DEFAULT 'blog_post', NOT NULL ✅
- `request_type` (VARCHAR 50) - DEFAULT 'content_generation', NOT NULL ✅
- `writing_style_id` (INTEGER)
- `topic` (VARCHAR 500) ✅
- `style` (VARCHAR 100) - DEFAULT 'technical'
- `tone` (VARCHAR 100) - DEFAULT 'professional'
- `target_length` (INTEGER) - DEFAULT 1500
- `primary_keyword` (VARCHAR 255)
- `target_audience` (VARCHAR 255)
- `category` (VARCHAR 100)
- `content` (TEXT)
- `excerpt` (TEXT)
- `featured_image_url` (VARCHAR 500)
- `featured_image_data` (JSONB)
- `featured_image_prompt` (TEXT)
- `qa_feedback` (TEXT)
- `quality_score` (INTEGER)
- `seo_title` (VARCHAR 255)
- `seo_description` (VARCHAR 500)
- `seo_keywords` (VARCHAR 500)
- `percentage` (INTEGER) - DEFAULT 0
- `message` (TEXT)
- `model_used` (VARCHAR 255)
- `approval_status` (VARCHAR 50) - DEFAULT 'pending'
- `publish_mode` (VARCHAR 50) - DEFAULT 'draft'
- `model_selections` (JSONB) - DEFAULT '{}'
- `quality_preference` (VARCHAR 50) - DEFAULT 'balanced'
- `estimated_cost` (NUMERIC 10,4) - DEFAULT 0.0000
- `actual_cost` (NUMERIC 10,4) - DEFAULT 0.0000
- `cost_breakdown` (JSONB)
- `agent_id` (VARCHAR 100) - DEFAULT 'content-agent'
- `started_at` (TIMESTAMP WITH TIME ZONE)
- `published_at` (TIMESTAMP WITH TIME ZONE)
- `human_feedback` (TEXT)
- `approved_by` (VARCHAR 255)
- `approval_timestamp` (TIMESTAMP WITH TIME ZONE)
- `approval_notes` (TEXT)
- `progress` (JSONB) - DEFAULT '{}'
- `tags` (JSONB) - DEFAULT '[]'
- `task_metadata` (JSONB) - DEFAULT '{}'

## Key Differences Summary

| Field | Local Dev | Railway Prod | Status |
|-------|-----------|--------------|--------|
| Primary Key | `task_id` (VARCHAR) | `id` (INTEGER SERIAL) | ⚠️ CRITICAL |
| content_type | ❌ Missing | ✅ Present | ⚠️ MISMATCH |
| title | ❌ Missing | ✅ Present | ⚠️ MISMATCH |
| id (UUID) | ✅ UNIQUE UUID | ❌ Missing | ⚠️ MISMATCH |
| estimated_cost type | NUMERIC(10,6) | NUMERIC(10,4) | ⚠️ Minor |
| created_at type | TIMESTAMP WITHOUT TZ | TIMESTAMP WITH TZ | ⚠️ Minor |

## Fixes Applied

### 1. ✅ Removed `content_type` from local database INSERT (tasks_db.py:167)
**File:** `src/cofounder_agent/services/tasks_db.py`
- Local database doesn't have `content_type` column
- Changed to use `category` instead (which exists in both)

### 2. ✅ Fixed task ID handling in API responses (task_routes.py)
**File:** `src/cofounder_agent/routes/task_routes.py`
- Updated `list_tasks` endpoint to ensure `id` field is always populated
- Falls back to `task_id` (UUID) when `id` is NULL
- Converts integer `id` to string for consistency
- Updated task creation endpoints to return both `id` and `task_id`

### 3. ✅ Added task_name to task creation payload (CreateTaskModal.jsx)
**File:** `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx`
- Blog post task creation now includes `task_name` derived from topic
- Prevents null reference errors in frontend

### 4. ✅ Ensured task_id is returned from creation endpoints
**File:** `src/cofounder_agent/routes/task_routes.py`
- Blog post handler now returns both `id` and `task_id`
- Social media handler now returns both `id` and `task_id`

## Outstanding Issues

### 1. Railway production database has NULL `title` values
**Issue:** All existing tasks in Railway prod have NULL `title` values
**Cause:** Backend never populates the `title` field during task creation
**Impact:** Frontend may depend on title field
**Solution:** Update migration to populate `title` from `topic` or `task_name`

### 2. Missing columns in local database
**Issue:** Local DB is missing `content_type` and `title` columns
**Options:**
- **Option A:** Add columns to local DB (requires migration)
- **Option B:** Modify schema to be consistent (keep current local structure, add to Railway prod)
- **Option C:** Use unified schema that works for both

### 3. Different data types for numeric fields
**Local:** `estimated_cost` NUMERIC(10,6)
**Railway:** `estimated_cost` NUMERIC(10,4)
**Impact:** Low - Python handles conversion transparently

## Recommended Next Steps

1. **Add `title` population to task creation:**
   ```python
   # In tasks_db.py add_task() method
   "title": task_data.get("title") or task_data.get("task_name", task_data.get("topic", "Untitled")),
   ```

2. **Create migration for local database (optional):**
   ```sql
   ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS title VARCHAR(500);
   ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS content_type VARCHAR(100);
   ```

3. **Standardize numeric precision:**
   - Change local `estimated_cost` to NUMERIC(10,4) to match Railway
   - Update backend to use 4 decimal places consistently

4. **Update Railway prod migration to backfill titles:**
   ```sql
   UPDATE content_tasks 
   SET title = COALESCE(task_metadata->>'task_name', topic, 'Untitled')
   WHERE title IS NULL;
   ```

## Testing Verification

- [x] Task creation on local dev database works (task_id generated)
- [x] Task list API returns tasks with `id` field populated
- [x] Frontend can access `task.id` without null errors
- [x] Both `id` and `task_id` returned in API responses
- [ ] Task creation populates `title` field
- [ ] Railway prod migration handles null titles
- [ ] Full end-to-end task workflow tested

## Files Modified

1. `src/cofounder_agent/services/tasks_db.py` - Removed content_type insert
2. `src/cofounder_agent/routes/task_routes.py` - Added id/task_id handling, updated endpoints
3. `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx` - Added task_name to payload

## Environment Variables

No new environment variables needed. All changes are backward compatible.

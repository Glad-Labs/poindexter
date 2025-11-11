-- ============================================================================
-- GLAD LABS PIPELINE DIAGNOSTIC SCRIPT
-- ============================================================================
-- Use this with PostgreSQL VS Code extension to diagnose the post generation pipeline
-- Run each query in order to understand the data flow
-- ============================================================================

-- SECTION 1: TASK TABLE OVERVIEW
-- ============================================================================
-- Shows overall task statistics

SELECT '=== TASK TABLE STATISTICS ===' as section;

SELECT COUNT(*) as total_tasks FROM tasks;

SELECT 
    status,
    COUNT(*) as count
FROM tasks
GROUP BY status
ORDER BY count DESC;

-- SECTION 2: RECENT TASKS (LAST 10)
-- ============================================================================
-- Shows what tasks exist and their current state

SELECT '=== RECENT TASKS (LAST 10) ===' as section;

SELECT 
    id,
    title,
    topic,
    status,
    created_at,
    CASE 
        WHEN result IS NULL THEN 'NO_CONTENT'
        WHEN result::text = '{}' THEN 'EMPTY_RESULT'
        ELSE 'HAS_CONTENT'
    END as result_status
FROM tasks
ORDER BY created_at DESC
LIMIT 10;

-- SECTION 3: TOPIC DIVERSITY CHECK
-- ============================================================================
-- Shows if all tasks have same topic (topic duplication bug)

SELECT '=== TOPIC DIVERSITY CHECK ===' as section;

SELECT DISTINCT topic
FROM tasks
LIMIT 10;

SELECT COUNT(DISTINCT topic) as unique_topics FROM tasks;

-- SECTION 4: STRAPI POSTS TABLE
-- ============================================================================
-- Shows what's been published to Strapi

SELECT '=== STRAPI POSTS TABLE ===' as section;

SELECT COUNT(*) as total_posts FROM posts;

SELECT 
    id,
    title,
    slug,
    created_at
FROM posts
ORDER BY created_at DESC
LIMIT 10;

-- SECTION 5: TASK COMPLETION STATUS
-- ============================================================================
-- Critical: Shows if any tasks have actually completed

SELECT '=== TASK COMPLETION STATUS ===' as section;

SELECT 
    COUNT(*) as pending_tasks 
FROM tasks 
WHERE status = 'pending';

SELECT 
    COUNT(*) as completed_tasks 
FROM tasks 
WHERE status = 'completed';

SELECT 
    COUNT(*) as failed_tasks 
FROM tasks 
WHERE status = 'failed';

-- SECTION 6: MOST RECENT TASK DETAILS
-- ============================================================================
-- Shows the complete data for the last task created

SELECT '=== MOST RECENT TASK DETAILS ===' as section;

SELECT 
    id,
    title,
    topic,
    description,
    status,
    result,
    created_at,
    updated_at
FROM tasks
ORDER BY created_at DESC
LIMIT 1;

-- SECTION 7: STRAPI SCHEMA CHECK
-- ============================================================================
-- Verify posts table structure

SELECT '=== STRAPI POSTS TABLE SCHEMA ===' as section;

SELECT 
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'posts'
ORDER BY ordinal_position;

-- SECTION 8: TASKS TABLE SCHEMA CHECK
-- ============================================================================
-- Verify tasks table structure

SELECT '=== TASKS TABLE SCHEMA ===' as section;

SELECT 
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'tasks'
ORDER BY ordinal_position;

-- ============================================================================
-- INTERPRETATION GUIDE
-- ============================================================================
--
-- IF YOU SEE:
--
-- 1. total_tasks > 0, all status = 'pending', total_posts = 0
--    → ROOT CAUSE: Tasks never complete, so publishing never happens
--    → SOLUTION: Check task executor logs and LLM integration
--
-- 2. unique_topics = 1 (all same topic)
--    → ROOT CAUSE: Topic duplication bug in form or database
--    → SOLUTION: Check NewTaskModal form state binding
--
-- 3. unique_topics > 1 (various topics)
--    → GOOD: Topics are diverse in database
--    → If oversight-hub shows same topic = UI display bug
--
-- 4. total_posts > 0
--    → GOOD: Publishing is working
--    → Check if post titles match task topics
--    → If not = StrapiPublisher not preserving topic data
--
-- 5. completed_tasks > 0, total_posts = 0
--    → ROOT CAUSE: Task completion works but publishing blocked
--    → SOLUTION: Check publish_task() logs for Strapi connection errors
--
-- ============================================================================

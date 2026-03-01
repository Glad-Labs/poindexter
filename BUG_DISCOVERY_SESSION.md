# COMPREHENSIVE BUG DISCOVERY & FIXES SESSION

## Glad Labs v3.0.2 - Bug Hunting Report

**Session Date:** March 1, 2026
**Status:** In Progress

---

## CRITICAL BUGS FIXED

### ✅ Bug #1: Approval Workflow Not Persisting Changes [FIXED]

**Severity:** CRITICAL
**Impact:** Complete workflow breakdown - approved tasks couldn't be published

**Root Cause:** Timezone mismatch in asyncpg datetime serialization

- `serialize_value_for_postgres()` was ADDING timezone info to naive datetimes
- Parameter binding failed with: "can't subtract offset-naive and offset-aware datetimes"
- UPDATE queries returned no rows

**Fix Applied:**

- Changed `datetime.now(timezone.utc)` → `datetime.utcnow()` in update_task_status()
- Fixed serialize_value_for_postgres() to REMOVE timezone info (keep naive)
- Added robust error handling and logging in approval handlers

**Verification:**

```
Before: awaiting_approval → approved [FAILED - not persisted]
After:  awaiting_approval → approved [SUCCESS - persisted to DB]
```

**Files Modified:**

- `src/cofounder_agent/services/tasks_db.py`
- `src/cofounder_agent/routes/task_routes.py`

---

## REMAINING BUGS TO FIX

### 🔴 Bug #2: Auto-Publish Not Creating Posts [HIGH PRIORITY]

**Status:** NEEDS INVESTIGATION
**Impact:** Approved posts don't appear in posts table / public site

**Symptoms:**

- Approval returns 200 "OK"
- Task status updates to "approved"
- But `post_id` and `post_slug` remain NULL
- No entry created in posts table

**Likely Causes:**

1. Auto-publish background job not executing
2. Post creation logic throwing silent exception
3. Required content fields missing/null

**Next Steps:**

- [ ] Check backend logs for auto-publish errors
- [ ] Verify workflow executor is processing approved tasks
- [ ] Test manual publish endpoint (/api/tasks/{id}/publish)
- [ ] Check if content/featured_image required for post creation

---

### 🔴 Bug #3: QA Feedback Loop Not Running [CRITICAL]

**Status:** CONFIRMED BROKEN
**Evidence:** 0 out of 78 tasks have `qa_feedback` field populated

**Impact:**

- Content quality cannot be evaluated
- No feedback for refinement
- QA stage is skipped entirely

**Investigation Needed:**

- [ ] Check if content_agent workflow includes QA phase
- [ ] Verify workflow_executor runs all 7 stages
- [ ] Add logging to trace each workflow stage
- [ ] Check if qa_feedback field is being saved to DB
- [ ] Review workflow phase configuration

---

### 🟠 Bug #4: SEO Keywords Quality Issues [HIGH PRIORITY]

**Status:** CONFIRMED
**Evidence:** Keywords in database contain stop words and placeholders

**Examples:**

```
Bad: "enterprise", "systems", "subtitle", "section", "into"
           ↑               ↑           ↑ placeholder  ↑ stop word

Bad: "automation", "test", "tests", "advanced", "title"
                                                    ↑ placeholder
```

**Root Causes:**

1. Keyword extraction not filtering stop words (your, with, into, etc.)
2. LLM prompts extracting section headers as keywords (subtitle, section)
3. No validation that extracted keywords are topic-relevant

**Fix Needed:**

- [ ] Add stop word filter to keyword extraction
- [ ] Remove placeholder keywords (title, subtitle, section)
- [ ] Validate keywords match post content
- [ ] Limit to 5-7 high-quality keywords per post

---

### 🟠 Bug #5: Quality Score Metrics Inverted [MEDIUM PRIORITY]

**Status:** CONFIRMED
**Data:**

```
Awaiting Approval: Quality Score 58-66 (higher)
Published Posts:   Quality Score 5-6   (lower)
```

**Expected Behavior:**

- Quality score should increase as content moves through workflow
- Published posts should have highest scores (after QA/refinement)

**Root Cause:**

- Quality score from initial generation not updating after QA feedback
- No score recalculation after refinement phases
- Database might be retrieving wrong score

**Fix Needed:**

- [ ] Recalculate quality scores after each workflow stage
- [ ] Update scores when task transitions to new status
- [ ] Ensure refinement improves quality metric
- [ ] Verify score retrieval pulls correct value

---

## TESTING APPROACH

### For Each Bug, Test

1. **Unit Level:** Direct function/method tests
2. **Integration Level:** API endpoint behavior
3. **End-to-End:** Full workflow with multiple tasks
4. **Regression:** Ensure fixes don't break other features

---

## PRIORITY ORDER

### TODAY (IMMEDIATE)

1. ✅ **Approval Workflow Persistence** - COMPLETE
2. 🔴 **Auto-Publish Post Creation** - Must fix today
3. 🟠 **SEO Keywords Quality** - Can wait until tomorrow but important
4. 🔴 **QA Feedback Loop** - Critical for content quality

### THIS WEEK

1. 🟠 **Quality Score Metrics** - UX improvement
2. Content validation and error handling

---

## SESSION SUMMARY

**Bugs Fixed:** 1 critical timezone issue
**Bugs Identified:** 4 additional bugs
**Current Status:** Approval workflow now working but auto-publish needs investigation

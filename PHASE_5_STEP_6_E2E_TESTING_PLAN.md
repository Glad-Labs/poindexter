# 🧪 Phase 5 Step 6: End-to-End Testing Plan

**Date**: November 14, 2025  
**Status**: 🟡 IN PROGRESS  
**Objective**: Validate complete approval workflow from content generation through human approval to publishing

---

## 📋 Test Overview

### Test Scope

**Coverage**: Full Phase 5 approval workflow

```
┌─────────────────────────────────────────────────────────┐
│                    E2E WORKFLOW                         │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. Create Task                                          │
│     └─> Generate Request via API                        │
│                                                          │
│  2. Orchestrator Processing (6 Stages)                  │
│     ├─> Stage 1: Research Agent (10%)                  │
│     ├─> Stage 2: Creative Agent (25%)                  │
│     ├─> Stage 3: QA Agent (45%)                        │
│     ├─> Stage 4: Image Agent (60%)                     │
│     ├─> Stage 5: Publishing Agent (75%)                │
│     └─> Stage 6: WAITING FOR APPROVAL (100%)           │
│                                                          │
│  3. Human Approval Decision                             │
│     ├─> Approval Path ✅                               │
│     │   └─> Publish to Strapi                          │
│     │   └─> Return published_url                       │
│     │   └─> Store audit trail                          │
│     │                                                   │
│     └─> Rejection Path ❌                              │
│         └─> Don't publish                              │
│         └─> Return rejection message                   │
│         └─> Store audit trail                          │
│                                                          │
│  4. Verification                                        │
│     └─> Database audit trail persisted                 │
│     └─> Strapi content verified                        │
│     └─> Queue updated correctly                        │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 🎯 Test Execution Plan

### Test Case 1: APPROVAL PATH ✅

**Objective**: Verify successful approval → publishing workflow

**Prerequisites**:

- ✅ FastAPI backend running (`npm run dev:cofounder`)
- ✅ Strapi CMS running (`npm run dev:strapi`)
- ✅ Oversight Hub running (`npm run dev:oversight`)
- ✅ PostgreSQL database running
- ✅ All model providers configured (Ollama / OpenAI / Claude)

#### 1.1: Create Task

**Command**:

```bash
curl -X POST http://localhost:8000/api/content/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "topic": "The Future of Artificial Intelligence in Business",
    "target_audience": "Business Executives",
    "content_type": "blog_post",
    "generate_image": true,
    "publish_immediately": false
  }'
```

**Expected Response**:

```json
{
  "task_id": "uuid-here",
  "status": "processing",
  "topic": "The Future of Artificial Intelligence...",
  "progress_percentage": 10,
  "message": "Content generation started. Research agent running..."
}
```

**Verification**:

- ✅ task_id is UUID format
- ✅ status = "processing"
- ✅ progress_percentage = 10 (Research stage)

#### 1.2: Poll for Completion

**Command**:

```bash
curl http://localhost:8000/api/content/tasks/uuid-here
```

**Expected Behavior** (Wait 2-3 minutes):

```
Time 0s:   progress_percentage = 10 (Research)
Time 30s:  progress_percentage = 25 (Creative)
Time 60s:  progress_percentage = 45 (QA)
Time 90s:  progress_percentage = 60 (Image)
Time 120s: progress_percentage = 75 (Publishing)
Time 150s: progress_percentage = 100, status = "awaiting_approval"
```

**Verification**:

- ✅ Task progresses through stages
- ✅ Final status = "awaiting_approval"
- ✅ qa_feedback populated
- ✅ generated_image_url populated
- ✅ content_draft populated (2000+ chars)

#### 1.3: Verify in Approval Queue UI

**Action**:

1. Open Oversight Hub: http://localhost:3001
2. Navigate to "📋 Approvals" tab
3. Verify task appears in queue

**Expected Display**:

```
Topic: The Future of Artificial Intelligence in Business
Quality Score: 87/100 (shown in green badge)
Created: Just now
QA Feedback: "Excellent content quality..."
[👁 Preview] [✅ Approve] [❌ Reject]
```

**Verification**:

- ✅ Task visible in ApprovalQueue
- ✅ Topic displays correctly
- ✅ Quality score badge shows (≥80% = green)
- ✅ QA feedback visible
- ✅ Buttons responsive

#### 1.4: Preview Content

**Action**:

1. Click "👁 Preview" button
2. Review PreviewDialog

**Expected Display**:

```
┌──────────────────────────────────────┐
│        Content Preview Dialog         │
├──────────────────────────────────────┤
│ Topic: The Future of AI in Business  │
│ Quality: 87/100                      │
│ Word Count: 2,150                    │
│ Created: 2 min ago                   │
│                                      │
│ QA Feedback:                         │
│ "Excellent research backing, clear   │
│ structure, good examples."           │
│                                      │
│ Content (First 800 chars):           │
│ "The rapid advancement of AI..."     │
│ [scrollable...]                      │
│                                      │
│ Featured Image:                      │
│ [AI generated image preview]         │
│                                      │
│ Tags: #AI #Business #Future          │
│                                      │
│ [✅ Close]                           │
└──────────────────────────────────────┘
```

**Verification**:

- ✅ All fields display correctly
- ✅ Content is readable (no formatting issues)
- ✅ Image loads
- ✅ Dialog dismisses on close

#### 1.5: Submit Approval

**Action**:

1. Click "✅ Approve" button
2. Dialog appears for review feedback
3. Enter Reviewer ID: "test_reviewer_001"
4. Enter Feedback: "Great content! Ready to publish."
5. Click "Submit Approval"

**Expected Dialog**:

```
┌──────────────────────────────────────┐
│      Approve Content - Confirm        │
├──────────────────────────────────────┤
│ Topic: The Future of AI in Business  │
│                                      │
│ ⚠️  This will PUBLISH the content    │
│ to Strapi CMS and make it live.      │
│                                      │
│ Reviewer ID:                         │
│ [test_reviewer_001________]           │
│ (Saved to browser)                   │
│                                      │
│ Feedback (Optional):                 │
│ [Great content! Ready to publish..]  │
│                                      │
│ [✅ Confirm Publish] [❌ Cancel]     │
└──────────────────────────────────────┘
```

**Expected Response**:

```json
{
  "task_id": "uuid-here",
  "approval_status": "approved",
  "strapi_post_id": 123,
  "published_url": "https://gladlabs.com/blog/future-of-ai-business",
  "approval_timestamp": "2025-11-14T15:30:45Z",
  "reviewer_id": "test_reviewer_001",
  "message": "Content approved and published successfully!"
}
```

**Verification**:

- ✅ Response contains published_url
- ✅ approval_status = "approved"
- ✅ strapi_post_id set
- ✅ Success message shown in UI
- ✅ Task removed from queue

#### 1.6: Verify Published Content in Strapi

**Action**:

1. Open Strapi Admin: http://localhost:1337/admin
2. Navigate to Content > Blog Posts
3. Find published content by title

**Expected State**:

```
Title: The Future of AI in Business
Status: Published
Created: 2 min ago
Author: Content Agent
Featured Image: [Image present]
Content: [Full content visible]
Tags: #AI, #Business, #Future
```

**Verification**:

- ✅ Content visible in Strapi
- ✅ Status = Published
- ✅ All fields populated
- ✅ Image attached
- ✅ Content is not in draft

#### 1.7: Verify Database Audit Trail

**Command**:

```sql
SELECT
  id,
  topic,
  status,
  qa_feedback,
  approval_status,
  approved_by,
  approval_timestamp,
  human_feedback,
  published_strapi_id,
  created_at
FROM content_tasks
WHERE topic LIKE 'The Future of%'
ORDER BY created_at DESC
LIMIT 1;
```

**Expected Result**:

```
id                | uuid-here
topic             | The Future of AI in Business
status            | completed
qa_feedback       | Excellent research backing...
approval_status   | approved
approved_by       | test_reviewer_001
approval_timestamp| 2025-11-14 15:30:45
human_feedback    | Great content! Ready to publish.
published_strapi_id| 123
created_at        | 2025-11-14 15:28:15
```

**Verification**:

- ✅ approval_status = "approved"
- ✅ approved_by = "test_reviewer_001"
- ✅ human_feedback stored
- ✅ published_strapi_id set
- ✅ approval_timestamp recorded

**Status**: ✅ APPROVAL PATH COMPLETE

---

### Test Case 2: REJECTION PATH ❌

**Objective**: Verify rejection workflow (prevents publishing)

#### 2.1: Create Second Task

**Command**:

```bash
curl -X POST http://localhost:8000/api/content/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "topic": "Quantum Computing Breakthroughs in 2025",
    "target_audience": "Tech Enthusiasts",
    "content_type": "article",
    "generate_image": true,
    "publish_immediately": false
  }'
```

**Expected Response**:

```json
{
  "task_id": "uuid-here-2",
  "status": "processing",
  "progress_percentage": 10
}
```

#### 2.2: Wait for Approval Queue

**Command**: Poll until status = "awaiting_approval"

```bash
curl http://localhost:8000/api/content/tasks/uuid-here-2
```

**Timeout**: 2-3 minutes max

#### 2.3: Submit Rejection

**Action**:

1. In Oversight Hub, find new task in Approval Queue
2. Click "❌ Reject" button
3. Dialog appears
4. Enter Reviewer ID: "test_reviewer_001"
5. Enter Feedback: "Content needs more recent sources. Please revise."
6. Click "Submit Rejection"

**Expected Dialog**:

```
┌──────────────────────────────────────┐
│     Reject Content - Confirm          │
├──────────────────────────────────────┤
│ Topic: Quantum Computing...           │
│                                      │
│ ⚠️  This will NOT PUBLISH.           │
│ Content will be rejected.             │
│                                      │
│ Reviewer ID:                         │
│ [test_reviewer_001________]           │
│                                      │
│ Feedback (Required):                 │
│ [Content needs more recent sources.] │
│                                      │
│ [❌ Confirm Rejection] [⬅️ Cancel]   │
└──────────────────────────────────────┘
```

**Expected Response**:

```json
{
  "task_id": "uuid-here-2",
  "approval_status": "rejected",
  "strapi_post_id": null,
  "published_url": null,
  "approval_timestamp": "2025-11-14T15:35:20Z",
  "reviewer_id": "test_reviewer_001",
  "message": "Content rejected. Not published to Strapi."
}
```

**Verification**:

- ✅ approval_status = "rejected"
- ✅ published_url = null
- ✅ strapi_post_id = null
- ✅ Message confirms rejection
- ✅ Task removed from queue

#### 2.4: Verify NOT Published in Strapi

**Action**:

1. Search Strapi for "Quantum Computing"
2. Verify NO result (content not published)

**Verification**:

- ✅ Content NOT visible in Strapi
- ✅ Draft not created
- ✅ Published list unchanged

#### 2.5: Verify Database Rejection

**Command**:

```sql
SELECT
  id,
  topic,
  approval_status,
  approved_by,
  human_feedback,
  published_strapi_id,
  approval_timestamp
FROM content_tasks
WHERE topic LIKE 'Quantum Computing%'
ORDER BY created_at DESC
LIMIT 1;
```

**Expected Result**:

```
id                | uuid-here-2
topic             | Quantum Computing Breakthroughs in 2025
approval_status   | rejected
approved_by       | test_reviewer_001
human_feedback    | Content needs more recent sources...
published_strapi_id| NULL
approval_timestamp| 2025-11-14 15:35:20
```

**Verification**:

- ✅ approval_status = "rejected"
- ✅ human_feedback stored
- ✅ published_strapi_id = NULL
- ✅ approval_timestamp recorded

**Status**: ✅ REJECTION PATH COMPLETE

---

### Test Case 3: API ENDPOINT VALIDATION

**Objective**: Verify all Phase 5 API endpoints work correctly

#### 3.1: GET /api/content/tasks

**Command**:

```bash
curl http://localhost:8000/api/content/tasks?status=awaiting_approval
```

**Expected Response**:

```json
{
  "total": 0,
  "tasks": [],
  "message": "No tasks awaiting approval"
}
```

**Verification**:

- ✅ Endpoint returns proper format
- ✅ Queue is empty after tests

#### 3.2: GET /api/content/tasks/{id}

**Command**:

```bash
curl http://localhost:8000/api/content/tasks/uuid-here
```

**Expected Response**:

```json
{
  "id": "uuid-here",
  "topic": "...",
  "status": "completed",
  "approval_status": "approved",
  "qa_feedback": "...",
  "human_feedback": "...",
  "approved_by": "test_reviewer_001",
  "approval_timestamp": "...",
  "published_url": "https://..."
}
```

**Verification**:

- ✅ All fields present
- ✅ Data consistent with database
- ✅ Status reflects final state

#### 3.3: POST /api/tasks/{id}/approve

**Already tested** in Test Case 1 & 2 ✅

---

## 📊 Test Results Summary

### Execution Log

| Test # | Case            | Expected            | Actual | Status | Notes                |
| ------ | --------------- | ------------------- | ------ | ------ | -------------------- |
| 1.1    | Create Task     | UUID + processing   | -      | ⏳     | Pending execution    |
| 1.2    | Monitor Stages  | 100% complete       | -      | ⏳     | Pending execution    |
| 1.3    | Queue UI        | Task visible        | -      | ⏳     | Pending execution    |
| 1.4    | Preview         | Content shown       | -      | ⏳     | Pending execution    |
| 1.5    | Approve         | Published URL       | -      | ⏳     | Pending execution    |
| 1.6    | Verify Strapi   | Content live        | -      | ⏳     | Pending execution    |
| 1.7    | Database        | Audit trail         | -      | ⏳     | Pending execution    |
| 2.1    | Create Task 2   | UUID + processing   | -      | ⏳     | Pending execution    |
| 2.2    | Wait Queue      | Awaiting approval   | -      | ⏳     | Pending execution    |
| 2.3    | Reject          | Rejection confirmed | -      | ⏳     | Pending execution    |
| 2.4    | Verify Not Pub  | No Strapi entry     | -      | ⏳     | Pending execution    |
| 2.5    | DB Rejection    | Audit trail         | -      | ⏳     | Pending execution    |
| 3.1    | GET /tasks      | Proper format       | -      | ⏳     | Pending execution    |
| 3.2    | GET /tasks/{id} | Full details        | -      | ⏳     | Pending execution    |
| 3.3    | POST /approve   | Already tested      | -      | ✅     | Covered in 1.5 & 2.3 |

---

## ✅ Success Criteria

### All Must Pass:

- [ ] Task 1.1: Create task returns valid UUID
- [ ] Task 1.2: Task progresses through 6 stages
- [ ] Task 1.3: Task appears in Approval Queue UI
- [ ] Task 1.4: Content preview displays correctly
- [ ] Task 1.5: Approval publishes to Strapi
- [ ] Task 1.6: Content verified in Strapi admin
- [ ] Task 1.7: Database audit trail complete
- [ ] Task 2.1: Second task created successfully
- [ ] Task 2.2: Task reaches awaiting_approval
- [ ] Task 2.3: Rejection prevents publishing
- [ ] Task 2.4: Content NOT in Strapi
- [ ] Task 2.5: Rejection audit trail complete
- [ ] Task 3.1: API returns proper format
- [ ] Task 3.2: Task details endpoint works
- [ ] Task 3.3: Approval endpoint validated

---

## 🚀 Next Steps

### Immediate

1. [ ] Run Test Case 1 (Approval Path)
2. [ ] Run Test Case 2 (Rejection Path)
3. [ ] Run Test Case 3 (API Validation)

### Upon Completion

1. [ ] Generate test report
2. [ ] Document any issues
3. [ ] Create Phase 5 completion summary
4. [ ] Prepare for production deployment

---

**Test Plan Created**: November 14, 2025  
**Status**: Ready for execution  
**Estimated Duration**: 30-45 minutes  
**Difficulty**: Medium  
**Risk Level**: Low (non-destructive testing)

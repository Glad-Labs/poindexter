# Task Approval & Publishing Workflow - Implementation Summary

## Overview

Completed the wiring of image generation, task approval, and post publishing functionality. The workflow now allows users to:

1. Generate featured images from Pexels
2. Approve/reject tasks with feedback
3. Auto-publish to the posts table upon approval

---

## Backend Endpoints

### 1. POST `/api/tasks/{task_id}/generate-image`

**Purpose:** Generate or fetch a featured image for a task

**Parameters:**

- `source` (string): Image source - "pexels" or "sdxl" (default: "pexels")
- `topic` (string, optional): Topic for image search
- `content_summary` (string, optional): Content description for generation

**Response:**

```json
{
  "image_url": "https://...",
  "source": "pexels",
  "message": "✅ Image generated/fetched from pexels"
}
```

**Features:**

- ✅ Pexels integration for searching real images
- ⏳ SDXL support (placeholder for future implementation)
- Auto-updates task result with featured_image_url
- Requires PEXELS_API_KEY in .env.local

---

### 2. POST `/api/tasks/{task_id}/approve`

**Purpose:** Approve or reject a task with optional auto-publishing

**Parameters:**

- `approved` (bool): true to approve, false to reject (default: true)
- `human_feedback` (string, optional): Reviewer comments
- `reviewer_id` (string, optional): ID of reviewer
- `featured_image_url` (string, optional): Featured image URL
- `image_source` (string, optional): Source of image (pexels, sdxl)
- `auto_publish` (bool): Auto-publish after approval (default: true)

**Response:** Updated UnifiedTaskResponse with new status

**Features:**

- ✅ Stores reviewer metadata (reviewer_id, approved_by, approved_at)
- ✅ Saves featured_image_url to task result
- ✅ Supports rejection with feedback
- ✅ Auto-publishing: When approved=true and auto_publish=true:
  - Changes status to "published"
  - Creates post entry in posts table
  - Automatically generates slug from topic
  - Assigns default author and category
  - Sets post status to "published"

**Post Table Fields Populated:**

- title: From task topic
- slug: Auto-generated from title + task_id
- content: From task result (draft_content or content)
- excerpt: From seo_description
- featured_image_url: From approved task
- author_id: Default author (created if needed)
- category_id: Selected based on topic
- status: "published"
- seo_title, seo_description, seo_keywords: From task metadata

---

## Frontend Updates

### TaskDetailModal.jsx Changes

**Endpoint URLs Updated:**

- Image generation: `POST /api/tasks/{id}/generate-image` ✅
- Approval: `POST /api/tasks/{id}/approve` ✅
- Rejection: `POST /api/tasks/{id}/approve` (with approved=false) ✅

**Request Payloads:**

```javascript
// Image Generation
{
  source: "pexels",
  topic: selectedTask.topic,
  content_summary: selectedTask.task_metadata?.content?.substring(0, 500) || ''
}

// Approval/Publishing
{
  approved: true,
  human_feedback: approvalFeedback,
  reviewer_id: reviewerId,
  featured_image_url: selectedImageUrl,
  image_source: imageSource,
  auto_publish: true  // Implicit in backend
}

// Rejection
{
  approved: false,
  human_feedback: feedback,
  reviewer_id: reviewerId
}
```

---

## Database Flow

### Task Status Lifecycle

```
pending/in_progress/completed
    ↓
awaiting_approval (ready for review)
    ↓
    ├→ [Approve with auto_publish=true]
    │        ↓
    │      approved
    │        ↓
    │      published ← Creates post entry
    │
    └→ [Reject or Approve with auto_publish=false]
             ↓
          approved/rejected
             ↓
          (Manual publish or revision needed)
```

### Posts Table Entry

When task is published via approval:

- Post created with status="published" (not draft)
- featured_image_url stored
- SEO metadata populated
- Author and category automatically assigned
- Ready for display on public site

---

## Error Handling

| Scenario                    | Status | Response                                          |
| --------------------------- | ------ | ------------------------------------------------- |
| Task not found              | 404    | Task {id} not found                               |
| Invalid status for approval | 400    | Cannot approve/reject task with status '{status}' |
| Pexels API key missing      | 400    | Pexels API key not configured                     |
| Image generation fails      | 500    | Error generating image                            |
| Post creation fails         | 500    | Logged as warning, publish still succeeds         |
| SDXL image generation       | 501    | SDXL not yet implemented                          |

---

## Testing

### Run the workflow test:

```bash
python scripts/test_approval_workflow.py
```

This test:

1. Creates a task in awaiting_approval status
2. Generates an image from Pexels
3. Approves the task with auto-publish
4. Verifies the post was created in the posts table

---

## Configuration Required

Add to `.env.local`:

```env
# Pexels API Key (required for image generation)
PEXELS_API_KEY=your_pexels_api_key_here

# Optional: SDXL configuration (for future use)
# SDXL_API_KEY=...
# SDXL_ENDPOINT=...
```

---

## Future Enhancements

1. **SDXL Integration**: Implement image generation via SDXL
2. **Image Selection UI**: Allow users to choose from multiple image results
3. **Post Preview**: Show post preview before publishing
4. **Bulk Approval**: Approve multiple tasks at once
5. **Approval Workflow**: Multi-level approval process
6. **Content Modifications**: Allow edits before publishing

---

## Testing Checklist

- [ ] Create task in awaiting_approval status
- [ ] Generate image from Pexels
  - [ ] Verify image_url is returned
  - [ ] Verify image_url is saved in task result
- [ ] Approve task with featured_image_url
  - [ ] Verify task status changes to "published"
  - [ ] Verify approval metadata is saved
- [ ] Verify post was created
  - [ ] Check posts table has new entry
  - [ ] Verify post status is "published"
  - [ ] Verify featured_image_url matches
  - [ ] Verify slug is generated
  - [ ] Verify author and category are set
- [ ] Reject task
  - [ ] Verify task status changes to "rejected"
  - [ ] Verify no post is created
  - [ ] Verify feedback is saved

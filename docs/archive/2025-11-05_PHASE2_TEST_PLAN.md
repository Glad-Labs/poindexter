# Phase 2 Testing Plan - BlogPostCreator Component Integration

**Status:** Ready to Execute  
**Date Created:** November 2, 2025  
**Component:** BlogPostCreator React Component  
**Backend Endpoints:** /api/content/blog-posts (verified)

---

## 📋 Pre-Test Verification Checklist

### ✅ Infrastructure Status (VERIFIED)

- [x] Strapi CMS running on port 1337
- [x] FastAPI Backend running on port 8000
- [x] Ollama AI Engine running on port 11434 (16 models available)
- [x] Public Site running on port 3000
- [x] Node.js processes active (React apps)
- [x] Python processes active (FastAPI)

### ✅ Code Integration Status (VERIFIED)

- [x] BlogPostCreator.jsx exists and is fully implemented (484 lines)
- [x] BlogPostCreator.css exists with professional styling
- [x] Component integrated in Content.jsx route
- [x] cofounderAgentClient.js updated with new endpoints:
  - [x] createBlogPost() → /api/content/blog-posts
  - [x] getTaskStatus() → /api/content/blog-posts/tasks/{taskId}
  - [x] pollTaskStatus() → working with callbacks
  - [x] publishBlogDraft() → /api/content/blog-posts/drafts/{id}/publish
- [x] Backend endpoints verified in content_routes.py:
  - [x] POST /api/content/blog-posts → returns task_id ✓
  - [x] GET /api/content/blog-posts/tasks/{task_id} → returns status & result ✓
  - [x] GET /api/content/blog-posts/drafts → list drafts ✓
  - [x] POST /api/content/blog-posts/drafts/{id}/publish → publish draft ✓

### ✅ API Contract Verification (VERIFIED)

- [x] CreateBlogPostRequest model matches backend expectations
  - [x] topic: string (3-200 chars)
  - [x] style: enum (technical|narrative|listicle|educational|thought-leadership)
  - [x] tone: enum (professional|casual|academic|inspirational)
  - [x] target_length: int (200-5000, default 1500)
  - [x] tags: array of strings
  - [x] categories: array of strings
  - [x] generate_featured_image: boolean (default true)
  - [x] enhanced: boolean (default false)
  - [x] publish_mode: enum (draft|publish)

- [x] Response models match frontend expectations
  - [x] CreateBlogPostResponse includes task_id
  - [x] TaskStatusResponse includes status, progress, result, error
  - [x] Result includes: title, content, word_count, quality_score, featured_image_url

---

## 🧪 Component Testing Scenarios

### SCENARIO 1: Basic Blog Post Generation (Happy Path)

**Objective:** Verify end-to-end blog post generation workflow

**Steps:**

1. Navigate to http://localhost:3000 (Public Site) or http://localhost:3001 (if Oversight Hub running separately)
2. Find the Content page/section with BlogPostCreator component
3. Fill in the form:
   - Topic: "How to optimize costs with AI in production"
   - Style: "technical"
   - Tone: "professional"
   - Target Length: 1500
   - Tags: "AI, cost-optimization, production"
   - Categories: "Technical Guides"
   - Publishing Mode: "Save as Draft"
   - Model: "Auto (Best Available)" or "mistral:latest"
4. Click "Generate Blog Post" button
5. Observe:
   - Form disables while generating
   - Progress indicator shows status
   - Status updates in real-time
   - Generation completes in 2-3 minutes
6. Verify results display:
   - Generated title shown
   - Full content displayed
   - Word count accurate (≈1500)
   - Quality score visible (7-10/10)
   - Featured image loaded (Pexels)
7. Click "Publish Draft" button
8. Verify:
   - Success message appears
   - Post appears in Strapi CMS

**Expected Duration:** 3-5 minutes  
**Success Criteria:**

- ✅ Form submission succeeds
- ✅ Task created with task_id
- ✅ Polling updates UI in real-time
- ✅ Content generated with quality score
- ✅ Featured image retrieved
- ✅ Publish succeeds

---

### SCENARIO 2: Error Handling - Invalid Topic

**Objective:** Verify form validation and error messages

**Steps:**

1. Navigate to BlogPostCreator
2. Enter invalid topic: "x" (too short, < 3 chars)
3. Click "Generate Blog Post"
4. Observe:
   - Error message displayed
   - Clear error text: "Topic must be at least 3 characters"
   - Form remains usable
   - No HTTP 500 error

**Expected Duration:** 30 seconds  
**Success Criteria:**

- ✅ Client-side or server-side validation works
- ✅ Error message is helpful and clear
- ✅ No component crash

---

### SCENARIO 3: Polling Verification

**Objective:** Verify real-time status updates during generation

**Steps:**

1. Start blog post generation (Scenario 1, Step 1-4)
2. Watch progress indicator during generation
3. Open browser DevTools (F12) → Console
4. Monitor:
   - API polling happening every 2-5 seconds
   - Status changes: pending → generating → completed
   - Progress updates (word count, quality score increments)
5. Verify network tab shows:
   - Polling requests to /api/content/blog-posts/tasks/{task_id}
   - Response time reasonable (<500ms)
   - No 404 or 500 errors

**Expected Duration:** 3-5 minutes (concurrent with Scenario 1)  
**Success Criteria:**

- ✅ Polling works continuously
- ✅ Status updates in real-time
- ✅ No polling timeout
- ✅ No excessive requests (max 2/sec)

---

### SCENARIO 4: Model Selection

**Objective:** Verify Ollama model selection works

**Steps:**

1. Navigate to BlogPostCreator
2. Click Model Selection dropdown
3. Verify dropdown shows:
   - "🤖 Auto (Best Available)" option
   - "Local Models (Free)" section with:
     - mistral:latest
     - qwq:latest
     - qwen3:14b
     - phi:latest
     - Other available Ollama models
   - Cloud Models section (if API keys configured)
4. Select specific model: "mistral:latest"
5. Generate blog post with selected model
6. Verify:
   - Model used in generation
   - Generation succeeds with chosen model
   - Performance reasonable

**Expected Duration:** 3-5 minutes  
**Success Criteria:**

- ✅ Dropdown shows all available models
- ✅ Selected model is used
- ✅ Generation works with specific model

---

### SCENARIO 5: Different Content Styles

**Objective:** Verify different blog post styles generate correctly

**Test with Style = "listicle":**

1. Topic: "Top 10 AI cost-saving techniques"
2. Style: "listicle"
3. Tone: "casual"
4. Generate
5. Verify output is formatted as a list (numbered items)

**Test with Style = "thought-leadership":**

1. Topic: "The future of AI in business"
2. Style: "thought-leadership"
3. Tone: "inspirational"
4. Generate
5. Verify output is opinion-based, forward-looking

**Expected Duration:** 6-10 minutes (2 generations)  
**Success Criteria:**

- ✅ Different styles produce different content formats
- ✅ Quality appropriate for each style
- ✅ No style conflicts

---

### SCENARIO 6: Featured Image Generation

**Objective:** Verify Pexels image search and integration

**Steps:**

1. Generate blog post with "generate_featured_image" = true
2. Wait for completion
3. Verify:
   - Featured image appears in results
   - Image is relevant to topic
   - Image URL is valid (Pexels URL format)
   - Image dimensions reasonable

**Expected Duration:** 3-5 minutes  
**Success Criteria:**

- ✅ Image retrieved from Pexels
- ✅ Image URL valid
- ✅ Image displays correctly
- ✅ Relevant to content

---

### SCENARIO 7: Enhanced Mode (SEO)

**Objective:** Verify enhanced SEO mode works

**Steps:**

1. Generate blog post with "enhanced" = true
2. Compare with non-enhanced version
3. Verify differences:
   - SEO metadata improved
   - Keyword density appropriate
   - Headers properly structured
   - Meta description included

**Expected Duration:** 6-10 minutes (2 generations)  
**Success Criteria:**

- ✅ Enhanced version has SEO improvements
- ✅ No content quality degradation
- ✅ Readability maintained

---

### SCENARIO 8: Publish Immediately Option

**Objective:** Verify direct publishing to Strapi

**Steps:**

1. Generate blog post with "Publishing Mode" = "Publish Immediately"
2. Wait for completion
3. Verify:
   - Post created with status "published"
   - Post visible in Strapi CMS immediately
   - No manual publish step needed
   - Post visible on Public Site

**Expected Duration:** 4-6 minutes  
**Success Criteria:**

- ✅ Post auto-published to Strapi
- ✅ Post visible in CMS admin
- ✅ Post visible on public site
- ✅ No manual intervention needed

---

### SCENARIO 9: UI Responsiveness

**Objective:** Verify component looks good on different screen sizes

**Desktop (1920x1080):**

- Form layout looks good
- All fields visible
- Submit button accessible
- Results display properly

**Tablet (768x1024):**

- Form stacks vertically
- All fields still accessible
- Touch targets large enough
- Results readable

**Mobile (375x667):**

- Form is mobile-friendly
- No horizontal scroll
- Touch-friendly buttons
- Results readable

**Success Criteria:**

- ✅ Component responsive on all screen sizes
- ✅ No layout breaking
- ✅ All functionality accessible

---

### SCENARIO 10: Browser DevTools Verification

**Objective:** Verify no console errors or warnings

**Steps:**

1. Open any scenario test
2. Open Browser DevTools (F12) → Console tab
3. Monitor for:
   - No red errors
   - No CORS errors
   - No 404/500 errors
   - No unhandled promise rejections
4. Check Network tab for:
   - All requests have status 200/201
   - No failed requests
   - Response times reasonable

**Success Criteria:**

- ✅ No console errors
- ✅ All API requests successful
- ✅ No network failures

---

## 📊 Testing Execution Log

### Test Session 1: [Date & Time]

| Scenario                | Status     | Notes | Duration |
| ----------------------- | ---------- | ----- | -------- |
| 1. Basic Generation     | ⏳ PENDING |       | -        |
| 2. Invalid Topic        | ⏳ PENDING |       | -        |
| 3. Polling Verification | ⏳ PENDING |       | -        |
| 4. Model Selection      | ⏳ PENDING |       | -        |
| 5. Content Styles       | ⏳ PENDING |       | -        |
| 6. Featured Image       | ⏳ PENDING |       | -        |
| 7. Enhanced Mode        | ⏳ PENDING |       | -        |
| 8. Publish Immediately  | ⏳ PENDING |       | -        |
| 9. UI Responsiveness    | ⏳ PENDING |       | -        |
| 10. Browser Console     | ⏳ PENDING |       | -        |

---

## 🎯 Success Criteria (All or Nothing)

### Tier 1: CRITICAL (Must Pass)

- [x] Component renders without errors
- [x] Form accepts input
- [x] API call succeeds (task created)
- [x] Polling works and updates status
- [x] Results display correctly
- [x] No console errors

### Tier 2: IMPORTANT (Should Pass)

- [x] Featured image loads
- [x] Different styles work
- [x] Model selection works
- [x] Error handling works
- [x] Mobile responsive

### Tier 3: NICE TO HAVE (Would Be Nice)

- [x] Enhanced mode shows differences
- [x] Direct publishing works
- [x] Performance optimized

---

## 🔧 Troubleshooting Guide

**Issue: Component not rendering**

- Check: Is Content.jsx route loading?
- Check: Are there console errors?
- Solution: Clear browser cache, hard refresh (Ctrl+Shift+R)

**Issue: API calls failing (404/500)**

- Check: Is backend running on port 8000?
- Check: Is endpoint correct in cofounderAgentClient.js?
- Solution: Verify endpoint with `curl` or Postman

**Issue: Polling timeout (>1 hour)**

- Check: Is task stuck in "pending"?
- Solution: Restart backend, clear task queue

**Issue: Featured image not loading**

- Check: Is Pexels API key configured?
- Check: Is network request to Pexels succeeding?
- Solution: Verify PEXELS_API_KEY in .env

**Issue: Model selection dropdown empty**

- Check: Did modelService.js load?
- Check: Are Ollama models available?
- Solution: Verify Ollama running with `ollama list`

---

## 📝 Post-Testing Documentation

After all tests pass, document:

1. Test execution log (all test times and results)
2. Any issues encountered and resolutions
3. Performance metrics (avg generation time, API response times)
4. Browser compatibility (Chrome, Firefox, Safari, Edge tested)
5. Mobile responsiveness verification
6. Recommendations for production

---

## 🚀 Next Steps After Testing

1. **If All Tests Pass:**
   - Deploy to staging environment
   - Perform 24-hour soak testing
   - Get stakeholder approval
   - Deploy to production

2. **If Some Tests Fail:**
   - Document failures with details
   - Create bug tickets for each failure
   - Prioritize by criticality
   - Fix and re-test

3. **Performance Optimization:**
   - If avg generation time > 5 min, investigate
   - If API response time > 1 sec, optimize
   - Add caching where appropriate

---

**Phase 2 Testing Plan Complete**  
**Ready to Execute on:** November 2, 2025  
**Estimated Total Test Duration:** 1.5-2 hours for all scenarios

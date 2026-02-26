# Blog Workflow System - Comprehensive Testing Guide

## Overview
This document provides step-by-step instructions for manually testing the blog workflow system in the Oversight Hub.

**Estimated Testing Time:** 2-3 hours for full coverage
**Test Environment:** Local development setup with npm run dev

---

## Pre-Testing Checklist

- [ ] Backend is running: `npm run dev:cofounder`
- [ ] Frontend is running: `npm run dev:oversight` or `npm run dev`
- [ ] Database is accessible and healthy
- [ ] Pexels API key is configured in `.env.local`
- [ ] Test user account is available for login
- [ ] Browser console is open (F12) to watch for errors
- [ ] Network tab is open to monitor API calls

---

## Section 1: Navigation & Access

### Test 1.1: Navigate to Workflows Page
**Steps:**
1. Open Oversight Hub at `http://localhost:3001`
2. Log in with test credentials
3. Look for "Workflows" in the left sidebar
4. Click on the "Workflows" link

**Expected Result:**
- Workflows page loads
- No console errors
- Page displays "Blog Post Workflow Builder" heading
- Stepper shows 4 steps

**Result:** ✓ Pass / ✗ Fail

---

### Test 1.2: Verify Sidebar Navigation
**Steps:**
1. From any page, click "Workflows" in sidebar
2. Verify it shows as active (highlighted)
3. Navigate to another page (Dashboard)
4. Navigate back to Workflows

**Expected Result:**
- Workflows page is highlighted when active
- Navigation works smoothly
- State is preserved when returning

**Result:** ✓ Pass / ✗ Fail

---

## Section 2: Step 1 - Design Workflow

### Test 2.1: Load Available Phases
**Steps:**
1. Navigate to Workflows page
2. Wait for page to fully load
3. Observe the phase checkboxes

**Expected Result:**
- 4 phases are displayed:
  - ✓ blog_generate_content
  - ✓ blog_quality_evaluation
  - ✓ blog_search_image
  - ✓ blog_create_post
- All phases have descriptions visible
- All phases are checked by default

**Network Check:**
- API call: `GET /api/workflows/phases` ✓ Success

**Result:** ✓ Pass / ✗ Fail

---

### Test 2.2: Toggle Phases On/Off
**Steps:**
1. Start on Step 1 (Design)
2. Uncheck "blog_quality_evaluation"
3. Verify checkbox state
4. Check it again
5. Uncheck all phases

**Expected Result:**
- Checkbox states change immediately
- Next button disables when no phases selected
- Can re-enable phases
- UI responds smoothly to clicks

**Result:** ✓ Pass / ✗ Fail

---

### Test 2.3: Next Button Behavior
**Steps:**
1. Uncheck all phases
2. Observe "Next: Configure Parameters" button
3. Check at least one phase
4. Click the Next button

**Expected Result:**
- Button is disabled when no phases selected
- Button is enabled when ≥1 phase selected
- Clicking advances to Step 2

**Result:** ✓ Pass / ✗ Fail

---

## Section 3: Step 2 - Configure Parameters

### Test 3.1: View Configuration Form
**Steps:**
1. Click "Next: Configure Parameters" button
2. Observe form fields
3. Check default values

**Expected Result:**
- Form displays 4 fields:
  - Blog Topic (text field)
  - Content Style (dropdown)
  - Content Tone (dropdown)
  - Target Word Count (number field)
- Default values are:
  - Topic: "Artificial Intelligence in Healthcare"
  - Style: "balanced"
  - Tone: "professional"
  - Word Count: "1500"

**Result:** ✓ Pass / ✗ Fail

---

### Test 3.2: Update Blog Topic
**Steps:**
1. Clear the Topic field
2. Type: "Web Development Best Practices 2025"
3. Tab to next field
4. Verify value is saved

**Expected Result:**
- New topic text appears in field
- Value persists when moving to other fields
- Execute button becomes enabled (if it was disabled)

**Result:** ✓ Pass / ✗ Fail

---

### Test 3.3: Change Content Style
**Steps:**
1. Click Content Style dropdown
2. Select "technical"
3. Verify selection
4. Try other options: "narrative", "listicle", "thought-leadership"

**Expected Result:**
- Dropdown opens with all 5 options
- Selection updates immediately
- All options are functional

**Options to test:**
- [ ] balanced
- [ ] technical
- [ ] narrative
- [ ] listicle
- [ ] thought-leadership

**Result:** ✓ Pass / ✗ Fail

---

### Test 3.4: Change Content Tone
**Steps:**
1. Click Content Tone dropdown
2. Select "casual"
3. Verify selection
4. Try other options: "academic", "inspirational"

**Expected Result:**
- Dropdown opens with all 4 options
- Selection updates immediately
- All options are functional

**Options to test:**
- [ ] professional
- [ ] casual
- [ ] academic
- [ ] inspirational

**Result:** ✓ Pass / ✗ Fail

---

### Test 3.5: Update Word Count
**Steps:**
1. Clear Word Count field
2. Type "2500"
3. Try values: "500", "1000", "5000"
4. Try invalid: "100", "10000"

**Expected Result:**
- Valid values (500-5000) are accepted
- Invalid values may be rejected or warned

**Result:** ✓ Pass / ✗ Fail

---

### Test 3.6: Form Validation
**Steps:**
1. Clear all fields
2. Leave Topic empty
3. Try to click "Execute Workflow"

**Expected Result:**
- Execute button is disabled
- Error message appears (if field is invalid)
- Cannot proceed with empty topic

**Result:** ✓ Pass / ✗ Fail

---

### Test 3.7: Back Button
**Steps:**
1. Update Topic to: "Machine Learning Basics"
2. Change Style to: "technical"
3. Click "Back" button
4. Verify Step 1 phase selections

**Expected Result:**
- Returns to Step 1
- Phase selections are preserved
- Can click Next again to return to Step 2
- Configuration changes should be saved (or note that they reset)

**Result:** ✓ Pass / ✗ Fail

---

## Section 4: Step 3 - Execute Workflow

### Test 4.1: Execute Workflow
**Steps:**
1. Configure parameters as desired:
   - Topic: "Artificial Intelligence in Healthcare"
   - Style: "balanced"
   - Tone: "professional"
   - Word Count: "1500"
2. Click "Execute Workflow" button

**Expected Result:**
- Page advances to Step 3
- Execution summary displays:
  - Topic
  - Number of phases selected (4)
  - Style and Tone
  - "Start Workflow" button is ready

**Network Check:**
- API call: `POST /api/workflows/custom` ✓ Success

**Result:** ✓ Pass / ✗ Fail

---

### Test 4.2: Start Workflow Execution
**Steps:**
1. On Step 3, click "Start Workflow" button
2. Observe execution progress

**Expected Result:**
- Execution ID is displayed
- Progress bar appears
- Current phase is shown
- Status updates show "running"

**Network Check:**
- API calls polling: `GET /api/workflows/progress/{id}` every 2 seconds

**Result:** ✓ Pass / ✗ Fail

---

### Test 4.3: Monitor Progress
**Steps:**
1. Watch progress bar advance
2. Note phase names as they complete:
   - blog_generate_content (should be ~2-3 min)
   - blog_quality_evaluation (should be ~5-10 sec)
   - blog_search_image (should be ~3-5 sec)
   - blog_create_post (should be ~2-5 sec)

**Expected Result:**
- Progress percentage increases
- Different phases are shown
- Current phase updates
- UI is responsive

**Timing Estimates:**
- Total workflow: ~2.5-3.5 minutes

**Result:** ✓ Pass / ✗ Fail

---

### Test 4.4: Cancel Workflow (Optional)
**Steps:**
1. Start a workflow
2. Wait until it's in progress
3. Click "Cancel Workflow" button
4. Observe status change

**Expected Result:**
- Cancel button becomes available during execution
- Clicking cancels the workflow
- Status updates to "cancelled"
- Polling stops

**Network Check:**
- API call: `POST /api/workflows/executions/{id}/cancel` ✓ Success

**Result:** ✓ Pass / ✗ Fail

---

## Section 5: Step 4 - Results

### Test 5.1: View Workflow Results
**Steps:**
1. Wait for workflow to complete (or resume after previous tests)
2. Observe Step 4 results

**Expected Result:**
- Page advances to Step 4
- Displays "Workflow Results" heading
- Shows overall status: "completed"

**Result:** ✓ Pass / ✗ Fail

---

### Test 5.2: View Phase Results Table
**Steps:**
1. Look for "Phase Results" section
2. Review the table with columns:
   - Phase name
   - Status
   - Duration

**Expected Result:**
- Table shows all 4 phases
- All show "completed" status
- Duration times are reasonable:
  - blog_generate_content: ~2000-3000 ms
  - blog_quality_evaluation: ~500-1000 ms
  - blog_search_image: ~1000-2000 ms
  - blog_create_post: ~300-500 ms

**Result:** ✓ Pass / ✗ Fail

---

### Test 5.3: View Published Post Link
**Steps:**
1. Look for "Blog post created successfully!" message (green box)
2. Find "View Post" button or link
3. Click the link
4. Verify post appears on public site

**Expected Result:**
- Success message is displayed
- Link is provided to view post
- Clicking link shows the blog post
- Post content matches generated output
- Post is accessible on public site at `/posts/{slug}`

**Result:** ✓ Pass / ✗ Fail

---

### Test 5.4: Create New Workflow
**Steps:**
1. From results page, click "Create New Workflow"
2. Observe page returns to Step 1

**Expected Result:**
- Returns to Step 1: Design Workflow
- All phases are checked by default
- Form is ready for new workflow

**Result:** ✓ Pass / ✗ Fail

---

## Section 6: Workflow History

### Test 6.1: View Workflow History
**Steps:**
1. Stay on results page or any page
2. Scroll down to "Recent Workflow Executions" section
3. Observe table

**Expected Result:**
- Table shows recent workflows
- Columns display:
  - Date
  - Workflow name
  - Status (with color-coded chips)
  - Duration
  - Actions button
- At least the current workflow is in the list

**Result:** ✓ Pass / ✗ Fail

---

### Test 6.2: Refresh History
**Steps:**
1. Click "Refresh History" button
2. Observe table updates

**Expected Result:**
- Latest executions are fetched
- Table refreshes
- No errors displayed

**Network Check:**
- API call: `GET /api/workflows/executions` ✓ Success

**Result:** ✓ Pass / ✗ Fail

---

## Section 7: Error Handling & Edge Cases

### Test 7.1: Empty Topic Validation
**Steps:**
1. Go to Step 2: Configure Parameters
2. Clear the Topic field completely
3. Try to click "Execute Workflow"

**Expected Result:**
- Execute button is disabled
- Cannot proceed with empty topic
- (Optional) Error message appears

**Result:** ✓ Pass / ✗ Fail

---

### Test 7.2: Special Characters in Topic
**Steps:**
1. Enter topic: "AI's Impact: 2025 & Beyond!?"
2. Proceed through workflow
3. Check generated post URL/slug

**Expected Result:**
- Special characters are handled
- Post slug is sanitized: "ais-impact-2025-beyond"
- No errors during generation

**Result:** ✓ Pass / ✗ Fail

---

### Test 7.3: Very Long Topic
**Steps:**
1. Enter long topic: "The Comprehensive Guide to Understanding the Latest Developments in Artificial Intelligence and Machine Learning in Contemporary Healthcare Systems"
2. Proceed through workflow

**Expected Result:**
- Long topic is handled
- No truncation or errors
- Post is created successfully

**Result:** ✓ Pass / ✗ Fail

---

### Test 7.4: Network Error During Execution
**Steps:**
1. Start a workflow
2. While running, disconnect internet or close connection
3. Observe UI behavior

**Expected Result:**
- UI gracefully handles timeout
- Error message appears
- User can retry or go back

**Result:** ✓ Pass / ✗ Fail

---

### Test 7.5: Multiple Concurrent Workflows
**Steps:**
1. Start a workflow
2. Before completion, start another in different tab/window
3. Monitor both executions

**Expected Result:**
- Both workflows execute independently
- No conflicts or race conditions
- Both complete successfully

**Result:** ✓ Pass / ✗ Fail

---

## Section 8: Performance Testing

### Test 8.1: Page Load Time
**Steps:**
1. Open DevTools Performance tab
2. Navigate to Workflows page
3. Measure load time

**Expected Result:**
- Initial load: < 2 seconds
- Content: < 3 seconds

**Result:** ✓ Pass / ✗ Fail / Time: _____ ms

---

### Test 8.2: Phase List Loading
**Steps:**
1. Measure time from page open to phases displayed
2. Check Network tab for API call timing

**Expected Result:**
- Phases load within 1 second
- API call `GET /api/workflows/phases`: < 500ms

**Result:** ✓ Pass / ✗ Fail / Time: _____ ms

---

### Test 8.3: Progress Updates
**Steps:**
1. During workflow execution, check for UI lag
2. Monitor console for errors
3. Check Network tab for poll frequency

**Expected Result:**
- Progress updates every ~2 seconds
- No memory leaks
- CPU usage is reasonable
- No console errors

**Result:** ✓ Pass / ✗ Fail

---

## Section 9: Browser Compatibility

### Test 9.1: Chrome
**Steps:**
1. Open Workflows in Chrome
2. Test full workflow cycle

**Expected Result:**
- All features work
- No console errors
- Responsive design works

**Result:** ✓ Pass / ✗ Fail

---

### Test 9.2: Firefox
**Steps:**
1. Open Workflows in Firefox
2. Test full workflow cycle

**Expected Result:**
- All features work
- No console errors
- Responsive design works

**Result:** ✓ Pass / ✗ Fail

---

### Test 9.3: Safari
**Steps:**
1. Open Workflows in Safari
2. Test full workflow cycle

**Expected Result:**
- All features work
- No console errors
- Responsive design works

**Result:** ✓ Pass / ✗ Fail

---

## Section 10: Responsive Design

### Test 10.1: Desktop View (1920x1080)
**Steps:**
1. Set browser to 1920x1080
2. Test all pages and interactions

**Expected Result:**
- All elements visible
- No horizontal scrolling
- Layout is optimal

**Result:** ✓ Pass / ✗ Fail

---

### Test 10.2: Tablet View (768x1024)
**Steps:**
1. Set browser to tablet size
2. Test all pages and interactions

**Expected Result:**
- All elements visible and accessible
- Touch interactions work
- Layout adapts properly

**Result:** ✓ Pass / ✗ Fail

---

### Test 10.3: Mobile View (375x667)
**Steps:**
1. Set browser to mobile size
2. Test all pages and interactions

**Expected Result:**
- Sidebar collapses or becomes overlay
- Form fields are tap-friendly
- No elements are cut off

**Result:** ✓ Pass / ✗ Fail

---

## Section 11: Authentication & Authorization

### Test 11.1: Unauthenticated Access
**Steps:**
1. Clear auth token from localStorage
2. Navigate to `/workflows`

**Expected Result:**
- Redirected to login page
- Cannot access workflows without auth

**Result:** ✓ Pass / ✗ Fail

---

### Test 11.2: Session Expiry
**Steps:**
1. Let session expire (or manually expire token)
2. Try to execute workflow

**Expected Result:**
- Redirected to login
- Clear error message

**Result:** ✓ Pass / ✗ Fail

---

## Section 12: Data Integrity

### Test 12.1: Generated Content Quality
**Steps:**
1. Complete a full workflow
2. View the published post
3. Verify content quality

**Expected Result:**
- Content is relevant to topic
- Grammar and spelling are correct
- Content is well-structured
- No duplicate content from multiple executions

**Result:** ✓ Pass / ✗ Fail

---

### Test 12.2: Quality Evaluation Accuracy
**Steps:**
1. Complete workflow
2. Check quality score in results
3. Verify score matches content quality

**Expected Result:**
- Quality score is between 0-100
- Score reflects actual content quality
- Passing threshold (70+) is appropriate

**Result:** ✓ Pass / ✗ Fail

---

### Test 12.3: Image Attribution
**Steps:**
1. Complete workflow
2. View published post
3. Check featured image

**Expected Result:**
- Image is present and relevant
- Photographer is credited
- Source (Pexels) is attributed
- Image rights are respected

**Result:** ✓ Pass / ✗ Fail

---

## Test Summary

### Completion Checklist
- [ ] All 60+ tests completed
- [ ] All critical tests passing
- [ ] No critical bugs found
- [ ] User experience is smooth
- [ ] Performance is acceptable
- [ ] No console errors

### Test Results Summary
| Section | Tests | Passed | Failed |
|---------|-------|--------|--------|
| 1. Navigation | 2 | __ | __ |
| 2. Step 1 Design | 3 | __ | __ |
| 3. Step 2 Configure | 7 | __ | __ |
| 4. Step 3 Execute | 4 | __ | __ |
| 5. Step 4 Results | 4 | __ | __ |
| 6. History | 2 | __ | __ |
| 7. Error Handling | 5 | __ | __ |
| 8. Performance | 3 | __ | __ |
| 9. Browsers | 3 | __ | __ |
| 10. Responsive | 3 | __ | __ |
| 11. Auth | 2 | __ | __ |
| 12. Data Integrity | 3 | __ | __ |
| **TOTAL** | **42** | **__** | **__** |

### Overall Status
- ✓ Ready for Production
- ⚠ Minor Issues - Ready with caveats
- ✗ Blocking Issues - Not Ready

### Sign-Off
- Tested By: _______________
- Date: _______________
- Approved By: _______________

---

## Known Issues & Notes

Document any issues found:

```
Issue #1:
Description:
Severity: Critical / High / Medium / Low
Steps to Reproduce:
Workaround:
Notes:
```

---

## Follow-Up Actions

- [ ] All tests passed - Ready for deployment
- [ ] Minor issues filed - deployment possible with caveats
- [ ] Critical issues found - requires fixes before deployment
- [ ] Performance optimization needed
- [ ] UX improvements suggested

---

## Additional Notes

Use this section for any observations, suggestions, or comments about the testing experience.

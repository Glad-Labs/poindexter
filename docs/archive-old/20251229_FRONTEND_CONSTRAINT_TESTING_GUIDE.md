# Frontend Constraint Integration - Testing Guide

**Purpose:** Validate all constraint UI/UX features work end-to-end  
**Scope:** CreateTaskModal → ResultPreviewPanel → TaskDetailModal  
**Time Estimate:** 15-20 minutes

---

## Pre-Test Checklist

- [ ] All 3 services running (`npm run dev` from project root)
  - Backend (port 8000)
  - Public Site (port 3000)
  - Oversight Hub (port 3001)
- [ ] `.env.local` has at least one LLM API key set
- [ ] PostgreSQL running and accessible
- [ ] Browser cache cleared (hard refresh: Ctrl+Shift+R)

---

## Test 1: Task Creation Form - Constraint Fields

### Objective

Verify constraint fields are present and functional in task creation form.

### Steps

1. **Open Oversight Hub**
   - Navigate to `http://localhost:3001`
   - Verify auth token is present

2. **Create New Blog Post Task**
   - Click "Create New Task"
   - Select "📝 Blog Post" task type
   - Verify constraint fields appear:
     - [ ] Word Count (300-5000, default 1500)
     - [ ] Writing Style (select dropdown with 5 options)
     - [ ] Word Count Tolerance (slider, 5-20%, default 10)
     - [ ] Enforce Constraints (checkbox)

3. **Test Word Count Field**
   - [ ] Enter "500" → Accepted
   - [ ] Enter "250" → Should show error or min validation
   - [ ] Enter "6000" → Should show error or max validation
   - [ ] Clear field → Should use default 1500

4. **Test Writing Style Dropdown**
   - [ ] Click dropdown → Shows 5 options:
     - [ ] Technical
     - [ ] Narrative
     - [ ] Listicle
     - [ ] Educational
     - [ ] Thought-leadership
   - [ ] Select "listicle" → Confirms selection

5. **Test Word Count Tolerance Slider**
   - [ ] Slider shows 5-20% range
   - [ ] Shows percentage value next to slider
   - [ ] Move slider left (5%) → Value updates
   - [ ] Move slider right (20%) → Value updates
   - [ ] Default position (10%) → Correct

6. **Test Enforce Constraints Checkbox**
   - [ ] Checkbox appears as toggle
   - [ ] Unchecked (default) → Task allows violations
   - [ ] Checked → Task fails on violations
   - [ ] Description shows: "If enabled, task fails if constraints are violated"

### Expected Result

All constraint fields render correctly with proper input types, defaults, and validation.

---

## Test 2: Task Submission - Payload Validation

### Objective

Verify constraint data is included in task submission payload.

### Steps

1. **Fill Blog Post Form**
   - Topic: "AI in Healthcare"
   - Keywords: "AI, healthcare, medical"
   - Target Audience: "Medical Professionals"
   - Word Count: "1500"
   - Writing Style: "educational"
   - Word Count Tolerance: "10"
   - Enforce Constraints: Checked (true)

2. **Open Browser DevTools**
   - Press F12 → Network tab
   - Filter by "tasks" to see API calls

3. **Submit Form**
   - Select model configuration (any option)
   - Click "Create Task"

4. **Check Network Request**
   - [ ] POST request to `/api/tasks/` or `/api/tasks/blog`
   - [ ] Request body includes `content_constraints` object:
     ```javascript
     {
       "content_constraints": {
         "word_count": 1500,
         "writing_style": "educational",
         "word_count_tolerance": 10,
         "strict_mode": true
       }
     }
     ```

5. **Check Response**
   - [ ] Status 200 or 201
   - [ ] Response includes task ID
   - [ ] Response includes `constraint_compliance` object (may be null initially)

### Expected Result

Task payload correctly includes content_constraints with all 4 parameters.

---

## Test 3: Task Approval - Compliance Display

### Objective

Verify ConstraintComplianceDisplay renders in ResultPreviewPanel during approval.

### Steps

1. **Wait for Task Completion**
   - Task should move to `awaiting_approval` status
   - Typically takes 2-5 minutes depending on backend
   - Check Oversight Hub task list → Status changes

2. **Open Task for Approval**
   - Find the blog post task in task list
   - Click "Review" or "View Details"

3. **Locate Compliance Display**
   - [ ] Scroll through ResultPreviewPanel
   - [ ] Find "Compliance Metrics" section (new, before approval section)
   - [ ] Compliance display shows:
     - [ ] Word count box: Shows actual vs target (e.g., "1523 / 1500")
     - [ ] Progress bar with color:
       - Green if within tolerance (±10%)
       - Orange if slightly over/under
       - Red if violation
     - [ ] Percentage variance (e.g., "+1.5%")
     - [ ] Writing style: Shows selected style
     - [ ] Strict mode: Shows "Enforced" or "Permissive"

4. **Check Phase Breakdown (If Available)**
   - [ ] Optional table shows word count per phase:
     - [ ] Research phase
     - [ ] Create phase
     - [ ] Critique phase
     - [ ] Refine phase
     - [ ] Image phase
     - [ ] Publish phase

5. **Verify Compliance Status**
   - [ ] Status indicator shows: ✅ (compliant), ⚠️ (warning), or ❌ (violation)
   - [ ] Color matches severity
   - [ ] Alerts visible if violations exist

### Expected Result

ConstraintComplianceDisplay renders cleanly with all metrics visible in approval flow.

---

## Test 4: Task Details - Historical Compliance

### Objective

Verify ConstraintComplianceDisplay shows in TaskDetailModal for completed tasks.

### Steps

1. **Navigate to Task History**
   - From Oversight Hub, find previously completed task
   - Click task to open detail modal

2. **Verify Compliance Section**
   - [ ] Task detail modal opens
   - [ ] After task metadata (status, ID, audience, URL)
   - [ ] Shows "Compliance Metrics" section
   - [ ] Displays same compliance data as approval view

3. **Check Phase Breakdown**
   - [ ] If available, shows phase-by-phase breakdown
   - [ ] Same format as ResultPreviewPanel version

### Expected Result

TaskDetailModal shows compliance data for historical reference.

---

## Test 5: Constraint Enforcement - Edge Cases

### Objective

Test constraint behavior with various inputs and scenarios.

### Scenario A: Tolerance Just Within Range

1. Set Word Count: 1500, Tolerance: 10%
2. Backend generates: 1510 words
3. Expected: ✅ Compliant (within +10%)

### Scenario B: Tolerance Exceeded

1. Set Word Count: 1500, Tolerance: 10%
2. Backend generates: 1700 words
3. Expected:
   - If strict_mode=false: ⚠️ Warning (acceptable)
   - If strict_mode=true: ❌ Violation (task fails)

### Scenario C: Minimum Word Count

1. Set Word Count: 300, Tolerance: 10%
2. Backend generates: 280 words
3. Expected: ⚠️ Warning (-6.7%, below min)

### Scenario D: Writing Style Enforcement

1. Set Writing Style: "technical"
2. Check that all phases use technical style
3. Expected: Consistent tone throughout content

### Steps for Each Scenario

1. Create task with specific constraint values
2. Wait for completion
3. Review compliance metrics in approval panel
4. Verify actual output matches expected status
5. Check console for any errors (F12 → Console)

---

## Test 6: Form Validation

### Objective

Verify form prevents invalid constraint inputs.

### Tests

1. **Word Count Validation**
   - [ ] Enter "299" → Min validation prevents or warns
   - [ ] Enter "5001" → Max validation prevents or warns
   - [ ] Enter "abc" → Non-numeric rejected
   - [ ] Leave blank → Uses default 1500

2. **Tolerance Validation**
   - [ ] Slider only allows 5-20%
   - [ ] Cannot manually type outside range
   - [ ] Default 10% pre-selected

3. **Style Validation**
   - [ ] Dropdown only shows valid options
   - [ ] Cannot submit with empty selection (required)
   - [ ] Default "educational" pre-selected

4. **Strict Mode Validation**
   - [ ] Checkbox always true/false
   - [ ] Cannot submit invalid state
   - [ ] Default false (unchecked)

### Expected Result

All constraint fields have proper validation preventing invalid states.

---

## Test 7: Component Rendering - Visual

### Objective

Verify UI components match design specifications.

### Checks

1. **ConstraintComplianceDisplay Component**
   - [ ] Card layout with padding p-4 to p-6
   - [ ] Header: "Compliance Metrics" text-cyan-400
   - [ ] Progress bar color matches status:
     - ✅ Green for compliant
     - ⚠️ Orange for warning
     - ❌ Red for violation
   - [ ] Text readable on dark background (gray-800)
   - [ ] Responsive on mobile/tablet

2. **CreateTaskModal Form**
   - [ ] Constraint section properly spaced
   - [ ] Field labels: text-gray-300, font-medium
   - [ ] Input backgrounds: bg-gray-700
   - [ ] Focus states: focus:ring-2 focus:ring-cyan-500
   - [ ] Help text: text-gray-400, text-xs

3. **ResultPreviewPanel**
   - [ ] Compliance display placed before approval section
   - [ ] Border separator: border-t border-gray-700 pt-4
   - [ ] No overlapping with other UI elements
   - [ ] Scrolling works if content large

4. **TaskDetailModal**
   - [ ] Compliance section renders after metadata
   - [ ] Modal scrollable if too tall
   - [ ] Proper spacing between sections

### Expected Result

All components render cleanly with proper styling, colors, and responsive design.

---

## Test 8: Error Scenarios

### Objective

Verify graceful error handling for edge cases.

### Scenarios

1. **Missing Constraint Data**
   - Backend doesn't return `constraint_compliance` (old task)
   - Expected: ConstraintComplianceDisplay doesn't render
   - No errors in console

2. **Partial Compliance Data**
   - Backend returns only `word_count`, missing other fields
   - Expected: Component handles gracefully, shows available data
   - No console errors

3. **Invalid Phase Breakdown**
   - Phase breakdown data is malformed
   - Expected: Table doesn't render, main display still works
   - No component crash

4. **Network Error During Submission**
   - Form submission fails (network error)
   - Expected: Error message shows to user
   - Constraint data properly formatted in request before error

### Expected Result

All error scenarios handled gracefully without console errors or UI crashes.

---

## Test 9: Browser Compatibility

### Objective

Verify UI works across different browsers.

### Browsers to Test

- [ ] Chrome/Chromium (latest)
- [ ] Firefox (latest)
- [ ] Safari (if Mac available)
- [ ] Edge (latest)

### Checks

- [ ] Form fields render correctly
- [ ] Sliders work smoothly
- [ ] Colors display consistently
- [ ] No layout shifts
- [ ] No console errors
- [ ] Responsive design works

---

## Test 10: Performance

### Objective

Verify constraint UI doesn't impact performance.

### Checks

1. **Form Load Time**
   - [ ] Task creation modal opens in <1 second
   - [ ] Form fields render without lag
   - [ ] No jank when typing in fields

2. **Compliance Display Render**
   - [ ] ConstraintComplianceDisplay renders in <500ms
   - [ ] Phase breakdown table renders in <500ms
   - [ ] No performance degradation with large phase lists

3. **Browser DevTools - Performance Tab**
   - [ ] Record page load from Oversight Hub
   - [ ] No long tasks (>50ms) from constraint UI
   - [ ] Main thread not blocked during render

### Expected Result

All constraint UI components perform smoothly without noticeable lag.

---

## Troubleshooting

### Problem: Constraint Fields Not Visible

**Solution:**

- Verify blog_post task type is selected
- Check CreateTaskModal.jsx has field definitions
- Clear browser cache (Ctrl+Shift+R)
- Check console (F12) for errors

### Problem: Form Data Not Submitted

**Solution:**

- Check network tab (F12 → Network) for request
- Verify formData includes `content_constraints` object
- Check field values are valid (word count 300-5000, etc.)
- Look for validation errors in form

### Problem: Compliance Display Not Showing

**Solution:**

- Verify task status is "awaiting_approval"
- Check task.constraint_compliance exists in response
- Open browser console (F12) and search for "Compliance"
- Check ResultPreviewPanel.jsx has import statement

### Problem: Slider Not Working

**Solution:**

- Verify field type is 'range' in taskTypes definition
- Check range input HTML rendering (range not number)
- Test in different browser
- Check min/max values are correct

### Problem: Strict Mode Checkbox Issues

**Solution:**

- Verify field type is 'checkbox' (not select)
- Check formData value is boolean (true/false)
- Verify task payload converts correctly: `const strictMode = formData.strict_mode === true || formData.strict_mode === 'true'`
- Check TaskDetailModal rendering updates state correctly

---

## Sign-Off Checklist

After completing all tests, check off:

- [ ] Test 1: Constraint fields render correctly
- [ ] Test 2: Task payload includes constraints
- [ ] Test 3: Compliance display shows in approval
- [ ] Test 4: Historical compliance visible in details
- [ ] Test 5: All constraint scenarios work
- [ ] Test 6: Form validation working
- [ ] Test 7: Visual design matches specs
- [ ] Test 8: Error handling graceful
- [ ] Test 9: Cross-browser compatible
- [ ] Test 10: Performance acceptable
- [ ] No console errors (F12)
- [ ] No network errors (F12 → Network)
- [ ] All actions respond within 1 second
- [ ] UI responsive on mobile/tablet
- [ ] Ready for production ✅

---

## Notes for Testers

1. **Timing:** Tasks typically take 2-5 minutes to complete. Start task, then continue with other tests while waiting.

2. **Data Persistence:** All compliance data is persisted in PostgreSQL. Historical compliance data remains even after page refresh.

3. **API Debugging:** Use browser Network tab to inspect exact request/response payloads for constraint data.

4. **Backend Logs:** Check backend terminal (`npm run dev:cofounder`) for constraint validation logs.

5. **Visual Testing:** The ConstraintComplianceDisplay uses Material-UI components, which may render slightly differently across browsers due to CSS defaults.

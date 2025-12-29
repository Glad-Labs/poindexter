# Image Generation Improvements - Test & Verification Plan

**Date:** December 17, 2025  
**Status:** ✅ Code Changes Verified  
**Goal:** Test 3-layer image generation improvements + approval workflow integration

---

## ✅ Code Changes Verified

### 1. seo_content_generator.py ✅

- Location: Line 188
- Change: "NO PEOPLE - Do not include any human figures, faces, or portraits"
- Status: ✅ Confirmed

### 2. pexels_client.py ✅

- New Method: `_is_content_appropriate()` at line 52
- Patterns: `inappropriate_patterns` list at line 64
- Integration: Called in `search_images()` at line 123
- Status: ✅ Confirmed

### 3. image_service.py ✅

- Concepts: `concept_keywords` at line 330
- Filtering: "person", "people", "portrait", "face", "human" at line 341
- Search: Multi-level strategy at line 347
- Status: ✅ Confirmed

---

## 🧪 Test Scenarios

### Test Scenario 1: Tech Article (NO PEOPLE requirement)

**Topic:** "AI-Powered NPCs in Games"
**Expected:** Image showing game interface/technology, NOT people

**Steps:**

1. Create task: "Write about AI-Powered NPCs in Games"
2. Generate featured image
3. Check Oversight Hub ApprovalQueue
4. Verify:
   - ✅ NO people in image
   - ✅ Relevant to gaming/AI topic
   - ✅ Professional quality

**Success Criteria:**

- Image shows technology/game concept
- No human figures/faces
- Appropriate for blog post

---

### Test Scenario 2: Pexels Filtering (NSFW removal)

**Trigger:** Search that might return inappropriate results
**Expected:** Inappropriate images filtered out

**Steps:**

1. Monitor logs during image search
2. Look for: "Filtered out X inappropriate images"
3. Verify results are clean
4. Check in ApprovalQueue

**Success Criteria:**

- Logs show filtering activity
- No NSFW/adult images in results
- Only appropriate images shown

**What to Look For in Logs:**

```
Pexels search for 'technology' returned 5 results
Filtered out 2 inappropriate images
✅ Found featured image for 'AI-Powered NPCs in Games'
```

---

### Test Scenario 3: Search Strategy (Multi-level)

**Topic:** "Business Productivity with AI"
**Expected:** Search tries multiple queries

**Steps:**

1. Create task with topic
2. Monitor logs for search queries
3. Count how many different searches attempted
4. Note which one succeeded

**Success Criteria:**

- Multiple search queries attempted
- At least 3 different strategies tried
- Concept keywords used (technology, digital, etc.)
- Found good result

**What to Look For in Logs:**

```
Searching Pexels for: 'Business Productivity with AI'
Searching Pexels for: 'technology'
Searching Pexels for: 'digital'
Searching Pexels for: 'Business Productivity with AI technology'
✅ Found featured image using query: 'technology'
```

---

### Test Scenario 4: Approval Workflow Integration

**Topic:** Any article
**Expected:** Image flows through existing approval system

**Steps:**

1. Create task → Generate image
2. Go to Oversight Hub ApprovalQueue
3. Verify task appears
4. Verify image displays
5. Click Approve
6. Verify task published

**Success Criteria:**

- ✅ Task appears in ApprovalQueue
- ✅ Featured image displays correctly
- ✅ Image quality meets standards
- ✅ Approval button works
- ✅ Task published successfully

---

### Test Scenario 5: SDXL Fallback (NO PEOPLE)

**Trigger:** When Pexels can't find good image
**Expected:** SDXL generates image with "NO PEOPLE" prompt

**Steps:**

1. Create task with niche topic
2. If Pexels fails to find image
3. SDXL should generate fallback
4. Verify: NO people in generated image

**Success Criteria:**

- SDXL fallback activated
- Generated image shows concept, not people
- Image useful for article

---

## 📊 Metrics to Collect

### Image Search Metrics

```
Total searches: _____
- Pexels success: _____ (%)
- SDXL fallback: _____ (%)

Filtering metrics:
- Total images fetched: _____
- Images filtered (inappropriate): _____
- Filter rate: _____ (%)
```

### Content Quality Metrics

```
Reviewed images: _____
- Appropriate: _____ (%)
- Relevant to topic: _____ (%)
- Professional quality: _____ (%)

Issues found:
- People in images: _____
- NSFW content: _____
- Irrelevant to topic: _____
```

### Approval Workflow Metrics

```
Tasks in ApprovalQueue: _____
- Approved: _____ (%)
- Rejected: _____ (%)
- Pending: _____

Issues:
- Image display problems: _____
- Metadata missing: _____
- CDN upload fails: _____
```

---

## 🔍 Monitoring Logs

### Where to Find Logs

**Backend (Co-founder Agent):**

```bash
# Terminal where agent is running
# Look for INFO messages with image generation details
```

### What to Search For

```
# Image prompt generation
"Generating image prompt for:"
"NO PEOPLE"

# Pexels searching
"Searching Pexels for:"
"Filtered out"
"Found featured image"

# SDXL generation
"Generating image with SDXL"
"Generated image:"
"Saved locally to:"

# Search strategy
"Trying concept fallback:"
"Multi-level search"
```

---

## 📋 Test Execution Checklist

### Pre-Test

- [ ] Services running (Agent, Hub, Database)
- [ ] Code changes verified (all 3 files)
- [ ] Pexels API key configured
- [ ] SDXL GPU available
- [ ] Terminal ready for log monitoring

### Test 1: Tech Article (NO PEOPLE)

- [ ] Create task in UI
- [ ] Generate image
- [ ] Check image in ApprovalQueue
- [ ] Verify: NO people
- [ ] Record result: \_\_\_\_

### Test 2: Filtering Activity

- [ ] Monitor logs during search
- [ ] Look for filter messages
- [ ] Count filtered images
- [ ] Record result: \_\_\_\_

### Test 3: Search Strategy

- [ ] Monitor log for multiple queries
- [ ] Count different searches
- [ ] Note which succeeded
- [ ] Record result: \_\_\_\_

### Test 4: Approval Workflow

- [ ] Task appears in queue
- [ ] Image displays
- [ ] Click approve
- [ ] Verify published
- [ ] Record result: \_\_\_\_

### Test 5: SDXL Fallback

- [ ] Use niche topic
- [ ] Wait for SDXL
- [ ] Verify: NO people
- [ ] Check quality
- [ ] Record result: \_\_\_\_

### Post-Test

- [ ] Collect metrics
- [ ] Review logs
- [ ] Document issues
- [ ] Note improvements

---

## 🎯 Success Criteria (Overall)

| Criterion                | Expected    | Status |
| ------------------------ | ----------- | ------ |
| Images without people    | 95%+        | ☐      |
| No NSFW content          | 100%        | ☐      |
| Relevant to topic        | 90%+        | ☐      |
| Approval workflow works  | 100%        | ☐      |
| SDXL fallback works      | When needed | ☐      |
| Search strategy attempts | 3+ queries  | ☐      |
| Professional quality     | 90%+        | ☐      |

---

## 🚀 How to Run Tests

### Option 1: Manual (Recommended for first test)

1. Open Oversight Hub in browser
2. Create new task manually
3. Monitor logs in terminal
4. Review image in ApprovalQueue
5. Approve and verify

### Option 2: Automated (Create test script)

```bash
# Create multiple test tasks
# Monitor logs and collect results
# Generate report
```

### Option 3: Bulk Testing (For metrics)

1. Create 5-10 tasks with varied topics
2. Generate all images
3. Collect metrics
4. Review in ApprovalQueue
5. Approve all
6. Document results

---

## 📝 Issue Tracking

If issues found, record here:

### Issue Template

```
**Issue:** [Description]
**Scenario:** [Which test triggered it]
**Expected:** [What should happen]
**Actual:** [What happened]
**Logs:** [Relevant log lines]
**Fix:** [Proposed fix]
**Status:** [New/In Progress/Fixed]
```

### Known Non-Issues

- None yet (first test cycle)

---

## 🔄 Next Steps After Testing

### If All Tests Pass ✅

1. Document results
2. Deploy to production
3. Monitor for 1 week
4. Adjust if needed

### If Issues Found 🔧

1. Document issue
2. Review logs
3. Modify code if needed
4. Re-test
5. Repeat until fixed

### Phase 2 (After validation)

1. Multi-image generation variations
2. User selection UI
3. Regenerate button
4. Image quality metrics

---

## 📊 Test Results Summary Template

```
Test Date: _______________
Tester: ___________________

Test Scenarios Passed: ___ / 5

Metrics:
- Images with NO people: ___%
- NSFW filtered: ___%
- Relevant to topic: ___%
- Approval workflow: ✅ / ❌
- Average search queries: ____

Issues Found: ___
Critical: ___
Minor: ___

Recommendation:
[ ] Ready for production
[ ] Needs fixes
[ ] Schedule retest

Next Actions:
1. _______________________
2. _______________________
3. _______________________
```

---

**Status:** ✅ Ready to Test  
**Services:** ✅ Running  
**Code Changes:** ✅ Verified  
**Next Action:** Execute Test Scenario 1

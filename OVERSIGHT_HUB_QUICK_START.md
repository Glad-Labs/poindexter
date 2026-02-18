# Oversight Hub Workflow Testing - Quick Start Summary

**Created:** February 17, 2026  
**Purpose:** Guide for stepping through Oversight Hub UI and testing all workflow types

---

## 📋 What's Available

### 5 Complete Workflow Types

| Type | Duration | Quality Bar | Auto-Publish | Phase Count |
|------|----------|-------------|--------------|-------------|
| Social Media | 5 min | 70% | ✅ Yes | 5 |
| Email | 4 min | 75% | ❌ No | 4 |
| Blog Post | 15 min | 75% | ❌ No | 7 |
| Market Analysis | 10 min | 80% | ❌ No | 5 |
| Newsletter | 20 min | 80% | ❌ No | 7 |

### 7-Point Quality Assessment

Every workflow is evaluated on:
1. **Clarity** - Clear, understandable language
2. **Accuracy** - Factually correct
3. **Completeness** - All elements included
4. **Relevance** - Matches topic
5. **SEO Quality** - Keyword optimization
6. **Readability** - Easy to scan
7. **Engagement** - Compelling content

**Overall Score** = Average of above 7 dimensions (0-100 scale)

---

## 🚀 Quick Start - 3 Options

### Option 1: UI Interactive Testing (Easiest)

**Prerequisites:**
- All services running: `npm run dev`
- Navigate to: `http://localhost:3001`

**Steps:**
1. Login (GitHub OAuth or provided credentials)
2. Go to **Marketplace** → **Workflows**
3. Click **Workflow History** tab
4. View executed workflows and their results
5. Click **View Details** to see phase outputs
6. Check **Statistics** tab for aggregate metrics
7. Review **Performance** tab for execution trends

**Full guide:** See `OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md`

---

### Option 2: Command-Line API Testing (Fastest)

**Quick commands:**

```bash
# Check if backend is running
curl -s http://localhost:8000/health | python3 -m json.tool

# List available workflows
curl -s -X POST http://localhost:8000/api/workflows/templates \
  -H "Content-Type: application/json" -d '{}'

# Create test workflow (social media)
curl -s -X POST http://localhost:8000/api/workflows/custom \
  -H "Content-Type: application/json" -d '{
    "name": "Quick Test",
    "description": "Test",
    "phases": [{"name": "draft", "agent": "content_agent", "timeout_seconds": 60}],
    "task_input": {"topic": "AI Orchestration", "platform": "twitter", "tone": "professional"}
  }'
```

**Interactive script:**

```bash
# Make the script executable
chmod +x ./oversight_hub_workflow_test.sh

# Run in interactive mode
./oversight_hub_workflow_test.sh

# Or run specific commands
./oversight_hub_workflow_test.sh health
./oversight_hub_workflow_test.sh templates
./oversight_hub_workflow_test.sh social
./oversight_hub_workflow_test.sh batch
```

---

### Option 3: Hybrid Testing (Recommended)

1. **Start with API testing** to understand workflow structure
2. **View results in UI** to see the interface
3. **Review quality assessments** for each workflow type
4. **Compare performance metrics** to expected values

---

## 🎯 Testing Checklist

### Before You Start

- [ ] Backend running: `curl http://localhost:8000/health` returns `{"status":"ok"}`
- [ ] Oversight Hub accessible: Browse to `http://localhost:3001`
- [ ] Authenticated (logged in to Oversight Hub UI)
- [ ] PostgreSQL is running and accessible

### Workflow Execution Tests

- [ ] **Social Media** - Execute and verify quick 5-min turnaround
  - Expected: Quality score ≥ 70%, Content ~280 characters
  - Check: Engaging, platform-appropriate tone

- [ ] **Email** - Execute and verify 4-min execution
  - Expected: Quality score ≥ 75%, Content ~350 words
  - Check: Clear CTA, professional tone

- [ ] **Blog Post** - Execute and verify 15-min execution
  - Expected: Quality score ≥ 75%, Content ~1500 words
  - Check: Comprehensive sections, proper headings

- [ ] **Market Analysis** - Execute and verify 10-min execution
  - Expected: Quality score ≥ 80%, Data-driven insights
  - Check: Citations, fact-based conclusions

- [ ] **Newsletter** - Execute and verify 20-min execution
  - Expected: Quality score ≥ 80%, Content ~2000 words
  - Check: Curated content, clear sections

### UI Component Tests

- [ ] **Workflow History Tab**
  - Loads without errors
  - Shows multiple workflow types
  - Status badges display correctly
  - Timestamps are reasonable
  - Can click "View Details"

- [ ] **Statistics Tab**
  - Shows total executed count
  - Success rate percentage displayed
  - Breakdown by workflow type
  - Numbers update on refresh

- [ ] **Performance Tab**
  - Shows execution time averages
  - Trend lines display correctly
  - Quality score trends visible
  - No errors on time range changes

- [ ] **Execution Details Modal**
  - All phases show results
  - Quality breakdown shows 7 scores
  - Content is readable and relevant
  - No null/empty critical fields
  - Duration times are realistic

### Quality Framework Tests

- [ ] Quality scores are in 0-100 range
- [ ] Overall score = average of 7 dimensions (within 1-2 points)
- [ ] Workflows below threshold marked for review
- [ ] High-quality workflows show consistent scores across dimensions
- [ ] Different workflow types have different quality emphases
  - Blog: Clarity and Completeness high
  - Social Media: Engagement and Readability high
  - Email: Clarity and Relevance high

### Performance Validation

**Expected Execution Times (±10%):**
- Social Media: 300 ± 30 seconds
- Email: 240 ± 24 seconds  
- Market Analysis: 600 ± 60 seconds
- Blog Post: 900 ± 90 seconds
- Newsletter: 1200 ± 120 seconds

**Quality Trends:**
- Average quality should be ≥ 75%
- Success rate (COMPLETED/total) ≥ 80%
- No systematic failures in any phase

---

## 🔍 What to Look For

### ✅ Positive Indicators

1. **Workflows execute to completion**
   - Status changes from PENDING → EXECUTING → COMPLETED
   - Total duration roughly matches estimate
   - All phases show output

2. **Quality scores are reasonable**
   - Scores in 0-100 range
   - Breakdown across 7 dimensions visible
   - Scores improve through refinement phases

3. **Content is high quality**
   - Well-structured with clear sections
   - Relevant to specified topic
   - Appropriate tone for platform
   - No obvious hallucinations or errors

4. **UI responds smoothly**
   - No loading spinners stuck
   - Modals open/close properly
   - Tabs switch content correctly
   - No console errors (Open DevTools → Console)

5. **Metrics are consistent**
   - Statistics numbers make sense
   - Performance trends are smooth (not erratic)
   - Quality averages align with individual scores

### ❌ Red Flags

1. **Workflows fail or timeout**
   - Status stuck in EXECUTING
   - Duration exceeds 2x estimated time
   - FAILED status or error messages

2. **Quality scores are unrealistic**
   - All scores reporting as 0 or 100
   - Breakdown doesn't average to overall score
   - Same score for all dimensions

3. **Content quality is poor**
   - Incoherent or rambling text
   - Wrong topic or tone
   - Obvious hallucinations ("This is a story about...")
   - Missing key sections

4. **UI shows errors**
   - 500 errors in network tab (F12 → Network)
   - "Failed to load" messages
   - Missing data in tables
   - Console errors (F12 → Console)

5. **Performance is degraded**
   - Execution times 2-3x higher than expected
   - Quality scores trending down
   - High failure rate (>20% of workflows)

---

## 📊 Data Points to Collect

While testing, note:

1. **Per Workflow Type:**
   - Actual execution time (vs. estimated)
   - Quality score achieved
   - Phases that took longest
   - Any phase failures

2. **Quality Assessment:**
   - 7-point breakdown (Clarity, Accuracy, Completeness, Relevance, SEO, Readability, Engagement)
   - Overall score calculation accuracy
   - Correlation between individual scores and overall
   - Content quality subjective assessment

3. **System Performance:**
   - Time to first phase output
   - Total workflow completion time
   - Peak resource usage (if monitoring)
   - Error messages (if any)

4. **UI Usability:**
   - Time to load Workflow History page
   - Time to view execution details
   - Responsiveness of tab switching
   - Accuracy of displayed metrics

---

## 🐛 If Something Doesn't Work

### Workflow Doesn't Execute

**Check:**
1. Backend is running: `curl http://localhost:8000/health`
2. PostgreSQL is accessible and has schema
3. At least one LLM provider is configured (Ollama, OpenAI, etc.)
4. Check backend logs for error messages

**Solution:**
```bash
# Restart backend
npm run dev:cofounder

# Watch logs while running test
npm run dev:cofounder --log-level debug
```

### UI Doesn't Load

**Check:**
1. Oversight Hub is running: `curl http://localhost:3001`
2. You're logged in (check browser's localStorage)
3. No console errors (F12 → Console tab)
4. Network requests are succeeding (F12 → Network tab)

**Solution:**
```bash
# Restart Oversight Hub
npm run dev:oversight

# Clear browser cache and refresh
# Press Ctrl+Shift+Delete then Ctrl+Shift+R
```

### Quality Scores Are Wrong

**Check:**
1. All 7 dimensions are scored
2. Overall score = average of 7 (within 1-2 points)
3. Quality service is initialized (check backend logs)
4. LLM provider is responding (check `/health` endpoint)

**Solution:**
```bash
# Verify quality assessment is working
curl -s http://localhost:8000/api/workflows/statistics | python3 -m json.tool | grep -i quality

# Check LLM provider health
curl -s http://localhost:11434/api/tags  # for Ollama
```

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| `OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md` | Comprehensive step-by-step UI testing guide |
| `oversight_hub_workflow_test.sh` | Command-line API testing tool (interactive & batch) |
| This file | Quick reference and checklist |

---

## 🎓 Key Concepts

### Workflow Phases

Each workflow type has specialized phases:
- **Research** - Gather background, sources
- **Draft** - Create initial content
- **Assess** - Evaluate quality (7-point framework)
- **Refine** - Improve based on assessment
- **Analyze** - Data-driven insights (market workflows)
- **Image Selection** - Find/generate visuals
- **Publish** - Format and distribute

### Quality Framework

The 7-point assessment ensures content meets standards:
- Each dimension scored 0-100
- Workflows below threshold require refinement
- High-performing workflows can auto-publish
- Scores guide improvement suggestions

### Auto-Publish Rules

| Workflow | Threshold | Auto-Publish |
|----------|-----------|--------------|
| Social Media | 70% | ✅ Yes |
| Email | 75% | ❌ No |
| Blog Post | 75% | ❌ No |
| Newsletter | 80% | ❌ No |
| Market Analysis | 80% | ❌ No |

---

## 🚀 Next Steps After Testing

1. **Document Results** - Note any issues found
2. **Performance Baseline** - Record execution times for reference
3. **Quality Profiles** - Note typical quality scores per workflow
4. **Optimization** - Identify slow phases or low-quality areas
5. **Production Readiness** - Verify meets all quality standards

---

## ✉️ Support

If you encounter issues:

1. **Check the comprehensive guide:** `OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md` (Section: Troubleshooting)
2. **Review backend logs:** All services output errors to console
3. **Test via API:** Use `oversight_hub_workflow_test.sh` to isolate issues
4. **Check configuration:** Verify `.env.local` has required values


# Oversight Hub Workflow Testing Guide

**Date:** February 17, 2026  
**Purpose:** Step-by-step guide to test workflows through the Oversight Hub UI and backend API

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Service Status Check](#service-status-check)
3. [Authentication Setup](#authentication-setup)
4. [Available Workflows](#available-workflows)
5. [UI Testing Path](#ui-testing-path)
6. [API Testing](#api-testing)
7. [Test Scenarios](#test-scenarios)
8. [Quality Assessment](#quality-assessment)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Ensure all services are running:
- ✅ Backend (FastAPI) running on port 8000
- ✅ Oversight Hub (React) running on port 3001
- ✅ PostgreSQL database accessible
- ✅ At least one LLM provider configured (Ollama, OpenAI, Anthropic, Google)

**Quick Check:**
```bash
curl http://localhost:8000/health
curl http://localhost:3001  # Should load React app
```

---

## Service Status Check

### Backend Health Status

All three components should be running:

**Check Backend:**
```bash
curl -s http://localhost:8000/health
# Expected: {"status":"ok","service":"cofounder-agent"}
```

**Check Available Workflow Templates:**
```bash
curl -s -X POST http://localhost:8000/api/workflows/templates \
  -H "Content-Type: application/json" \
  -d '{}'
```

Response shows 5 available workflows:
- `blog_post` - 900 seconds, quality threshold 0.75
- `social_media` - 300 seconds, quality threshold 0.70
- `email` - 240 seconds, quality threshold 0.75
- `newsletter` - 1200 seconds, quality threshold 0.80
- `market_analysis` - 600 seconds, quality threshold 0.80

---

## Authentication Setup

The Oversight Hub UI requires authentication to:
1. Create and submit workflows
2. View execution history and details
3. Monitor workflow progress

### Option A: Use Browser Login (Recommended for UI Testing)

1. Navigate to `http://localhost:3001` in your browser
2. Click **Login** on the initial screen
3. Choose authentication method:
   - **GitHub OAuth** - Click "Login with GitHub" (requires GH_OAUTH_CLIENT_ID configured)
   - **JWT Token** - Manually provide Bearer token if available
   - **Demo Mode** - Some instances support test/demo login

### Option B: Generate JWT Token (For API Testing)

If you need to test via API:

```bash
# Request a token (endpoint structure varies by implementation)
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "test-user",
    "password": "test-password"
  }'

# Response will include:
# {
#   "access_token": "eyJhbGciOiJIUzI1NiIs...",
#   "token_type": "bearer",
#   "expires_in": 3600
# }
```

Store the token and use in subsequent API calls:
```bash
export AUTH_TOKEN="<your-token-here>"
```

---

## Available Workflows

### 1. Blog Post Workflow

**Duration:** 900 seconds (15 minutes)  
**Quality Threshold:** 0.75 (75%)  
**Target Output:** 1500 words  
**Requires Approval:** Yes

**Phases:**
- Research (gather background/sources)
- Draft (write initial content)
- Assess (evaluate quality)
- Refine (improve based on feedback)
- Finalize (prepare for publishing)
- Image Selection (find/generate images)
- Publish (post to platform)

**Best For:** Long-form content, detailed analysis, guides

---

### 2. Social Media Workflow

**Duration:** 300 seconds (5 minutes)  
**Quality Threshold:** 0.70 (70%)  
**Target Output:** 280 characters  
**Requires Approval:** No (auto-publish)

**Phases:**
- Research (trending topics, context)
- Draft (write social post)
- Assess (check engagement potential)
- Finalize (optimize hashtags)
- Publish (post immediately)

**Best For:** Quick posts, announcements, engagement

---

### 3. Email Workflow

**Duration:** 240 seconds (4 minutes)  
**Quality Threshold:** 0.75 (75%)  
**Target Output:** 350 words  
**Requires Approval:** Yes

**Phases:**
- Draft (write email body)
- Assess (review tone, CTA clarity)
- Finalize (format, add metadata)
- Publish (send/queue)

**Best For:** Marketing emails, newsletters, professional communication

---

### 4. Newsletter Workflow

**Duration:** 1200 seconds (20 minutes)  
**Quality Threshold:** 0.80 (80%)  
**Target Output:** 2000 words  
**Requires Approval:** Yes

**Phases:**
- Research (gather news, articles, trends)
- Draft (write newsletter content)
- Assess (review layout, readability)
- Refine (improve sections)
- Finalize (add headers, footers)
- Image Selection (select/optimize images)
- Publish (send or schedule)

**Best For:** Comprehensive newsletters, curated content

---

### 5. Market Analysis Workflow

**Duration:** 600 seconds (10 minutes)  
**Quality Threshold:** 0.80 (80%)  
**Requires Approval:** Yes

**Phases:**
- Research (gather market data)
- Assess (analyze findings)
- Analyze (create insights)
- Report (format analysis)
- Publish (distribute report)

**Best For:** Market research, competitive analysis, data-driven insights

---

## UI Testing Path

### Step 1: Navigate to the Oversight Hub

1. Open browser and go to `http://localhost:3001`
2. You should see the main Oversight Hub interface
3. If prompted, complete authentication (see [Authentication Setup](#authentication-setup))

### Step 2: Access the Workflow Builder

**Location:** Main Navigation → **Marketplace** → **Workflows**

The Workflow Builder interface has 3 tabs:
- **Workflow History** - View past workflow executions
- **Statistics** - Aggregate workflow metrics
- **Performance** - Execution timing and success rates

### Step 3: View Workflow History (Tab 1)

1. Click **Workflow History** tab
2. Observe the table with columns:
   - Execution ID
   - Workflow Type (blog_post, social_media, etc.)
   - Status (PENDING, RUNNING, COMPLETED, FAILED)
   - Start Time / Duration
   - Quality Score (if completed)
   - Actions (View Details)

**What to Look For:**
- ✅ Table loads successfully (no errors)
- ✅ Multiple workflow types shown
- ✅ Status indicators correctly color-coded
- ✅ Timestamps are reasonable
- ✅ Quality scores appear for completed workflows

### Step 4: View Execution Details

1. Find a **COMPLETED** workflow in the history table
2. Click **View Details** or the row itself
3. A modal dialog should open showing:
   - Execution ID
   - Workflow type
   - Input parameters (topic, tone, style, etc.)
   - Phase results:
     * Research output
     * Draft content
     * Quality assessment scores
     * Refinement changes
     * Final output
     * Image metadata
   - Overall quality score
   - Start/end times
   - Total duration

**What to Look For:**
- ✅ All phases show results (not null/empty)
- ✅ Quality scores are realistic (0-100 scale)
- ✅ Content is coherent and relevant
- ✅ Images have proper metadata
- ✅ No error messages or failed phases

### Step 5: View Statistics (Tab 2)

1. Click **Statistics** tab
2. Should display:
   - Total workflows executed
   - Success rate (percentage)
   - Failed workflows count
   - Average quality score across all workflows
   - Workflows by type (breakdown)
   - Status distribution pie chart

**What to Look For:**
- ✅ Numbers make sense (success rate 0-100%)
- ✅ All workflow types represented
- ✅ Statistics update when new workflows complete
- ✅ Charts render without errors

### Step 6: View Performance (Tab 3)

1. Click **Performance** tab
2. Select time range (7d, 30d, 90d, all)
3. Should show:
   - Average execution time per workflow type
   - Min/max execution times
   - Success rate over time (trend chart)
   - Performance vs. quality scatter plot
   - Execution latency breakdown

**What to Look For:**
- ✅ Execution times are realistic:
   - Social media: 300 ± 30 seconds
   - Email: 240 ± 20 seconds
   - Blog: 900 ± 90 seconds
   - Newsletter: 1200 ± 120 seconds
   - Market: 600 ± 60 seconds
- ✅ Quality scores trend upward (iterative improvement)
- ✅ Success rate stays above acceptable threshold

---

## API Testing

### Test 1: Get Workflow Templates

```bash
curl -s -X POST http://localhost:8000/api/workflows/templates \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

**Expected Response:**
- Array of 5 workflow objects
- Each has name, description, phases array, duration, metadata

---

### Test 2: Get Workflow History (with Auth)

```bash
curl -s http://localhost:8000/api/workflows/history?limit=10 \
  -H "Authorization: Bearer $AUTH_TOKEN" | python3 -m json.tool
```

**Expected Response:**
- executions array with workflow execution objects
- Each includes: id, workflow_type, status, start_time, duration, quality_score

---

### Test 3: Get Workflow Statistics

```bash
curl -s http://localhost:8000/api/workflows/statistics \
  -H "Authorization: Bearer $AUTH_TOKEN" | python3 -m json.tool
```

**Expected Response:**
- total_executed: number
- total_succeeded: number
- total_failed: number
- average_quality_score: 0-100
- by_workflow_type: object with counts per type

---

### Test 4: Get Execution Details

```bash
EXECUTION_ID="<id-from-history>"
curl -s http://localhost:8000/api/workflow/$EXECUTION_ID/details \
  -H "Authorization: Bearer $AUTH_TOKEN" | python3 -m json.tool
```

**Expected Response:**
- execution_id
- workflow_type
- input_data (original parameters)
- phase_results array:
  * phase_name
  * status
  * duration_ms
  * output (content/results)
- overall_quality_score
- quality_breakdown (7-point assessment)

---

### Test 5: Get Performance Metrics

```bash
curl -s "http://localhost:8000/api/workflows/performance?range=30d" \
  -H "Authorization: Bearer $AUTH_TOKEN" | python3 -m json.tool
```

**Expected Response:**
- metrics_by_workflow_type object:
  * avg_duration_ms
  * min_duration_ms
  * max_duration_ms
  * success_rate
  * avg_quality_score
- trend_data array with daily/weekly aggregates

---

## Test Scenarios

### Scenario 1: Basic Social Media Workflow (Fastest)

**Goal:** Test a complete workflow execution end-to-end

**Steps:**

1. **Create Workflow Request** (if UI supports it)
   ```bash
   curl -s -X POST http://localhost:8000/api/workflows/custom \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $AUTH_TOKEN" \
     -d '{
       "name": "Test Social Media Content",
       "description": "Testing social media workflow",
       "phases": [
         {
           "name": "draft",
           "agent": "content_agent",
           "timeout_seconds": 60
         }
       ],
       "task_input": {
         "topic": "AI Orchestration Benefits",
         "platform": "twitter",
         "tone": "professional"
       }
     }'
   ```

2. **Monitor in UI:**
   - Navigate to Workflow History
   - Look for your workflow in the list (might need to refresh)
   - Watch status change from PENDING → EXECUTING → COMPLETED
   - Verify quality score appears

3. **View Results:**
   - Click "View Details" on completed workflow
   - Examine the generated content
   - Check quality assessment scores

---

### Scenario 2: Blog Post Workflow (Comprehensive)

**Goal:** Test a multi-phase, long-running workflow

**Expected Timeline:**
- 0-2 min: Research phase (gathering sources)
- 2-7 min: Draft phase (writing content)
- 7-9 min: Assess phase (quality evaluation)
- 9-14 min: Refine phase (improvements)
- 14-15 min: Image/Publish phases

**Testing in UI:**
1. Look for blog_post workflows in history
2. Sort by most recent
3. Watch status changes in real-time (if implemented with WebSocket)
4. Check that quality score is **≥ 0.75**
5. Verify all 7 phases show results

---

### Scenario 3: Email Workflow (Quality Assessment Focus)

**Goal:** Validate the quality assessment framework

**Quality Dimensions to Check:**

The 7-point quality assessment evaluates:

1. **Clarity** - Clear, understandable language (0-100)
2. **Accuracy** - Factually correct information (0-100)
3. **Completeness** - All elements included (0-100)
4. **Relevance** - Content matches topic (0-100)
5. **SEO Quality** - Keywords, readability (0-100)
6. **Readability** - Easy to scan/read (0-100)
7. **Engagement** - Compelling, interesting (0-100)

**Overall Score** = Average of 7 dimensions

**Test in UI:**
1. Find completed email workflows
2. Click "View Details"
3. Look for quality breakdown showing individual scores
4. Verify:
   - Each dimension scored 0-100
   - Overall score = average (e.g., if all are 80, overall should be 80)
   - Email-specific dimensions (Clarity, Engagement) are high

---

### Scenario 4: Monitor Real-Time Progress

**Goal:** Watch a workflow execute in real-time

**Implementation Details:**
The backend supports WebSocket connections at `ws://localhost:8000/api/workflows/{workflow_id}`

**Manual Testing:**
1. Start watching Workflow History
2. Trigger a new workflow (if possible through UI or API)
3. Refresh page frequently to see status updates
4. Observe:
   - Status transitions: PENDING → EXECUTING → COMPLETED
   - Phase progress indicators
   - Duration accumulation
   - Quality score appearance (when complete)

---

### Scenario 5: Error Handling

**Goal:** Test workflow error scenarios

**Test Cases:**
1. **Invalid topic** - Empty or extremely long topic (>5000 chars)
2. **Timeout** - Very short timeout value (1 second)
3. **Invalid workflow type** - Request non-existent workflow
4. **Database failure** - Kill PostgreSQL connection (observe graceful failure)

**Expected Behavior:**
- UI shows error message (not blank loading)
- Error details are readable
- Graceful fallback to previous state
- Workflow marked as FAILED in history

---

## Quality Assessment

### Quality Framework Details

Each workflow execution receives a 7-point quality assessment. Workflows passing the threshold can auto-publish; those below require refinement.

**Quality Thresholds by Workflow Type:**
- Blog Post: **0.75** (75%) - Requires human approval
- Social Media: **0.70** (70%) - Auto-publishes if passing
- Email: **0.75** (75%) - Requires human approval
- Newsletter: **0.80** (80%) - Requires human approval
- Market Analysis: **0.80** (80%) - Requires human approval

### Scoring Rubric

| Score | Rating | Interpretation |
|-------|--------|-----------------|
| 90-100 | Excellent | Production-ready with minimal review |
| 80-89 | Good | Ready with minor sign-off |
| 70-79 | Acceptable | Meets requirements, some refinement suggested |
| 60-69 | Marginal | Below threshold, requires refinement |
| 0-59 | Poor | Severe issues, major rewrite needed |

### Quality Indicators to Check

✅ **Pass Indicators:**
- Well-structured content with clear sections
- Proper grammar and spelling
- Relevant examples and supporting details
- Appropriate tone for platform
- SEO-friendly (keywords naturally included)
- Engaging opening and closing
- Proper CTAs (calls-to-action)

❌ **Fail Indicators:**
- Incoherent or rambling content
- Spelling/grammar errors
- Hallucinated information (false claims)
- Wrong tone for platform
- Too short or too long
- Missing key information
- Weak or missing CTA

---

## Troubleshooting

### Issue: WorkflowBuilder Component Not Loading

**Symptoms:**
- Blank page in Marketplace → Workflows
- Error loading workflow data
- CircularProgress spinner stuck

**Solutions:**

1. **Check backend connectivity:**
   ```bash
   curl -s http://localhost:8000/health
   ```

2. **Verify authentication:**
   - Ensure you're logged in (see browser storage: localStorage with auth token)
   - Try logging out and back in

3. **Check browser console:**
   - F12 → Console tab
   - Look for error messages about failed API calls
   - Common errors:
     * "401 Unauthorized" - Auth token expired
     * "404 Not Found" - Wrong endpoint path
     * "CORS" - Cross-origin request blocked

4. **Restart services:**
   ```bash
   # Restart backend
   npm run dev:cofounder
   
   # Restart Oversight Hub
   npm run dev:oversight
   ```

---

### Issue: Workflow Execution Returns 501 Not Implemented

**Symptoms:**
- Attempt to execute workflow returns error message
- Status code 501 in network tab

**Explanation:**
The template execution endpoint (`/api/workflows/execute/{template_name}`) is not yet fully implemented. This is a known limitation.

**Workaround:**
- Use custom workflow creation with `/api/workflows/custom`
- Most UI viewing/monitoring features work even if execution isn't available

---

### Issue: Quality Scores Not Showing

**Symptoms:**
- Completed workflows show null/empty quality_score
- Quality breakdown missing in details

**Likely Causes:**
1. Quality service not initialized
2. LLM provider timeout during assessment
3. Database persistence issue

**Solutions:**
1. Check backend logs for errors
2. Verify LLM provider is accessible:
   ```bash
   # For Ollama
   curl -s http://localhost:11434/api/tags
   
   # For OpenAI
   curl -s https://api.openai.com/v1/models \
     -H "Authorization: Bearer $OPENAI_API_KEY"
   ```

3. Examine database quality_assessments table:
   ```bash
   psql $DATABASE_URL -c "SELECT * FROM quality_assessments ORDER BY created_at DESC LIMIT 5;"
   ```

---

### Issue: Unable to Get Authorization Token

**Symptoms:**
- Login fails
- "Invalid or expired token" errors on API calls
- Can't authenticate at all

**Solutions:**

1. **Check auth provider configuration:**
   ```bash
   echo $GH_OAUTH_CLIENT_ID
   echo $GH_OAUTH_CLIENT_SECRET
   ```

2. **Verify JWT secret is set:**
   ```bash
   echo $JWT_SECRET
   ```

3. **Check database has users table:**
   ```bash
   psql $DATABASE_URL -c "\dt users"
   ```

4. **Create test user directly (if DB access available):**
   ```bash
   psql $DATABASE_URL -c "INSERT INTO users (username, email) VALUES ('test-user', 'test@example.com');"
   ```

---

### Issue: Workflows Execute But Quality Scores Are Always Low

**Symptoms:**
- Quality scores consistently < 0.5
- Quality breakdown shows low individual scores
- Content quality appears acceptable but scoring system is harsh

**Root Causes:**
1. Quality assessment thresholds set too strict
2. LLM evaluation criteria misaligned with actual content
3. Missing feedback loops to improve quality

**Solutions:**
1. Review quality assessment configuration
2. Examine several failed/passing examples to understand pattern
3. Adjust quality thresholds if needed
4. Check that feedback loops are activ (Refine phase should improve scores)

---

## End-to-End Test Checklist

- [ ] Backend health check passes
- [ ] Navigate to http://localhost:3001 successfully
- [ ] Authentication works (login succeeds)
- [ ] Marketplace → Workflows section loads
- [ ] Workflow History tab displays past workflows
- [ ] At least one COMPLETED workflow visible
- [ ] Can click "View Details" on a completed workflow
- [ ] Execution details modal shows all phase results
- [ ] Quality score is visible and 0-100 range
- [ ] Statistics tab shows aggregate metrics
- [ ] Performance tab shows execution time trends
- [ ] No console errors or 5xx HTTP errors
- [ ] Timestamps are recent/reasonable
- [ ] Quality assessment includes 7-point breakdown
- [ ] Workflows by type breakdown matches data
- [ ] Success rate is > 70% (acceptable threshold)

---

## Next Steps

After completing this testing guide:

1. **Document Findings** - Note any issues encountered
2. **Quality Assessment** - Verify 7-point quality framework is working
3. **Performance Validation** - Check execution times match estimates
4. **Production Readiness** - Confirm all workflows meet quality thresholds


# Oversight Hub Workflow Testing - Master Index

**Updated:** February 17, 2026  
**Purpose:** Complete guide to testing workflows through the Oversight Hub UI and API

---

## 📖 Documentation Overview

This testing suite includes 4 key documents:

### 1. **OVERSIGHT_HUB_QUICK_START.md** ⭐ START HERE
- 3 testing options (UI, API, Hybrid)
- Quick reference checklist
- What to look for (✅/❌ indicators)
- Troubleshooting quick tips

**Use this if:** You want to get started immediately and need a quick overview

### 2. **OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md** 📚 COMPREHENSIVE GUIDE
- Detailed prerequisites and setup
- Step-by-step UI navigation (all 3 tabs)
- Complete API endpoint reference (with curl examples)
- 5 detailed test scenarios
- Quality assessment framework details
- Full troubleshooting section

**Use this if:** You want detailed instructions for each workflow type and need deeper understanding

### 3. **oversight_hub_workflow_test.sh** 🛠️ AUTOMATION TOOL
- Interactive command-line tool
- Pre-built test scenarios
- Batch testing mode
- Quality analysis automation

**Use this if:** You prefer command-line testing or want to run tests in bulk without UI interaction

### 4. **OVERSIGHT_HUB_TEST_RESULTS_TEMPLATE.md** 📋 DATA COLLECTION
- Structured form for documenting results
- All 5 workflow types with expected vs actual
- UI component test checklist
- Quality framework validation
- Performance benchmarking metrics

**Use this if:** You need to systematically collect test data for reporting

---

## 🚀 Getting Started (5 Minutes)

### Prerequisites
```bash
# Ensure services are running
npm run dev

# If using bash script tools
chmod +x ./oversight_hub_workflow_test.sh
```

### Option A: UI Testing (Easiest)
1. Open `http://localhost:3001` in browser
2. Follow step-by-step instructions in OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md (Section: UI Testing Path)
3. Complete checklist in OVERSIGHT_HUB_QUICK_START.md (Section: Testing Checklist)

### Option B: API Testing (Fastest)
```bash
# Quick health check
curl -s http://localhost:8000/health | python3 -m json.tool

# List templates
curl -s -X POST http://localhost:8000/api/workflows/templates \
  -H "Content-Type: application/json" -d '{}'

# Interactive testing
./oversight_hub_workflow_test.sh
```

### Option C: Structured Testing (Most Thorough)
1. Use OVERSIGHT_HUB_TEST_RESULTS_TEMPLATE.md to document each test
2. Follow procedures in OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md
3. Use oversight_hub_workflow_test.sh for API calls
4. Collect metrics for analysis

---

## 📊 Workflow Types At a Glance

| Workflow | Duration | Quality Bar | Auto-Publish | Phases | Best For |
|----------|----------|-------------|--------------|--------|----------|
| **Social Media** | 5 min | 70% | ✅ | 5 | Quick posts, announcements |
| **Email** | 4 min | 75% | ❌ | 4 | Marketing emails, newsletters |
| **Blog Post** | 15 min | 75% | ❌ | 7 | Long-form, detailed analysis |
| **Market Analysis** | 10 min | 80% | ❌ | 5 | Research, competitive analysis |
| **Newsletter** | 20 min | 80% | ❌ | 7 | Comprehensive newsletters |

---

## ✅ Quick Testing Checklist

### Before You Start
- [ ] Backend running on port 8000
- [ ] Oversight Hub running on port 3001
- [ ] PostgreSQL accessible
- [ ] At least one LLM provider configured

### Workflow Tests
- [ ] Social Media - 5 min test
- [ ] Email - 4 min test
- [ ] Blog Post - 15 min test
- [ ] Market Analysis - 10 min test
- [ ] Newsletter - 20 min test

### UI Tests
- [ ] Workflow History tab loads
- [ ] Statistics tab displays metrics
- [ ] Performance tab shows trends
- [ ] Can view execution details

### Quality Tests
- [ ] Quality scores in 0-100 range
- [ ] 7-point breakdown visible
- [ ] Overall = average of 7 dimensions
- [ ] Thresholds enforced (70-80%)

---

## 🔍 Key Testing Concepts

### Quality Framework
Each workflow is evaluated on **7 dimensions**:
1. **Clarity** - Clear, understandable language
2. **Accuracy** - Factually correct information
3. **Completeness** - All required elements
4. **Relevance** - Matches topic
5. **SEO Quality** - Keyword optimization
6. **Readability** - Easy to scan
7. **Engagement** - Compelling content

**Overall Score** = Average of 7 dimensions (0-100 scale)

### Quality Thresholds
- **Social Media: 70%** - Auto-publishes if passing
- **Email: 75%** - Requires approval
- **Blog: 75%** - Requires approval
- **Newsletter: 80%** - Requires approval
- **Market: 80%** - Requires approval

### Workflow Phases
```
Research → Draft → Assess → Refine → Finalize → Image Selection → Publish
```
- Each phase builds on previous output
- Assess phase provides feedback for Refine phase
- Quality scored at completion

---

## 🎯 Test Scenarios

### Scenario 1: Fast Validation (5 minutes)
**Goal:** Quickly verify system is working

```bash
# Check health
curl -s http://localhost:8000/health

# List templates
curl -s -X POST http://localhost:8000/api/workflows/templates \
  -H "Content-Type: application/json" -d '{}'
```

### Scenario 2: UI Walkthrough (20 minutes)
**Goal:** Understand the Oversight Hub interface

1. Navigate to http://localhost:3001
2. Go to Marketplace → Workflows
3. View Workflow History (existing executions)
4. Click one execution → View Details
5. Check Statistics tab
6. Review Performance tab

### Scenario 3: Complete Testing (1-2 hours)
**Goal:** Comprehensive validation of all features

1. Test each workflow type (5 workflows × avg 10 min = 50 min)
2. Verify UI components (20 min)
3. Validate quality framework (15 min)
4. Document results in template (15 min)

### Scenario 4: Batch Testing (30 minutes automated)
**Goal:** Test multiple workflows without manual UI interaction

```bash
./oversight_hub_workflow_test.sh batch
```

---

## 📋 Document Navigation

### By Use Case

**"I want to understand the UI quickly"**
→ OVERSIGHT_HUB_QUICK_START.md → Section: "UI Interactive Testing"

**"I need detailed step-by-step instructions"**
→ OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md → Section: "UI Testing Path"

**"I want to test via command line"**
→ oversight_hub_workflow_test.sh → Run `./oversight_hub_workflow_test.sh`

**"I need to document my testing"**
→ OVERSIGHT_HUB_TEST_RESULTS_TEMPLATE.md → Fill in the form

**"Something isn't working"**
→ OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md → Section: "Troubleshooting"

### By Topic

**Workflows:**
- Overview: OVERSIGHT_HUB_QUICK_START.md → "5 Complete Workflow Types"
- Details: OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md → "Available Workflows"
- Testing: OVERSIGHT_HUB_TEST_RESULTS_TEMPLATE.md → Sections 2.1-2.5

**Quality Assessment:**
- Framework: OVERSIGHT_HUB_QUICK_START.md → "7-Point Quality Assessment"
- Details: OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md → "Quality Assessment"
- Validation: OVERSIGHT_HUB_TEST_RESULTS_TEMPLATE.md → Section 4

**UI Components:**
- Overview: OVERSIGHT_HUB_QUICK_START.md → "UI Interactive Testing"
- Details: OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md → "UI Testing Path"
- Checklist: OVERSIGHT_HUB_TEST_RESULTS_TEMPLATE.md → Section 3

**API Reference:**
- Quick: OVERSIGHT_HUB_QUICK_START.md → "Command-Line API Testing"
- Complete: OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md → "API Testing"
- Tool: oversight_hub_workflow_test.sh

**Troubleshooting:**
- Quick Fixes: OVERSIGHT_HUB_QUICK_START.md → "If Something Doesn't Work"
- Complete: OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md → "Troubleshooting"

---

## 🎓 Key Information

### Service Ports
- Backend: **8000** (http://localhost:8000)
- Oversight Hub: **3001** (http://localhost:3001)
- PostgreSQL: **5432** (localhost)

### Recommended Testing Order
1. **Health Check** (2 min) - Verify backend is up
2. **Templates List** (1 min) - See what's available
3. **UI Walkthrough** (10 min) - Navigate the interface
4. **Social Media Test** (5 min) - Fastest workflow
5. **Email Test** (4 min) - Quick test
6. **Blog Test** (15 min) - Comprehensive test
7. **Results Review** (10 min) - Document findings

**Total: ~50 minutes for complete testing**

---

## 🐛 Common Issues

| Issue | Quick Fix |
|-------|-----------|
| Backend not responding | `curl http://localhost:8000/health` → Restart if down |
| UI won't load | Clear browser cache (Ctrl+Shift+Delete) + Refresh |
| Quality scores wrong | Check LLM provider health → Verify 7 dimensions calculate |
| Execution times slow | Check resource usage → Verify LLM not throttled |
| API returns 401 | Ensure authentication header included → Get valid token |

**Full troubleshooting:** See documents section 5+ or "Troubleshooting" section in guides

---

## 📚 File Reference

```
.
├── OVERSIGHT_HUB_QUICK_START.md                    (Quick reference)
├── OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md         (Comprehensive)
├── oversight_hub_workflow_test.sh                  (CLI tool)
├── OVERSIGHT_HUB_TEST_RESULTS_TEMPLATE.md          (Data collection)
└── [This file]                                     (Index & navigation)
```

---

## 🚀 Quick Commands Reference

```bash
# Health checks
curl -s http://localhost:8000/health

# List templates
curl -s -X POST http://localhost:8000/api/workflows/templates \
  -H "Content-Type: application/json" -d '{}'

# Get history (requires auth)
curl -s http://localhost:8000/api/workflows/history?limit=10

# Run interactive testing tool
./oversight_hub_workflow_test.sh

# Run specific test
./oversight_hub_workflow_test.sh health
./oversight_hub_workflow_test.sh social
./oversight_hub_workflow_test.sh batch
```

---

## 📊 Expected Performance

| Metric | Expected | Acceptable |
|--------|----------|------------|
| Social Media Duration | 300s | 270-330s |
| Email Duration | 240s | 216-264s |
| Blog Duration | 900s | 810-990s |
| Newsletter Duration | 1200s | 1080-1320s |
| Market Duration | 600s | 540-660s |
| Avg Quality Score | >75% | >70% |
| Success Rate | >90% | >80% |

---

## ✨ Key Success Indicators

- ✅ All 5 workflow types execute to completion
- ✅ Quality scores average >75%
- ✅ Execution times within ±10% of estimates
- ✅ No error messages in UI or API
- ✅ Quality breakdown across 7 dimensions visible
- ✅ Overall quality = average of 7 scores
- ✅ Content is coherent and relevant
- ✅ UI components respond smoothly
- ✅ Database queries execute quickly
- ✅ LLM provider handles all requests

---

## 🎯 Success Criteria

**Testing is complete when:**
- [ ] All workflows execute successfully
- [ ] Quality assessment framework works correctly
- [ ] UI displays all information correctly
- [ ] API endpoints return expected data
- [ ] No critical errors encountered
- [ ] Results documented in template
- [ ] Performance meets targets

---

## 📞 Support Resources

**Before asking for help, check:**
1. OVERSIGHT_HUB_QUICK_START.md (Troubleshooting section)
2. OVERSIGHT_HUB_WORKFLOW_TESTING_GUIDE.md (Troubleshooting section)
3. Backend logs (check `npm run dev:cofounder` terminal output)
4. Browser console (F12 → Console tab)
5. Network requests (F12 → Network tab)

---

## 📝 Notes & Observations

Use this space to record any findings:

_________________________________________________________________
_________________________________________________________________
_________________________________________________________________
_________________________________________________________________

---

**Last Updated:** February 17, 2026  
**Version:** 1.0  
**Status:** Ready for Testing  


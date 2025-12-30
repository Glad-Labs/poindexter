# 🚀 Phase 2.5 Execution - Verify GitHub Secrets in CI/CD

**Date:** November 5, 2025  
**Phase:** 2.5 of 4  
**Status:** ⏳ READY TO EXECUTE  
**Estimated Time:** 15 minutes  
**Blocking Issues:** 0

---

## 📋 Quick Summary

**What we're doing:** Testing that the 5 GitHub Secrets we added are working in GitHub Actions workflows.

**Why it matters:** Without verification, we can't be sure the CI/CD pipeline will actually use the secrets when deploying to staging and production.

**Success criteria:** All 5 secrets are accessible, no "missing secret" errors, tests pass.

---

## ✅ Pre-Execution Checklist

Before starting Phase 2.5, verify these prerequisites are complete:

**Phase 1: Monorepo Fixes**

- ✅ Windows rimraf glob patterns fixed (explicit paths)
- ✅ Python removed from npm workspaces
- ✅ Package versions updated to 3.0.0
- ✅ Package names fixed (oversight-hub, strapi-cms)
- ✅ `npm run clean:install` works (2911 packages)

**Phase 1.5: Lock File Sync**

- ✅ `package-lock.json` regenerated with new package names
- ✅ `npm ci` works with workspaces
- ✅ Lock file committed to git (hash: fe33ba6a0)

**Phase 2: GitHub Secrets**

- ✅ All 5 secrets added to GitHub:
  - OPENAI_API_KEY (or Anthropic/Google)
  - RAILWAY_TOKEN
  - RAILWAY_PROD_PROJECT_ID
  - VERCEL_TOKEN
  - VERCEL_PROJECT_ID
- ✅ Secrets visible in GitHub Settings → Secrets and variables → Actions

**If ANY of above is not complete, STOP and go back to fix it before continuing.**

---

## 🎯 Step-by-Step Execution

### Step 1: Verify Secrets Exist in GitHub (2 minutes)

**Go to GitHub:**

1. Navigate to: https://github.com/Glad-Labs/glad-labs-codebase/settings/secrets/actions
2. Or: Repository → Settings → Secrets and variables → Actions

**Verify all 5 secrets are listed:**

```
✓ OPENAI_API_KEY           (or ANTHROPIC_API_KEY or GOOGLE_API_KEY)
✓ RAILWAY_TOKEN
✓ RAILWAY_PROD_PROJECT_ID
✓ VERCEL_TOKEN
✓ VERCEL_PROJECT_ID
```

**Expected:** Each secret shows:

- Name (exact spelling matters)
- "Last used: Never" or a recent timestamp
- A "Delete" button

**If missing:** Go back to add them (see GITHUB_SECRETS_QUICK_SETUP.md)

---

### Step 2: Trigger Test Workflow (2 minutes)

**Option A: Push to dev branch (Recommended)**

This most closely simulates what CI/CD will do in production.

```powershell
# In PowerShell from c:\Users\mattm\glad-labs-website

# Make a small test commit
git checkout dev
echo "# Phase 2.5 Verification $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" >> VERIFICATION_LOG.md
git add VERIFICATION_LOG.md
git commit -m "ci: trigger Phase 2.5 secret verification workflow"
git push origin dev
```

**Expected:** No errors from git.

**Option B: Manually Trigger from GitHub Actions (Alternative)**

If you prefer not to push changes:

1. Go to: GitHub → Actions → "Test on Dev Branch"
2. Click: "Run workflow" button
3. Select branch: `dev`
4. Click: "Run workflow"

**Expected:** Workflow appears in Actions tab (may take 30 seconds to show)

---

### Step 3: Monitor Workflow Execution (8 minutes)

**Watch the workflow run:**

1. Go to: GitHub → Actions tab
2. Find: Latest workflow run (should be "Test on Dev Branch" or similar)
3. Click: The workflow run to see details

**Workflow should show these steps:**

```
✓ Checkout code
✓ Setup Node.js
✓ Setup Python
✓ Install Node dependencies
✓ Install Python dependencies
✓ Run frontend tests
✓ Run backend tests
✓ Run linting
✓ Build check
✓ Testing complete for staging
```

**Look for status indicator:**

- ⏳ (Yellow) = In progress (wait)
- ✅ (Green) = Success (great!)
- ❌ (Red) = Failed (see Step 4 - troubleshooting)

**Estimated time:** 3-8 minutes for full workflow completion

---

### Step 4: Check Logs for Success or Errors (3 minutes)

**In the workflow run details, look for:**

#### ✅ GOOD SIGNS (Secrets are working):

```
✓ Checkout code
✓ Setup Node.js (v22)
✓ Setup Python (3.12)
✓ Install Node dependencies
✓ Install Python dependencies
✓ Run frontend tests (11 passed)
✓ Run backend tests (passed)
✓ Run linting (passed)
✓ Build check (succeeded)
✓ Testing complete for staging
```

No errors mentioning "RAILWAY_TOKEN", "VERCEL_TOKEN", "OPENAI_API_KEY", etc.

#### ❌ BAD SIGNS (Secrets not working):

Watch for these error messages in logs:

```
Error: RAILWAY_TOKEN is not defined
Error: VERCEL_TOKEN not found
Error: missing required secret OPENAI_API_KEY
Error: undefined variable RAILWAY_PROD_PROJECT_ID
Error: VERCEL_PROJECT_ID: undefined
```

Or:

```
401 Unauthorized - authentication failed
403 Forbidden - insufficient permissions
Connection refused - cannot reach Railway/Vercel
```

#### How to read logs:

1. Click: "Test on Dev Branch" job
2. Expand: Each step to see output
3. Search for: "error", "fail", "unauthorized", "missing"
4. Read: Full context of any errors

---

## 🔍 Verification Checklist

Use this checklist to confirm Phase 2.5 success:

```
GitHub Secrets Setup:
☐ OPENAI_API_KEY visible in GitHub Secrets
☐ RAILWAY_TOKEN visible in GitHub Secrets
☐ RAILWAY_PROD_PROJECT_ID visible in GitHub Secrets
☐ VERCEL_TOKEN visible in GitHub Secrets
☐ VERCEL_PROJECT_ID visible in GitHub Secrets

Workflow Execution:
☐ Workflow triggered successfully (no git errors)
☐ Workflow appears in GitHub Actions tab
☐ Workflow status: ✅ GREEN (not red)
☐ Workflow completes within 10 minutes

Log Analysis:
☐ No "missing secret" errors in logs
☐ No "undefined variable" errors in logs
☐ No "401 Unauthorized" errors
☐ No "403 Forbidden" errors
☐ Frontend tests pass (11 tests)
☐ Backend tests pass
☐ Linting passes
☐ Build check passes
☐ Final message: "Testing complete for staging"

Overall Result:
☐ ALL checks above are checked
☐ Ready to proceed to Phase 3
```

---

## ⚠️ Troubleshooting

### Issue: "Workflow not showing in Actions tab"

**Symptoms:**

- You pushed to dev, but no workflow appears
- Or: "Run workflow" doesn't start

**Solutions:**

1. Wait 1 minute - GitHub sometimes delays
2. Refresh page (F5)
3. Verify branch name is exactly "dev" (case-sensitive)
4. Check `.github/workflows/test-on-dev.yml` exists
5. Try manual trigger: Actions → Test on Dev Branch → Run workflow

### Issue: "missing secret" error in logs

**Symptoms:**

```
Error: RAILWAY_TOKEN is not defined
Error: VERCEL_TOKEN: undefined
```

**Solutions:**

1. Go to GitHub Settings → Secrets and verify secret exists
2. Check exact spelling (typos matter!)
3. Verify no extra spaces in secret value
4. Wait 30 seconds and retry workflow
5. If still failing: Delete secret and re-add it

### Issue: "401 Unauthorized" or "Connection refused"

**Symptoms:**

```
Authentication failed
403 Forbidden
Connection refused to Railway
Connection refused to Vercel
```

**Solutions:**

1. Verify secret value is correct (copy from provider dashboard again)
2. Check token hasn't expired (go to Railway/Vercel to verify)
3. Verify token has correct permissions
4. Test token manually:

   ```
   # For Railway:
   curl -H "Authorization: Bearer YOUR_RAILWAY_TOKEN" https://api.railway.app/

   # For Vercel:
   curl https://api.vercel.com/v1/projects -H "Authorization: Bearer YOUR_VERCEL_TOKEN"
   ```

### Issue: "Tests fail" but secrets look fine

**Symptoms:**

- Workflow is green
- Secrets not mentioned in errors
- Tests are failing

**Solutions:**

1. Check if tests were passing before (they should be)
2. Look at specific test failure (may be unrelated to secrets)
3. Run tests locally: `npm test`
4. This is NOT a secret issue - proceed to Phase 3 anyway

---

## ✅ Success Criteria

**Phase 2.5 is COMPLETE when:**

All of the following are true:

1. ✅ All 5 secrets visible in GitHub Settings
2. ✅ Workflow triggered successfully (no git errors)
3. ✅ Workflow status is GREEN ✅
4. ✅ Workflow completes within 10 minutes
5. ✅ No "missing secret" errors in logs
6. ✅ No "401 Unauthorized" errors
7. ✅ No "403 Forbidden" errors
8. ✅ Tests pass (or pass with expected skips)
9. ✅ Build succeeds
10. ✅ Final status: "Testing complete for staging"

---

## 🎯 After Phase 2.5 - What's Next

**When Phase 2.5 is successful:**

✅ Secrets are working → Phase 3 begins

**Phase 3: Documentation & Testing (2-3 hours)**

- Update 8 core documentation files
- Test staging deployment with secrets
- Review production readiness checklist
- Plan production deployment window

**Then Phase 4: Production Deployment (4-6 hours)**

- Execute production deployment
- Verify all services operational
- Monitor for errors

---

## 📊 Current Status Summary

```
Phase 1: ✅ COMPLETE - Monorepo fixes (2911 packages)
Phase 1.5: ✅ COMPLETE - Lock file sync (npm ci works)
Phase 2: ✅ COMPLETE - GitHub Secrets added (5 secrets)
Phase 2.5: ⏳ IN PROGRESS - Verify secrets (you are here)
Phase 3: ⏰ PENDING - Documentation & testing
Phase 4: ⏰ PENDING - Production deployment

Progress: 55% → ~57% (Phase 2.5 execution)
```

---

## 🔗 Key Resources

- **GitHub Secrets:** https://github.com/Glad-Labs/glad-labs-codebase/settings/secrets/actions
- **GitHub Actions:** https://github.com/Glad-Labs/glad-labs-codebase/actions
- **Workflow File:** `.github/workflows/test-on-dev.yml`
- **Setup Guide:** `GITHUB_SECRETS_QUICK_SETUP.md`
- **Verification:** `PHASE_2_5_VERIFICATION.md` (this file)

---

## ⏱️ Time Estimate Breakdown

| Step                 | Time       | Action                              |
| -------------------- | ---------- | ----------------------------------- |
| 1. Verify secrets    | 2 min      | Check GitHub Settings               |
| 2. Trigger workflow  | 2 min      | Push to dev OR manual trigger       |
| 3. Monitor execution | 8 min      | Watch workflow run (3-8 min actual) |
| 4. Check logs        | 3 min      | Verify success or troubleshoot      |
| **TOTAL**            | **15 min** | **Complete Phase 2.5**              |

---

## 🚀 Ready to Start?

**When you're ready, do this:**

1. ✅ Verify all 5 secrets in GitHub Settings
2. ✅ Push to dev branch OR manually trigger workflow
3. ✅ Monitor GitHub Actions for success
4. ✅ Report results

**Expected Outcome:** Workflow completes green ✅ with no secret errors

**Then:** Report success and we move to Phase 3 (documentation & testing)

---

**Status: Ready to execute Phase 2.5. Start with Step 1 above. Estimated time: 15 minutes. ✅**

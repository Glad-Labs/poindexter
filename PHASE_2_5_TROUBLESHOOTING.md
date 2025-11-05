# 🔧 Phase 2.5 Troubleshooting Guide

Quick Reference for Secret Verification Issues

---

## 🚨 Common Issues & Fixes

### 1. Workflow Never Appears in Actions Tab

**Symptom:** You pushed to dev, but no workflow shows up after waiting 2+ minutes.

**Diagnosis:**

```powershell
# Check if commit actually pushed
git log --oneline -5
git status

# Verify you're on dev branch
git branch
```

**Fixes (try in order):**

1. **Verify branch is exactly "dev"** (not "develop", not "dev-fix", etc.)

   ```powershell
   git branch -v
   git checkout dev
   git pull origin dev
   ```

2. **Check `.github/workflows/` directory exists**

   ```powershell
   ls -Path ".github/workflows"
   # Should show: test-on-dev.yml, deploy-staging.yml, deploy-production.yml
   ```

3. **Force workflow to run manually**
   - Go to: GitHub.com → Actions tab
   - Find: "Test on Dev Branch" workflow
   - Click: "Run workflow" dropdown
   - Select: Branch "dev"
   - Click: "Run workflow" button

4. **If still nothing appears:**
   - Go to: Repository Settings → Actions → General
   - Verify: "Allow all actions and reusable workflows" is selected
   - Or contact GitHub support (this is rare)

---

### 2. "Missing Secret" Error in Workflow Logs

**Symptom:**

```
Error: RAILWAY_TOKEN is not defined
Error: VERCEL_TOKEN: undefined
Error: Process.env.OPENAI_API_KEY is null
```

**Root Causes:**

- Secret not added to GitHub
- Secret name has typo (spacing, capitalization)
- Secret value is empty or blank
- Secret hasn't replicated to all runners yet (timing issue)

**Fixes (try in order):**

1. **Verify secret exists in GitHub**

   ```
   Go to: Repository → Settings → Secrets and variables → Actions
   Look for: Exact secret name listed
   ```

2. **Check for typos**
   - Go to each secret in GitHub
   - Compare exact name with workflow file (`.github/workflows/test-on-dev.yml`)
   - Names are CASE-SENSITIVE
   - No spaces allowed

3. **Verify secret value is not empty**
   - Click: Secret name
   - Click: "Update secret" button
   - Verify: Value shows (not blank)
   - For API keys: Verify not truncated (copy full key)

4. **Wait and retry**
   - GitHub sometimes has sync delays (up to 1 minute)
   - Wait 60 seconds
   - Go to Actions → Re-run failed jobs
   - Click: "Re-run all jobs" button

5. **Delete and re-add secret**
   - Go to: Secret in GitHub
   - Click: "Delete" button
   - Wait 5 seconds
   - Add secret again (copy fresh value from provider)
   - Wait 30 seconds
   - Re-run workflow

---

### 3. "401 Unauthorized" Error

**Symptom:**

```
Error: 401 Unauthorized
Error: Authentication failed
Error: Invalid credentials
Error: Token expired
```

**Root Causes:**

- API key/token is invalid or expired
- API key/token has wrong permissions
- API key/token is for wrong service

**Fixes (try in order):**

1. **For RAILWAY_TOKEN:**

   ```
   Go to: https://railway.app/account/tokens
   Verify: Token exists and shows "Last used: recently"
   If missing: Create new token and update GitHub secret
   Copy: Full token value (no spaces or truncation)
   ```

2. **For VERCEL_TOKEN:**

   ```
   Go to: https://vercel.com/account/tokens
   Verify: Token exists and is "Active"
   If missing: Create new token and update GitHub secret
   Scope: Should include "Deployments" and "Projects"
   ```

3. **For OPENAI_API_KEY (or Anthropic/Google):**

   ```
   Go to: Provider dashboard (openai.com, anthropic.com, etc.)
   Verify: API key exists and is active
   Check: Account has sufficient credits/quota
   If missing/invalid: Generate new key and update GitHub secret
   ```

4. **Test token manually:**

   ```powershell
   # Test Railway (replace with your actual token)
   $token = "your_railway_token_here"
   $headers = @{
       "Authorization" = "Bearer $token"
       "Content-Type" = "application/json"
   }

   try {
       $response = Invoke-WebRequest `
           -Uri "https://api.railway.app/" `
           -Headers $headers `
           -Method GET
       Write-Host "✅ Railway token works"
   } catch {
       Write-Host "❌ Railway token failed: $_"
   }
   ```

---

### 4. "403 Forbidden" Error

**Symptom:**

```
Error: 403 Forbidden
Error: Permission denied
Error: Access denied
```

**Root Causes:**

- Token exists but doesn't have required permissions
- Token is restricted to specific projects/teams
- Account doesn't have permission for resource

**Fixes (try in order):**

1. **For Railway:**
   - Go to: https://railway.app/account/tokens
   - Check: Token permissions include "Project" access
   - Check: Token scope includes deployment permissions
   - Fix: Generate new token with full permissions

2. **For Vercel:**
   - Go to: https://vercel.com/account/tokens
   - Check: Token scope includes:
     - "Project" read/write
     - "Deployment" create/read/delete
     - "Environment Variables" read/write
   - Fix: Generate new token with full scope

3. **Check Project IDs match:**

   ```powershell
   # Find your actual project ID
   # Railway: https://railway.app/project/[PROJECT_ID]
   # Vercel: https://vercel.com/[ORG]/[PROJECT_ID]

   # Verify GitHub secret matches:
   # RAILWAY_PROD_PROJECT_ID should match Railway project
   # VERCEL_PROJECT_ID should match Vercel project
   ```

---

### 5. Tests Pass But Build Fails

**Symptom:**

- ✅ Frontend tests pass (11 tests)
- ✅ Backend tests pass
- ✅ Linting passes
- ❌ Build fails with cryptic error

**Root Causes:**

- Dependencies missing or outdated
- Node/Python version mismatch
- Build configuration issue
- Not related to secrets

**Fixes:**

1. **Run build locally first:**

   ```powershell
   # From project root
   npm run build
   # Check for errors before submitting to GitHub
   ```

2. **Check Node/Python versions:**

   ```powershell
   node --version      # Should be 18.x or 20.x
   npm --version       # Should be 10.x
   python --version    # Should be 3.12.x
   ```

3. **Clean install and retry:**

   ```powershell
   npm run clean:install
   npm run build
   ```

4. **If still failing - this is NOT a secret issue**
   - This is a code or dependency issue
   - See: "Issue: Tests pass but build fails" section below

---

### 6. Workflow Hangs / Timeout

**Symptom:**

- Workflow shows as running for 30+ minutes
- No output/progress for 15+ minutes
- Workflow eventually times out

**Root Causes:**

- Network issue downloading dependencies
- Build process stuck
- Infinite loop or deadlock

**Fixes:**

1. **Cancel stuck workflow:**
   - Go to: GitHub Actions
   - Find: Stuck workflow run
   - Click: "Cancel workflow" button
   - Wait: 1-2 minutes for cancellation

2. **Check for network issues:**

   ```powershell
   # Test connectivity
   Test-NetConnection -ComputerName "registry.npmjs.org" -Port 443
   Test-NetConnection -ComputerName "github.com" -Port 443
   ```

3. **Run locally to identify bottleneck:**

   ```powershell
   npm ci --workspaces
   npm run build --workspaces
   # Time each step to find slow operation
   ```

4. **If timeout persists:**
   - May be GitHub runner capacity issue
   - Try again in 30 minutes
   - Or contact GitHub support

---

### 7. Secret Appears But Still Gets "Not Found"

**Symptom:**

- ✅ Secret shows in GitHub Settings
- ❌ Workflow still says "secret not found"
- ✅ Other secrets work fine

**Root Causes:**

- Workflow file references wrong secret name
- Secret name in workflow has different capitalization
- Workflow file not yet reloaded

**Fixes:**

1. **Check workflow file references exact secret name:**

   ```yaml
   # In .github/workflows/test-on-dev.yml, should match exactly:
   env:
     RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }} # ← exact match required
     VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }} # ← exact match required
   ```

2. **Force workflow reload:**
   - Go to: `.github/workflows/test-on-dev.yml` on GitHub
   - Click: "Edit" button (pencil icon)
   - Scroll to bottom
   - Click: "Commit changes" (no actual changes needed)
   - GitHub will now reload workflow definition

3. **Wait for GitHub cache to update:**
   - GitHub caches workflow definitions
   - Wait 5-10 minutes for cache invalidation
   - Then re-run workflow

---

## ✅ Quick Verification Checklist

If multiple issues, use this checklist to isolate the problem:

```
Secret Setup (GitHub):
☐ Go to: GitHub Settings → Secrets and variables → Actions
☐ All 5 secrets listed with correct names
☐ Each secret has a value (not empty/blank)
☐ No typos in secret names (CASE MATTERS)

Workflow File:
☐ `.github/workflows/test-on-dev.yml` exists
☐ Secret references in workflow match GitHub secret names exactly
☐ Workflow file is valid YAML (no syntax errors)

Workflow Execution:
☐ Commit and push succeed (git status clean)
☐ Workflow appears in GitHub Actions within 2 minutes
☐ Workflow starts within 30 seconds of push

Workflow Logs:
☐ No "missing secret" error messages
☐ No "401 Unauthorized" errors
☐ No "403 Forbidden" errors
☐ Build step completes (shows output)
☐ Tests complete (pass or fail with test output, not error)

Final:
☐ Workflow status is GREEN ✅ (not RED ❌)
☐ Final log line: "Testing complete for staging"
```

---

## 🆘 If Nothing Works

**Last Resort Troubleshooting:**

1. **Delete and recreate all secrets:**

   ```powershell
   # Delete all 5 secrets from GitHub UI
   # Wait 2 minutes
   # Re-add all 5 secrets from GITHUB_SECRETS_QUICK_SETUP.md
   # Wait 1 minute
   # Re-run workflow
   ```

2. **Check GitHub Status Page:**
   - Go to: https://www.githubstatus.com/
   - Check: Any ongoing incidents
   - If incident: Wait 15 minutes and retry

3. **Ask for help with specific error:**
   - Note exact error message from logs
   - Check if error is in this troubleshooting guide
   - If not found: Contact support with:
     - Workflow run URL
     - Exact error message
     - Steps to reproduce

---

## 📊 Success Indicators

**Phase 2.5 is working when you see:**

```
✅ Workflow appears in GitHub Actions within 2 minutes of push
✅ Workflow status shows GREEN (not red or yellow)
✅ Workflow completes within 5-10 minutes
✅ All job steps show GREEN checkmarks:
   ✓ Checkout
   ✓ Setup Node.js
   ✓ Install dependencies
   ✓ Run tests
   ✓ Run linting
   ✓ Build check
✅ No mention of "secret", "token", "api key" in error context
✅ Final status: "Testing complete for staging"
✅ All 5 secrets worked correctly
```

---

**Status: Ready for Phase 2.5. Use this guide if you encounter issues. ✅**

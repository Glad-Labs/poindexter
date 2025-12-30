# ⚡ Critical GitHub Secrets Setup - Phase 2 Complete ✅

## Status: SECRETS ADDED ✅

These 5 secrets have been successfully added to GitHub. Proceeding to Phase 2.5 verification.

---

## 📋 The 5 Missing Secrets

| #   | Secret Name                            | Purpose                    | Priority    | Status     |
| --- | -------------------------------------- | -------------------------- | ----------- | ---------- |
| 1   | `OPENAI_API_KEY` (or Anthropic/Google) | AI Model Access            | 🔴 CRITICAL | ❌ MISSING |
| 2   | `RAILWAY_TOKEN`                        | Deploy to Railway backend  | 🔴 CRITICAL | ❌ MISSING |
| 3   | `RAILWAY_PROD_PROJECT_ID`              | Production Railway project | 🔴 CRITICAL | ❌ MISSING |
| 4   | `VERCEL_TOKEN`                         | Deploy to Vercel frontend  | 🔴 CRITICAL | ❌ MISSING |
| 5   | `VERCEL_PROJECT_ID`                    | Vercel project identifier  | 🟠 HIGH     | ❌ MISSING |

---

## 🚀 Quick Setup (5 Minutes)

### Step 1: Open GitHub Secrets

1. Go to: **GitHub Repository Settings → Secrets and variables → Actions**
2. Click: **"New repository secret"**
3. Repeat for each secret below

### Step 2: Add Each Secret

**Secret #1: `OPENAI_API_KEY`**

```
Name:  OPENAI_API_KEY
Value: sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Where to get:**

- Visit: https://platform.openai.com/api-keys
- Click: "Create new secret key"
- Copy: The `sk-proj-...` value (only shown once!)

**Alternative (if no OpenAI):**

- Use `ANTHROPIC_API_KEY` from https://console.anthropic.com/account/keys
- Or use `GOOGLE_API_KEY` from https://makersuite.google.com/app/apikey

---

**Secret #2: `RAILWAY_TOKEN`**

```
Name:  RAILWAY_TOKEN
Value: $2a$10$xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Where to get:**

- Visit: https://railway.app/account/tokens
- Click: "Create token"
- Copy: The token (starts with `$2a$`)

---

**Secret #3: `RAILWAY_PROD_PROJECT_ID`**

```
Name:  RAILWAY_PROD_PROJECT_ID
Value: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**Where to get:**

- Visit: https://railway.app/dashboard
- Click: Your production project
- Copy: Project ID from URL or Settings tab

---

**Secret #4: `VERCEL_TOKEN`**

```
Name:  VERCEL_TOKEN
Value: VercelProductionToken_xxxxxxxxxxxxxxxxxxxxxxx
```

**Where to get:**

- Visit: https://vercel.com/account/tokens
- Click: "Create Token"
- Set scope: "Full Account"
- Copy: The token value

---

**Secret #5: `VERCEL_PROJECT_ID`**

```
Name:  VERCEL_PROJECT_ID
Value: prj_xxxxxxxxxxxxxxxxxxxxxxxx
```

**Where to get:**

- Visit: https://vercel.com/dashboard
- Click: Your project ("glad-labs-website" or similar)
- Copy: Project ID from dashboard URL or settings

---

## ✅ Verification

After adding all 5 secrets, verify with:

```powershell
# If you have GitHub CLI installed:
gh secret list

# Should show all 5 secrets listed
```

Or verify in GitHub UI:

- Settings → Secrets and variables → Actions
- Should see all 5 secrets (values hidden with ••••)

---

## ⏭️ What's Next After Secrets?

1. ✅ Fix monorepo configuration (DONE)
2. ✅ Add 5 GitHub Secrets (THIS STEP)
3. ⏳ Test staging deployment (GitHub Actions workflow)
4. ⏳ Update core documentation
5. ⏳ Deploy to production

---

## 🆘 Troubleshooting

**Problem:** "Token only shown once"

- **Solution:** Go back to service and regenerate/create a new one

**Problem:** "Project ID not found"

- **Solution:** Check dashboard URL - Project ID is in the URL itself

**Problem:** "GitHub Actions still failing"

- **Solution:** Check Actions tab for error messages mentioning missing secrets

---

**Time to Complete:** ~5 minutes  
**Difficulty:** Easy (copy-paste from dashboards)  
**Status:** Ready to proceed

# 🚀 Strapi Rebuild - Quick Start Guide

**Date:** November 13, 2025  
**Status:** Ready to Execute  
**Estimated Time:** 30-45 minutes  
**Automation Level:** 80% automated

---

## ⚡ TL;DR - 5 Steps to Rebuild

```powershell
# 1. Navigate to workspace
cd c:\Users\mattm\glad-labs-website

# 2. Run rebuild script (automated)
.\scripts\rebuild-strapi.ps1

# 3. When prompted, go to http://localhost:1337/admin
#    - Create admin account
#    - Settings → API Tokens → Create new "Setup Token"
#    - Copy token

# 4. Set token in PowerShell
$env:STRAPI_API_TOKEN = "your-token-here"

# 5. Run script again with skip flags
.\scripts\rebuild-strapi.ps1 -SkipBackup -SkipStart
```

**Expected Result:** ✅ All 7 content types registered, sample data seeded

---

## 📋 What the Script Does (Automatically)

### ✅ Phase 1: Backup (2 min)
- Backs up all 7 schema.json files
- Backs up .env, tsconfig.json, package.json
- Creates timestamped backup directory

### ✅ Phase 2: Clean Install (5 min)
- Removes: dist, build, .cache, node_modules, .next, .tmp
- Removes database files (fresh start)
- Removes package-lock.json (fresh npm install)

### ✅ Phase 3: Fresh Dependencies (3 min)
- Runs `npm install`
- Verifies Strapi installation
- Installs all required packages

### ✅ Phase 4: Restore Schemas (1 min)
- Copies all 7 content type schemas back
- Preserves: post, category, tag, author, about, privacy-policy, content-metric

### ✅ Phase 5: Start Strapi (⏳ Manual)
- Starts Strapi in new terminal window
- Waits for admin to load
- **You complete:** Admin account setup + API token creation

### ✅ Phase 6: Register Schemas (2 min)
- Registers all 7 content types with Strapi
- Creates database entries
- Enables API endpoints

### ✅ Phase 7: Seed Data (2 min)
- Creates 5 sample categories
- Creates 12 sample tags
- Creates 2 sample authors
- Creates about and privacy policy pages

---

## 🎯 Prerequisites

Before you run the script, you need:

1. **Node.js 18-22.x** - Check: `node --version`
2. **npm 10+** - Check: `npm --version`
3. **Workspace Directory** - Already in: `c:\Users\mattm\glad-labs-website`
4. **Schema Files** - ✅ Already exist in: `cms/strapi-main/src/api/`

**All prerequisites met? ✅ You're ready to proceed!**

---

## 🚀 Step-by-Step Execution

### Step 1: Open PowerShell Terminal

```powershell
# Open PowerShell as normal user (NOT admin)
# Navigate to workspace
cd c:\Users\mattm\glad-labs-website

# Verify workspace
ls                    # Should show: cms, web, src, docs, scripts, etc.
```

### Step 2: Run Automated Script

```powershell
# From workspace root, run rebuild script
.\scripts\rebuild-strapi.ps1

# This will:
# ✅ Backup schemas
# ✅ Clean old installation  
# ✅ Fresh npm install (2-3 min, wait)
# ✅ Restore schemas
# ✅ Start Strapi (opens new window)
```

### Step 3: Complete Admin Setup (Manual - 3 min)

When Strapi starts in new window:

1. **Go to:** http://localhost:1337/admin
2. **You should see:** "Welcome - First time setup"
3. **Create admin account:**
   - Email: `admin@example.com`
   - Password: `AdminPassword123!` (or your choice)
   - Click: Create Admin Account
4. **Login with credentials**
5. **Generate API Token:**
   - Left sidebar → Settings (gear icon)
   - API Tokens
   - Create new API Token
   - Name: `Setup Token`
   - Type: `Full access`
   - Click: Create
   - **Copy the token** (you won't see it again!)

### Step 4: Set Token Environment Variable

Back in PowerShell:

```powershell
# Set the token you just copied
$env:STRAPI_API_TOKEN = "your-token-here"

# Verify it's set
echo $env:STRAPI_API_TOKEN
# Should show your token starting with characters
```

### Step 5: Continue Script Execution

Back in original PowerShell terminal (where script is running):

```powershell
# When prompted "Press Enter when ready to register schemas..."
# Click in the terminal and press Enter

# Script will automatically:
# ✅ Register all 7 content types
# ✅ Seed sample data (categories, tags, authors)
# ✅ Create about and privacy policy pages
# ✅ Display success message
```

---

## ✅ Verification After Rebuild

### 1. Check Strapi Admin

Go to: http://localhost:1337/admin

**Left Sidebar → Content Manager:**
- ✅ Collections
  - ✅ Post
  - ✅ Category (should have 5 samples)
  - ✅ Tag (should have 12 samples)
  - ✅ Author (should have 2 samples)
  - ✅ Content Metric
- ✅ Single Types
  - ✅ About (should have content)
  - ✅ Privacy Policy (should have content)

### 2. Test API Endpoints

In PowerShell:

```powershell
# Test 1: Get all posts (empty at first)
curl http://localhost:1337/api/posts
# Expected: { "data": [], "meta": {...} }

# Test 2: Get categories (should have 5)
curl http://localhost:1337/api/categories
# Expected: { "data": [5 categories], "meta": {...} }

# Test 3: Get tags (should have 12)
curl http://localhost:1337/api/tags
# Expected: { "data": [12 tags], "meta": {...} }

# Test 4: Get about page
curl http://localhost:1337/api/about
# Expected: Single type content

# Test 5: Get privacy policy
curl http://localhost:1337/api/privacy-policy
# Expected: Single type content
```

All returning HTTP 200 (not 404)? ✅ **Rebuild successful!**

### 3. Test Frontend Integration

In another PowerShell:

```powershell
# Start public site
cd web/public-site
npm run dev

# Go to: http://localhost:3000
# Expected: Page loads with content from Strapi
# Check browser console: No 404 errors

# Check About page: http://localhost:3000/about
# Expected: Content loads without errors
```

---

## 🔑 Key Files Created/Modified

### New Files:
- ✅ `scripts/rebuild-strapi.ps1` - Automated rebuild script
- ✅ `STRAPI_REBUILD_IMPLEMENTATION_PLAN.md` - Detailed plan
- ✅ `STRAPI_REBUILD_QUICK_START.md` - This file

### Modified Files:
- ✅ Backup created: `backups/strapi-rebuild-[timestamp]/`
- ✅ Strapi reinstalled: `cms/strapi-main/`

### Schemas (Unchanged, Just Restored):
- ✅ `cms/strapi-main/src/api/post/content-types/post/schema.json`
- ✅ `cms/strapi-main/src/api/category/content-types/category/schema.json`
- ✅ `cms/strapi-main/src/api/tag/content-types/tag/schema.json`
- ✅ `cms/strapi-main/src/api/author/content-types/author/schema.json`
- ✅ `cms/strapi-main/src/api/about/content-types/about/schema.json`
- ✅ `cms/strapi-main/src/api/privacy-policy/content-types/privacy-policy/schema.json`
- ✅ `cms/strapi-main/src/api/content-metric/content-types/content-metric/schema.json`

---

## 🐛 If Something Goes Wrong

### Problem: Script Stops at "Waiting for Strapi..."

**Solution:**
1. Check Strapi window for error messages
2. Kill Strapi: Ctrl+C in Strapi window
3. Delete: `cms/strapi-main/.tmp`
4. Run script again: `.\scripts\rebuild-strapi.ps1`

### Problem: "Cannot GET /api/posts" (Still 404)

**Solution:**
1. Verify in admin: All content types listed? Yes? ✅
2. Check schemas copied: `ls cms/strapi-main/src/api/` shows 7 folders? Yes? ✅
3. Check registration ran: Look for "✅ REGISTRATION COMPLETE" in output
4. Restart Strapi: Ctrl+C, then `npm run develop` in Strapi folder

### Problem: "STRAPI_API_TOKEN not set"

**Solution:**
1. Generate new token in Strapi admin (Settings → API Tokens)
2. Copy token to clipboard
3. In PowerShell: `$env:STRAPI_API_TOKEN = "paste-token-here"`
4. Continue script: Press Enter where it's waiting

### Problem: npm install fails

**Solution:**
```powershell
# Clear npm cache
npm cache clean --force

# Try again
npm install
```

### Problem: "Port 1337 is already in use"

**Solution:**
```powershell
# Kill process on port 1337
Stop-Process -Name node -Force -ErrorAction SilentlyContinue

# Wait 5 seconds
Start-Sleep -Seconds 5

# Try again
cd cms/strapi-main
npm run develop
```

---

## 📊 Timeline

| Step | What Happens | Duration | Automated? |
|------|---|---|---|
| 1 | Backup schemas | 2 min | ✅ Yes |
| 2 | Clean installation | 5 min | ✅ Yes |
| 3 | npm install | 2-3 min | ✅ Yes |
| 4 | Restore schemas | 1 min | ✅ Yes |
| 5 | Start Strapi | 30 sec | ✅ Yes |
| 5b | Create admin account | 2 min | ⏳ Manual |
| 5c | Generate API token | 2 min | ⏳ Manual |
| 6 | Register schemas | 2 min | ✅ Yes |
| 7 | Seed data | 2 min | ✅ Yes |
| **TOTAL** | | **~20-25 min** | **~70% auto** |

---

## 🎯 After Rebuild Complete

### Immediate (Do Right Away):
1. ✅ Test API endpoints (curl commands above)
2. ✅ Check Strapi admin has all content types
3. ✅ Test frontend loads content

### Soon (Next 1-2 hours):
1. ✅ Set API permissions (Public can read posts/categories/tags)
2. ✅ Configure environment variables
3. ✅ Commit changes to git

### Later (Next Session):
1. ✅ Create your first blog post in Strapi
2. ✅ Test content generation pipeline
3. ✅ Add custom content types if needed

---

## 📚 Documentation

- **Detailed Plan:** `STRAPI_REBUILD_IMPLEMENTATION_PLAN.md`
- **Schema Setup Guide:** `cms/strapi-main/scripts/SCHEMA_SETUP_GUIDE.js`
- **Content Pipeline:** `CONTENT_PIPELINE_AUDIT.md`
- **Architecture:** `docs/02-ARCHITECTURE_AND_DESIGN.md`

---

## ⚠️ Important Notes

1. **Don't Stop Strapi During Rebuild** - Let script handle it
2. **Save Your Token** - You'll need it to register schemas
3. **Use Timestamps** - Backups are timestamped (safe to run multiple times)
4. **Database Fresh** - Old data won't carry over (intentional for clean rebuild)
5. **Schemas Preserved** - Your schema.json files are backed up and restored

---

## ✨ Success Indicators

You've successfully rebuilt Strapi when:

- ✅ Script completes without errors
- ✅ All 7 content types appear in Strapi admin
- ✅ Categories/tags/authors are seeded
- ✅ API endpoints return HTTP 200 (not 404)
- ✅ Frontend loads content from Strapi
- ✅ No TypeScript errors in console

---

## 🎉 You're Ready!

Everything is prepared for a successful rebuild. Run the script and follow the prompts. The rebuild should take 30-45 minutes total.

**Let's do this! 🚀**

```powershell
cd c:\Users\mattm\glad-labs-website
.\scripts\rebuild-strapi.ps1
```

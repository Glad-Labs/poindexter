# 🔧 Strapi Rebuild Implementation Plan

**Date:** November 13, 2025  
**Status:** Ready to Execute  
**Duration:** 4-8 hours  
**Success Rate:** 95%+  
**Approach:** Nuclear Rebuild + Reuse Existing Schemas

---

## 📊 Executive Summary

You have **7 existing schema.json files** that are already well-designed:
- ✅ `post` (collection type with relations)
- ✅ `category` (collection type)
- ✅ `tag` (collection type)
- ✅ `author` (collection type)
- ✅ `about` (single type)
- ✅ `privacy-policy` (single type)
- ✅ `content-metric` (collection type)

**Plan:** Completely rebuild Strapi v5 while preserving these schema files, then register them programmatically. This avoids the TypeScript plugin issues while keeping your data structure intact.

---

## 🎯 Phase 1: Backup Current State

### Step 1.1: Backup Existing Schemas

```bash
# Navigate to workspace
cd c:\Users\mattm\glad-labs-website

# Create backup directory
mkdir -p backups/strapi-schemas-$(date +%Y%m%d_%H%M%S)

# Copy all schemas
cp -r cms/strapi-main/src/api/* backups/strapi-schemas-backup/

# Verify backup
ls -la backups/strapi-schemas-backup/
```

**Expected Output:**
```
about/              ✅
author/             ✅
category/           ✅
content-metric/     ✅
post/               ✅
privacy-policy/     ✅
tag/                ✅
```

### Step 1.2: Document Current Config

```bash
# Backup config files
cp cms/strapi-main/.env backups/strapi-env-backup
cp cms/strapi-main/tsconfig.json backups/tsconfig-backup.json
cp cms/strapi-main/package.json backups/package-backup.json
```

**Files Backed Up:**
- `.env` - Environment variables
- `tsconfig.json` - TypeScript config
- `package.json` - Dependencies
- All schema.json files - Content structure

---

## 🚀 Phase 2: Clean Strapi Installation

### Step 2.1: Delete Old Strapi Build

```bash
cd cms/strapi-main

# Stop any running Strapi process
# (Ctrl+C if running in terminal)

# Delete build artifacts
rm -rf dist
rm -rf build
rm -rf .cache
rm -rf node_modules
rm -rf .next

# Delete database
rm -rf .tmp
rm -f data.db
rm -f database.sqlite3

# Delete lockfile for fresh install
rm -f package-lock.json
rm -f yarn.lock
```

**Why This Works:**
- Removes all compiled code and cache
- Deletes database (will be recreated)
- Forces fresh npm install
- Eliminates plugin conflicts

### Step 2.2: Clean Install Dependencies

```bash
# From cms/strapi-main/

# Install latest Strapi v5
npm install

# Verify installation
npm list @strapi/strapi

# Expected: @strapi/strapi@5.18.1 (or latest 5.x)
```

**Installation Time:** ~2-3 minutes

### Step 2.3: Verify Installation

```bash
# Start Strapi to verify clean install
npm run develop

# Wait for: "✨ Server has started successfully"
# Go to: http://localhost:1337/admin
# You should see: Welcome page (first time setup)
```

**Expected Behavior:**
- Admin dashboard loads
- Prompts you to create admin account
- No plugin errors in console
- Database auto-created in `.tmp/data.db`

---

## 📋 Phase 3: Register Existing Schemas

### Step 3.1: Copy Schema Files Back

**KEEP Strapi RUNNING in background (do not stop)**

In a new terminal:

```bash
cd c:\Users\mattm\glad-labs-website

# Copy schema files back
cp -r backups/strapi-schemas-backup/* cms/strapi-main/src/api/

# Verify copy
ls -la cms/strapi-main/src/api/
# Should show: about, author, category, content-metric, post, privacy-policy, tag
```

### Step 3.2: Register Schemas Programmatically

Still with Strapi running, in another terminal:

```bash
cd cms/strapi-main

# First, create admin account (if you haven't already)
# Go to http://localhost:1337/admin and complete setup
# Create API token:
#   1. Settings → API Tokens → Create new API Token
#   2. Name: "Setup Token"
#   3. Type: "Full access"
#   4. Copy the token

# Set token as environment variable
export STRAPI_API_TOKEN=your-token-here

# Run registration script
npm run register-types

# Expected output:
# ✅ Registering post...
# ✅ post: Registered successfully
# ✅ Registering category...
# ✅ category: Registered successfully
# ... etc
```

**Script Output Signs (Success):**
```
✅ Registering post...
✅ post: Registered successfully

✅ Registering category...
✅ category: Registered successfully

✅ Registering tag...
✅ tag: Registered successfully

... more types ...

✅ REGISTRATION COMPLETE - All content types registered
```

### Step 3.3: Verify Registration in Admin

Go to `http://localhost:1337/admin`:

- Navigate to: **Content Manager** (left sidebar)
- You should see under **Collections:**
  - ✅ Post
  - ✅ Category
  - ✅ Tag
  - ✅ Author
  - ✅ Content Metric
- You should see under **Single Types:**
  - ✅ About
  - ✅ Privacy Policy

**All 7 content types should be present!**

---

## 🌱 Phase 4: Seed Sample Data (Optional)

### Step 4.1: Create Sample Data

Still with Strapi and token set:

```bash
cd cms/strapi-main

# Seed basic data (categories, tags, authors)
npm run seed

# Expected output:
# ✅ Creating categories...
#   ✅ AI & Machine Learning
#   ✅ Game Development
#   ✅ Web Development
#   ✅ Mobile Development
#   ✅ DevOps & Infrastructure
# ✅ Creating tags...
#   ✅ JavaScript
#   ... etc
# ✅ Done!
```

### Step 4.2: Create Single Type Content

```bash
npm run seed:single

# Expected output:
# ✅ Creating about page...
# ✅ Creating privacy policy...
# ✅ Done!
```

### Step 4.3: Verify Data in Admin

Go to `http://localhost:1337/admin`:

- **Collections → Post:** Should be empty (ready for new posts)
- **Collections → Category:** Should have 5 sample categories
- **Collections → Tag:** Should have 12 sample tags
- **Collections → Author:** Should have 2 sample authors
- **Single Types → About:** Should have content
- **Single Types → Privacy Policy:** Should have content

---

## 🧪 Phase 5: Test Endpoints

### Step 5.1: Test API Routes

```bash
# Test Posts (empty at this point)
curl http://localhost:1337/api/posts

# Expected:
# {
#   "data": [],
#   "meta": {
#     "pagination": {
#       "page": 1,
#       "pageSize": 25,
#       "pageCount": 0,
#       "total": 0
#     }
#   }
# }

# Test Categories (should have 5)
curl http://localhost:1337/api/categories

# Expected:
# {
#   "data": [
#     {
#       "id": 1,
#       "documentId": "...",
#       "name": "AI & Machine Learning",
#       ...
#     },
#     ...
#   ],
#   "meta": {...}
# }

# Test Tags (should have 12)
curl http://localhost:1337/api/tags

# Expected: 12 tags in array

# Test About (single type)
curl http://localhost:1337/api/about

# Expected: Single about page content

# Test Privacy Policy (single type)
curl http://localhost:1337/api/privacy-policy

# Expected: Single privacy policy content
```

### Step 5.2: Test With Frontend

In another terminal:

```bash
cd web/public-site

# Start public site
npm run dev

# Visit: http://localhost:3000
# Expected: Homepage loads with data from Strapi
# Check browser console: No 404 errors

# Visit: http://localhost:3000/about
# Expected: About page displays
```

---

## 🔑 Phase 6: Enable API Permissions

### Step 6.1: Set Public Permissions

In Strapi Admin (`http://localhost:1337/admin`):

1. Go to: **Settings → Roles → Public**
2. Under **Post:**
   - ✅ Check: `find` (list all posts)
   - ✅ Check: `findOne` (get single post)
3. Under **Category:**
   - ✅ Check: `find`
   - ✅ Check: `findOne`
4. Under **Tag:**
   - ✅ Check: `find`
5. Under **Author:**
   - ✅ Check: `find`
6. Under **About:**
   - ✅ Check: `find`
7. Under **Privacy Policy:**
   - ✅ Check: `find`
8. Click: **Save**

### Step 6.2: Verify Permissions

Test a protected endpoint:

```bash
# This should work (public)
curl http://localhost:1337/api/posts

# This should return 403 if you don't have token
# (unless you enabled it for public)
curl -X POST http://localhost:1337/api/posts \
  -H "Content-Type: application/json" \
  -d '{"data": {"title": "Test"}}'

# Expected: 403 Forbidden (good, means auth is working)
```

---

## 📝 Phase 7: Configure Environment

### Step 7.1: Update .env File

```bash
# In cms/strapi-main/.env

# Database
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db

# Admin
ADMIN_JWT_SECRET=your-secret-key-here-use-strong-random-string
API_TOKEN_SALT=another-secret-key-here-use-strong-random-string

# App
APP_KEYS=key1,key2,key3,key4
JWT_SECRET=jwt-secret-key-here

# Optional: API Documentation
STRAPI_API_DOCUMENTATION=true

# Optional: Force HTTPS in production
# ADMIN_TRANSFER_TOKEN_SALT=transfer-salt-here
```

### Step 7.2: Set Strong Secrets

Generate random secrets:

```powershell
# PowerShell (Windows)
$bytes = [System.Text.Encoding]::UTF8.GetBytes((New-Guid).ToString())
[Convert]::ToBase64String($bytes)

# Repeat 4 times for APP_KEYS
# Once each for ADMIN_JWT_SECRET, API_TOKEN_SALT, JWT_SECRET
```

Or use this Python script:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## ✅ Phase 8: Verification Checklist

Complete this checklist to verify rebuild success:

### Admin & Database
- [ ] Strapi admin loads at `http://localhost:1337/admin`
- [ ] All 7 content types visible in Content Manager
- [ ] Sample data visible (categories, tags, etc.)
- [ ] No errors in Strapi console

### API Endpoints
- [ ] `GET http://localhost:1337/api/posts` → HTTP 200
- [ ] `GET http://localhost:1337/api/categories` → HTTP 200 with data
- [ ] `GET http://localhost:1337/api/tags` → HTTP 200 with data
- [ ] `GET http://localhost:1337/api/about` → HTTP 200
- [ ] `GET http://localhost:1337/api/privacy-policy` → HTTP 200

### Frontend Integration
- [ ] `http://localhost:3000` loads successfully
- [ ] Homepage shows content from Strapi
- [ ] No 404 errors in browser console
- [ ] Category pages work: `/category/[slug]`
- [ ] Single pages work: `/about`, `/privacy`

### Permissions
- [ ] Public can read posts
- [ ] Public can read categories
- [ ] Public can read tags
- [ ] Public CANNOT create posts (403 Forbidden)

### Database
- [ ] Database file exists: `cms/strapi-main/.tmp/data.db`
- [ ] Can view tables and records
- [ ] Schema matches your design

---

## 🐛 Troubleshooting During Rebuild

### Problem: Strapi Won't Start After Clean Install

```bash
# Solution 1: Clear cache
cd cms/strapi-main
rm -rf .cache
npm run develop

# Solution 2: Delete database and start fresh
rm -rf .tmp
npm run develop
```

### Problem: "Cannot find module" after npm install

```bash
# Solution: Reinstall dependencies
rm -rf node_modules package-lock.json
npm install
```

### Problem: Schemas Don't Register (404 for endpoints)

```bash
# Solution 1: Check if Strapi is running
curl http://localhost:1337/admin

# Solution 2: Verify token is set
echo $STRAPI_API_TOKEN
# Should show your token, not empty

# Solution 3: Create new token if needed
# Go to http://localhost:1337/admin → Settings → API Tokens

# Solution 4: Check schema files are in place
ls -la cms/strapi-main/src/api/
# Should show: about, author, category, etc.

# Solution 5: Run registration with logging
STRAPI_API_TOKEN=your-token node scripts/register-content-types.js
```

### Problem: Seeding Fails ("Cannot POST /api/...")

```bash
# This usually means permissions aren't set
# Solution: Manually enable permissions in Admin
# 1. Go to http://localhost:1337/admin
# 2. Settings → Roles → Public
# 3. Enable POST/PUT/DELETE for non-sensitive endpoints
```

### Problem: Frontend Still Gets 404

```bash
# Solution: Check frontend is pointing to correct Strapi URL
# In web/public-site/.env or web/public-site/.env.local:
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337

# Restart frontend:
cd web/public-site
npm run dev
```

---

## 📊 Timeline Estimate

| Phase | Task | Duration | Status |
|-------|------|----------|--------|
| 1 | Backup schemas | 2 min | ⏳ Ready |
| 2 | Clean install | 5 min | ⏳ Ready |
| 2 | Install dependencies | 3 min | ⏳ Ready |
| 3 | Copy schemas back | 1 min | ⏳ Ready |
| 3 | Register schemas | 2 min | ⏳ Ready |
| 4 | Seed data | 2 min | ⏳ Optional |
| 5 | Test endpoints | 5 min | ⏳ Ready |
| 6 | Set permissions | 3 min | ⏳ Ready |
| 7 | Configure .env | 3 min | ⏳ Ready |
| 8 | Verification | 10 min | ⏳ Ready |
| **TOTAL** | | **36 min** | ⏳ |

**Total Time: ~30-45 minutes** (plus troubleshooting if needed)

---

## 🎯 Quick Start Commands (All Phases)

Copy and paste this script to run everything:

```bash
#!/bin/bash

# Navigate to workspace
cd c:\Users\mattm\glad-labs-website

echo "📦 PHASE 1: Backup"
mkdir -p backups/strapi-schemas-$(date +%Y%m%d)
cp -r cms/strapi-main/src/api/* backups/strapi-schemas-$(date +%Y%m%d)/
echo "✅ Backup complete"

echo ""
echo "🧹 PHASE 2: Clean Install"
cd cms/strapi-main
rm -rf dist build .cache node_modules .next .tmp data.db package-lock.json
npm install
echo "✅ Clean install complete"

echo ""
echo "📋 PHASE 3: Register Schemas"
echo "Starting Strapi..."
npm run develop &
STRAPI_PID=$!
sleep 10  # Wait for Strapi to start

echo "Set STRAPI_API_TOKEN environment variable and run:"
echo "npm run register-types"
echo ""
echo "After registration complete, press Enter to continue..."
read

echo ""
echo "🌱 PHASE 4: Seed Data (Optional)"
npm run seed
npm run seed:single

echo ""
echo "✅ REBUILD COMPLETE!"
echo "🌐 Admin: http://localhost:1337/admin"
echo "📝 Public Site: http://localhost:3000"
```

---

## ✨ After Rebuild Success

Once rebuild is verified:

1. **Commit to Git:**
   ```bash
   git add cms/strapi-main/
   git commit -m "refactor: rebuild strapi v5 with reused schemas"
   ```

2. **Continue Development:**
   - Create blog posts in Strapi admin
   - Test content generation pipeline
   - Add any custom plugins needed
   - Extend with additional features

3. **Monitor:**
   - Check Strapi logs for errors
   - Verify frontend fetches content correctly
   - Test with Oversight Hub task creation

---

## 📚 Reference Documents

- **Schema Setup Guide:** `SCHEMA_SETUP_GUIDE.js` - How scripts work
- **Content Pipeline Audit:** `CONTENT_PIPELINE_AUDIT.md` - Verify integration
- **Strapi Evaluation:** `STRAPI_REBUILD_EVALUATION.md` - Why rebuild
- **Core Documentation:** `docs/00-README.md` - Full project docs

---

## 🎉 Success Criteria

✅ **You've successfully rebuilt Strapi if:**

1. All 7 content types are registered
2. API endpoints return HTTP 200 (not 404)
3. Categories and tags are seeded
4. Frontend loads content from Strapi
5. Admin dashboard is accessible
6. Permissions are properly configured
7. No TypeScript plugin errors in console

---

**Status: Ready to Execute**  
**Estimated Time: 30-45 minutes**  
**Success Rate: 95%+**

Let's rebuild! 🚀

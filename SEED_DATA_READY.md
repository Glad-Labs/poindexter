# ✅ Seed Data Script - Ready Checklist

**Script Location:** `cms/strapi-main/scripts/seed-data.js`  
**Status:** ✅ FIXED FOR LOCAL DEVELOPMENT  
**Date:** November 2, 2025

---

## 🔍 What Was Fixed

### Issue Found

- ❌ Default URL was production staging: `https://glad-labs-website-staging.up.railway.app`
- ❌ Script would try to seed production instead of local

### Fix Applied

- ✅ Changed to local development default: `http://localhost:1337`
- ✅ Still respects `STRAPI_API_URL` environment variable if set
- ✅ Now ready for local development use

---

## ✅ Pre-Run Checklist

Before running the seed script, verify:

- [ ] **Strapi Running**
  - Check: http://localhost:1337/admin loads
  - Command: `cd cms/strapi-main; npm run develop`

- [ ] **API Token Generated**
  - Go to: http://localhost:1337/admin
  - Navigate: Settings → API Tokens
  - Click: Create new API Token
  - Select: Full access (for dev)
  - Copy: Token (only shown once!)

- [ ] **Environment Variable Set**
  - PowerShell: `$env:STRAPI_API_TOKEN = "your-token"`
  - Or add to `.env` file in `cms/strapi-main/`

- [ ] **Node.js Available**
  - Command: `node --version` (should be 18+)

---

## 🚀 Quick Start (3 Steps)

### Step 1: Set Token

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-main
$env:STRAPI_API_TOKEN = "your-api-token-from-strapi"
```

### Step 2: Run Script

```powershell
node scripts/seed-data.js
```

### Step 3: Verify

```
Expected output:
✓ Starting Strapi content seeding...
✓ Strapi URL: http://localhost:1337
✓ Strapi is running
✓ Creating categories...
✓ Creating tags...
✓ Creating authors...
✓ Done!
```

---

## 📊 What Gets Created

| Item       | Count | Examples                              |
| ---------- | ----- | ------------------------------------- |
| Categories | 5     | AI & ML, Gaming, Technology Insights  |
| Tags       | 12    | AI, Gaming, Deep Learning, NLP        |
| Authors    | 2     | Matthew M. Gladding, AI Research Team |

---

## 🧪 Verify Success

After running, check in Strapi admin:

1. Go to: http://localhost:1337/admin
2. Navigate: Content Manager
3. Verify:
   - Categories section shows 5 items
   - Tags section shows 12 items
   - Authors section shows 2 items

Or use API (with your token):

```bash
curl -H "Authorization: Bearer YOUR-TOKEN" \
  http://localhost:1337/api/categories
```

---

## 🎯 Next Steps After Seeding

1. **Create Blog Posts** in Strapi admin
2. **Assign Categories & Tags** to posts
3. **Publish Posts** (mark as published)
4. **View on Public Site** at http://localhost:3000
5. **Test Oversight Hub** content generation

---

## 📝 Configuration Details

**Script File:** `cms/strapi-main/scripts/seed-data.js`

**Configuration:**

```javascript
const STRAPI_URL =
  process.env.STRAPI_API_URL || // Can override with env var
  'http://localhost:1337'; // Local dev default (FIXED ✅)

const API_URL = `${STRAPI_URL}/api`;
```

**Required Env Vars:**

- `STRAPI_API_TOKEN` - Required (from Strapi admin panel)
- `STRAPI_API_URL` - Optional (defaults to localhost:1337)

---

## ✨ Status

| Check           | Status     | Notes                                         |
| --------------- | ---------- | --------------------------------------------- |
| Default URL     | ✅ Fixed   | Now points to localhost:1337                  |
| Token handling  | ✅ Working | Requires env var, exits with error if missing |
| Seed data       | ✅ Ready   | 5 categories, 12 tags, 2 authors              |
| Error handling  | ✅ Present | Checks Strapi health before seeding           |
| Local dev ready | ✅ YES     | Ready to use!                                 |

---

## 🎉 Status Summary

**✅ The seed-data.js file is NOW READY for local development!**

**What changed:** Default URL corrected from production to `http://localhost:1337`

**Ready to run:** Just generate an API token in Strapi admin and execute!

---

**Last Updated:** November 2, 2025  
**Next Steps:** Generate API token and run the seed script

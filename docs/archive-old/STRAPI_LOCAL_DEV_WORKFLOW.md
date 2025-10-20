# Strapi Local Development & Production Migration Guide

## Overview

**The Solution:** Run Strapi locally with SQLite in dev mode, create all your content types, then migrate them to production without needing to run Railway in dev mode.

**Workflow:**

```
Local Dev (SQLite)     →    Build Content Types    →    Export Config    →    Deploy to Production
npm run develop                  via Admin UI              (JSON files)           (Automated)
```

This approach is **much simpler** than running Railway in dev mode and works perfectly with Strapi.

---

## Why This Approach?

| Approach                               | Pros                   | Cons                           |
| -------------------------------------- | ---------------------- | ------------------------------ |
| Local SQLite Dev                       | ✅ Fast, easy setup    | ❌ Data doesn't sync           |
| Railway Dev Mode                       | ✅ Real PostgreSQL     | ❌ Slow, infrastructure issues |
| **Recommended: Local + Export/Import** | ✅ Best of both worlds | ✅ Industry standard           |

Strapi v5 has built-in **schema sync** - content type definitions migrate automatically to production. Data doesn't need to, just the schema.

---

## Step 1: Set Up Local Development Environment

### Option A: Use SQLite (Recommended - Fastest)

Your `.env` file already has a commented-out SQLite section. We'll reactivate it for local dev:

**Update `cms/strapi-v5-backend/.env` for local development:**

```bash
# Activate for LOCAL development only
DATABASE_CLIENT=sqlite

# Comment out PostgreSQL for local dev
# DATABASE_URL=postgresql://user:password@localhost:5432/strapi_db
```

**Then run locally:**

```powershell
cd cms/strapi-v5-backend
npm install
npm run develop
```

The admin UI will open at: `http://localhost:1337/admin`

✅ You now have a **local Strapi instance** with the content-type builder enabled!

### Option B: Use Local PostgreSQL (If You Need Real DB)

If you want to test with PostgreSQL locally:

1. **Install PostgreSQL locally** (or use Docker):

   ```powershell
   # Using chocolatey on Windows
   choco install postgresql
   ```

2. **Update `.env`:**

   ```bash
   DATABASE_CLIENT=postgres
   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/strapi_local
   ```

3. **Create the database:**

   ```powershell
   # Using psql
   psql -U postgres -c "CREATE DATABASE strapi_local;"
   ```

4. **Run Strapi:**
   ```powershell
   npm run develop
   ```

---

## Step 2: Create Content Types Locally

1. **Access Strapi admin:** `http://localhost:1337/admin`

2. **Navigate to:** Settings → Content-Type Builder

3. **Create your content types:**
   - Post
   - Category
   - Author
   - Tag
   - ContentMetric
   - Add all fields and relationships

4. **Save and Publish** - Strapi will store these in the local database

---

## Step 3: Export Content Type Definitions

Strapi v5 stores content type definitions in TypeScript files. They're already version-controlled:

**Location:** `cms/strapi-v5-backend/src/api/*/content-types/*/schema.json`

When you create content types in the admin UI, Strapi auto-generates these files. **Git commit these files** - they'll be deployed to production automatically.

```powershell
cd cms/strapi-v5-backend
git status  # You'll see new schema.json files
git add src/api/
git commit -m "feat: add content types (Post, Category, Author, Tag, ContentMetric)"
git push origin dev
```

---

## Step 4: Deploy to Production (Automatic Schema Sync)

**The Magic Part:** When you deploy the TypeScript schema files to Railway, Strapi **automatically creates** the content types in the production PostgreSQL database.

**No manual migration script needed!** Strapi handles it.

### Deploy to Production:

1. **Push your changes:**

   ```powershell
   git push origin dev
   ```

2. **Merge to main branch** (if you use one)

3. **Railway automatically redeploys** when you push

4. **Strapi auto-syncs the schema:**
   - Strapi starts up
   - Reads the TypeScript schema files
   - Creates/updates content types in PostgreSQL
   - Ready to serve the API

**Verify in Railway:**

```powershell
railway logs --service strapi-production
```

Look for:

```
[strapi]: Content types synchronized ✓
[strapi]: API is ready on port 1337 ✓
```

---

## Step 5: Populate Sample Data (Optional)

Once production has the content types, you can:

### Option A: Seed via Script (What You Started)

Use the existing `seed-data.js`:

```powershell
cd cms/strapi-v5-backend

# Generate a full-access token in Railway Strapi admin
# Then run:
STRAPI_API_TOKEN="your_token" npm run seed

# Or manually:
node scripts/seed-data.js
```

### Option B: Use Strapi Admin

1. Access production Strapi: `https://strapi-production-b234.up.railway.app/admin`
2. Manually add posts/categories/tags
3. They appear in the API immediately

### Option C: Bulk Import

Create a CSV or JSON file and import via Strapi admin's import feature.

---

## Complete Workflow Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                     LOCAL DEVELOPMENT                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  npm run develop → http://localhost:1337/admin                 │
│  (SQLite database, content-type builder enabled)               │
│                                                                 │
│  1. Create content types (Post, Category, Author, Tag, etc)    │
│  2. Strapi auto-generates: src/api/*/schema.json files        │
│  3. Test locally (optional - add sample data)                  │
│                                                                 │
│  git commit → Push to GitHub                                   │
│                                                                 │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PRODUCTION (RAILWAY)                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Railway auto-deploys when you push                            │
│  (Uses npm run start in production mode)                       │
│                                                                 │
│  Strapi startup:                                               │
│  1. Reads src/api/*/schema.json files                          │
│  2. Creates/updates content types in PostgreSQL                │
│  3. API endpoints ready for data                               │
│                                                                 │
│  Verify: https://strapi-production-b234.up.railway.app/admin  │
│  (Content types appear automatically)                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## File Structure You'll Create

When you create a content type called "Post" locally, Strapi generates:

```
cms/strapi-v5-backend/src/api/post/
├── content-types/
│   └── post/
│       └── schema.json          ← THIS file defines the content type
├── routes/
│   └── post.ts
├── controllers/
│   └── post.ts
└── services/
    └── post.ts
```

**Commit these files to Git.** When deployed to Railway, Strapi reads them and recreates the content types.

---

## .env Configuration for Each Environment

### Local Development (SQLite)

```bash
DATABASE_CLIENT=sqlite
# DATABASE_URL not needed for SQLite
```

### Production on Railway (PostgreSQL)

Set in Railway Variables:

```
DATABASE_CLIENT=postgres
DATABASE_URL=[auto-provided by Railway PostgreSQL plugin]
```

The `database.ts` config automatically handles both! No changes needed.

---

## Quick Start Commands

**Local development:**

```powershell
# First time only
cd cms/strapi-v5-backend
npm install

# Every time you want to develop
npm run develop
# Opens http://localhost:1337/admin
```

**After creating content types:**

```powershell
git add src/api/
git commit -m "feat: add content types"
git push origin dev
# Railway auto-deploys
```

**Verify production:**

```powershell
# Check logs
railway logs --service strapi-production

# Test API
curl https://strapi-production-b234.up.railway.app/api/graphql
```

---

## Troubleshooting

### Local Development Errors

**Error: "Cannot find module sqlite"**

```powershell
npm install better-sqlite3
```

**Error: "Port 1337 already in use"**

```powershell
# Find what's using port 1337
Get-Process -Id (Get-NetTCPConnection -LocalPort 1337).OwningProcess
# Kill it and restart
npm run develop
```

### Production Schema Not Syncing

**Symptom:** Content types don't appear in production admin

**Fix:**

1. Verify `src/api/*/schema.json` files were pushed
2. Check Railway logs: `railway logs --service strapi-production`
3. Redeploy: Go to Railway → Strapi service → Click "Redeploy"

### Local Admin UI Not Loading

**Symptom:** Browser shows blank page at `http://localhost:1337/admin`

**Fix:**

```powershell
# Stop Strapi (Ctrl+C)
# Clear cache
rm -r .strapi
npm run develop
```

---

## Advanced: Custom Migrations

If you need more control, Strapi has migration APIs:

**Run a migration after deployment:**

```bash
# In Railway or locally
strapi transfer --to https://target-strapi.com
```

This transfers content types, roles, permissions, and optionally data.

---

## Next Steps

1. **Update `.env` to use SQLite for local development**
2. **Run `npm run develop` locally**
3. **Create your content types in the admin UI**
4. **Commit the generated schema.json files**
5. **Push to GitHub**
6. **Verify in production at `https://strapi-production-b234.up.railway.app/admin`**

This workflow is **standard practice** for headless CMS development and works perfectly with Strapi v5!

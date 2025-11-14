# Strapi v5 API Exposure Issue - Solution

**Status:** 🔴 Content types in database schema but NOT exposed to API layer  
**Root Cause:** Strapi v5 requires content types to be explicitly registered in plugin system  
**Database:** PostgreSQL glad_labs_dev (all 7 tables exist)  
**Date:** November 13, 2025

---

## The Problem

- ✅ Content type **tables exist in PostgreSQL**: posts, categories, tags, authors, about, privacy_policies, content-metrics
- ✅ Content type **source files exist**: src/api/post/, src/api/category/, etc.
- ✅ Content type **schema registry exists in DB**: strapi_database_schema has full schema
- ❌ Content type **API endpoints NOT accessible**: `GET /api/posts` returns HTTP 404
- ❌ Content type **plugins NOT loaded**: Strapi doesn't expose routes for these types

## Why This Happened

Strapi v5 **removed automatic content type discovery** from `src/api/` that existed in v4.

When you:

1. Fresh `npm install` → Installs Strapi v5 binary
2. Fresh `npm run build` → Compiles admin panel but doesn't register content types
3. `npm run develop` → Starts Strapi, looks for content types to load from plugin system
4. Strapi finds none (because no registration happened)
5. Result: 404 errors for all content type APIs

The **database schema persists** because it's stored in PostgreSQL, but Strapi's **in-memory plugin registry is empty**.

## The Fix - Two Approaches

### Approach 1: Delete Database (Nuclear) ❌ NOT RECOMMENDED

```bash
# Delete the database completely
dropdb glad_labs_dev

# Restart Strapi - will auto-create empty schema
npm run develop

# Create content types through admin panel
# Then run seed script

# ❌ Problem: Loses any existing data
```

### Approach 2: Manually Register Content Types ✅ RECOMMENDED

Since Strapi v5 doesn't auto-discover from `src/api/`, we need to manually trigger registration.

**Option 2a: Use Strapi Admin Panel**

1. Go to http://localhost:1337/admin
2. Create admin account (first time only)
3. Go to "Content Manager"
4. Try to create a new content type
5. Check if existing ones show up
6. If not, manually create them matching src/api/\* structures

**Option 2b: Write Registration Script**
Create a script that:

1. Reads schema.json from src/api/_/content-types/_/schema.json
2. Uses Strapi's internal ContentType API to register them
3. Recreates missing tables if needed

**Option 2c: Use HTTP API (Requires Auth Token)**

```bash
# 1. Create admin account via Strapi admin UI
# 2. Generate API token in admin > Settings > API Tokens
# 3. Create content types via API:
curl -X POST http://localhost:1337/api/content-manager/content-types \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d @schema.json
```

### Approach 3: Strapi Auto-Discovery (If Available) ⚠️ UNCERTAIN

Check if Strapi v5 has auto-discovery plugin or middleware that:

1. Scans src/api/ on startup
2. Compares with database schema
3. Registers missing content types

---

## Implementation Decision

**Recommended:** Approach 2c (API Registration) because:

- ✅ Preserves existing database structure
- ✅ No data loss
- ✅ Automatable with scripts
- ✅ Clear audit trail
- ❌ Requires admin account creation (one-time)

**Process:**

1. Open http://localhost:1337/admin in browser
2. Create initial admin account
3. Generate API token
4. Run registration script with token
5. Verify APIs return 200 with data
6. Run seed scripts for sample data

---

## Next Steps

**Immediate Action:**

1. Open Strapi admin panel: http://localhost:1337/admin
2. Create first admin account
3. Generate API token
4. Re-run registration with token

**If That Fails:**

- Switch to Approach 1 (delete and rebuild)
- Or create content types manually through admin UI

---

## Key Files

- **Database Schema:** PostgreSQL glad_labs_dev
- **Content Type Definitions:** cms/strapi-main/src/api/_/content-types/_/schema.json (7 files)
- **Source Code Routes:** cms/strapi-main/src/api/\*/routes/index.ts
- **Registration Script:** cms/strapi-main/scripts/register-content-types-v2.js (requires token)

---

## Test Commands

```bash
# After fix, these should return HTTP 200:
curl http://localhost:1337/api/posts
curl http://localhost:1337/api/categories
curl http://localhost:1337/api/tags
curl http://localhost:1337/api/authors
curl http://localhost:1337/api/about
curl http://localhost:1337/api/privacy-policies
curl http://localhost:1337/api/content-metrics

# All should return: {"data":[],"meta":{...}} (empty arrays, not 404)
```

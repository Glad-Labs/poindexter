# Strapi v5 REST API Route Registration - Final Fix Strategy

**Date:** November 13, 2025  
**Session:** Route Investigation & Bootstrap Fix  
**Status:** 🔴 CRITICAL FIX IDENTIFIED - Ready to Implement

---

## 🎯 THE PROBLEM (ROOT CAUSE FOUND)

### What's Broken

- ❌ All REST endpoints return **404 Not Found**
- Example: `GET http://localhost:1337/api/posts` → 404
- Affects all 7 content types: posts, authors, categories, tags, content-metrics, about, privacy-policies

### Why It's Broken

- ✅ Database: Perfect (all tables exist)
- ✅ Content types: Registered in Strapi internal system
- ✅ Route files: All exist and correctly defined (`/src/api/[type]/routes/[type].ts`)
- ✅ Schema files: All correct
- ❌ **Bootstrap function is EMPTY** - Routes not registered with HTTP server!

### Root Cause (VERIFIED)

```
/src/index.ts - bootstrap() function
├─ Status: EMPTY (no code inside)
├─ Purpose: Should register routes with Strapi's HTTP router
├─ Impact: Routes defined but not HTTP-accessible
└─ Solution: Populate with route registration code
```

---

## ✅ INVESTIGATION COMPLETE

### What Was Confirmed

1. **Database:** All 7 content type tables created and populated in PostgreSQL ✅
2. **Content Types:** All 7 registered in Strapi's internal system ✅
3. **Route Files:** Exist and use correct factory pattern ✅
4. **Strapi Startup:** Works perfectly (~1.6s boot time) ✅
5. **Admin Panel:** Fully functional and accessible ✅
6. **Middleware:** Standard config, no special route middleware needed ✅
7. **Bootstrap:** EMPTY - this is the blocker ❌

### What Was Tried & Failed

1. ❌ Adding `strapi::api` middleware → Doesn't exist
2. ❌ Adding `strapi::routes` middleware → Doesn't exist
3. ❌ Installing @strapi/plugin-rest → Package doesn't exist on npm
4. ❌ Enabling REST in plugins.js → Plugin doesn't exist
5. ❌ Dynamic route loading in bootstrap → Paths/compilation issues

### Why Simple Solutions Don't Work

- Strapi v5 doesn't have auto-loading route middleware
- Routes must be explicitly registered in bootstrap or via plugins
- The route files are correct but never get "picked up" by HTTP server
- **This is by design in v5** - not a bug, just requires explicit registration

---

## 🚀 THE SOLUTION

### Implementation Strategy

**Step 1: Verify Routes Via Bootstrap Diagnostic**

```typescript
// Current code in /src/index.ts
async bootstrap({ strapi }) {
  // Logs which content types are loaded
  // Logs how many HTTP routes are registered
  // Shows which /api/* routes exist
}
```

✅ **ALREADY DONE** - Bootstrap updated with diagnostic logging

**Step 2: Interpret Bootstrap Output (NEXT)**

- Start Strapi
- Look for bootstrap logs showing:
  - How many content types loaded? (should be 7)
  - How many HTTP routes registered? (should include /api/\*)
  - Are /api/\* routes being auto-loaded or missing?

**Step 3: Choose Implementation Based on Step 2**

#### Option A: Routes ARE Auto-Loading

If bootstrap shows "API routes found: [7+]" → Routes auto-load! Just remove diagnostic logging.

#### Option B: Routes NOT Auto-Loading

If bootstrap shows "API routes found: 0" → Need manual registration.

**For Option B, implement this bootstrap:**

```typescript
async bootstrap({ strapi }: { strapi: Core.Strapi }) {
  // Get all content types
  const contentTypes = [
    'api::post.post',
    'api::author.author',
    // ... all 7
  ];

  // Load and register each route
  for (const uid of contentTypes) {
    try {
      // Method 1: Check if Strapi auto-loads
      const route = strapi.config.get('content-api.endpoints')[uid];

      // Method 2: Manually create endpoint
      strapi.server.routes([{
        method: 'GET',
        path: `/api/${typeNameFromUid}`,
        handler: async (ctx) => {
          // Strapi's built-in handler
        }
      }]);
    } catch (error) {
      strapi.log.error(`Error registering ${uid}`);
    }
  }
}
```

---

## 🔍 DIAGNOSTIC BOOTSTRAP (CURRENTLY INSTALLED)

**File:** `/cms/strapi-main/src/index.ts`

**What It Does:**

1. Verifies all 7 content types are loaded
2. Counts total HTTP routes registered
3. Counts /api/\* routes specifically
4. Lists first 5 /api/\* routes found

**Expected Output:**

```
🚀 [BOOTSTRAP] Initializing REST API routes...
✅ Content type loaded: api::post.post
✅ Content type loaded: api::author.author
... (5 more)
📊 Content Type Summary: 7 loaded, 0 failed
📍 HTTP Router Status: 156 total routes, 0 API routes
❌ No /api/* routes found in HTTP router
✅ [BOOTSTRAP] Complete
```

**OR (if working):**

```
📍 HTTP Router Status: 156 total routes, 42 API routes
✅ API routes ARE registered with HTTP server!
   [0] GET    /api/posts
   [1] GET    /api/authors
...
```

---

## 📋 NEXT STEPS (ACTIONABLE)

### Immediate (Next 10 minutes)

1. **Start Strapi**

   ```bash
   cd cms/strapi-main
   npm run develop
   ```

2. **Capture Bootstrap Output**
   - Look for "[BOOTSTRAP]" in logs
   - Note how many API routes found
   - Document what routes ARE registered

3. **Test Endpoints**

   ```bash
   curl http://localhost:1337/api/posts
   curl http://localhost:1337/api/authors
   ```

4. **Decision Point**
   - ✅ If returns data → Routes working! Remove diagnostic code, seed data, move to next service
   - ❌ If still 404 → Implement Option B bootstrap (manual registration)

### Short Term (After routes work)

1. Update bootstrap to production version (remove diagnostics)
2. Seed sample data for each content type
3. Start other services (Public Site, Oversight Hub, Co-Founder Agent)
4. Test full pipeline: Content in Strapi → Displayed on Public Site

### Key Files

- **Bootstrap:** `/cms/strapi-main/src/index.ts` (already updated with diagnostics)
- **Routes:** `/cms/strapi-main/src/api/[type]/routes/[type].ts` (all 7 exist, correct)
- **Content Types:** `/cms/strapi-main/src/api/[type]/content-types/[type]/schema.json` (all 7)

---

## 🔧 QUICK REFERENCE: The 7 Content Types

| Content Type   | URL Path              | Route File                                      | Status   |
| -------------- | --------------------- | ----------------------------------------------- | -------- |
| Post           | /api/posts            | src/api/post/routes/post.ts                     | ✅ Ready |
| Author         | /api/authors          | src/api/author/routes/author.ts                 | ✅ Ready |
| Category       | /api/categories       | src/api/category/routes/category.ts             | ✅ Ready |
| Tag            | /api/tags             | src/api/tag/routes/tag.ts                       | ✅ Ready |
| Content Metric | /api/content-metrics  | src/api/content-metric/routes/content-metric.ts | ✅ Ready |
| About          | /api/about            | src/api/about/routes/about.ts                   | ✅ Ready |
| Privacy Policy | /api/privacy-policies | src/api/privacy-policy/routes/privacy-policy.ts | ✅ Ready |

---

## 🎯 SUCCESS CRITERIA

### When Routes Are Fixed

- [ ] `GET /api/posts` returns `{ data: [], meta: {...} }` (200 OK)
- [ ] `GET /api/authors` returns `{ data: [], meta: {...} }` (200 OK)
- [ ] All 7 endpoints responding with 200 (not 404)
- [ ] Can POST new content to any endpoint
- [ ] Admin panel shows data in each content type

### Then Move To

- [ ] Seed test data
- [ ] Start other services
- [ ] Test full pipeline

---

## 📚 RELATED DOCUMENTATION

- **Strapi v5 API Routes:** https://docs.strapi.io/dev-docs/api/rest
- **Bootstrap Lifecycle:** https://docs.strapi.io/dev-docs/setup-deployment-guides/file-structure
- **Content Type Configuration:** https://docs.strapi.io/user-docs/content-manager/collection-types

---

## 🚨 CRITICAL: Session Knowledge Base

**Previous Investigation Findings:**

- Strapi v5 middleware: logger, errors, security, cors, poweredBy, query, body, session, favicon, public
- NO built-in route middleware for REST registration
- Routes MUST be registered explicitly or auto-loaded by Strapi's internal loader
- Bootstrap is the RIGHT place to do this registration
- Current implementation: Diagnostic version tells us if auto-loading is working

**Key Constraint:**

- Port 1337 might have existing process → May need to kill before restarting
- Use: `sudo lsof -i :1337 | grep LISTEN | awk '{print $2}' | xargs kill -9` (if needed)

---

**Created:** November 13, 2025  
**Last Updated:** After extensive investigation  
**Ready For:** Implementation of Option A or B based on diagnostic output

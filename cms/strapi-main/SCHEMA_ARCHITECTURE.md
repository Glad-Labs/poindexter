# Strapi Schema Setup - Visual Architecture

## The Problem You Had

```
📁 Your Project Structure
└── cms/strapi-main/src/api/
    ├── post/
    │   ├── content-types/post/schema.json  ← EXISTS but not registered ❌
    │   ├── routes/post.ts                  ← Route file exists
    │   └── ...
    ├── category/
    │   ├── content-types/category/schema.json  ← EXISTS but not registered ❌
    │   └── ...
    └── [etc...]

💾 Strapi Database (SQLite/PostgreSQL)
├── content_types table = EMPTY ❌
├── No type definitions
└── Routes return 404 ❌

Result: GET /api/posts → 404 Not Found ❌
```

---

## The Solution I Created

```
LAYER 1: SCHEMA REGISTRATION
├─ register-content-types.js ⭐ NEW
│  └─ Discovers: Finds all schema.json files
│  └─ Reads: Parses JSON files
│  └─ Sends: POST to Strapi Content-Type Builder API
│  └─ Registers: Stores in database
│  └─ Result: Content types now available ✅
│
└─ Flow:
   schema.json files → register-content-types.js → Strapi Database

LAYER 2: ORCHESTRATION
├─ setup-complete.js ⭐ NEW
│  └─ Waits for Strapi to start
│  └─ Runs register-content-types.js
│  └─ Optionally runs seed scripts
│  └─ Provides helpful output
│
└─ Flow:
   Strapi startup → setup-complete → all 3 steps in order

LAYER 3: DATA SEEDING
├─ seed-data-fixed.js (ENHANCED)
│  └─ Creates: Categories, tags, authors
│  └─ Depends on: Content types (created by layer 1)
│  └─ Method: REST API calls (now working because types exist)
│
├─ seed-single-types.js (ENHANCED)
│  └─ Creates: About page, privacy policy
│  └─ Depends on: Content types
│  └─ Method: REST API calls
│
└─ Flow:
   API endpoints (now active) ← seed-data.js ← Categories, tags
```

---

## How It Fits Together

```
User runs: npm run setup

   ↓

setup-complete.js (NEW MASTER SCRIPT)
   ├─ Checks: Is Strapi running? ✓
   │
   ├─ Step 1: Calls register-content-types.js ⭐ NEW
   │  │
   │  └─ Does:
   │     ├─ Find: src/api/*/content-types/*/schema.json
   │     ├─ Parse: Convert JSON to Strapi format
   │     ├─ Register: POST to /content-type-builder/content-types
   │     └─ Result: 7 content types in database ✅
   │
   ├─ Step 2: (Optional) Calls seed-data-fixed.js
   │  │
   │  └─ Does:
   │     ├─ Create: 5 categories
   │     ├─ Create: 12 tags
   │     ├─ Create: 2 authors
   │     └─ Result: Sample data in database ✅
   │
   ├─ Step 3: (Optional) Calls seed-single-types.js
   │  │
   │  └─ Does:
   │     ├─ Create: About page
   │     ├─ Create: Privacy Policy
   │     └─ Result: Static pages in database ✅
   │
   └─ Output: Success messages and next steps

   ↓

Result:
   ✅ Content types registered
   ✅ API endpoints working
   ✅ Sample data available
   ✅ curl http://localhost:1337/api/posts → 200 OK
```

---

## Before vs After

### BEFORE (Original Setup)

```
User runs: npm run seed

❌ Error: "Cannot POST /api/categories"
   Reason: Content types don't exist

❌ Gets 405 Method Not Allowed
   Reason: Routes exist but no schema definition

❌ API returns 404 for GET requests
   Reason: Content types not registered in database
```

### AFTER (New Setup)

```
User runs: npm run setup

✅ register-content-types.js runs FIRST
   └─ Creates all content types in database

✅ seed-data-fixed.js runs SECOND
   └─ Now POST endpoints work (types exist)

✅ seed-single-types.js runs THIRD
   └─ Creates static page content

✅ All API endpoints now working
   └─ GET /api/posts → 200 OK with data
   └─ POST /api/categories → 201 Created
```

---

## File Reference

### register-content-types.js ⭐ KEY FILE

**What it does:**

1. Scans `cms/strapi-main/src/api/` directory
2. For each subdirectory (post, category, tag, etc.)
3. Looks for `content-types/{name}/schema.json`
4. If found, reads and parses the file
5. Sends to Strapi's Content-Type Builder API
6. Database now has registered content type

**Input:**

```
Directory structure:
cms/strapi-main/src/api/
├── post/content-types/post/schema.json
├── category/content-types/category/schema.json
└── ...
```

**Output:**

```json
{
  "data": [
    { "name": "post", "registered": true },
    { "name": "category", "registered": true },
    ...
  ]
}
```

**Key Code:**

```javascript
const schemas = discoverSchemas(); // Find all schema.json files
for (const schema of schemas) {
  await registerContentType(schema); // POST to Strapi API
}
```

---

### setup-complete.js ⭐ KEY FILE

**What it does:**

1. Orchestrator - runs everything in correct order
2. Waits for Strapi to start (polls http://localhost:1337)
3. Runs register-content-types.js
4. Optionally runs seed-data-fixed.js (if SEED_DATA=true)
5. Provides helpful output and next steps

**Run it with:**

```bash
npm run setup                    # Register types only
SEED_DATA=true npm run setup     # Register + seed data
```

**Key Code:**

```javascript
await waitForStrapi(); // Wait for Strapi
await runScript('register-content-types.js'); // Register
if (process.env.SEED_DATA === 'true') {
  await runScript('seed-data-fixed.js'); // Seed (optional)
}
```

---

### seed-data-fixed.js (Your Script, Enhanced)

**What it does:**

1. Creates 5 sample categories
2. Creates 12 sample tags
3. Creates 2 sample authors
4. All via REST API (now working because types exist)

**Prerequisites:**

- Strapi must be running
- Content types must be registered (register-content-types.js must run first)
- API token must be set

**Key Code:**

```javascript
for (const cat of data.categories) {
  await apiRequest('POST', '/categories', { data: cat });
}
```

---

### seed-single-types.js (Your Script, Enhanced)

**What it does:**

1. Creates About page (single type) with detailed content
2. Creates Privacy Policy (single type)
3. Both via REST API

**Prerequisites:**

- Content types must be registered first

---

## The Schema Format (For Reference)

```json
{
  "kind": "collectionType",
  "collectionName": "posts",
  "info": {
    "singularName": "post",
    "pluralName": "posts",
    "displayName": "Post"
  },
  "options": {
    "draftAndPublish": true
  },
  "attributes": {
    "title": { "type": "string", "required": true },
    "slug": { "type": "uid", "targetField": "title" },
    "content": { "type": "richtext" },
    "category": {
      "type": "relation",
      "relation": "manyToOne",
      "target": "api::category.category"
    }
  }
}
```

This is what register-content-types.js reads, processes, and sends to Strapi.

---

## Database State Over Time

```
INITIAL STATE (Before Scripts Run)
┌─────────────────────────────────┐
│ PostgreSQL/SQLite               │
├─────────────────────────────────┤
│ content_types table:            │
│ (empty)                         │
│                                 │
│ posts table: DOESN'T EXIST      │
│ categories table: DOESN'T EXIST │
│ tags table: DOESN'T EXIST       │
└─────────────────────────────────┘
  ❌ API returns 404

AFTER register-content-types.js
┌─────────────────────────────────┐
│ PostgreSQL/SQLite               │
├─────────────────────────────────┤
│ content_types table:            │
│ ├─ post (registered) ✅         │
│ ├─ category (registered) ✅     │
│ ├─ tag (registered) ✅          │
│ └─ ... (7 total)                │
│                                 │
│ posts table: CREATED ✅         │
│ categories table: CREATED ✅    │
│ tags table: CREATED ✅          │
└─────────────────────────────────┘
  ✅ API endpoints exist

AFTER seed-data-fixed.js
┌─────────────────────────────────┐
│ PostgreSQL/SQLite               │
├─────────────────────────────────┤
│ posts table:                    │
│ (0 rows)                        │
│                                 │
│ categories table:               │
│ ├─ AI & Machine Learning ✅     │
│ ├─ Game Development ✅          │
│ ├─ Technology Insights ✅       │
│ ├─ Business Strategy ✅         │
│ └─ Innovation ✅                │
│                                 │
│ tags table: (12 rows) ✅        │
│ authors table: (2 rows) ✅      │
└─────────────────────────────────┘
  ✅ API returns sample data
```

---

## Command Quick Reference

```bash
# All three options do the same thing in different ways:

# OPTION 1: One command (automatic)
npm run setup

# OPTION 2: Step by step
npm run register-types
npm run seed
npm run seed:single

# OPTION 3: Direct node commands
node scripts/register-content-types.js
node scripts/seed-data-fixed.js
node scripts/seed-single-types.js

# OPTION 4: With seeding included
SEED_DATA=true npm run setup
```

---

## Success Indicators

```
✅ Scripts complete without errors
✅ See "✅ REGISTRATION COMPLETE" message
✅ curl http://localhost:1337/api/posts returns 200 OK
✅ Strapi Admin shows 7 content types
✅ Database has posts, categories, tags tables
✅ Frontend can fetch data without 404 errors
```

---

## Summary

| Step | Script                       | Purpose                     | Time |
| ---- | ---------------------------- | --------------------------- | ---- |
| 1    | register-content-types.js ⭐ | Discover & register schemas | ~10s |
| 2    | seed-data-fixed.js           | Create sample data          | ~5s  |
| 3    | seed-single-types.js         | Create static pages         | ~3s  |
| -    | setup-complete.js            | Automate all 3              | ~20s |

**Your original seed scripts:** ✅ Still work (just needed step 1 first)

**What was missing:** ❌ Schema registration script (now created)

**Result:** ✅ Complete automated setup with one command

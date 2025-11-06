#!/usr/bin/env node

/**
 * 🚀 STRAPI SCHEMA SETUP GUIDE
 *
 * This document explains the complete workflow for programmatically
 * creating and registering Strapi content types.
 */

const guide = `
╔════════════════════════════════════════════════════════════════════╗
║           🚀 Strapi Content Type Setup - Complete Guide            ║
║                                                                    ║
║  How to programmatically create schemas and get your API working   ║
╚════════════════════════════════════════════════════════════════════╝

📋 TABLE OF CONTENTS
─────────────────────

1. OVERVIEW: What's Happening
2. THE PROBLEM: Why You're Getting 404s
3. THE SOLUTION: Three-Script Approach
4. HOW TO RUN IT
5. TROUBLESHOOTING
6. VERIFICATION CHECKLIST

═══════════════════════════════════════════════════════════════════════

1️⃣  OVERVIEW: What's Happening?
─────────────────────────────────

Your project HAS schema files:
  ✅ cms/strapi-main/src/api/post/content-types/post/schema.json
  ✅ cms/strapi-main/src/api/category/content-types/category/schema.json
  ✅ cms/strapi-main/src/api/tag/content-types/tag/schema.json
  ✅ (and others)

But Strapi DOESN'T recognize them yet:
  ❌ GET /api/posts → 404 Not Found
  ❌ GET /api/categories → 404 Not Found
  ❌ GET /api/tags → 404 Not Found

Why? Because Strapi v5 requires:
  1. Schema files must be READ from disk
  2. Schema must be REGISTERED with Strapi database
  3. Content types must be ACTIVATED in admin
  4. API PERMISSIONS must be enabled

═══════════════════════════════════════════════════════════════════════

2️⃣  THE PROBLEM: Why You're Getting 404s
──────────────────────────────────────────

File vs Database Reality:

  📁 Filesystem (schema.json files)
     └─ exist ✅
     └─ are valid JSON ✅
     └─ have correct structure ✅

  💾 Strapi Database (sqlite/postgres)
     └─ content types table = EMPTY ❌
     └─ routes exist but no schema definition ❌
     └─ no permissions configured ❌

Result:
  GET /api/posts → Router checks database
                → Content type not found
                → Returns 404

═══════════════════════════════════════════════════════════════════════

3️⃣  THE SOLUTION: Three-Script Approach
─────────────────────────────────────────

We've created 3 NEW scripts for you:

┌─────────────────────────────────────────────────────────────────────┐
│ SCRIPT 1: register-content-types.js                                 │
│────────────────────────────────────────────────────────────────────│
│ What it does:                                                       │
│  ✓ Discovers all schema.json files in src/api/*/content-types/     │
│  ✓ Reads each schema                                               │
│  ✓ Sends to Strapi Content-Type Builder API                        │
│  ✓ Registers them in database                                      │
│                                                                     │
│ Run this FIRST after starting Strapi                               │
│                                                                     │
│ Command: npm run register-types                                    │
│ Time: ~10 seconds                                                  │
│ Idempotent: Yes (safe to run multiple times)                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ SCRIPT 2: seed-data-fixed.js                                        │
│────────────────────────────────────────────────────────────────────│
│ What it does:                                                       │
│  ✓ Creates sample categories (5 items)                              │
│  ✓ Creates sample tags (12 items)                                   │
│  ✓ Creates sample authors (2 items)                                 │
│                                                                     │
│ Run this SECOND after types are registered                          │
│                                                                     │
│ Command: npm run seed                                              │
│ Time: ~5 seconds                                                   │
│ Idempotent: No (can create duplicates)                              │
│ Prerequisites: register-content-types.js must run first             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ SCRIPT 3: seed-single-types.js                                      │
│────────────────────────────────────────────────────────────────────│
│ What it does:                                                       │
│  ✓ Creates About page content (single type)                         │
│  ✓ Creates Privacy Policy content (single type)                     │
│                                                                     │
│ Run this AFTER data seeding                                         │
│                                                                     │
│ Command: npm run seed:single                                       │
│ Time: ~3 seconds                                                   │
│ Idempotent: No (can create duplicates)                              │
│ Prerequisites: register-content-types.js must run first             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ BONUS SCRIPT: setup-complete.js (Master Orchestrator)              │
│────────────────────────────────────────────────────────────────────│
│ What it does:                                                       │
│  ✓ Waits for Strapi to be ready                                     │
│  ✓ Runs register-content-types.js                                   │
│  ✓ Optionally runs seed-data-fixed.js (if SEED_DATA=true)          │
│  ✓ Runs all in correct order                                        │
│  ✓ Provides helpful output and next steps                           │
│                                                                     │
│ Run this ONCE after Strapi starts                                   │
│                                                                     │
│ Command: npm run setup                                             │
│ With seeding: SEED_DATA=true npm run setup                         │
│ Time: ~20 seconds                                                  │
│ Idempotent: Mostly (skips if already registered)                    │
└─────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════

4️⃣  HOW TO RUN IT
──────────────────

OPTION A: Automatic Setup (Recommended)
────────────────────────────────────────

1. Start Strapi:
   cd cms/strapi-main
   npm run develop

   (Leave this running in one terminal)

2. In another terminal, register types:
   cd cms/strapi-main
   npm run register-types

   Expected output:
   ✅ Registering post... ✅ post: Registered successfully
   ✅ Registering category... ✅ category: Registered successfully
   ✅ Registering tag... ✅ tag: Registered successfully
   ... etc

3. Seed sample data:
   npm run seed

   Expected output:
   ✅ Creating categories...
   ✅  AI & Machine Learning
   ✅  Game Development
   ... etc

4. Seed single types:
   npm run seed:single

   Expected output:
   ✅ Creating about page...
   ✅ Creating privacy policy...

Done! ✅

═══════════════════════════════════════════════════════════════════════

OPTION B: Manual Setup (Step by Step)
──────────────────────────────────────

# Terminal 1: Start Strapi
cd cms/strapi-main
npm run develop
# Wait for: "Application has booted"

# Terminal 2: Register types
cd cms/strapi-main
node scripts/register-content-types.js
# Wait for: "✅ REGISTRATION COMPLETE"

# Still Terminal 2: Seed data
node scripts/seed-data-fixed.js
# Wait for: "Done!"

# Still Terminal 2: Seed single types
node scripts/seed-single-types.js
# Wait for: "Done!"

═══════════════════════════════════════════════════════════════════════

OPTION C: All-in-One (Master Script)
─────────────────────────────────────

# Terminal 1: Start Strapi
cd cms/strapi-main
npm run develop

# Terminal 2: Run everything
cd cms/strapi-main
SEED_DATA=true npm run setup

# This will:
# 1. Wait for Strapi to start
# 2. Register all content types
# 3. Seed categories, tags, authors
# 4. Display helpful next steps

═══════════════════════════════════════════════════════════════════════

5️⃣  TROUBLESHOOTING
────────────────────

❌ Error: "Cannot find module axios"

Solution:
  cd cms/strapi-main
  npm install axios

───────────────────────────────────────────────────────────────────

❌ Error: "STRAPI_API_TOKEN not set"

Solution: 
  Option 1 - Use default for local dev:
    STRAPI_API_TOKEN=test-token npm run register-types

  Option 2 - Generate a real token:
    1. Go to http://localhost:1337/admin
    2. Settings → API Tokens → Create new API Token
    3. Copy the token
    4. Export it: export STRAPI_API_TOKEN=your-token-here
    5. Run: npm run register-types

───────────────────────────────────────────────────────────────────

❌ Error: "Cannot GET /api/posts" (still 404 after running scripts)

Solution:
  1. Verify scripts ran successfully (look for ✅ output)
  2. Check Strapi Admin: http://localhost:1337/admin
  3. Look in Content Manager for Post, Category, Tag
  4. If missing, check script errors
  5. Make sure Strapi hasn't crashed (check Terminal 1)

───────────────────────────────────────────────────────────────────

❌ Error: "409 Conflict - Content Type Already Exists"

Solution (this is normal):
  You can safely ignore this - it means the content type was
  already registered. Just continue!

───────────────────────────────────────────────────────────────────

❌ Scripts hang or don't complete

Solution:
  1. Check if Strapi is running: http://localhost:1337/admin
  2. Check if port 1337 is in use
  3. Restart Strapi:
     - Kill Terminal 1 (Ctrl+C)
     - Wait 5 seconds
     - Run npm run develop again
  4. Try scripts again

═══════════════════════════════════════════════════════════════════════

6️⃣  VERIFICATION CHECKLIST
───────────────────────────

After running the scripts, verify everything works:

□ Check Strapi Admin
  Go to: http://localhost:1337/admin
  Look for: Content Manager → Collections (Post, Category, Tag)
  
□ Check Database
  For SQLite: cms/strapi-main/.tmp/data.db
  For PostgreSQL: SELECT * FROM information_schema.tables WHERE table_schema='public'
  
□ Test API - Get Posts
  curl http://localhost:1337/api/posts
  Expected: {"data": [...], "meta": {...}}
  NOT: 404 or {"error": ...}

□ Test API - Get Categories
  curl http://localhost:1337/api/categories
  Expected: {"data": [...], "meta": {...}}

□ Test API - Get Tags
  curl http://localhost:1337/api/tags
  Expected: {"data": [...], "meta": {...}}

□ Check Permissions
  In Strapi Admin: Settings → Roles → Public
  Look for: post (find, findOne), category (find), tag (find)
  If missing: Enable them manually

□ Test Frontend Integration
  curl http://localhost:3000/
  Should fetch content from Strapi without 404 errors

═══════════════════════════════════════════════════════════════════════

📊 SUMMARY OF SCRIPTS
──────────────────────

File                          | Purpose                        | Run When
──────────────────────────────┼────────────────────────────────┼──────────────────
register-content-types.js     | Register schemas in database   | First (after Strapi starts)
seed-data-fixed.js            | Create sample data             | Second (after registration)
seed-single-types.js          | Create about/privacy pages     | Third (after data seeding)
setup-complete.js             | Orchestrate all three          | Instead of manual steps

═══════════════════════════════════════════════════════════════════════

🎯 YOUR NEXT STEPS
────────────────────

1. ✅ Verify Strapi is running: npm run develop
2. ✅ Run setup: npm run register-types
3. ✅ Seed data: npm run seed
4. ✅ Check Strapi Admin: http://localhost:1337/admin
5. ✅ Test API: curl http://localhost:1337/api/posts
6. ✅ Check frontend: http://localhost:3000

🎉 You're done!

═══════════════════════════════════════════════════════════════════════
`;

console.log(guide);

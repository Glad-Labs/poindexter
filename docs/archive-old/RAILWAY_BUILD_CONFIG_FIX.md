# Railway Strapi Build Configuration Fix

## The Problem

Your `src/api/` schema files are in Git, but **Railway isn't building them**.

When you deploy to Railway, Strapi starts with `npm run start`, but the TypeScript hasn't been compiled and the build hasn't happened. The content types aren't recognized because the build process is skipped.

## The Solution

Create a `Procfile` and update Railway to use a proper build script.

### Step 1: Create a Procfile

**File:** `cms/strapi-v5-backend/Procfile`

```
release: npm run build
web: npm run start
```

This tells Railway to:

1. **Before starting:** Run `npm run build` (compiles TypeScript, discovers content types)
2. **Then start:** Run `npm run start` (serves the application)

### Step 2: Push to GitHub

```powershell
cd cms/strapi-v5-backend
git add Procfile
git commit -m "chore: add Procfile for Railway build configuration"
git push origin dev
```

### Step 3: Redeploy on Railway

1. Go to [railway.app](https://railway.app)
2. Select your project → **strapi-production** service
3. Click **"Redeploy"** button
4. Wait for logs to show:
   ```
   [BUILD] Running release command: npm run build
   ✔ Compiling TS
   ✔ Building build context
   ✔ Building admin panel
   [START] Running web command: npm run start
   Strapi is ready on port 1337
   ```

### Step 4: Verify Content Types

1. Check logs: `railway logs --service strapi-production`
2. Look for: `[strapi]: Content types synchronized ✓`
3. Test API: `curl https://strapi-production-b234.up.railway.app/api/graphql`

---

## Why This Works

```
Without Procfile:
Railway starts → npm run start → Strapi tries to serve
                                 (TypeScript NOT compiled)
                                 (Content types NOT loaded)
                                 ❌ FAIL

With Procfile:
Railway starts → npm run build → (TypeScript compiled)
              → npm run start  → (Content types loaded)
              → Strapi serves  ✅ SUCCESS
```

---

## Alternative: Update Railway Start Command

If you can't use a `Procfile`, update Railway service directly:

**In Railway UI:**

1. Go to strapi-production service
2. Click **"Variables"** tab
3. Add new variable: `RAILWAY_RUN_SETUP_COMMANDS=true`
4. Go to **"Deploy"** tab
5. Change "Start Command" to:
   ```bash
   npm run build && npm run start
   ```

---

## What Gets Loaded

When `npm run build` runs, Strapi discovers and compiles:

```
src/api/
├── post/
│   └── content-types/post/schema.json      ✅ Loaded
├── category/
│   └── content-types/category/schema.json  ✅ Loaded
├── tag/
│   └── content-types/tag/schema.json       ✅ Loaded
├── author/
│   └── content-types/author/schema.json    ✅ Loaded
├── about/
│   └── content-types/about/schema.json     ✅ Loaded
├── privacy-policy/
│   └── content-types/privacy-policy/schema.json  ✅ Loaded
└── content-metric/
    └── content-types/content-metric/schema.json  ✅ Loaded
```

All 7 content types will be created in PostgreSQL automatically.

---

## After This Works

1. ✅ Content types appear in production
2. ✅ API endpoints become available
3. ✅ You can seed data (manually or via script)
4. ✅ Vercel build will succeed (API has content)

---

## Verification Checklist

- [ ] `Procfile` created in `cms/strapi-v5-backend/`
- [ ] Pushed to GitHub
- [ ] Railway redeployed
- [ ] Build logs show `npm run build` succeeded
- [ ] Admin UI shows all 7 content types
- [ ] API returns GraphQL schema with Post, Category, Tag, etc.

---

## Troubleshooting

### Procfile Not Working

**Symptom:** Railway still doesn't run build

**Fix:**

1. Delete `Procfile`
2. Use Railway UI: Settings → "Start Command" → Change to `npm run build && npm run start`
3. Redeploy

### Still No Content Types

**Symptom:** Admin shows no content types after build

**Check:**

1. Verify `src/api/*/content-types/*/schema.json` files are in Git
2. Check Railway logs: `railway logs --service strapi-production`
3. Look for errors in compile step
4. Try manual redeploy

### Build Timeout

**Symptom:** Build takes >10 minutes

**Fix:**

- Build is normal for first deploy (installs dependencies)
- Clear Railway cache and retry
- Check for dependency issues: `npm install` locally first

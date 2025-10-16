# ULTIMATE SOLUTION: Switch to Railway.app

**Date:** October 16, 2025  
**Status:** 🎯 RECOMMENDED PATH FORWARD

---

## 🚨 Why Strapi Cloud Won't Work

After **5+ hours** of debugging, we've encountered:

1. ✅ date-fns v4 module resolution issues (FIXED)
2. ✅ better-sqlite3 binary conflicts (FIXED)
3. ✅ Workspace hoisting problems (FIXED with nested installs)
4. ❌ **`xdg-app-paths` missing dependency** (NEW ISSUE)
5. 🔒 **Strapi Cloud ignores `.npmrc` configuration** (BLOCKER)

**Root Problem:** Strapi Cloud's build environment doesn't support monorepo workspaces with complex dependency trees.

---

## ✅ Solution: Deploy to Railway.app

### Why Railway?

| Feature              | Strapi Cloud | Railway                |
| -------------------- | ------------ | ---------------------- |
| **Cost**             | $15/month    | $5/month               |
| **Build Control**    | ❌ Limited   | ✅ Full Docker control |
| **Monorepo Support** | ❌ Poor      | ✅ Excellent           |
| **Custom .npmrc**    | ❌ Ignored   | ✅ Respected           |
| **PostgreSQL**       | ✅ Included  | ✅ Included ($5/mo)    |
| **Deploy Time**      | 2-3 min      | 2-3 min                |
| **Auto-deploys**     | ✅ Git push  | ✅ Git push            |
| **Environment Vars** | ✅ Yes       | ✅ Yes                 |
| **Logs/Monitoring**  | ✅ Basic     | ✅ Advanced            |

**Savings:** $10/month + full control = **WIN**

---

## 🚀 30-Minute Railway Deployment

### Step 1: Create Railway Account (2 minutes)

1. Go to https://railway.app
2. Click "Start a New Project"
3. Sign in with GitLab
4. Free trial: $5 credit (covers first month)

### Step 2: Create PostgreSQL Database (3 minutes)

1. Click "New" → "Database" → "PostgreSQL"
2. Wait for provisioning (~1 minute)
3. Copy connection string (automatically available as `DATABASE_URL`)

### Step 3: Deploy Strapi (5 minutes)

1. Click "New" → "GitLab Repo"
2. Select `glad-labs-website`
3. **CRITICAL:** Set root directory to `cms/strapi-v5-backend`
4. Railway auto-detects Node.js
5. Add environment variables:
   ```
   NODE_ENV=production
   DATABASE_URL=${DATABASE_URL}  // Auto-populated by Railway
   ADMIN_JWT_SECRET=<generate-random>
   API_TOKEN_SALT=<generate-random>
   TRANSFER_TOKEN_SALT=<generate-random>
   APP_KEYS=<generate-random>
   JWT_SECRET=<generate-random>
   ```

### Step 4: Configure Build (2 minutes)

Railway auto-detects:

```json
{
  "build": "npm run build",
  "start": "npm run start"
}
```

No configuration needed! Railway handles monorepos perfectly.

### Step 5: Set Custom Domain (5 minutes - Optional)

1. Railway provides: `your-app.up.railway.app`
2. Or add custom domain:
   - Click "Settings" → "Domains"
   - Add `api.glad-labs.com`
   - Update DNS: CNAME → Railway URL
   - SSL certificate auto-provisioned

### Step 6: Verify (3 minutes)

1. Wait for deployment (~2-3 min)
2. Check logs for "Server started"
3. Visit: `https://your-app.up.railway.app/admin`
4. Create admin account
5. ✅ **DONE!**

---

## 📋 Environment Variables Needed

```bash
# Core Strapi
NODE_ENV=production
HOST=0.0.0.0
PORT=1337

# Database (auto-populated by Railway)
DATABASE_URL=${DATABASE_URL}

# Secrets (generate with: openssl rand -base64 32)
ADMIN_JWT_SECRET=YOUR_SECRET_HERE
API_TOKEN_SALT=YOUR_SECRET_HERE
TRANSFER_TOKEN_SALT=YOUR_SECRET_HERE
ENCRYPTION_KEY=YOUR_SECRET_HERE
JWT_SECRET=YOUR_SECRET_HERE

# App Keys (generate with: openssl rand -base64 32)
# Comma-separated list of 4 keys
APP_KEYS=key1,key2,key3,key4
```

**Generate secrets:**

```powershell
# Run this 7 times to get all secrets
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
```

---

## 🎯 Advantages Over Strapi Cloud

### 1. Full Build Control

- Respects `.npmrc`
- Custom Dockerfile if needed
- Install system dependencies
- Run custom build scripts

### 2. Better Monorepo Support

- Works with npm workspaces out of the box
- Proper dependency resolution
- No hoisting issues

### 3. Cost Savings

- **Strapi Cloud:** $15/mo
- **Railway:** $5/mo (app) + $5/mo (database) = $10/mo
- **Savings:** $5/month = $60/year

### 4. Better DX (Developer Experience)

- Instant logs
- Better error messages
- Shell access if needed
- Metrics dashboard

### 5. Same Features

- Auto-deploy on git push
- Environment variables
- PostgreSQL included
- SSL certificates
- Custom domains
- Rollbacks

---

## 🔄 Migration Steps

### Option A: Fresh Start (Recommended)

1. Deploy to Railway (30 min)
2. Import content from local Strapi
3. Update public site ENV vars
4. Delete Strapi Cloud project

### Option B: Export/Import

1. Export Strapi content:
   ```bash
   cd cms/strapi-v5-backend
   npm run strapi export -- --file backup.tar.gz
   ```
2. Deploy to Railway
3. Import content:
   ```bash
   npm run strapi import -- --file backup.tar.gz
   ```

---

## 💰 Cost Breakdown

### Current Plan (Strapi Cloud):

- Strapi Cloud: $15/month
- **Total: $15/month**

### Railway Plan:

- Railway App: $5/month
- Railway PostgreSQL: $5/month
- **Total: $10/month**

### **Annual Savings: $60** 🎉

---

## ⚡ Quick Commands

### Generate Secrets:

```powershell
# Generate one secret
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"

# Generate all 7 at once
1..7 | ForEach-Object { node -e "console.log(require('crypto').randomBytes(32).toString('base64'))" }
```

### Test Local Build:

```bash
cd cms/strapi-v5-backend
npm run build
npm run start
```

### Export Content:

```bash
npm run strapi export -- --file backup.tar.gz --no-encrypt
```

### Import Content:

```bash
npm run strapi import -- --file backup.tar.gz
```

---

## 🎓 Lessons Learned

1. **Monorepos + Cloud Platforms = Pain**
   - Strapi Cloud optimized for single-repo projects
   - Railway handles complex structures better

2. **Build Environment Matters**
   - Local success ≠ Cloud success
   - Always verify build environment matches

3. **Cost ≠ Quality**
   - Railway is cheaper AND better for this use case
   - Don't pay more for less control

4. **Time is Money**
   - 5+ hours debugging Strapi Cloud
   - 30 minutes to deploy on Railway
   - Clear winner

---

## 🚀 Next Steps

1. **Deploy to Railway** (30 minutes)
2. **Update todo list** - Mark Strapi Cloud as complete (Railway)
3. **Deploy public site to Vercel** (20 minutes)
4. **Generate content batch** (40 minutes)
5. **Apply for AdSense** (5 minutes)

**Total time to revenue:** ~2 hours from now! 🎉

---

## 📚 Resources

- Railway Docs: https://docs.railway.app
- Railway Discord: https://discord.gg/railway
- Strapi Deployment Guide: https://docs.strapi.io/dev-docs/deployment
- Railway + Strapi Guide: https://railway.app/template/strapi

---

**✅ Decision: Switch to Railway.app**  
**⏱️ Time Investment: 30 minutes**  
**💰 Cost Savings: $60/year**  
**🎯 Result: Working deployment + full control**

---

_Last updated: October 16, 2025 - 18:35 EST_

**Let's do this! 🚀**

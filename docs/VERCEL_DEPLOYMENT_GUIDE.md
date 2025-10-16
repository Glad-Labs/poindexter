# 🚀 Vercel Deployment Guide - GLAD Labs Public Site

**Goal:** Get your Next.js public site live in production in under 30 minutes

---

## Prerequisites

- ✅ GitLab repository set up
- ✅ Strapi CMS running (can be local for now)
- ✅ Next.js site working locally
- ✅ Vercel account (free tier)

---

## Step 1: Prepare for Deployment

### 1.1 Check Environment Variables

Your public site needs to know where Strapi is. Check/create `.env.local`:

**Location:** `web/public-site/.env.local`

```bash
# Strapi API URL (will change to production URL later)
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337

# Your site URL (for SEO, sitemaps)
NEXT_PUBLIC_SITE_URL=https://your-site.vercel.app

# Google Analytics (optional - add later)
NEXT_PUBLIC_GA_ID=G-XXXXXXXXXX

# Google AdSense (optional - add after approval)
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-XXXXXXXXXX
```

### 1.2 Update Strapi URL Configuration

For now, you have two options:

**Option A: Keep Strapi Local (Quick Start)**

- Use ngrok or similar to expose local Strapi
- Good for testing, not ideal for production

**Option B: Deploy Strapi Too (Recommended)**

- Deploy Strapi to Railway, Render, or Cloud Run
- Then update `NEXT_PUBLIC_STRAPI_API_URL`

Let's start with **Option A** for speed, then upgrade to **Option B**.

---

## Step 2: Push to GitLab

### 2.1 Commit All Changes

```powershell
# Make sure you're in the project root
cd C:\Users\mattm\glad-labs-website

# Check status
git status

# Add all files
git add .

# Commit
git commit -m "Prepare for Vercel deployment - public site ready"

# Push to GitLab
git push origin main
```

### 2.2 Verify on GitLab

Go to your GitLab repo and verify all files are there:

- `web/public-site/` directory
- `package.json` with correct scripts
- All pages and components

---

## Step 3: Set Up Vercel

### 3.1 Create Vercel Account

1. Go to https://vercel.com
2. Sign up with GitHub, GitLab, or email
3. **Important:** Choose "Import Git Repository" option

### 3.2 Connect GitLab

1. In Vercel dashboard, click "Add New Project"
2. Select "GitLab" as the provider
3. Authorize Vercel to access your GitLab
4. Select your `glad-labs-website` repository

### 3.3 Configure Project

**Root Directory:**

```
web/public-site
```

**Framework Preset:**

- Auto-detected: Next.js ✅

**Build Command:**

```bash
npm run build
```

**Output Directory:**

```
.next
```

**Install Command:**

```bash
npm install
```

### 3.4 Environment Variables

Add these in Vercel's "Environment Variables" section:

| Name                         | Value                                                   |
| ---------------------------- | ------------------------------------------------------- |
| `NEXT_PUBLIC_STRAPI_API_URL` | `https://your-ngrok-url.ngrok.io` (temporary)           |
| `NEXT_PUBLIC_SITE_URL`       | `https://your-project.vercel.app` (Vercel will provide) |

**Note:** You'll get the Vercel URL after first deployment, then update this.

---

## Step 4: Deploy!

### 4.1 Click "Deploy"

Vercel will:

1. Clone your repo
2. Install dependencies
3. Build your Next.js site
4. Deploy to their edge network

**Expected time:** 2-5 minutes

### 4.2 Watch the Build Log

Monitor for errors:

- ✅ Installing dependencies... (30-60s)
- ✅ Building Next.js... (1-2 min)
- ✅ Deploying... (30s)
- ✅ Ready! (link will appear)

### 4.3 Visit Your Live Site!

You'll get a URL like: `https://glad-labs-website-xxx.vercel.app`

---

## Step 5: Test Your Deployment

### 5.1 Check Homepage

Visit your site and verify:

- ✅ Page loads
- ✅ Styles applied correctly
- ✅ No console errors

### 5.2 Check Strapi Connection

- ✅ Featured post loads (if you have one in Strapi)
- ✅ Recent posts appear
- ✅ Blog post pages work (`/posts/[slug]`)

**If posts don't load:**

- Check `NEXT_PUBLIC_STRAPI_API_URL` is correct
- Verify Strapi is accessible from internet
- Check browser console for CORS errors

### 5.3 Test Navigation

- ✅ About page loads
- ✅ Privacy Policy loads
- ✅ Archive/pagination works
- ✅ Category/tag pages work (if you have data)

---

## Step 6: Fix Strapi Connection (If Needed)

### Option A: Use ngrok (Temporary)

**Install ngrok:**

```powershell
# Download from https://ngrok.com/download
# Or use chocolatey
choco install ngrok
```

**Expose Strapi:**

```powershell
# In a new terminal, run:
ngrok http 1337
```

You'll get a URL like: `https://abc123.ngrok.io`

**Update Vercel Environment Variable:**

1. Go to Vercel project settings
2. Update `NEXT_PUBLIC_STRAPI_API_URL` to ngrok URL
3. Redeploy (Vercel will auto-deploy on env var change)

**Add to Strapi CORS:**

Edit `cms/strapi-v5-backend/config/middleware.ts`:

```typescript
export default [
  'strapi::logger',
  'strapi::errors',
  'strapi::security',
  {
    name: 'strapi::cors',
    config: {
      enabled: true,
      origin: [
        'http://localhost:3000',
        'https://your-site.vercel.app',
        'https://*.ngrok.io', // Allow all ngrok URLs
      ],
    },
  },
  // ... rest of middleware
];
```

**Restart Strapi:**

```powershell
cd cms/strapi-v5-backend
npm run develop
```

### Option B: Deploy Strapi (Production-Ready)

**Recommended Services:**

1. **Railway** (Easiest, $5-10/month)
   - Connect GitLab
   - Select Strapi directory
   - Add PostgreSQL database
   - Deploy!

2. **Render** (Free tier available)
   - Docker-based deployment
   - PostgreSQL included
   - Good for production

3. **Google Cloud Run** (You're already using GCP)
   - Container-based
   - More complex setup
   - Scales to zero

**I can help you deploy Strapi separately if needed!**

---

## Step 7: Configure Custom Domain (Optional)

### 7.1 Add Domain in Vercel

1. Go to Project Settings → Domains
2. Add your domain (e.g., `www.glad-labs.com`)
3. Follow Vercel's DNS instructions

### 7.2 Update DNS

Add these records to your domain provider:

```
Type: CNAME
Name: www
Value: cname.vercel-dns.com
```

**Or for apex domain (glad-labs.com):**

```
Type: A
Name: @
Value: 76.76.21.21
```

### 7.3 Update Environment Variables

Once domain is connected:

```bash
NEXT_PUBLIC_SITE_URL=https://www.glad-labs.com
```

Redeploy for changes to take effect.

---

## Step 8: Set Up Automatic Deployments

**Already done!** Vercel automatically deploys on every push to `main`.

**To deploy a specific branch:**

1. Go to Project Settings → Git
2. Change "Production Branch" if needed
3. Set up preview branches for testing

**Workflow:**

```
Push to GitLab → Vercel detects → Auto builds → Auto deploys
```

---

## Step 9: Optimize for Production

### 9.1 Add Production Environment Variables

```bash
# Add in Vercel dashboard
NODE_ENV=production
```

### 9.2 Enable Analytics (Vercel Analytics - Free)

1. Go to Analytics tab in Vercel
2. Enable Vercel Analytics
3. Get real-time performance data

### 9.3 Test Performance

Run Lighthouse audit:

1. Open DevTools (F12)
2. Go to Lighthouse tab
3. Run audit

**Target Scores:**

- Performance: 90+
- SEO: 95+
- Best Practices: 90+
- Accessibility: 90+

---

## Troubleshooting

### Build Fails

**Error:** "Module not found"

- Check `package.json` has all dependencies
- Run `npm install` locally first
- Push updated `package-lock.json`

**Error:** "Build timeout"

- Increase timeout in Vercel settings
- Or optimize build (remove unused deps)

### Page Shows 404

**Cause:** Page not generated at build time

- Check `getStaticPaths` in dynamic routes
- Verify Strapi data exists
- Check `fallback: 'blocking'` in `getStaticPaths`

### Strapi Connection Fails

**CORS Error:**

- Update Strapi CORS config (see Step 6)
- Verify `NEXT_PUBLIC_STRAPI_API_URL` is correct
- Check Strapi is accessible from internet

**API Returns 404:**

- Verify Strapi endpoints are correct (`/api/posts`, etc.)
- Check Strapi content types are published
- Test Strapi API directly in browser

### Styles Not Loading

**CSS Missing:**

- Verify Tailwind config is correct
- Check `postcss.config.js` exists
- Rebuild locally to test

---

## Post-Deployment Checklist

After your site is live:

- [ ] ✅ Site loads at Vercel URL
- [ ] ✅ All pages accessible
- [ ] ✅ Strapi content displays
- [ ] ✅ Images load correctly
- [ ] ✅ No console errors
- [ ] ✅ Mobile responsive (test on phone)
- [ ] ✅ Lighthouse score 90+
- [ ] ✅ Site indexed by Google (may take days/weeks)

---

## Next Steps

Once deployed:

1. **Generate Content** (Task 3) - Create initial blog posts
2. **Apply for AdSense** (Task 4) - Need live site for approval
3. **Set Up SEO** (Task 5) - Google Analytics, Search Console
4. **Automate** (Tasks 6-7) - Content pipeline and scheduling

---

## Cost Breakdown

**Vercel Free Tier Includes:**

- ✅ 100 GB bandwidth/month
- ✅ Unlimited sites
- ✅ Automatic SSL
- ✅ Edge network (fast globally)
- ✅ Automatic deployments

**When You Need to Upgrade ($20/month):**

- More than 100 GB bandwidth
- More than 100 GB-hours compute
- Advanced analytics
- Custom edge functions

**Estimated Timeline to Upgrade:**

- With 10K monthly visitors: Stay on free tier
- With 50K+ monthly visitors: Consider Pro tier
- With 100K+ monthly visitors: Upgrade to Pro

---

## 🚀 Ready to Deploy?

**Run these commands:**

```powershell
# 1. Commit changes
cd C:\Users\mattm\glad-labs-website
git add .
git commit -m "Ready for deployment"
git push origin main

# 2. Go to Vercel
# https://vercel.com/new

# 3. Import from GitLab
# Select: glad-labs-website
# Root: web/public-site
# Click Deploy!
```

**Estimated time: 5-10 minutes** ⏱️

---

**Want me to help with any specific step? Let me know!** 🚀

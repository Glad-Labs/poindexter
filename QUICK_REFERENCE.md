# 📌 PUBLIC SITE QUICK REFERENCE

## 🚀 TL;DR

Your blog pipeline works but posts aren't displaying. Fix in 2 hours:

1. Update 2 files with data mapper integration (30 min)
2. Add images to posts (30 min)
3. Deploy to production (1 hour)

Posts go live. Then wait 30 days for traffic history, apply for AdSense.

---

## 📁 Files Created (Use These)

```
📄 README_PUBLIC_SITE.md ← START HERE
📄 PUBLIC_SITE_EXECUTIVE_SUMMARY.md
📄 PUBLIC_SITE_INTEGRATION_GUIDE.md ← FOLLOW THIS
📄 PUBLIC_SITE_PRODUCTION_READINESS.md
📝 web/public-site/lib/post-mapper.js ← USE THIS
🔧 scripts/fix-public-site.sh
✅ public-site-checklist.sh
```

---

## ⚡ 30-Minute Quick Integration

### Step 1: Update API (10 min)

File: `web/public-site/lib/api-fastapi.js`

**Add at top:**

```javascript
import {
  mapDatabasePostsToComponents,
  mapDatabasePostToComponent,
} from './post-mapper';
```

**In getPaginatedPosts():**

```javascript
const data = mapDatabasePostsToComponents(response.data || []);
```

**In getFeaturedPost():**

```javascript
return mapDatabasePostToComponent(response.data[0]);
```

### Step 2: Update Homepage (5 min)

File: `web/public-site/pages/index.js`

Remove: `getStrapiURL` import and references
Update: `coverImage?.data?.attributes?.url` → use mapped data directly

### Step 3: Test (10 min)

```bash
cd web/public-site
npm run dev
# Go to http://localhost:3000
# Posts should appear!
```

### Step 4: Deploy (5 min)

```bash
npm run build
npm run start  # Test locally
# Push to production
```

---

## 🖼️ Image Options

### Option A: Placeholder (5 min)

```sql
UPDATE posts
SET featured_image_url = CONCAT('https://picsum.photos/800/600?random=', id)
WHERE featured_image_url IS NULL;
```

### Option B: Generate (30 min - 2 hours)

Implement `/api/media/generate-image` endpoint with DALL-E or Stable Diffusion

### Option C: Manual Upload (30 min - 1 hour)

1. Create images (Canva, Figma, DALL-E)
2. Upload to CDN
3. Update database

---

## 📊 Database Quick Checks

```sql
-- All posts
SELECT title, slug, featured_image_url, published_at FROM posts;

-- Posts missing images
SELECT slug FROM posts WHERE featured_image_url IS NULL;

-- Posts without proper titles
SELECT slug FROM posts WHERE title = 'Untitled';

-- Add timestamps
UPDATE posts SET published_at = created_at WHERE published_at IS NULL;

-- Count posts by status
SELECT COUNT(*), COUNT(featured_image_url) with_images FROM posts;
```

---

## 📱 Testing Checklist

- [ ] Posts appear on homepage
- [ ] Images load (if available)
- [ ] Links work
- [ ] Mobile responsive
- [ ] No console errors
- [ ] Page load < 3 seconds

---

## 📈 Timeline to AdSense

```
Day 1:     Deploy site
Day 1-30:  Accumulate traffic
Day 30:    Apply for AdSense
Week 2-4:  Approval process
```

**Requirement:** 1,000+ monthly page views + 30 days history

---

## ⚠️ AdSense Gotchas

❌ **Don't:** Have ads from other networks
❌ **Don't:** Have excessive pop-ups
❌ **Don't:** Have thin or duplicate content
❌ **Don't:** Click your own ads
❌ **Don't:** Buy traffic artificially

✅ **Do:** Have original/unique content (disclose AI generation)
✅ **Do:** Have complete privacy policy
✅ **Do:** Have proper site structure (about, contact pages)
✅ **Do:** Let traffic accumulate naturally

---

## 🛠️ Common Errors & Fixes

### Posts Not Showing

```
❌ Wrong: Data still in Strapi format
✅ Fix: Use post-mapper.js to convert data
```

### Images Not Loading

```
❌ Wrong: featured_image_url is NULL
✅ Fix: Add images with SQL or upload
```

### API Errors in Console

```
❌ Wrong: NEXT_PUBLIC_FASTAPI_URL not set
✅ Fix: Check .env.local has correct URL
```

### Build Fails

```
❌ Wrong: Missing dependencies
✅ Fix: npm install && npm run build
```

---

## 📞 Quick Commands

```bash
# Development
cd web/public-site && npm run dev

# Production
npm run build && npm run start

# Check posts in DB
psql -h localhost -U postgres -d glad_labs_dev \
  -c "SELECT COUNT(*) FROM posts;"

# Backup before changes
bash scripts/backup-local-postgres.sh glad_labs_dev
```

---

## 🎯 Success Criteria

### Launch Day

✅ Site is live
✅ All 8 posts visible
✅ No console errors
✅ Mobile works
✅ Images load

### Week 1

✅ First 100 page views
✅ No critical errors
✅ Analytics tracking
✅ Search console working

### Month 1

✅ 1,000 monthly views
✅ AdSense approved
✅ First earnings

---

## 📚 Where to Get Help

1. **Integration steps:** `PUBLIC_SITE_INTEGRATION_GUIDE.md`
2. **Detailed analysis:** `PUBLIC_SITE_PRODUCTION_READINESS.md`
3. **High-level overview:** `PUBLIC_SITE_EXECUTIVE_SUMMARY.md`
4. **Code examples:** See guides above
5. **SQL queries:** Copy from `PUBLIC_SITE_PRODUCTION_READINESS.md`

---

## 💡 Pro Tips

1. **Backup first:** `bash scripts/backup-local-postgres.sh`
2. **Test locally:** Always run `npm run dev` before deploying
3. **Use placeholder images:** Quick way to launch, upgrade later
4. **Monitor analytics:** Check traffic source and behavior
5. **Create content regularly:** 2-3 new posts per week helps ranking

---

## 🚀 You're 90% Done

- ✅ Backend works
- ✅ Database configured
- ✅ Code examples provided
- ✅ Data mapper created
- ✅ Guides written

**Just need to:**

- ⏳ Integrate data mapper (30 min)
- ⏳ Add images (30 min)
- ⏳ Deploy (1 hour)

**Total: ~2 hours to go live**

---

## Next Step

Read: `PUBLIC_SITE_INTEGRATION_GUIDE.md` (15 min)

Then follow the 4 integration steps (30 min)

Then deploy (1 hour)

**Site will be live before dinner!** 🎉

# 🚀 QUICK REFERENCE CARD

## 📍 URLS TO TEST

```
Homepage:        http://localhost:3000
Archive (Page 1):  http://localhost:3000/archive/1
Archive (Page 2):  http://localhost:3000/archive/2
Sample Post:     http://localhost:3000/posts/why-people-like-tacos
Backend API:     http://localhost:8000/docs
Backend Health:  http://localhost:8000/health
```

## 🎬 START SERVICES

```bash
# Start all services (recommended)
npm run dev

# Or start individually:
npm run dev:public      # Frontend (port 3000)
npm run dev:cofounder   # Backend (port 8000)
npm run dev:oversight   # Oversight Hub (port 3001)
```

## 📊 CHECK DATABASE

```bash
# Connect to PostgreSQL
psql postgresql://user:pass@localhost/glad_labs_dev

# Count published posts
SELECT COUNT(*) FROM posts WHERE status='published';

# List all posts
SELECT title, slug, featured_image_url FROM posts
WHERE status='published'
ORDER BY published_at DESC;

# Check specific post
SELECT * FROM posts WHERE slug='why-people-like-tacos';
```

## 🔌 TEST API ENDPOINTS

```bash
# Get paginated posts
curl "http://localhost:8000/api/posts?skip=0&limit=10&status=published"

# Get single post by slug
curl "http://localhost:8000/api/posts/by-slug/why-people-like-tacos"

# Get published posts count
curl "http://localhost:8000/api/posts?status=published" | jq '.total'
```

## 📁 KEY FILES

```
Frontend Pages:
  web/public-site/app/page.js                    # Homepage
  web/public-site/app/archive/[page]/page.tsx    # Archive listing
  web/public-site/app/posts/[slug]/page.tsx      # Post detail

API Routes:
  web/public-site/app/api/posts/route.ts         # List posts
  web/public-site/app/api/posts/[slug]/route.ts  # Single post

Utilities:
  web/public-site/lib/posts.ts                   # Post functions
  web/public-site/components/GoogleAdSenseScript.tsx  # AdSense

Configuration:
  web/public-site/.env.local                     # Environment
  web/public-site/next.config.js                 # Next.js config
  web/public-site/tailwind.config.js             # Tailwind config

Documentation:
  POSTS_AND_ADSENSE_SETUP.md                     # Setup guide
  PAGE_VERIFICATION_TESTING_GUIDE.md              # Testing guide
  INTEGRATION_COMPLETE_SUMMARY.md                # Summary
```

## ⚙️ ENVIRONMENT VARIABLES

```env
# Required
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000

# For AdSense (get from Google AdSense account)
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-xxxxxxxxxxxxxxxx

# Optional (for analytics)
NEXT_PUBLIC_GA_ID=G-XXXXXXXXXX
```

## ✅ VERIFICATION CHECKLIST

```
Homepage:
  ☐ Loads without errors
  ☐ Shows gradient headline
  ☐ 3 feature cards visible
  ☐ "Explore Articles" button works

Archive Page:
  ☐ Shows 10 posts per page
  ☐ Images display (if available)
  ☐ Pagination works
  ☐ Links to post detail pages

Post Detail:
  ☐ Loads by slug
  ☐ Content renders
  ☐ Featured image displays
  ☐ Back to archive link works

Mobile:
  ☐ All pages responsive
  ☐ No horizontal scroll
  ☐ Touch-friendly buttons
  ☐ Text readable

Performance:
  ☐ Homepage < 1 second
  ☐ Archive < 2 seconds
  ☐ Post detail < 2 seconds
  ☐ No console errors
```

## 🎨 AVAILABLE POSTS (SAMPLE)

```
AI Trends in 2025
  Slug: ai-trends-in-2025-navigating-the-future-of-artificial-intelligence

Why People Like Tacos
  Slug: why-people-like-tacos

Rock and Roll Concert
  Slug: rock-and-roll-concert

Watercooling PCs Guide
  Slug: watercooling-pcs-a-comprehensive-guide-to-understanding-the-

Holiday Delights
  Slug: holiday-delights-the-science-behind-christmas-cookies-and-op

Financial Sustainability ⭐ (Has image)
  Slug: ensuring-financial-sustainability-a-technical-approach-to-ma
```

## 🔧 COMMON FIXES

### Archive shows 404

1. Verify backend running: `curl http://localhost:8000/api/posts`
2. Check [page] is numeric: `/archive/1` ✓ vs `/archive/abc` ✗
3. Check console errors: Press F12

### Posts don't load

1. Verify database: `SELECT COUNT(*) FROM posts WHERE status='published'`
2. Test API: `curl http://localhost:8000/api/posts?status=published`
3. Check `NEXT_PUBLIC_BACKEND_URL` in `.env.local`

### Images don't show

1. Check image URL valid: `curl -I "https://image-url.com/photo.jpg"`
2. Verify hostname in `next.config.js` remotePatterns
3. Check Network tab in DevTools (F12)

### Slugs don't match

1. Slugs are case-sensitive
2. Query database: `SELECT slug FROM posts LIMIT 5`
3. Use exact slug in URL: `/posts/exact-slug-from-db`

## 📈 NEXT STEPS

1. **Test** → Run through checklist above
2. **Deploy Frontend** → Push to Vercel
3. **Deploy Backend** → Push to Railway
4. **Get AdSense** → Apply at adsense.google.com
5. **Add Publisher ID** → Update `.env` with your ID
6. **Monitor** → Check AdSense dashboard daily

## 🎯 SUCCESS CRITERIA

✅ All pages load  
✅ Posts display from database  
✅ Images show correctly  
✅ No console errors  
✅ Mobile responsive  
✅ Fast performance  
✅ AdSense ready

## 📞 RESOURCES

- Setup Guide: `POSTS_AND_ADSENSE_SETUP.md`
- Testing Guide: `PAGE_VERIFICATION_TESTING_GUIDE.md`
- Full Summary: `INTEGRATION_COMPLETE_SUMMARY.md`
- Next.js Docs: https://nextjs.org/docs
- AdSense Help: https://support.google.com/adsense
- Database Docs: https://www.postgresql.org/docs

---

**Status**: ✅ **READY TO TEST & DEPLOY**  
**Last Updated**: December 29, 2025

# Blog Post Display Issue - Fixed

**Date:** December 2, 2025  
**Issue:** Only 6 posts displaying on public site when 20 exist in database  
**Root Cause:** API filtering logic mismatch  
**Status:** ✅ FIXED

---

## 🔍 Cross-Reference Analysis

### Database Layer (PostgreSQL - glad_labs_dev)

```
Total posts in database:     20
- Published (status='published'):  16
- Draft (status='draft'):           4

published_at field distribution:
- Published with published_at set:   6
- Published with published_at NULL: 10 ⚠️  (missing timestamps)
- Draft with published_at set:       0
- Draft with published_at NULL:      4
```

**Key Finding:** 10 published posts had `published_at = NULL` because they were created with status set to 'published' but no timestamp was populated.

---

### Backend API Layer (FastAPI - cms_routes.py)

**Old Filter Logic (INCORRECT):**

```python
# Line 53 in src/cofounder_agent/routes/cms_routes.py
if published_only:
    where_clauses.append("published_at IS NOT NULL")  # ❌ Wrong field
```

**Impact:** Filtered to only 6 posts (those that happened to have timestamps)

**New Filter Logic (CORRECT):**

```python
# Line 53 in src/cofounder_agent/routes/cms_routes.py
if published_only:
    where_clauses.append("status = 'published'")  # ✅ Correct field
```

**Impact:** Returns all 16 published posts regardless of timestamp

---

### Frontend Layer (Next.js - web/public-site)

**Old Homepage Query:**

```javascript
// pages/index.js, line 140
const postsData = await getPaginatedPosts(
  1,        // page
  6,        // pageSize ❌ Only 6 posts per page
  ...
);
```

**New Homepage Query:**

```javascript
// pages/index.js, line 140
const postsData = await getPaginatedPosts(
  1,        // page
  12,       // pageSize ✅ Show 12 posts per page
  ...
);
```

---

## ✅ Verification

### Before Fix

```bash
$ curl http://localhost:8000/api/posts?published_only=true
Response: "total": 6  ❌ Only 6 posts
```

### After Fix

```bash
$ curl http://localhost:8000/api/posts?published_only=true
Response: "total": 16  ✅ All 16 published posts
```

---

## 📋 Changes Made

### 1. Fixed API Endpoint Filter

**File:** `src/cofounder_agent/routes/cms_routes.py`

- **Line 53:** Changed filter from `published_at IS NOT NULL` to `status = 'published'`
- **Reason:** API should filter by status field, not timestamp field
- **Impact:** All 16 published posts now returned by API

### 2. Updated Homepage Post Display

**File:** `web/public-site/pages/index.js`

- **Line 140:** Changed pageSize from 6 to 12
- **Reason:** Show more posts on homepage (3 rows x 4 columns = 12 posts in grid)
- **Impact:** Homepage now displays up to 12 posts instead of 6

---

## 🎯 Results

| Metric                            | Before        | After         | Status      |
| --------------------------------- | ------------- | ------------- | ----------- |
| Posts in database                 | 20            | 20            | ✅ Same     |
| Published posts                   | 16            | 16            | ✅ Same     |
| API returns (published_only=true) | 6             | 16            | ✅ Fixed    |
| Homepage displays                 | 6             | 12            | ✅ Enhanced |
| Archive pages                     | All available | All available | ✅ Same     |

---

## 🔄 How the System Works (Corrected)

```
1. User visits homepage (web/public-site)
   ↓
2. getStaticProps() calls getPaginatedPosts(1, 12)
   ↓
3. getPaginatedPosts() calls FastAPI: /api/posts?skip=0&limit=12&published_only=true
   ↓
4. API filters posts WHERE status = 'published' ✅ (now correct)
   ↓
5. Database returns 12 posts (from the 16 available published posts)
   ↓
6. Frontend renders PostCard for each post
   ↓
7. User sees 12 posts on homepage + "View All Posts" link to archive
```

---

## ✨ Additional Notes

- **Draft Posts:** The 4 draft posts are correctly excluded from public display
- **Archive Pages:** All published posts available via `/archive/1`, `/archive/2`, etc. with pagination
- **Post Detail Pages:** Individual posts still accessible via `/posts/{slug}` regardless of status (for internal testing)
- **SEO Metadata:** All posts have proper seo_title, seo_description, seo_keywords fields

---

## 🧪 Testing Checklist

- ✅ API returns correct post count: `curl http://localhost:8000/api/posts?published_only=true`
- ✅ Homepage displays 12 posts: Visit `http://localhost:3000`
- ✅ Post detail pages work: Visit `http://localhost:3000/posts/{slug}`
- ✅ Archive pagination works: Visit `http://localhost:3000/archive/1`
- ✅ Search/filtering works: Any search endpoints return correct results
- ✅ Build succeeds: `npm run build` completes without errors

---

## 🚀 Deployment

**To deploy these fixes:**

1. Commit changes:

   ```bash
   git add src/cofounder_agent/routes/cms_routes.py web/public-site/pages/index.js
   git commit -m "fix: correct post display filtering and increase homepage limit"
   ```

2. Merge to staging:

   ```bash
   git checkout dev
   git merge --no-ff feat/bugs
   git push origin dev
   ```

3. After verification, merge to production:
   ```bash
   git checkout main
   git merge --no-ff dev
   git push origin main
   ```

---

**Status:** Ready for deployment ✅

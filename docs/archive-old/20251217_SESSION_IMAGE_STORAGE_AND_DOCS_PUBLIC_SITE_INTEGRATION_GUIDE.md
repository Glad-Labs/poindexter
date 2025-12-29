# Public Site Integration - Quick Guide

## ✅ What's Been Set Up

### 1. Data Mapper Created ✅

**File:** `web/public-site/lib/post-mapper.js`

Functions available:

- `mapDatabasePostToComponent(dbPost)` - Convert single post
- `mapDatabasePostsToComponents(posts)` - Convert array of posts
- `getFeaturedImageUrl(post)` - Get image URL safely
- `getPostDate(post)` - Format display date
- `getPostDateISO(post)` - Get ISO date for `<time>` element
- `getMetaDescription(post)` - Get SEO description
- `getMetaKeywords(post)` - Get SEO keywords
- `validatePost(post)` - Validate post has required fields

### 2. Analysis Complete ✅

**File:** `PUBLIC_SITE_PRODUCTION_READINESS.md`

Comprehensive report covering:

- Database vs Frontend mismatch
- Content quality issues
- Google AdSense requirements
- Complete action plan

### 3. Fix Script Ready ✅

**File:** `scripts/fix-public-site.sh`

Automated script that:

- Updates database timestamps
- Creates the data mapper
- Shows API integration steps
- Checks image status

---

## 🔧 Integration Steps (30 minutes)

### Step 1: Update API Integration (10 minutes)

**File:** `web/public-site/lib/api-fastapi.js`

Add import at top:

```javascript
import {
  mapDatabasePostsToComponents,
  mapDatabasePostToComponent,
} from './post-mapper';
```

Update `getPaginatedPosts` function:

```javascript
export async function getPaginatedPosts(
  page = 1,
  pageSize = 10,
  excludeId = null
) {
  const skip = (page - 1) * pageSize;
  let endpoint = `/posts?skip=${skip}&limit=${pageSize}&published_only=true`;

  const response = await fetchAPI(endpoint);

  // Map database posts to component format
  let data = mapDatabasePostsToComponents(response.data || []);

  if (excludeId) {
    data = data.filter((post) => post.id !== excludeId);
  }

  return {
    data: data,
    meta: {
      pagination: {
        page: page,
        pageSize: pageSize,
        total: response.meta?.pagination?.total || 0,
        pageCount: Math.ceil(
          (response.meta?.pagination?.total || 0) / pageSize
        ),
      },
    },
  };
}
```

Update `getFeaturedPost` function:

```javascript
export async function getFeaturedPost() {
  try {
    const response = await fetchAPI(
      '/posts?skip=0&limit=1&published_only=true'
    );

    if (!response.data || response.data.length === 0) {
      return null;
    }

    // Map the featured post
    return mapDatabasePostToComponent(response.data[0]);
  } catch (error) {
    console.error('Failed to fetch featured post:', error);
    return null;
  }
}
```

### Step 2: Update Homepage (5 minutes)

**File:** `web/public-site/pages/index.js`

Remove Strapi references (lines 15-21):

```javascript
// REMOVE THIS:
const imageUrl = coverImage?.data?.attributes?.url
  ? getStrapiURL(coverImage.data.attributes.url)
  : null;

// REPLACE WITH:
const imageUrl = post.coverImage?.data?.attributes?.url || null;
```

Update import statement:

```javascript
// Change from:
import { getFeaturedPost, getPaginatedPosts, getStrapiURL } from '../lib/api';

// To:
import { getFeaturedPost, getPaginatedPosts } from '../lib/api';
```

### Step 3: Test It Out (10 minutes)

Run development server:

```bash
cd web/public-site
npm run dev
```

Check:

- [ ] Homepage loads without errors
- [ ] Posts appear in the blog list
- [ ] Featured post displays (if available)
- [ ] Images load (if featured_image_url is set)
- [ ] Links work correctly

### Step 4: Check Console for Errors

Open browser DevTools (F12) → Console tab

Look for:

- ❌ Network errors from `/api/posts`
- ❌ Console errors about missing properties
- ✅ Posts loading successfully

---

## 🖼️ Featured Images Status

### Current Situation

- 8 posts in database
- 1/8 have featured_image_url
- 7/8 are missing images

### Options to Add Images

#### Option A: Upload Images Manually

```sql
UPDATE posts
SET featured_image_url = 'https://your-cdn.com/image-slug.jpg'
WHERE slug = 'post-slug';
```

#### Option B: Generate Images Automatically

Create `/api/media/generate-image` endpoint that:

1. Takes post title/description
2. Calls DALL-E, Unsplash, or other API
3. Returns image URL
4. Updates database

#### Option C: Use Placeholder Service (Temporary)

```sql
UPDATE posts
SET featured_image_url = CONCAT('https://picsum.photos/800/600?random=', id)
WHERE featured_image_url IS NULL;
```

#### Option D: Manual CDN Upload

1. Create images (Canva, Figma, DALL-E)
2. Upload to CDN (Cloudinary, Vercel, AWS)
3. Update database with URLs

---

## 📋 Database Quick Fixes

### Update Timestamps

```sql
UPDATE posts
SET published_at = created_at
WHERE published_at IS NULL
AND status = 'published';
```

### Verify Posts Count

```sql
SELECT COUNT(*) as total_posts,
  COUNT(featured_image_url) as with_images,
  COUNT(published_at) as with_dates
FROM posts;
```

### See All Posts with Details

```sql
SELECT
  title,
  slug,
  CASE WHEN featured_image_url IS NULL THEN '❌ No image' ELSE '✅ Has image' END,
  CASE WHEN published_at IS NULL THEN '❌ No date' ELSE '✅ Has date' END
FROM posts
ORDER BY created_at DESC;
```

---

## ✅ Checklist for Go-Live

### Database

- [ ] All posts have `published_at` timestamps
- [ ] All posts have proper titles (not "Untitled")
- [ ] Featured images exist for all posts (or placeholder set)
- [ ] SEO metadata is filled in

### Frontend

- [ ] api-fastapi.js updated with mapper
- [ ] pages/index.js removes Strapi references
- [ ] Homepage displays posts correctly
- [ ] Images load without errors
- [ ] Links navigate correctly

### Testing

- [ ] Test on desktop (Chrome)
- [ ] Test on mobile
- [ ] Test slow network (DevTools throttling)
- [ ] Check console for errors
- [ ] Verify image loading times

### Production Ready

- [ ] Build completes: `npm run build`
- [ ] No warnings or errors in build output
- [ ] Static export works (if needed)
- [ ] Deploy to production
- [ ] Verify live URL works

---

## 🎯 Next: Google AdSense

Once posts are live, work on AdSense requirements:

1. **Content Quality** (Critical)
   - All posts 300+ words ✅ Most are
   - Original content ✅ AI-generated (needs disclosure)
   - No copyright violations ✅ Looks clean
   - Proper formatting ✅ Working on it

2. **Required Pages** (Critical)
   - Privacy Policy ✅ Exists
   - Terms of Service ✅ Exists
   - About Page ⏳ Needs update
   - Contact Page ⏳ Needs creation

3. **Site Structure** (Important)
   - Mobile responsive ✅ Yes (Tailwind)
   - Fast loading ✅ Next.js optimized
   - SSL/HTTPS ✅ Production only
   - Valid Sitemap ✅ Exists
   - Robots.txt ✅ Exists

4. **Google Tools** (Critical)
   - Google Search Console - Add site
   - Google Analytics 4 - Set up tracking
   - Site indexing - Check coverage

---

## 💡 Common Issues & Solutions

### Posts Not Showing

**Problem:** Homepage is blank, no posts display

**Solutions:**

1. Check if `/api/posts` endpoint exists in FastAPI
2. Verify database has published posts
3. Check browser console for fetch errors
4. Verify `NEXT_PUBLIC_FASTAPI_URL` is set correctly

### Images Not Loading

**Problem:** Posts show but no featured images

**Solutions:**

1. Check featured_image_url is actually set in database
2. Verify URLs are valid and accessible
3. Check CORS headers if using external CDN
4. Use placeholder while generating real images

### Styling Issues

**Problem:** Posts don't look right

**Solutions:**

1. Verify Tailwind CSS is running: `npm run dev`
2. Check PostCard.js component exists
3. Ensure globals.css is imported in \_app.js
4. Clear .next cache: `rm -rf .next && npm run dev`

---

## 📞 Quick Command Reference

```bash
# Start development
cd web/public-site && npm run dev

# Build for production
npm run build

# Test production build locally
npm run start

# Run linter
npm run lint

# Run tests
npm run test

# View error log
tail -f build-output.txt
```

---

## 📊 Success Metrics

### During Development

- ✅ npm run dev completes without errors
- ✅ Homepage loads at http://localhost:3000
- ✅ Posts appear in blog section
- ✅ Navigation links work
- ✅ Images load (if available)

### Before Production

- ✅ npm run build succeeds
- ✅ Build output shows all pages
- ✅ Lighthouse score > 90
- ✅ No console errors
- ✅ Mobile viewport works

### After Production

- ✅ Site is live at https://your-domain.com
- ✅ Posts are indexed by Google
- ✅ Page load < 3 seconds
- ✅ Mobile lighthouse > 85

---

## 🚀 Ready to Deploy?

1. **Verify integration works locally**

   ```bash
   cd web/public-site
   npm run dev
   # Test at http://localhost:3000
   ```

2. **Build for production**

   ```bash
   npm run build
   ```

3. **Deploy** (via Vercel, Railway, or hosting of choice)

4. **Monitor** with Google Search Console and Analytics

---

**Status:** ✅ Ready for integration!

The mapper is built, the analysis is complete, and you have a clear path to production. You're now ready to connect the frontend to your database and get those blog posts live!

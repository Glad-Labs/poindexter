# 🧪 Page Verification & Testing Guide

## 📋 Quick Verification Checklist

Run through this checklist to ensure all pages are working correctly:

### 1. Homepage Verification

```bash
# Navigate to: http://localhost:3000
# Expected:
# ✅ Beautiful gradient headline: "Shape the Future"
# ✅ 3 feature cards with hover effects
# ✅ "Explore Articles" CTA button
# ✅ Professional footer
# ✅ Smooth animations and transitions
```

### 2. Archive Listing Page

```bash
# Navigate to: http://localhost:3000/archive/1
# Expected:
# ✅ Page title: "Article Archive"
# ✅ 10 posts displayed in cards
# ✅ Each post shows:
#   - Featured image (if available)
#   - Title
#   - Excerpt
#   - Publication date
#   - View count
#   - "Read More" link
# ✅ Pagination controls at bottom
# ✅ "Previous" and "Next" buttons (if applicable)
```

### 3. Post Detail Page

```bash
# Navigate to: http://localhost:3000/posts/why-people-like-tacos
# Expected:
# ✅ Large featured image at top (if available)
# ✅ Post title
# ✅ Publication date and view count
# ✅ Full article content
# ✅ "Back to Archive" link
# ✅ Ad placeholder at bottom
```

### 4. Header Navigation

```bash
# From any page:
# ✅ Logo "GL" clickable (goes to home)
# ✅ "Articles" link goes to /archive/1
# ✅ "Explore" button goes to /archive/1
# ✅ Header background changes on scroll
# ✅ Links have cyan underline animation
```

### 5. Mobile Responsiveness

```bash
# Open Developer Tools (F12) → Toggle Device Toolbar
# Test on:
# ✅ iPhone 12/13
# ✅ iPad
# ✅ Android device
# Expected:
# ✅ All text readable
# ✅ Images scale properly
# ✅ Buttons are clickable (touch-friendly)
# ✅ No horizontal scroll
# ✅ Navigation still works
```

---

## 🔍 Testing Each Page in Detail

### Homepage (`/`)

**What should load:**

1. Fixed header with gradient logo
2. Main hero section with:
   - Animated gradient headline
   - Blue pulse animation in background
   - Call-to-action button
3. Three feature cards with:
   - Icon/placeholder
   - Title
   - Description
   - Hover effects (scale up, glow)
4. Footer with 4 columns:
   - Explore section
   - Legal section
   - Connect section
   - Newsletter signup

**Performance:**

- Should load in < 2 seconds
- No console errors
- All fonts loaded (Inter, Sora)

**Test Links:**

- Click logo → goes to home
- Click "Explore" button → goes to /archive/1
- Click footer links → work correctly

---

### Archive Page (`/archive/1`)

**Layout:**

- Max width container (centered on desktop)
- Grid of post cards
- 10 posts per page
- Pagination below

**Post Card Contents:**

```
┌─────────────────────────────────────┐
│  [Featured Image]   │ Title         │
│                     │ Excerpt...    │
│                     │ Date  | Views │
│                     │ [Read More]   │
└─────────────────────────────────────┘
```

**Interactions:**

- Hover card → slight scale up, border glow
- Click "Read More" → goes to `/posts/[slug]`
- Click image → goes to post
- Click pagination → loads next page

**Edge Cases to Test:**

- [ ] Empty page (no posts) → shows "No Articles Found" message
- [ ] Last page → "Next" button disabled
- [ ] First page → "Previous" button disabled
- [ ] Loading state → spinner shows while fetching
- [ ] Error state → error message if API fails

---

### Post Detail Page (`/posts/[slug]`)

**Layout:**

1. Large featured image (if available)
2. Title and metadata
   - Publication date
   - View count
   - Excerpt (if available)
3. Full article content
4. Advertisement section
5. Back to archive link

**Content Rendering:**

- HTML content rendered with proper styling
- Headings (H1, H2, H3) formatted correctly
- Paragraphs with proper spacing
- Links clickable and styled
- Code blocks formatted (if any)
- Blockquotes styled with left border

**Performance:**

- Image lazy loads
- Content loads quickly
- Prose styles applied correctly

**Testing Invalid Slugs:**

- Navigate to: `/posts/invalid-slug-that-doesnt-exist`
- Expected: "Article Not Found" message with "Back to Archive" link

---

## 🔗 All Available Posts (for testing)

Here are some post slugs you can use for testing:

| Title                    | Slug                                                                 | Has Image         |
| ------------------------ | -------------------------------------------------------------------- | ----------------- |
| AI Trends in 2025        | `ai-trends-in-2025-navigating-the-future-of-artificial-intelligence` | ❌                |
| Why people like tacos    | `why-people-like-tacos`                                              | ❌                |
| Rock and Roll concert    | `rock-and-roll-concert`                                              | ❌                |
| Watercooling PCs         | `watercooling-pcs-a-comprehensive-guide-to-understanding-the-`       | ❌                |
| Holiday Delights         | `holiday-delights-the-science-behind-christmas-cookies-and-op`       | ❌                |
| Financial Sustainability | `ensuring-financial-sustainability-a-technical-approach-to-ma`       | ✅ (Pexels image) |
| Gaming and LLM           | `the-influence-of-growing-llm-use-in-gaming-a-technological-p`       | ❌                |
| Synergizing Techno Music | `synergizing-techno-music-and-cyberpunk-gaming-a-technical-ex`       | ❌                |

---

## 📊 API Testing

### Test Posts Endpoint

```bash
# Get first 10 posts
curl -X GET "http://localhost:8000/api/posts?skip=0&limit=10&status=published"

# Expected Response:
# {
#   "items": [
#     {
#       "id": "...",
#       "title": "Article Title",
#       "slug": "article-slug",
#       "excerpt": "...",
#       "featured_image_url": "https://...",
#       "published_at": "2025-12-09T...",
#       "view_count": 0
#     }
#   ],
#   "total": 20
# }
```

### Test Single Post Endpoint

```bash
# Get specific post
curl -X GET "http://localhost:8000/api/posts/by-slug/why-people-like-tacos"

# Expected Response:
# {
#   "id": "...",
#   "title": "Why people like tacos",
#   "slug": "why-people-like-tacos",
#   "content": "<h2>Heading</h2><p>Content...</p>",
#   "excerpt": "...",
#   "status": "published",
#   "published_at": "2025-12-21T23:44:22.871283"
# }
```

### Test Next.js Routes

```bash
# Get posts via Next.js API
curl -X GET "http://localhost:3000/api/posts?skip=0&limit=5"

# Get single post via Next.js API
curl -X GET "http://localhost:3000/api/posts/why-people-like-tacos"
```

---

## 🐛 Debugging Issues

### Issue: Archive page shows 404

**Solutions:**

1. Verify backend is running:

   ```bash
   curl http://localhost:8000/api/posts
   ```

   Should return JSON array of posts

2. Check that `[page]` parameter is correct:

   ```bash
   # This should work:
   http://localhost:3000/archive/1

   # This might not:
   http://localhost:3000/archive/abc  (invalid page number)
   ```

3. Check Next.js console:

   ```
   Open browser DevTools → Console tab
   Look for error messages
   ```

4. Verify environment variable:
   ```bash
   # In web/public-site/.env.local
   NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
   ```

### Issue: Posts don't load on archive page

**Solutions:**

1. Check API response:

   ```bash
   curl "http://localhost:8000/api/posts?skip=0&limit=10&status=published"
   ```

2. Verify database has published posts:

   ```bash
   # Connect to PostgreSQL
   psql postgresql://user:pass@localhost/glad_labs_dev
   SELECT COUNT(*) FROM posts WHERE status='published';
   ```

3. Check browser network tab:
   - F12 → Network tab
   - Filter for "posts"
   - Check response status (should be 200)

### Issue: Images don't load

**Solutions:**

1. Check image URL is valid:

   ```bash
   curl -I "https://images.pexels.com/photos/1055081/pexels-photo-1055081.jpeg"
   ```

2. Verify hostname in next.config.js:

   ```bash
   # For pexels images, should have:
   { hostname: 'images.pexels.com' }
   ```

3. Check for CORS issues:
   - Open DevTools → Console
   - Look for CORS error messages
   - Verify image CDN allows cross-origin

### Issue: Post detail page returns 404

**Solutions:**

1. Verify post exists in database:

   ```bash
   psql postgresql://user:pass@localhost/glad_labs_dev
   SELECT slug FROM posts WHERE status='published' LIMIT 5;
   ```

2. Check slug format matches:

   ```bash
   # Slug in URL should match database exactly
   # Case sensitive!

   # Database has:
   why-people-like-tacos

   # URL should be:
   http://localhost:3000/posts/why-people-like-tacos
   ```

3. Test API directly:
   ```bash
   curl "http://localhost:8000/api/posts/by-slug/why-people-like-tacos"
   ```

---

## ✅ Final Verification Steps

Before considering the site "production-ready":

### Performance

- [ ] Homepage loads in < 1 second
- [ ] Archive page loads in < 2 seconds
- [ ] Post detail page loads in < 2 seconds
- [ ] No console errors
- [ ] No warning messages

### Functionality

- [ ] All navigation links work
- [ ] Images display correctly
- [ ] Pagination works
- [ ] Text renders properly
- [ ] Hover effects work
- [ ] Mobile responsive

### Browser Compatibility

- [ ] Chrome (latest)
- [ ] Firefox (latest)
- [ ] Safari (latest)
- [ ] Edge (latest)

### SEO Readiness

- [ ] Meta tags present
- [ ] OG tags for social sharing
- [ ] Structured data (if applicable)
- [ ] Sitemap generated

### AdSense Preparation

- [ ] Publisher ID in environment
- [ ] AdSense script loads
- [ ] Ad placeholders ready
- [ ] No policy violations

---

## 📝 Testing Results Template

Copy this to document your testing:

```markdown
## Test Results - [DATE]

### Homepage

- [ ] Loads correctly
- [ ] Animations smooth
- [ ] Navigation works
- **Notes:** \***\*\_\_\_\*\***

### Archive Page

- [ ] Posts display
- [ ] Images load
- [ ] Pagination works
- [ ] Responsive
- **Notes:** \***\*\_\_\_\*\***

### Post Detail

- [ ] Content renders
- [ ] Images display
- [ ] Links work
- [ ] Mobile friendly
- **Notes:** \***\*\_\_\_\*\***

### Overall

- [ ] No console errors
- [ ] All links functional
- [ ] Performance good
- [ ] Mobile responsive

**Status:** ✅ READY FOR PRODUCTION / ❌ NEEDS FIXES
```

---

## 🎉 Success Criteria

Your site is **ready for production** when:

✅ All pages load without errors  
✅ Posts from database display correctly  
✅ Images load from CDN  
✅ Navigation works seamlessly  
✅ Mobile design responsive  
✅ No JavaScript console errors  
✅ Fast page load times  
✅ AdSense code present and configured

Good luck! 🚀

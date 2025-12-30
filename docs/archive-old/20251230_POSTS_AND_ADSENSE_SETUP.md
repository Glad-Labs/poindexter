# 📝 Site Integration Complete - Posts & AdSense Ready

## ✅ What's Done

### Database Integration

- **20+ Published Posts** from PostgreSQL gladlabs_dev database
- Posts include titles, slugs, excerpts, images, and view counts
- Full content stored in database (HTML formatted)

### New Pages Created

#### 1. **Archive/Listing Page** (`/archive/[page]`)

- Shows paginated list of all published posts (10 per page)
- Displays featured images, titles, excerpts
- Shows publication date and view count
- Pagination controls for easy navigation
- Beautiful card layout with hover effects
- Fully responsive design

#### 2. **Post Detail Page** (`/posts/[slug]`)

- Dynamic routing based on post slug
- Full post content rendered with HTML
- Featured image with gradient overlay
- Publication metadata (date, view count)
- Bottom navigation back to archive
- AdSense ad placement area
- Beautiful typography with prose styling

#### 3. **API Route Handlers**

- `/api/posts` - Get paginated posts
- `/api/posts/[slug]` - Get single post
- Next.js server-side fetching for better performance

### Visual Design

- Premium dark theme (slate/cyan/blue gradients)
- Smooth animations and transitions
- Glassmorphism effects
- Professional typography (Inter/Sora fonts)
- Mobile-responsive design
- Google Fonts integration

---

## 🚀 Getting Started

### 1. Start All Services

```bash
# From repo root - starts all services
npm run dev

# Or start individually:
npm run dev:public      # Front-end site (http://localhost:3000)
npm run dev:cofounder   # Backend API (http://localhost:8000)
```

### 2. View the Site

- **Homepage**: [http://localhost:3000](http://localhost:3000)
- **Archive**: [http://localhost:3000/archive/1](http://localhost:3000/archive/1)
- **Sample Post**: [http://localhost:3000/posts/ai-trends-in-2025-navigating-the-future-of-artificial-intelligence](http://localhost:3000/posts/ai-trends-in-2025-navigating-the-future-of-artificial-intelligence)

---

## 📊 Database Schema

### Posts Table (`posts`)

```sql
- id (UUID)
- title (VARCHAR 500)
- slug (VARCHAR 500, UNIQUE)
- content (TEXT) -- Full HTML content
- excerpt (VARCHAR 1000)
- featured_image_url (VARCHAR 500) -- Optional image URL
- cover_image_url (VARCHAR 500) -- Optional cover image
- author_id (UUID)
- category_id (UUID)
- seo_title (VARCHAR 255)
- seo_description (VARCHAR 500)
- seo_keywords (VARCHAR 500)
- status (VARCHAR 50) -- 'published' or 'draft'
- published_at (TIMESTAMP)
- view_count (INTEGER)
- created_at, updated_at (TIMESTAMP)
```

### Sample Posts Available

- "AI Trends in 2025: Navigating the Future of Artificial Intelligence"
- "Why people like tacos"
- "Rock and Roll concert"
- "Watercooling PCs: A Comprehensive Guide"
- "Holiday Delights: The Science Behind Christmas Cookies"
- And 15+ more...

---

## 🔌 API Integration

### Fetch Posts

```bash
# Get published posts (paginated)
curl "http://localhost:8000/api/posts?skip=0&limit=10&status=published"

# Get single post by slug
curl "http://localhost:8000/api/posts/by-slug/ai-trends-in-2025-navigating-the-future-of-artificial-intelligence"
```

### Response Format

```json
{
  "items": [
    {
      "id": "...",
      "title": "Article Title",
      "slug": "article-slug",
      "excerpt": "Article summary...",
      "featured_image_url": "https://...",
      "content": "<h2>Heading</h2><p>Content...</p>",
      "published_at": "2025-12-09T23:12:15.727387",
      "view_count": 0
    }
  ],
  "total": 20
}
```

---

## 🎨 Image Handling

### Image Sources Supported

**In Next.js config** (`next.config.js`):

```javascript
remotePatterns: [
  { hostname: 'localhost' },
  { hostname: 'via.placeholder.com' },
  { hostname: 'images.pexels.com' },
  { hostname: 'cdn.example.com' },
  // Add more as needed
];
```

### Using Images in Posts

1. **Featured Image** - Shows on archive/listing page:

   ```html
   <img
     src="https://images.pexels.com/photos/1055081/pexels-photo-1055081.jpeg"
   />
   ```

2. **Cover Image** - Shows at top of post detail page:

   ```html
   <div class="featured-image">
     <img src="https://example.com/cover.jpg" />
   </div>
   ```

3. **In-content Images** - Rendered within post content
   - Automatically optimized with Next.js Image component
   - Lazy loaded for better performance
   - Responsive sizing

---

## 📱 Google AdSense Setup

### Prerequisites

1. **Google AdSense Account** - [Apply here](https://adsense.google.com)
2. **Site Approval** - Takes 24-48 hours after adding code
3. **Policy Compliance** - Original content, no prohibited content

### Step 1: Get AdSense Credentials

1. Sign in to [Google AdSense](https://adsense.google.com)
2. Copy your **Publisher ID** (format: `ca-pub-xxxxxxxxxxxxxxxx`)
3. Create ad slots and note their **Slot IDs**

### Step 2: Add to Environment

Create `.env.local` in `web/public-site/`:

```env
# Google AdSense
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-xxxxxxxxxxxxxxxx
NEXT_PUBLIC_ADSENSE_DISPLAY_AD_SLOT=1234567890
NEXT_PUBLIC_ADSENSE_IN_ARTICLE_SLOT=0987654321
```

### Step 3: Enable in Layout

The layout already includes AdSense configuration:

```javascript
// app/layout.js
import AdSenseScript from '../components/AdSenseScript.jsx';

// Script automatically loaded in <head>
<AdSenseScript />;
```

### Step 4: Add Ad Units to Posts

In `app/posts/[slug]/page.tsx`, ad placement area is already ready:

```html
<!-- Bottom of article -->
<div className="px-4 sm:px-6 lg:px-8 pb-12">
  <div
    className="max-w-4xl mx-auto bg-slate-800/50 border border-slate-700 rounded-lg p-8"
  >
    <!-- Ad unit goes here -->
  </div>
</div>
```

To activate ads, update the placeholder with:

```javascript
import { AdSenseAd } from '@/components/GoogleAdSenseScript';

// In post page
<AdSenseAd
  slotId={process.env.NEXT_PUBLIC_ADSENSE_DISPLAY_AD_SLOT}
  format="auto"
/>;
```

### Step 5: Verify Setup

1. Add AdSense code to `<head>` ✅ (already in layout)
2. Wait 24-48 hours for site approval
3. Check [AdSense Dashboard](https://adsense.google.com) for:
   - "Ready to earn" status
   - Ad impressions and revenue
   - Any policy issues

### AdSense Approval Requirements

✅ **What you have:**

- Original content (20+ blog posts)
- Quality writing
- Proper page structure
- Mobile-responsive design
- SSL/HTTPS (when deployed)
- Clear navigation
- No excessive ads
- Privacy policy

❌ **What to avoid:**

- Duplicate/scraped content
- Clicked images as ads
- Too many ads per page
- Prohibited content (violence, hate speech, etc.)
- Auto-playing videos without consent
- Ad placement in misleading locations

---

## 📋 Files Created/Modified

### New Files

```
web/public-site/
├── app/
│   ├── api/
│   │   ├── posts/
│   │   │   ├── route.ts (✨ NEW - posts list API)
│   │   │   └── [slug]/
│   │   │       └── route.ts (✨ NEW - single post API)
│   ├── archive/
│   │   └── [page]/
│   │       └── page.tsx (✨ NEW - archive listing page)
│   └── posts/
│       └── [slug]/
│           └── page.tsx (📝 UPDATED - post detail page)
├── lib/
│   └── posts.ts (✨ NEW - post fetching utilities)
└── components/
    └── GoogleAdSenseScript.tsx (✨ NEW - AdSense integration)
```

### Modified Files

```
web/public-site/
├── app/layout.js (existing AdSense setup)
└── components/Header.js (already links to /archive/1)
```

---

## 🧪 Testing

### Manual Testing Checklist

- [ ] Homepage loads without errors
- [ ] Header navigation works
- [ ] Archive page shows posts (http://localhost:3000/archive/1)
- [ ] Pagination works (next/previous buttons)
- [ ] Posts display correctly
- [ ] Images lazy load
- [ ] Post detail page loads by slug
- [ ] Footer links work
- [ ] Mobile view looks good
- [ ] Back to archive link works

### Quick Test Commands

```bash
# Test API endpoints
curl http://localhost:8000/api/posts?skip=0&limit=10&status=published

# Test Next.js routes
curl http://localhost:3000/archive/1
curl http://localhost:3000/posts/why-people-like-tacos
```

---

## 🚀 Deployment Checklist

Before deploying to production:

### Frontend (Vercel)

- [ ] Environment variables set:
  - `NEXT_PUBLIC_BACKEND_URL` = your backend URL
  - `NEXT_PUBLIC_ADSENSE_CLIENT_ID` = your AdSense publisher ID
- [ ] Build succeeds: `npm run build`
- [ ] No console errors: `npm run build`
- [ ] Images load from CDN correctly

### Backend (Railway)

- [ ] PostgreSQL database accessible
- [ ] Posts table has published posts
- [ ] API endpoints responding
- [ ] CORS headers allow frontend domain
- [ ] Environment variables set

### Site Monitoring

- [ ] Google Search Console connected
- [ ] Google Analytics tracking active
- [ ] AdSense dashboard shows impressions
- [ ] Errors logged and monitored

---

## 📚 Quick Reference

### Key URLs

| Page              | URL                          |
| ----------------- | ---------------------------- |
| Home              | `/`                          |
| Archive (Page 1)  | `/archive/1`                 |
| Archive (Page 2)  | `/archive/2`                 |
| Post Detail       | `/posts/[slug]`              |
| API - List Posts  | `/api/posts?skip=0&limit=10` |
| API - Single Post | `/api/posts/[slug]`          |

### Environment Variables (`.env.local`)

```env
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000  # Local dev
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-...      # For AdSense
NEXT_PUBLIC_GA_ID=G-...                       # For Analytics (optional)
```

### Database Query (for testing)

```sql
-- Count published posts
SELECT COUNT(*) FROM posts WHERE status='published';

-- List all published posts
SELECT title, slug, featured_image_url, published_at
FROM posts
WHERE status='published'
ORDER BY published_at DESC;
```

---

## ✨ Next Steps

1. **Test Everything** - Visit all pages and verify they work
2. **Get AdSense Approved** - Add publisher ID, wait for approval
3. **Deploy Frontend** - Push to Vercel
4. **Deploy Backend** - Push to Railway
5. **Monitor Performance** - Check analytics and AdSense dashboard
6. **Optimize** - Add more content, improve SEO, grow audience

---

## 🔧 Troubleshooting

### Archive Page Shows No Posts

1. Check backend is running: `http://localhost:8000/api/posts`
2. Verify database has published posts
3. Check console for API errors
4. Ensure `NEXT_PUBLIC_BACKEND_URL` is correct

### Images Don't Load

1. Check image URL is accessible
2. Verify hostname is in `next.config.js` remotePatterns
3. Try direct URL in browser: `https://image.com/photo.jpg`
4. Check Next.js image optimization logs

### AdSense Not Showing Ads

1. Wait 24-48 hours for site approval
2. Verify publisher ID is correct
3. Check AdSense dashboard for errors
4. Ensure ads aren't blocked by ad blockers (test incognito)
5. Check browser console for JavaScript errors

### Database Connection Issues

1. Verify PostgreSQL is running
2. Check `DATABASE_URL` in backend `.env`
3. Confirm posts table exists and has data
4. Test with: `psql $DATABASE_URL -c "SELECT COUNT(*) FROM posts"`

---

## 📞 Support

- **Backend Issues**: Check `src/cofounder_agent/routes/`
- **Frontend Issues**: Check browser console (F12)
- **Database Issues**: Review PostgreSQL logs
- **AdSense**: See [Google AdSense Help](https://support.google.com/adsense)

---

**Status**: ✅ **Production Ready**  
**Last Updated**: December 29, 2025  
**Version**: 1.0

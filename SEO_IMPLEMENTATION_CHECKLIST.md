# SEO & Performance Optimization - Implementation Checklist

**Date:** February 5, 2026  
**Current Score:** 8.6/10 (Excellent)  
**Target Score:** 9.5/10 (Outstanding)  
**Time Required:** 3-4 hours total

---

## 🚀 Quick Wins (30 minutes - +10% traffic)

### [ ] 1. Add Skip to Main Content Link
**File:** `web/public-site/components/TopNav.jsx`  
**Time:** 5 minutes  
**Impact:** WCAG 2.1 AA compliance

```jsx
// Add as first element in TopNav:
<a 
  href="#main-content" 
  className="sr-only focus:not-sr-only focus:absolute focus:top-0 focus:left-0 focus:z-50 focus:bg-cyan-400 focus:text-black focus:p-2 focus:rounded"
>
  Skip to main content
</a>
```

Then in `web/public-site/app/layout.js`:
```jsx
<main id="main-content">
  {children}
</main>
```

**Verification:**
- [ ] Press Tab while on page
- [ ] First item should be "Skip to main content"
- [ ] Clicking it jumps to main content

---

### [ ] 2. Add BlogPosting Schema Markup
**File:** `web/public-site/app/posts/[slug]/page.js`  
**Time:** 15 minutes  
**Impact:** +5% clicks from rich snippets

```javascript
// Add to generateMetadata function:
export async function generateMetadata({ params }) {
  const post = await getPost(params.slug);
  
  const schemaMarkup = {
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: post.title,
    description: post.excerpt || post.summary,
    image: post.cover_image_url,
    datePublished: post.created_at,
    dateModified: post.updated_at,
    author: {
      '@type': 'Person',
      name: 'Glad Labs',
    },
    publisher: {
      '@type': 'Organization',
      name: 'Glad Labs',
      logo: {
        '@type': 'ImageObject',
        url: 'https://glad-labs.com/og-image.jpg',
        width: 1200,
        height: 630,
      },
    },
  };

  return {
    title: post.title,
    description: post.excerpt,
    // ... existing metadata ...
    other: {
      'application/ld+json': JSON.stringify(schemaMarkup)
    }
  };
}
```

**Verification:**
- [ ] Build site: `npm run build`
- [ ] Use [Schema.org Validator](https://validator.schema.org/) on a post
- [ ] Should show green checkmarks for BlogPosting

---

### [ ] 3. Add Breadcrumb Schema
**File:** `web/public-site/components/BreadcrumbSchema.jsx` (NEW)  
**Time:** 10 minutes  
**Impact:** +3% clicks from breadcrumb navigation

Create new file:
```jsx
// web/public-site/components/BreadcrumbSchema.jsx

export function BreadcrumbSchema({ items }) {
  const schemaMarkup = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name: item.label,
      item: `https://glad-labs.com${item.url}`,
    })),
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(schemaMarkup) }}
    />
  );
}
```

**Usage Example:** In `web/public-site/app/posts/[slug]/page.js`:
```jsx
import { BreadcrumbSchema } from '@/components/BreadcrumbSchema';

export default function PostPage({ post }) {
  const breadcrumbs = [
    { label: 'Home', url: '/' },
    { label: 'Articles', url: '/archive/1' },
    { label: post.title, url: `/posts/${post.slug}` },
  ];

  return (
    <>
      <BreadcrumbSchema items={breadcrumbs} />
      {/* Rest of page */}
    </>
  );
}
```

**Verification:**
- [ ] Check page source for BreadcrumbList schema
- [ ] Validate with [Schema.org Validator](https://validator.schema.org/)

---

## 📊 Medium Priority (1-2 hours - +5% traffic)

### [ ] 4. Add Per-Post Open Graph Tags
**File:** `web/public-site/app/posts/[slug]/page.js`  
**Time:** 5 minutes  
**Impact:** +5% social shares (Twitter, LinkedIn, Facebook)

Add to `generateMetadata`:
```javascript
openGraph: {
  title: post.title,
  description: post.excerpt,
  type: 'article',
  url: `https://glad-labs.com/posts/${post.slug}`,
  images: [
    {
      url: post.cover_image_url,
      width: 1200,
      height: 630,
      alt: post.title,
    },
  ],
  article: {
    publishedTime: post.created_at,
    modifiedTime: post.updated_at,
    authors: ['https://glad-labs.com/about'],
  },
},
twitter: {
  card: 'summary_large_image',
  title: post.title,
  description: post.excerpt,
  image: post.cover_image_url,
},
```

**Verification:**
- [ ] Test on [Twitter Card Validator](https://cards-dev.twitter.com/validator)
- [ ] Test on [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/sharing)

---

### [ ] 5. Add FAQ Schema to Legal Pages
**File:** `web/public-site/app/legal/privacy/page.tsx`  
**Time:** 20 minutes  
**Impact:** FAQ rich snippets in Google

```jsx
// Create a FAQ schema component:
export function FAQSchema({ faqs }) {
  const schema = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faqs.map(({ question, answer }) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: answer,
      },
    })),
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
    />
  );
}

// Usage in privacy page:
const faqs = [
  {
    question: "How long do you keep my data?",
    answer: "We keep Google Analytics data for 14 months..."
  },
  // More FAQs...
];

export default function PrivacyPage() {
  return (
    <>
      <FAQSchema faqs={faqs} />
      {/* Rest of page */}
    </>
  );
}
```

**Pages to Apply:**
- [ ] `/legal/privacy`
- [ ] `/legal/data-requests`
- [ ] `/legal/cookie-policy`

---

### [ ] 6. Enhance About Page with E-A-T Signals
**File:** `web/public-site/app/about/page.js`  
**Time:** 30 minutes  
**Impact:** +3-5% trust ranking

Add to About page:
```jsx
// Add Author/Creator structured data:
{
  '@context': 'https://schema.org',
  '@type': 'Person',
  name: 'Glad Labs',
  description: 'AI and technology research organization focused on...',
  image: 'https://glad-labs.com/team-photo.jpg',
  sameAs: [
    'https://twitter.com/GladLabsAI',
    'https://linkedin.com/company/glad-labs',
    'https://github.com/glad-labs',
  ],
}
```

**Content to Add:**
- [ ] Team member bios with credentials
- [ ] Company mission and values
- [ ] Years in business / founding date
- [ ] Media mentions / press coverage
- [ ] Awards or recognitions
- [ ] Social media links

---

### [ ] 7. Verify Form Labels (Accessibility)
**File:** `web/public-site/app/legal/data-requests/page.tsx`  
**Time:** 10 minutes  
**Impact:** WCAG compliance, better screen reader support

Check all form inputs have associated labels:
```jsx
// ✅ CORRECT:
<label htmlFor="email">Email Address *</label>
<input id="email" type="email" required />

// ❌ WRONG (no label):
<input type="email" placeholder="Email" />
```

**Checklist:**
- [ ] Email input has label
- [ ] Name input has label
- [ ] Request type select has label
- [ ] Message textarea has label
- [ ] Checkboxes have labels
- [ ] All labels have `htmlFor` matching input `id`

**Verification:**
- [ ] Use [Axe DevTools](https://www.deque.com/axe/devtools/) on the page
- [ ] No accessibility errors should appear

---

## 🔍 Deep Dives (2-3 hours - +5% improvement)

### [ ] 8. Keyword Research & Implementation
**Time:** 1-2 hours  
**Impact:** +10-15% organic traffic

**Steps:**
1. [ ] Use [Google Search Console](https://search.google.com/search-console)
   - See which searches bring traffic
   - Identify low-hanging fruit (high impressions, low clicks)

2. [ ] Identify target keywords for each post:
   - [ ] Main keyword (e.g., "AI marketing automation")
   - [ ] Related keywords (e.g., "machine learning marketing", "AI sales")
   - [ ] Long-tail keywords (e.g., "how to automate marketing with AI")

3. [ ] Update post content:
   - [ ] Add target keyword to title
   - [ ] Add to first paragraph (first 100 words)
   - [ ] Use in headings (h2, h3)
   - [ ] Naturally throughout content
   - [ ] In image alt text

4. [ ] Verify keyword density (1-2% is good):
   ```bash
   # Count keyword occurrences
   grep -o "your keyword" post.md | wc -l
   ```

---

### [ ] 9. Internal Linking Audit
**Time:** 30 minutes  
**Impact:** Better crawlability, +3% ranking

**Steps:**
1. [ ] List all main topic areas:
   - AI & machine learning
   - Technology trends
   - Digital innovation
   - etc.

2. [ ] Create topic clusters:
   - Main article (pillar)
   - 3-5 related articles (cluster)

3. [ ] Add internal links:
   ```jsx
   // Example in blog post:
   Read more about <Link href="/posts/ai-fundamentals">AI fundamentals</Link>
   ```

4. [ ] Verify:
   - [ ] Each post links to 2-3 related posts
   - [ ] No orphan pages (unreachable from home)
   - [ ] Links use descriptive anchor text (not "click here")

---

### [ ] 10. Image Alt Text Audit
**File:** All components with images  
**Time:** 30 minutes  
**Impact:** Better SEO + accessibility

**Check all `<Image>` tags have:**
```jsx
// ✅ GOOD:
<Image
  src="/banner.jpg"
  alt="AI-powered customer insights dashboard interface"
  width={1200}
  height={630}
/>

// ❌ BAD:
<Image src="/banner.jpg" alt="image" />
<Image src="/banner.jpg" />  {/* No alt */}
```

**Alt text formula:**
- Describe what the image shows
- Include relevant keywords naturally
- 120 characters max
- Don't say "image of..." (users know it's an image)

**Quick Check:**
```bash
# Find all images without alt text:
grep -r "<Image" web/public-site/components --include="*.jsx" --include="*.js" | grep -v "alt="
```

---

## 📈 Monitoring (Ongoing - 30 min setup)

### [ ] 11. Set Up Google Search Console
**Time:** 5 minutes  
**Impact:** Monitor rankings, identify issues

**Steps:**
1. [ ] Go to [Google Search Console](https://search.google.com/search-console)
2. [ ] Add property: `https://glad-labs.com`
3. [ ] Verify ownership (follow Next.js on Vercel instructions)
4. [ ] Submit sitemap: `https://glad-labs.com/sitemap.xml`
5. [ ] Check for crawl errors
6. [ ] Monitor search performance

**What to Monitor:**
- [ ] Average ranking position
- [ ] Impressions vs. clicks (CTR)
- [ ] Core Web Vitals
- [ ] Crawl errors

---

### [ ] 12. Set Up Core Web Vitals Monitoring
**Time:** 10 minutes  
**Impact:** Identify performance issues

**In Chrome DevTools:**
1. [ ] Open DevTools (F12)
2. [ ] Go to "Performance" tab
3. [ ] Refresh page
4. [ ] Check metrics:
   - LCP (Largest Contentful Paint): Should be < 2.5s
   - FID (First Input Delay): Should be < 100ms
   - CLS (Cumulative Layout Shift): Should be < 0.1

**Using PageSpeed Insights:**
1. [ ] Go to [PageSpeed Insights](https://pagespeed.web.dev)
2. [ ] Enter `https://glad-labs.com`
3. [ ] Check Mobile & Desktop scores
4. [ ] Review "Opportunities" section

**Expected Results:**
- Performance: 85-92/100
- SEO: 90-95/100
- Accessibility: 85-90/100
- Best Practices: 90-95/100

---

### [ ] 13. Set Up Google Analytics Goals
**Time:** 15 minutes  
**Impact:** Measure conversions

**In Google Analytics:**
1. [ ] Create goals for:
   - [ ] Post read (scroll depth > 50%)
   - [ ] External link click
   - [ ] Social share
   - [ ] Newsletter signup (when added)

2. [ ] Create custom segments:
   - [ ] Organic traffic only
   - [ ] Mobile vs. desktop
   - [ ] New vs. returning

3. [ ] Set up dashboard to monitor:
   - [ ] Organic traffic trend
   - [ ] Top performing pages
   - [ ] User journey funnels

---

## 📋 Verification Checklist

### Before Going Live

- [ ] All schema markup validates (use [Schema.org Validator](https://validator.schema.org/))
- [ ] All forms have labels
- [ ] Skip link works (Tab key)
- [ ] Images have descriptive alt text
- [ ] No broken links
- [ ] Mobile responsive (test on mobile device)
- [ ] Analytics installed and tracking
- [ ] Open Graph tags preview on Twitter/Facebook
- [ ] Lighthouse score > 85 on all pages
- [ ] Core Web Vitals pass on Chrome UX Report

---

## Priority Implementation Order

**Week 1: Quick Wins (3-4 hours)**
1. [ ] Add skip link (5 min)
2. [ ] Add BlogPosting schema (15 min)
3. [ ] Add Breadcrumb schema (10 min)
4. [ ] Add per-post Open Graph (5 min)
5. [ ] Verify form labels (10 min)

**Week 2: Medium Priority (2-3 hours)**
6. [ ] Add FAQ schema (20 min)
7. [ ] Enhance About page (30 min)
8. [ ] Keyword research (1 hour)

**Week 3: Deep Dives (2 hours)**
9. [ ] Internal linking audit (30 min)
10. [ ] Image alt text audit (30 min)
11. [ ] Content optimization (1 hour)

**Week 4: Monitoring (30 min)**
12. [ ] Google Search Console setup (5 min)
13. [ ] Core Web Vitals monitoring (10 min)
14. [ ] Google Analytics goals (15 min)

---

## Expected Results

After completing all items:

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Lighthouse SEO | 90 | 98 | +8 |
| Accessibility | 85 | 95 | +10 |
| Search visibility | Baseline | +25% | Significant |
| Organic traffic | Baseline | +15-25% | Projected |
| Social shares | Baseline | +5-10% | Estimated |

---

## Tools You'll Need (All Free)

- Chrome DevTools (built-in, F12)
- [Google Search Console](https://search.google.com/search-console)
- [Google PageSpeed Insights](https://pagespeed.web.dev)
- [Schema.org Validator](https://validator.schema.org/)
- [Axe DevTools](https://www.deque.com/axe/devtools/) (browser extension)
- [WAVE](https://wave.webaim.org/extension/) (browser extension)
- [Twitter Card Validator](https://cards-dev.twitter.com/validator)

---

## Questions?

Refer back to: `WEB_PERFORMANCE_AND_SEO_AUDIT.md` for full details and explanations.

---

**Last Updated:** February 5, 2026  
**Status:** Ready for implementation  
**Estimated Total Time:** 5-6 hours for all items

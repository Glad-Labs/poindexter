# Strapi-Backed Pages Implementation Guide

**Quick Reference for Using Strapi to Manage Legal & Info Pages**

---

## Overview

Your public site uses a **Strapi-backed architecture** with **markdown fallbacks** for legal and informational pages. This provides:

✅ **Content Management** - Edit content in Strapi without code changes  
✅ **Fallback Safety** - Pages render markdown even if Strapi is down  
✅ **ISR Revalidation** - Pages update every 60 seconds automatically  
✅ **SEO Support** - Strapi entries include SEO metadata fields

---

## Pages Using This Architecture

| Page             | File                        | Route               | Strapi Endpoint         | Status   |
| ---------------- | --------------------------- | ------------------- | ----------------------- | -------- |
| About            | `pages/about.js`            | `/about`            | `/api/about`            | ✅ Ready |
| Privacy Policy   | `pages/privacy-policy.js`   | `/privacy-policy`   | `/api/privacy-policy`   | ✅ Ready |
| Terms of Service | `pages/terms-of-service.js` | `/terms-of-service` | `/api/terms-of-service` | ✅ Ready |

---

## How It Works

### Architecture Pattern

```javascript
export default function Page({ content }) {
  const data = content || fallbackMarkdownContent;

  return <Markdown>{data}</Markdown>;
}

export async function getStaticProps() {
  try {
    // 1. Try to fetch from Strapi
    const response = await fetch(strapiEndpoint);
    const content = response.json().data.content;

    return {
      props: { content },
      revalidate: 60, // ISR: Revalidate every 60 seconds
    };
  } catch (error) {
    // 2. Fall back to markdown if Strapi fails
    return {
      props: { content: null }, // Triggers fallback
      revalidate: 60,
    };
  }
}
```

### How Each Page Works

**About Page** (`pages/about.js`)

- Fetches from `GET /api/about` with Authorization header
- Expects Strapi entry with fields: `title`, `content`, `seo`
- Route: http://localhost:3000/about
- Markdown fallback included for Strapi downtime

**Privacy Policy** (`pages/privacy-policy.js`)

- Fetches from `GET /api/privacy-policy` with Authorization header
- Expects Strapi entry with fields: `title`, `content`, `seo`
- Route: http://localhost:3000/privacy-policy
- Markdown fallback included for Strapi downtime

**Terms of Service** (`pages/terms-of-service.js`)

- Fetches from `GET /api/terms-of-service` (no auth required)
- Expects Strapi entry with fields: `title`, `content`, `seo`
- Route: http://localhost:3000/terms-of-service
- Markdown fallback included for Strapi downtime

---

## Setting Up Content in Strapi

### Step 1: Create Content Type (if not exists)

In Strapi Admin:

1. Go to **Content Type Builder**
2. Create new Collection Type or Singleton Type
3. Add fields:
   - `title` (Short Text, required)
   - `content` (Rich Text or Long Text, required) - **Use markdown format**
   - `seo` (JSON, optional) - For SEO metadata

### Step 2: Create Content Entry

For **About Page**:

1. Go to **Content Manager** → **About**
2. Create new entry (or edit existing)
3. Fill in:
   - **Title:** "About GLAD Labs"
   - **Content:** (Markdown format) - See fallback content in pages/about.js for template
   - **SEO:**
     ```json
     {
       "metaTitle": "About GLAD Labs",
       "metaDescription": "Learn about GLAD Labs' mission..."
     }
     ```
4. **Publish** the entry

For **Privacy Policy**:

1. Go to **Content Manager** → **Privacy Policy**
2. Create new entry
3. Fill in similar fields
4. **Publish**

For **Terms of Service**:

1. Go to **Content Manager** → **Terms of Service**
2. Create new entry
3. Fill in similar fields
4. **Publish**

### Step 3: Verify Pages Load

1. Start Next.js: `npm run dev`
2. Visit: http://localhost:3000/about
3. Visit: http://localhost:3000/privacy-policy
4. Visit: http://localhost:3000/terms-of-service
5. If Strapi content exists, it renders. Otherwise, markdown fallback is shown.

---

## Content Format Guidelines

### Markdown Content

The `content` field should be valid Markdown:

```markdown
# Page Title

## Section 1

Description text here.

## Section 2

- Bullet point 1
- Bullet point 2

### Subsection

More content...

## Contact

Email: hello@gladlabs.com
```

### Strapi API Response Format (v5)

The endpoint returns:

```json
{
  "data": {
    "id": 1,
    "title": "About GLAD Labs",
    "content": "# About GLAD Labs\n\nContent here...",
    "seo": {
      "metaTitle": "About GLAD Labs",
      "metaDescription": "..."
    },
    "createdAt": "2025-10-20T...",
    "updatedAt": "2025-10-20T..."
  }
}
```

---

## Fallback Content

If Strapi is unavailable or endpoint fails, pages render markdown fallback content defined in each page file.

**Current Fallbacks:**

- **about.js:** Mission, vision, technology stack
- **privacy-policy.js:** GDPR/CCPA sections, data handling
- **terms-of-service.js:** Usage terms, liability disclaimers

These fallbacks ensure pages remain accessible even during Strapi downtime.

---

## Testing

### Test Strapi-Backed Rendering

1. Ensure Strapi is running: `npm run develop` (cms/strapi-v5-backend)
2. Create entries in Strapi admin
3. Start Next.js: `npm run dev` (web/public-site)
4. Visit page URL and verify content loads from Strapi

### Test Fallback Rendering

1. Stop Strapi or don't create entries
2. Start Next.js: `npm run dev`
3. Visit page URL and verify fallback markdown renders

### Test ISR Revalidation

1. Edit content in Strapi
2. Save/Publish changes
3. Wait up to 60 seconds
4. Refresh page - should see new content

---

## Common Issues & Solutions

**Issue:** Page shows fallback content instead of Strapi content

- **Solution:** Verify entry is published in Strapi
- **Solution:** Check API endpoint in browser: http://localhost:1337/api/about
- **Solution:** Verify `STRAPI_API_TOKEN` env var is set for auth endpoints

**Issue:** "Cannot find Strapi backend"

- **Solution:** Ensure Strapi is running: `npm run develop` in cms/strapi-v5-backend
- **Solution:** Check Strapi is accessible at http://localhost:1337
- **Solution:** Verify firewall/network connectivity

**Issue:** Markdown not rendering correctly

- **Solution:** Use valid Markdown syntax in Strapi `content` field
- **Solution:** Check markdown is actually markdown, not HTML

**Issue:** SEO fields not showing

- **Solution:** Ensure Strapi entry has `seo` JSON field
- **Solution:** Check page is using `seo` object: `seo.metaTitle`, `seo.metaDescription`

---

## Next Steps

1. ✅ **Create Strapi entries** for About, Privacy Policy, and Terms of Service
2. ✅ **Update navigation** links in Header/Footer components
3. ✅ **Test locally** to ensure pages render correctly
4. ✅ **Deploy** to Vercel when ready

**Time Estimate:** 20-30 minutes total

---

**Created:** October 20, 2025  
**Purpose:** Quick reference for maintaining Strapi-backed pages  
**Maintenance:** Update this guide when page structure changes

# Content Setup Guide for GLAD Labs Strapi

## 📋 Overview

This guide explains how to populate your Strapi CMS with content for the About page and Privacy Policy page using the provided seed script and Strapi admin interface.

---

## 🚀 Quick Start: Seed Content via Script

### Step 1: Start Strapi

```bash
cd cms/strapi-main
npm run develop
# Strapi will start on http://localhost:1337
```

### Step 2: Run the Seed Script

```bash
# From repository root
cd cms/strapi-main
node scripts/seed-single-types.js
```

**Expected Output:**

```bash
🌱 Starting single type content seeding...
📡 Strapi URL: http://localhost:1337
✓ Strapi is running
Creating About page...
✓ Created About page
Creating Privacy Policy page...
✓ Created Privacy Policy page
🎉 Single type content seeding completed successfully!
📖 Visit http://localhost:1337/admin to manage your content
🌐 API available at http://localhost:1337/api
```

### Step 3: Verify in Strapi Admin

1. Visit: **http://localhost:1337/admin**
2. Navigate to **Content Manager** (left sidebar)
3. You should see:
   - ✅ **About Page** - populated with comprehensive content
   - ✅ **Privacy Policy** - populated with CCPA/GDPR compliant content

---

## 🎨 Manual Content Management

### Accessing the Admin Interface

1. **Go to:** http://localhost:1337/admin
2. **Create admin account** on first visit (email, password)
3. **Dashboard** shows all content types

### Editing the About Page

1. **Content Manager** → **About Page**
2. Edit these fields:

| Field          | Type      | Description                                                        |
| -------------- | --------- | ------------------------------------------------------------------ |
| **Title**      | String    | Main page heading (e.g., "About GLAD Labs")                        |
| **Subtitle**   | String    | Secondary heading (e.g., "Building the AI Co-Founder of Tomorrow") |
| **Content**    | Rich Text | Main body content with sections, bullet points, formatting         |
| **Mission**    | Rich Text | Mission statement with HTML formatting                             |
| **Vision**     | Rich Text | Vision statement and future goals                                  |
| **Values**     | Rich Text | Core company values as formatted list                              |
| **Hero Image** | Image     | Optional banner image for the page                                 |
| **SEO**        | Component | Meta title, description, keywords                                  |

**Content Includes:**

- Who We Are section
- What We Build section
- Our Vision statement
- Core Values (5 key values with descriptions)
- Mission-driven content aligned with GLAD Labs

---

### Editing the Privacy Policy

1. **Content Manager** → **Privacy Policy**
2. Edit these fields:

| Field              | Type      | Description                       |
| ------------------ | --------- | --------------------------------- |
| **Title**          | String    | "Privacy Policy"                  |
| **Content**        | Rich Text | Full policy text with 15 sections |
| **Effective Date** | DateTime  | When policy becomes effective     |
| **Last Updated**   | DateTime  | When policy was last updated      |
| **Contact Email**  | Email     | privacy@gladlabs.com              |
| **SEO**            | Component | Meta title, description, keywords |

**Content Includes:**

- Introduction and commitment to privacy
- Information collection practices (personal, usage, cookies)
- Usage purposes (service delivery, personalization, etc.)
- Information sharing and disclosure practices
- Data security measures
- User rights and choices
- CCPA rights (California)
- GDPR rights (European Union)
- Data retention policies
- International data transfers
- Children's privacy protections
- Third-party links
- Automated decision-making
- Contact information
- Regulatory compliance

---

## 📱 Publishing Content

### Make Content Visible to Public

1. **Content Manager** → Select content item
2. Look for **"Publish"** button (top right)
3. Click **"Publish"** to make content public
4. Status changes from **"Draft"** to **"Published"**

⚠️ **Important:** Content must be **Published** to appear on the website!

---

## 🔗 Frontend Integration

### How Frontend Fetches Content

**Public Site** (`web/public-site/pages/about.js`):

```javascript
// Fetches from Strapi during build time
const url = `${getStrapiURL('/api/about')}?populate=*`;
const response = await fetch(url);
const { data: about } = await response.json();
```

**API Endpoint:** `GET /api/about`

---

## 🧪 Testing Content Display

### 1. Local Testing

```bash
# Terminal 1: Start Strapi
cd cms/strapi-main
npm run develop

# Terminal 2: Start Public Site
cd web/public-site
npm run dev
```

### 2. Visit Pages

- **About:** http://localhost:3000/about
- **Privacy Policy:** http://localhost:3000/privacy-policy

### 3. Verify Content Loads

- Check that your content displays correctly
- Verify SEO meta tags (F12 → Head section)
- Test responsive design (mobile/tablet/desktop)

---

## 🚀 Production Deployment

### Publishing to Railway

1. **Commit your content creation:**

   ```bash
   git add cms/strapi-main/
   git commit -m "chore: seed About and Privacy Policy content"
   git push origin main
   ```

2. **Content persists in production:**
   - Railway Strapi uses PostgreSQL
   - Content is automatically saved to database
   - No additional setup needed

### Environment Variables for Production

For your deployed Railway Strapi, set:

```bash
# Railway Project Settings → Variables
DATABASE_URL=postgresql://user:pass@host:port/dbname
ADMIN_JWT_SECRET=your-secret-key
API_TOKEN_SALT=your-salt
APP_KEYS=key1,key2,key3,key4
```

---

## 📝 Customization Guide

### Adding Custom Sections to About

To add new sections, edit the **About Page** schema:

**File:** `cms/strapi-main/src/api/about/content-types/about/schema.json`

Example: Add a "Why Us" section:

```json
{
  "whyUs": {
    "type": "richtext",
    "description": "Why customers choose GLAD Labs"
  }
}
```

Then in admin UI, populate the new field.

### Adding Custom Fields to Privacy Policy

Example: Add "Effective Date by Region":

```json
{
  "effectiveDateEU": {
    "type": "datetime",
    "description": "GDPR compliance effective date"
  }
}
```

---

## 🔐 Security Best Practices

### For Production

1. **Restrict Admin Access:**
   - Use strong passwords
   - Enable two-factor authentication (if available)
   - Limit admin accounts to authorized personnel

2. **Protect API Tokens:**
   - Create read-only tokens for public site
   - Use Railway secrets for sensitive credentials
   - Rotate tokens regularly

3. **Monitor Changes:**
   - Keep audit logs of content changes
   - Review access patterns
   - Set up alerts for unauthorized changes

---

## 🐛 Troubleshooting

### Issue: Content Not Appearing on Website

**Solution:**

1. Verify content is **Published** (not Draft)
2. Check API token has **read permission**
3. Verify frontend `.env.local` has correct `NEXT_PUBLIC_STRAPI_API_URL`
4. Check browser console for CORS errors

### Issue: Seed Script Fails

**Solution:**

1. Ensure Strapi is running: `npm run develop`
2. Check database connection: No errors in Strapi console
3. Run manually: Check Strapi admin UI directly

### Issue: SEO Fields Not Saving

**Solution:**

1. Verify `shared.seo` component exists
2. File: `cms/strapi-main/src/components/shared/seo.json`
3. Should contain: `metaTitle`, `metaDescription`, `keywords`

---

## 📚 Content Files Reference

### Seed Script

- **File:** `cms/strapi-main/scripts/seed-single-types.js`
- **Purpose:** Automatically populates About and Privacy Policy
- **Run:** `node scripts/seed-single-types.js`

### About Page Schema

- **File:** `cms/strapi-main/src/api/about/content-types/about/schema.json`
- **Fields:** title, subtitle, content, mission, vision, values, heroImage, seo

### Privacy Policy Schema

- **File:** `cms/strapi-main/src/api/privacy-policy/content-types/privacy-policy/schema.json`
- **Fields:** title, content, lastUpdated, effectiveDate, contactEmail, seo

### Frontend Pages

- **About:** `web/public-site/pages/about.js` (fetches from `/api/about`)
- **Privacy:** `web/public-site/pages/privacy-policy.js` (fetches from `/api/privacy-policy`)

---

## ✅ Checklist: Content Setup Complete

- [ ] Strapi running locally on http://localhost:1337
- [ ] Seed script executed successfully: `node scripts/seed-single-types.js`
- [ ] About page visible in Strapi Admin
- [ ] Privacy Policy visible in Strapi Admin
- [ ] Both content items **Published**
- [ ] About page displays at http://localhost:3000/about
- [ ] Privacy Policy displays at http://localhost:3000/privacy-policy
- [ ] SEO meta tags appear correctly in page source
- [ ] Responsive design works on mobile/tablet
- [ ] Production Railway Strapi configured
- [ ] Environment variables set for production
- [ ] Content persists after Railway deploy

---

## 📞 Support

For issues or questions about content management:

1. **Check Strapi Docs:** https://docs.strapi.io
2. **Review Schema Files:** `cms/strapi-main/src/api/*/content-types/*/schema.json`
3. **Test API Directly:**
   ```bash
   curl http://localhost:1337/api/about?populate=*
   curl http://localhost:1337/api/privacy-policy?populate=*
   ```

---

**Last Updated:** October 23, 2025  
**Status:** ✅ Ready for deployment

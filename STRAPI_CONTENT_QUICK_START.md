# 🚀 Quick Content Setup for GLAD Labs

## What Just Happened?

✅ Updated the seed script (`seed-single-types.js`) with **comprehensive, production-ready content** for:
- **About Page** - Full about GLAD Labs with mission, vision, and values
- **Privacy Policy** - 15-section policy covering CCPA, GDPR, data security, and more

## How to Populate Your Content

### Option 1: Automatic (Recommended - 1 minute)

```powershell
# Terminal 1: Start Strapi
cd cms\strapi-main
npm run develop

# Terminal 2: Run seed script (in a new terminal)
cd cms\strapi-main
node scripts\seed-single-types.js
```

**You should see:**
```
✓ Strapi is running
✓ Created About page
✓ Created Privacy Policy page
🎉 Single type content seeding completed successfully!
```

### Option 2: Manual (In Strapi Admin UI)

1. Go to **http://localhost:1337/admin**
2. **Content Manager** → **About Page** or **Privacy Policy**
3. Copy content from seed script and paste into fields
4. Click **Save** then **Publish**

## Content Fields

### About Page
- **Title:** About GLAD Labs
- **Subtitle:** Building the AI Co-Founder of Tomorrow
- **Content:** Who We Are, What We Build sections
- **Mission:** Company mission statement
- **Vision:** Vision for the future
- **Values:** 5 core company values
- **SEO:** Meta title, description, keywords

### Privacy Policy
- **Title:** Privacy Policy
- **Content:** 15 comprehensive sections covering:
  - Information collection and usage
  - Data security and encryption
  - CCPA and GDPR compliance
  - User rights and choices
  - International data transfers
  - Children's privacy
  - And more...
- **Effective Date:** 2024-10-01
- **Last Updated:** 2025-10-23
- **Contact Email:** privacy@gladlabs.com
- **SEO:** Optimized meta tags

## Verify It Worked

### 1. Check Strapi Admin
- Visit http://localhost:1337/admin
- Look for "About Page" and "Privacy Policy" in Content Manager
- They should show as **Published** (green status)

### 2. Check Frontend
- Start public site: `cd web\public-site && npm run dev`
- Visit **http://localhost:3000/about** - Should show your About page content
- Visit **http://localhost:3000/privacy-policy** - Should show your Privacy Policy

### 3. Test API Directly
```powershell
# PowerShell
Invoke-WebRequest -Uri "http://localhost:1337/api/about" | Select-Object -ExpandProperty Content | ConvertFrom-Json | Select-Object -ExpandProperty data

Invoke-WebRequest -Uri "http://localhost:1337/api/privacy-policy" | Select-Object -ExpandProperty Content | ConvertFrom-Json | Select-Object -ExpandProperty data
```

## What Content Is Included?

### About Page Content
✅ Who We Are - Introduction to GLAD Labs  
✅ Our Vision - Future-focused vision statement  
✅ What We Build - 5 key products/services with bullet points  
✅ Our Mission - Democratizing AI automation  
✅ Core Values - 5 values with descriptions (Innovation, Collaboration, etc.)  
✅ SEO - Optimized meta tags for search engines  

### Privacy Policy Content
✅ Comprehensive 15-section policy  
✅ Information collection practices  
✅ CCPA rights (California residents)  
✅ GDPR compliance (EU residents)  
✅ Data security measures  
✅ User rights and choices  
✅ Automated decision-making (AI transparency)  
✅ Third-party integrations  
✅ Children's privacy protections  
✅ Regulatory compliance statement  

## Environment Variables (Already Set)

For **local development**, the frontend automatically uses:
```
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
```

For **production** (Railway), you need to set:
```
NEXT_PUBLIC_STRAPI_API_URL=https://your-strapi-railway-url.railway.app
NEXT_PUBLIC_STRAPI_API_TOKEN=your-api-token
```

## Customizing Content

### Edit Content in Strapi Admin
1. Go to http://localhost:1337/admin
2. Click on "About Page" or "Privacy Policy"
3. Edit any field (title, content, etc.)
4. Click **Save**
5. Click **Publish** to make visible on website

### Edit Content in Code (Before Seeding)
File: `cms/strapi-main/scripts/seed-single-types.js`
- Update `aboutData` object for About page
- Update `privacyPolicyData` object for Privacy Policy
- Re-run: `node scripts/seed-single-types.js`

## Production Deployment

### 1. Commit Your Changes
```bash
git add cms/strapi-main/scripts/seed-single-types.js
git commit -m "feat: populate About and Privacy Policy content"
git push origin main
```

### 2. Push to Production
```bash
# Strapi is on Railway
# Content automatically syncs to Railway PostgreSQL database
# Frontends deployed to Vercel automatically get the content
```

### 3. Verify on Production
- Visit https://your-domain.com/about
- Visit https://your-domain.com/privacy-policy
- Check both pages load correctly

## Troubleshooting

### Content Not Showing on Website?
✅ Make sure content is **Published** (not Draft)  
✅ Check `.env.local` has correct Strapi URL  
✅ Clear browser cache: Ctrl+Shift+Delete  
✅ Check browser console (F12) for errors  

### Seed Script Fails?
✅ Make sure Strapi is running: `npm run develop`  
✅ Check console for error messages  
✅ Try manually creating in Strapi admin UI instead  

### Can't Connect to Strapi?
✅ Verify URL is http://localhost:1337 (not https)  
✅ Check Strapi console output  
✅ Port 1337 not in use: Check `netstat -ano | findstr :1337`  

## Files Modified

- ✅ `cms/strapi-main/scripts/seed-single-types.js` - Updated with production-ready content
- ✅ `cms/strapi-main/CONTENT_SETUP_GUIDE.md` - Comprehensive setup guide (for reference)
- ✅ Commit: `945ab0c70` - "feat: populate comprehensive About and Privacy Policy content for Strapi seeding"

## Next Steps

1. **Populate Content:**
   - Start Strapi: `npm run develop`
   - Run seed script: `node scripts/seed-single-types.js`
   - Verify in admin UI: http://localhost:1337/admin

2. **Test Locally:**
   - Start public site: `npm run dev`
   - Visit http://localhost:3000/about
   - Visit http://localhost:3000/privacy-policy

3. **Deploy to Production:**
   - Git commit and push
   - Content automatically deploys with Railway Strapi
   - Frontends pull updated content

4. **Customize if Needed:**
   - Edit content in Strapi admin or in seed script
   - Update branding, company info, legal text as needed
   - Re-publish and test

---

**Status:** ✅ Ready to populate  
**Content:** Production-ready  
**Next:** Run the seed script to populate your Strapi instance!

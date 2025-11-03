# 🌱 Strapi Seed Data Guide

**Script:** `scripts/seed-data.js`  
**Purpose:** Populate Strapi with categories, tags, and authors for local development  
**Status:** ✅ FIXED for local development

---

## ✅ Prerequisites

### 1. Strapi Running Locally

```powershell
# Terminal 1: Start Strapi
cd cms/strapi-main
npm run develop
# Should be running on http://localhost:1337/admin
```

### 2. Generate API Token in Strapi Admin

**Steps:**

1. Go to http://localhost:1337/admin
2. Login with your admin account
3. Navigate to: **Settings** (⚙️ icon) → **API Tokens**
4. Click **Create new API Token**
5. Fill in:
   - **Name:** `Local Dev Seed`
   - **Type:** `Full access` (for development)
   - **Description:** `For seed script in local development`
6. Click **Save**
7. **Copy the token** (you'll only see it once!)

---

## 🚀 Running the Seed Script

### Option 1: Set Token in Environment (Recommended)

**PowerShell:**

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-main
$env:STRAPI_API_TOKEN = "your-token-here"
$env:STRAPI_API_URL = "http://localhost:1337"
node scripts/seed-data.js
```

**Bash:**

```bash
cd cms/strapi-main
export STRAPI_API_TOKEN="your-token-here"
export STRAPI_API_URL="http://localhost:1337"
node scripts/seed-data.js
```

### Option 2: Quick Command (PowerShell)

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-main; `
$env:STRAPI_API_TOKEN="your-token-here"; `
node scripts/seed-data.js
```

### Option 3: Create .env File

Create `cms/strapi-main/.env` with:

```bash
STRAPI_API_TOKEN=your-token-here
STRAPI_API_URL=http://localhost:1337
```

Then run:

```powershell
cd cms/strapi-main
node scripts/seed-data.js
```

---

## 📊 What Gets Seeded

### Categories (5)

- AI & Machine Learning
- Game Development
- Technology Insights
- Business Strategy
- Innovation

### Tags (12)

- Artificial Intelligence
- Gaming
- Neural Networks
- Deep Learning
- Computer Vision
- NLP
- Unity
- Unreal Engine
- Indie Games
- Tech Trends
- Startups
- Digital Transformation

### Authors (2)

- Matthew M. Gladding (matthew@gladlabs.com)
- AI Research Team (research@gladlabs.com)

---

## ✅ Expected Output

**Successful Run:**

```
Starting Strapi content seeding...
Strapi URL: http://localhost:1337
Strapi is running
Creating categories...
Creating tags...
Creating authors...
Done!
```

**Error - Token Not Set:**

```
ERROR: STRAPI_API_TOKEN not set
```

**Error - Strapi Not Running:**

```
Cannot connect to Strapi
```

---

## 🧪 Verify Seeding Worked

1. Go to http://localhost:1337/admin
2. Check **Content Manager**:
   - **Categories** section should show 5 categories
   - **Tags** section should show 12 tags
   - **Authors** section should show 2 authors

3. Or use the API:

```bash
curl -H "Authorization: Bearer YOUR-TOKEN" \
  http://localhost:1337/api/categories
```

---

## 🐛 Troubleshooting

### Issue: "STRAPI_API_TOKEN not set"

**Solution:**

- Generate token in Strapi admin (Settings → API Tokens)
- Set environment variable before running script
- Or add to `.env` file in `cms/strapi-main/`

### Issue: "Cannot connect to Strapi"

**Solution:**

- Verify Strapi is running: http://localhost:1337/admin
- Check URL is `http://localhost:1337` (not production URL)
- Check internet connection (if using remote Strapi)

### Issue: API Error 403 (Forbidden)

**Solution:**

- Token may be expired or revoked
- Generate new token in Strapi admin
- Check token permissions (should be "Full access" for dev)

### Issue: API Error 422 (Validation Error)

**Solution:**

- Some entities may already exist
- Safe to run again (script upserts data)
- Check Strapi logs for details

---

## 📝 Configuration Reference

### Environment Variables

| Variable           | Default                                          | Local Dev             |
| ------------------ | ------------------------------------------------ | --------------------- |
| `STRAPI_API_URL`   | https://glad-labs-website-staging.up.railway.app | http://localhost:1337 |
| `STRAPI_API_TOKEN` | (required)                                       | Your generated token  |

### Script Endpoints Used

```
POST /api/categories
POST /api/tags
POST /api/authors
GET  /api/users/me       (health check)
```

---

## ✨ Next Steps

After seeding:

1. **Verify data in Strapi admin**
2. **Create blog posts** using the categories and authors
3. **Test Next.js public site** to see content rendering
4. **Generate content** with the Oversight Hub

---

## 📚 Related Files

- **Script:** `cms/strapi-main/scripts/seed-data.js`
- **Strapi Admin:** http://localhost:1337/admin
- **API Docs:** http://localhost:1337/api/documentation
- **Public Site:** http://localhost:3000 (should show seeded content)

---

**Last Updated:** November 2, 2025  
**Status:** ✅ Ready for Local Development

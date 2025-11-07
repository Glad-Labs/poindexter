# ✅ INTEGRATION IMPLEMENTATION CHECKLIST

**Current Status:** All systems analyzed and mostly ready  
**Objective:** Connect Oversight Hub → Cofounder Agent → Strapi → Public Site  
**Time Estimate:** 30 minutes to complete everything

---

## 🔍 Pre-Integration Verification

### ✅ VERIFY 1: All Services Running

```
powershell
# In separate PowerShell terminals, verify each service is running:

# Terminal 1: Check Cofounder Agent Backend
curl http://localhost:8000/api/health
# Expected: {"status": "healthy", "services": {...}}

# Terminal 2: Check Strapi CMS
curl http://localhost:1337/admin
# Expected: 200 OK

# Terminal 3: Check Oversight Hub
curl http://localhost:3001
# Expected: 200 OK (HTML homepage)

# Terminal 4: Check Public Site
curl http://localhost:3000
# Expected: 200 OK (HTML homepage)

# Terminal 5: Check Ollama
curl http://localhost:11434/api/tags
# Expected: {"models": [...]}
```

---

## 🛠️ Configuration Tasks

### ✅ TASK 1: Configure Oversight Hub API Endpoint

**File:** `web/oversight-hub/.env.local`

Create this file if it doesn't exist with:

```bash
# Cofounder Agent Backend URL
REACT_APP_API_URL=http://localhost:8000

# Optional settings
REACT_APP_API_TIMEOUT=180000
REACT_APP_DEBUG=true
```

**Verification:**

```powershell
# Check file exists and has correct content
Get-Content web/oversight-hub/.env.local

# Expected output:
# REACT_APP_API_URL=http://localhost:8000
```

---

### ✅ TASK 2: Configure Cofounder Agent Environment

**File:** `src/cofounder_agent/.env`

Update with Strapi credentials:

```sh
# ========== STRAPI CONFIGURATION ==========
STRAPI_BASE_URL=http://localhost:1337
STRAPI_API_TOKEN=<GET_FROM_STRAPI_ADMIN>  # See instructions below
STRAPI_PUBLISH_IMMEDIATELY=false

# ========== OLLAMA CONFIGURATION ==========
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral
# Optional alternatives: llama2, neural-chat, phi

# ========== DATABASE ==========
# Development (SQLite)
DATABASE_URL=sqlite:///./test.db

# Production (PostgreSQL - if using)
# DATABASE_URL=postgresql://user:password@localhost:5432/glad_labs

# ========== MODEL PREFERENCES ==========
PREFERRED_MODEL=ollama  # 'ollama' for local, or 'openai', 'anthropic'
```

**How to get Strapi API Token:**

1. Open `http://localhost:1337/admin`
2. Login with admin credentials
3. Click **Settings** (bottom left gear icon)
4. Select **API Tokens** → **Create new API token**
5. Name: `Cofounder Agent`
6. Type: **Full access** (for development)
7. Click **Save** and copy the full token
8. Paste into `.env` as `STRAPI_API_TOKEN=<paste-here>`

---

### ✅ TASK 3: Configure Public Site Strapi Connection

**File:** `web/public-site/.env.local`

Create this file with:

```sh
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=<SAME_TOKEN_AS_ABOVE>
```

**Verification:**

```
powershell
Get-Content web/public-site/.env.local
```

Expected output:

```
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=...
```

---

## 📊 Integration Validation

### ✅ VALIDATION 1: Test API Connectivity

**Command:**

```
powershell
curl http://localhost:8000/docs
```

Expected: Swagger UI page loads (interactive API docs)

```
powershell
curl "http://localhost:1337/api/posts" -H "Authorization: Bearer YOUR_TOKEN"
```

Expected: `{"data": [], "meta": {...}}`

---

### ✅ VALIDATION 2: Verify Strapi Content Types

**Manual Check:**

1. Open `http://localhost:1337/admin`
2. Go to **Content Manager** (left sidebar)
3. You should see these collections:
   - ✅ **Posts** (main blog content)
   - ✅ **Categories** (blog categories)
   - ✅ **Tags** (blog tags)
   - ✅ **Authors** (byline information)

**If missing, create them:**

```
bash
# Content type: Post
# Fields:
#   - title (string, required)
#   - slug (uid, from title)
#   - content (richtext)
#   - excerpt (text)
#   - featured_image (media)
#   - category (relation to Category)
#   - tags (relation to Tags)
#   - published (boolean)
#   - date (datetime)
```

---

### ✅ VALIDATION 3: Test Blog Post Creation Flow

**Using Integration Test Script:**

```
powershell
cd c:\Users\mattm\glad-labs-website
python integration_test.py --skip-polling
```

Or full test (wait for completion):

```
powershell
python integration_test.py --verbose
```

**Expected Output:**

```
✅ Cofounder Agent is running
✅ Strapi CMS is running
✅ Public Site is running
✅ Oversight Hub is running
✅ Task created with ID: abc-123-uuid
✅ Task completed successfully!
✅ Latest post in Strapi: "AI in Business..." (ID: 42)
✅ Post page is accessible at /posts/[slug]
```

---

## 🚀 End-to-End Manual Test

### ✅ STEP-BY-STEP: Create Your First Blog Post

#### Step 1: Open Oversight Hub (5 minutes)

1. Open browser: `http://localhost:3001`
2. Navigate to "Blog Post Creator" or similar section
3. You should see a form with fields:
   - Topic
   - Style (technical, casual, creative)
   - Tone (professional, friendly, humorous)
   - Target Length
   - Tags
   - Categories
   - Publish Mode (draft, publish_immediately)

#### Step 2: Fill Out Form

Topic: "The Future of Artificial Intelligence in 2025"
Style: "technical"
Tone: "professional"
Target Length: 1500
Tags: "AI, Technology, 2025, Future"
Categories: "Technology"
Publish Mode: "draft"

#### Step 3: Click "Generate Blog Post"

Expected UI Updates:

- Status changes to "queued" (0%)
- Then "generating" (progress bar fills)
- Then "assessing" (quality check)
- Finally "completed" (100%) with quality score

#### Step 4: Verify in Strapi (5 minutes)

1. Open: `http://localhost:1337/admin`
2. Click "Content Manager" → "Posts"
3. You should see your new post in the list
4. Click to view full content with metadata:
   - Title ✅
   - Content ✅
   - Featured Image (if enabled) ✅
   - Tags ✅
   - Category ✅
5. Verify "Publish" status is set appropriately

#### Step 5: Verify on Public Site (5 minutes)

1. Open: `http://localhost:3000`
2. Homepage should show your post in "Featured Posts" or recent posts
3. Click post title to view full article
4. Verify:
   - Title displays ✅
   - Content renders correctly ✅
   - Featured image displays ✅
   - Metadata (date, tags, category) shows ✅
   - Related posts appear ✅

Alternative: Visit direct URL: `http://localhost:3000/posts/[slug-of-your-post]`

---

## 🔧 Troubleshooting Guide

### ❌ Issue: "Cannot connect to Cofounder Agent"

**Symptom:** Oversight Hub shows "API connection failed"

**Solution:**

```
powershell
curl http://localhost:8000/api/health
```

Check if .env file exists:

```
powershell
Get-Content web/oversight-hub/.env.local
```

If not, create it. Then restart Oversight Hub.

---

### ❌ Issue: "Strapi API token invalid"

**Symptom:** Task created but doesn't publish to Strapi

**Solution:**

1. Open Strapi admin: `http://localhost:1337/admin`
2. Generate new token:
   - Settings → API Tokens → Create new
   - Name: "Cofounder Agent"
   - Type: "Full access"
   - Copy the full token
3. Update .env files with new token
4. Restart both services

---

### ❌ Issue: "No models available in Ollama"

**Symptom:** Backend logs show "Ollama: No models available"

**Solution:**

```powershell
# 1. Verify Ollama is running
curl http://localhost:11434/api/tags

# 2. If empty, pull a model
ollama pull mistral

# 3. Verify model is available
ollama list

# 4. Update .env
src/cofounder_agent/.env:
OLLAMA_MODEL=mistral

# 5. Restart backend
```

---

### ❌ Issue: "Blog post appears in Strapi but not on Public Site"

**Symptom:** Task completes, content in Strapi, but not visible at http://localhost:3000

**Solution:**

```powershell
# 1. Verify post is "published" not "draft"
# In Strapi admin, check the post status

# 2. Clear Next.js cache
cd web/public-site
Remove-Item -Recurse .next

# 3. Rebuild public site
npm run build

# 4. Clear browser cache
# Ctrl+Shift+Delete in browser

# 5. Verify Strapi token in .env.local
Get-Content web/public-site/.env.local
```

---

## 📋 Final Checklist

Before declaring integration complete:

- [ ] All 4 services running (Oversight Hub, Cofounder Agent, Strapi, Public Site)
- [ ] Oversight Hub connects to Cofounder Agent API (no console errors)
- [ ] Blog Post Creator form visible and functional
- [ ] Can create blog post task successfully
- [ ] Task status updates in real-time (UI updates)
- [ ] Task completes with quality score displayed
- [ ] Post appears in Strapi CMS Content Manager
- [ ] Post is marked as "Published" in Strapi
- [ ] Post appears on Public Site homepage/blog
- [ ] Post accessible via direct URL `/posts/[slug]`
- [ ] Featured image displays correctly
- [ ] Tags and categories appear on post
- [ ] Multiple posts can be created and managed
- [ ] Full workflow takes <5 minutes from task creation to publication

---

## 🎯 Success Criteria

You'll know everything is working when:

```
✅ Create blog post in Oversight Hub UI
   ↓
✅ See task status update in real-time
   ↓
✅ Task completes with quality score (>7/10 is good)
   ↓
✅ Post appears in Strapi CMS
   ↓
✅ Post visible on Public Site
   ↓
✅ Full workflow completes in <5 minutes
```

---

## 📚 Next Steps After Integration

1. **Automate**: Set up scheduled blog generation (weekly/daily)
2. **Monitor**: Track post performance metrics in dashboard
3. **Optimize**: Adjust Ollama model based on content quality
4. **Scale**: Generate multiple posts in parallel
5. **Customize**: Fine-tune content generation for your domain
6. **Analytics**: Add Google Analytics to track post views

---

## 🆘 Getting Help

If integration fails:

1. **Check logs**: Look at terminal output where services are running
2. **Verify connectivity**: Run `curl` tests for each service
3. **Check .env files**: Ensure all required variables are set
4. **Clear caches**: Delete `.next`, `node_modules`, `__pycache__`
5. **Restart services**: Kill and restart each service
6. **Use test script**: `python integration_test.py --verbose`

---

**Ready to integrate? Start from STEP 1 above!** 🚀

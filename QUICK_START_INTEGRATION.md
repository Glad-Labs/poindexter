# 🚀 GLAD Labs Full System Integration - Quick Start

**Status:** ✅ All Systems Configured & Running
**Last Updated:** November 6, 2025
**Configuration Complete:** 3 environment files updated

---

## ✅ WHAT'S BEEN COMPLETED

✅ **Configuration Files Created/Updated:**

- ✅ `web/oversight-hub/.env.local` → `REACT_APP_API_URL=http://localhost:8000`
- ✅ `src/cofounder_agent/.env` → Strapi connection configured with API token
- ✅ `web/public-site/.env.local` → Already had Strapi token configured

✅ **All Services Running:**

- ✅ **Strapi CMS** (Port 1337): Running & Ready
- ✅ **Oversight Hub** (Port 3001): Running & Configured
- ✅ **Public Site** (Port 3000): Running & Configured
- ✅ **Cofounder Agent** (Port 8000): Running & Connected to Ollama

✅ **Ollama Status:**

- ✅ Running on localhost:11434
- ✅ Model loaded: `llama2` (can use `mistral` or `phi` if you prefer)
- ✅ Ready for content generation

---

## 🎯 YOUR NEXT STEPS (10 MINUTES)

### STEP 1: Open Oversight Hub & Create a Blog Post (5 min)

**URL:** `http://localhost:3001`

1. Look for "Blog Post Creator" or "Create Blog" section
2. Fill in the form:
   - **Topic:** "The Rise of AI in Business 2025"
   - **Style:** Technical
   - **Tone:** Professional
   - **Target Length:** 1500
   - **Tags:** AI, Business, Technology
   - **Publish Mode:** Draft (so we can verify before publishing)

3. Click **Generate Blog Post**

**What to watch for:**

- Status bar shows: `queued` → `generating` → `assessing` → `completed`
- Quality score displayed (target 0.7+)
- Generated content shown in preview

### STEP 2: Verify in Strapi Admin (2 min)

**URL:** `http://localhost:1337/admin`

1. Click **Content Manager** (left sidebar)
2. Click **Posts**
3. Your new blog post should appear in the list
4. Click it to verify:
   - ✅ Title matches what you entered
   - ✅ Content is well-formatted
   - ✅ Tags are assigned
   - ✅ Status is "Draft" (or "Published" if you chose publish_immediate)

### STEP 3: Verify on Public Site (2 min)

**URL:** `http://localhost:3000`

1. Go to homepage
2. Look for your new post in recent posts or featured section
3. Click post title
4. Verify:
   - ✅ Full content displays
   - ✅ Formatting looks correct
   - ✅ Title and metadata show correctly

**Alternative:** Visit direct URL: `http://localhost:3000/posts/your-post-slug`

### STEP 4: Success! You've Completed Full System Integration 🎉

---

## 🔄 WORKFLOW SUMMARY

```
┌─────────────────────────────────────────────────────┐
│ 1. Oversight Hub (UI)                              │
│    - Create blog post form                         │
│    - Fill topic, style, tone, length               │
│    - Click "Generate"                              │
└────────────────┬────────────────────────────────────┘
                 │ HTTP POST to localhost:8000
┌────────────────▼────────────────────────────────────┐
│ 2. Cofounder Agent (FastAPI Backend)               │
│    - Receive task request                          │
│    - Route to content generation                   │
│    - Call Ollama for content generation            │
│    - Run quality assessment (QA agent)             │
│    - Format for publishing                         │
└────────────────┬────────────────────────────────────┘
                 │ HTTP POST to Strapi API
┌────────────────▼────────────────────────────────────┐
│ 3. Strapi CMS (Content Management)                 │
│    - Receive formatted content                     │
│    - Store in database                             │
│    - Create post entry with metadata               │
└────────────────┬────────────────────────────────────┘
                 │ REST API fetch during Next.js build
┌────────────────▼────────────────────────────────────┐
│ 4. Public Site (Next.js Frontend)                  │
│    - Fetch latest posts from Strapi                │
│    - Render on homepage                            │
│    - Display individual post pages                 │
└─────────────────────────────────────────────────────┘
```

---

## 📊 SYSTEM ARCHITECTURE AT A GLANCE

| Component           | Technology | Port  | Status       | Purpose                       |
| ------------------- | ---------- | ----- | ------------ | ----------------------------- |
| **Oversight Hub**   | React 18   | 3001  | ✅ Running   | Task creation UI              |
| **Cofounder Agent** | FastAPI    | 8000  | ✅ Running   | Content pipeline orchestrator |
| **Strapi CMS**      | Node.js    | 1337  | ✅ Running   | Content database & API        |
| **Public Site**     | Next.js 15 | 3000  | ✅ Running   | Public blog display           |
| **Ollama**          | Local LLM  | 11434 | ✅ Running   | Content generation engine     |
| **Database**        | PostgreSQL | 5432  | ✅ Available | Data persistence              |

---

## 🔍 TROUBLESHOOTING

### "Cannot connect to Cofounder Agent"

```powershell
# Check if backend is running
curl http://localhost:8000/docs

# You should see Swagger UI documentation page
```

### "Blog post created but not in Strapi"

```powershell
# Check Strapi API token
Get-Content src/cofounder_agent/.env | Select-String STRAPI_API_TOKEN

# Verify token is valid by testing:
$headers = @{"Authorization" = "Bearer YOUR_TOKEN"}
curl "http://localhost:1337/api/posts" -Headers $headers
```

### "Post in Strapi but not on public site"

```powershell
# Clear Next.js cache
cd web/public-site
Remove-Item -Recurse .next

# Rebuild
npm run build
```

### "Ollama content generation slow"

This is normal - Ollama runs locally on your machine.

- Mistral: ~20-30 seconds for 1500 words
- Llama2: ~30-40 seconds for 1500 words
- Phi: ~15-20 seconds for 1500 words

To use a faster model, update `.env`:

```bash
OLLAMA_MODEL=mistral  # or phi, neural-chat, orca-mini
```

---

## 📈 PERFORMANCE EXPECTATIONS

**Typical Task Timeline:**

- ⏱️ **Create Task:** 1 second (instant response)
- ⏱️ **Content Generation:** 20-40 seconds (depends on topic length & model)
- ⏱️ **Quality Assessment:** 5-10 seconds
- ⏱️ **Strapi Publishing:** 2-3 seconds
- ⏱️ **Public Site Rendering:** <1 second (cached)

**Total Time: 30-60 seconds from creation to published**

---

## 🎓 API ENDPOINTS (For Advanced Use)

### Create Blog Post

```bash
POST http://localhost:8000/api/content/blog-posts

Request Body:
{
  "topic": "Your topic here",
  "style": "technical",           # technical | casual | creative
  "tone": "professional",          # professional | friendly | humorous
  "target_length": 1500,
  "tags": ["AI", "Business"],
  "categories": ["Technology"],
  "generate_featured_image": true,
  "publish_mode": "draft",         # draft | publish_immediate
  "target_environment": "web"
}

Response:
{
  "task_id": "abc-123-uuid",
  "status": "queued",
  "polling_url": "/api/content/blog-posts/tasks/abc-123-uuid"
}
```

### Poll Task Status

```bash
GET http://localhost:8000/api/content/blog-posts/tasks/{task_id}

Response:
{
  "task_id": "abc-123-uuid",
  "status": "generating",          # queued | generating | assessing | completed
  "progress": {
    "stage": "content generation",
    "percentage": 65,
    "tokens_generated": 850,
    "quality_score": null          # Set when assessing complete
  },
  "result": null                   # Contains content when completed
}
```

---

## 🔐 CREDENTIALS REFERENCE

All systems use these shared credentials:

| System              | Admin URL                   | Default Credentials                   |
| ------------------- | --------------------------- | ------------------------------------- |
| **Strapi**          | http://localhost:1337/admin | Check your Strapi setup email         |
| **Oversight Hub**   | http://localhost:3001       | GitHub OAuth or mock auth             |
| **Cofounder Agent** | http://localhost:8000/docs  | Swagger UI (no auth needed for local) |
| **Public Site**     | http://localhost:3000       | Public (no auth needed)               |

---

## 📚 NEXT ADVANCED STEPS

After confirming the basic workflow works:

1. **Publish Multiple Posts**: Try different styles/tones and compare quality
2. **Customize Content**: Adjust prompts in `content_routes.py` for different output
3. **Monitor Performance**: Check task completion times and quality scores
4. **Scale Models**: Try `mistral` or `phi` for faster generation
5. **Add Categories**: Create more blog categories in Strapi for organization
6. **Tag Management**: Build a robust tag system in Strapi

---

## 📞 INTEGRATION TEST (Automated)

To run the automated end-to-end test:

```powershell
cd c:\Users\mattm\glad-labs-website
python integration_test.py --verbose
```

This will:

1. ✅ Verify all 4 services are running
2. ✅ Create a test blog post
3. ✅ Poll until task completes
4. ✅ Verify content in Strapi
5. ✅ Verify content on public site
6. ✅ Generate timing report

**Expected Output:**

```
✅ Cofounder Agent is running on localhost:8000
✅ Strapi CMS is running on localhost:1337
✅ Public Site is running on localhost:3000
✅ Oversight Hub is running on localhost:3001
✅ Blog post task created: id-abc-123
→ Polling status... [generating 65%]
✅ Task completed in 45 seconds
✅ Post found in Strapi: "The Rise of AI in Business 2025"
✅ Post accessible on public site at /posts/the-rise-of-ai-in-business-2025
✅ Full workflow completed successfully!
```

---

## ✨ YOU'RE ALL SET!

Your complete AI content pipeline is now connected:

- 🎯 **Oversight Hub** creates tasks with just a few clicks
- ⚡ **Cofounder Agent** processes them through intelligent pipeline
- 📦 **Strapi CMS** stores the generated content
- 🌐 **Public Site** displays it to the world

**Next: Open your browser and create your first blog post! 🚀**

---

**Questions?** Check:

- `SYSTEM_INTEGRATION_GUIDE.md` - Complete architecture reference
- `INTEGRATION_CHECKLIST.md` - Detailed step-by-step guide
- `integration_test.py` - Automated validation script

**Last Updated:** November 6, 2025
**Status:** ✅ READY TO USE

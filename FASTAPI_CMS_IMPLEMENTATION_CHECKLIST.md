# FastAPI CMS Implementation - Complete Checklist

**Status:** ✅ Ready for Full Implementation  
**Date:** November 2025  
**Timeline:** Ready to Start

---

## 🎯 Implementation Phases

### Phase 1: Database & Schema ✅ COMPLETE

**What's Done:**

- [x] `cms_routes.py` created with all endpoints
- [x] Database schema file (`init_cms_schema.py`) ready
- [x] Routes integrated into main.py
- [x] Error handling implemented

**What to Do:**

```bash
# 1. Start PostgreSQL (if not running)
# Windows: Start Docker Desktop and run PostgreSQL container
docker run --name postgres-glad -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres

# 2. Create database
psql -U postgres -c "CREATE DATABASE glad_labs_dev;"

# 3. Run schema initialization
cd src/cofounder_agent
python init_cms_schema.py

# Expected output:
# ✓ Categories table created
# ✓ Tags table created
# ✓ Posts table created
# ✓ Posts indexes created
# ✓ Post-Tags junction table created
# ✅ All CMS tables created successfully!
```

---

### Phase 2: Sample Data & Testing ✅ READY

**Setup Script:** `src/cofounder_agent/setup_cms.py`

```bash
# Run setup to create schema + sample data
cd src/cofounder_agent
python setup_cms.py

# Expected output:
# ✓ Categories: 3 records
# ✓ Tags: 5 records
# ✓ Posts: 3 sample posts

# Verify with API
curl http://localhost:8000/api/posts
curl http://localhost:8000/api/posts/future-of-ai-in-business
curl http://localhost:8000/api/categories
curl http://localhost:8000/api/tags
```

---

### Phase 3: Next.js Integration ✅ READY

**Files to Update:**

- `web/public-site/lib/api-fastapi.js` - Already created ✅
- `web/public-site/lib/api.js` - Already updated to use FastAPI ✅
- `.env.local` - Add FastAPI URL

**Setup:**

```bash
# Update .env.local in project root
echo "NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000" >> .env.local

# Start public site
cd web/public-site
npm run dev

# Test pages
# http://localhost:3000 - Homepage
# http://localhost:3000/posts/future-of-ai-in-business - Post page
# http://localhost:3000/category/technology - Category page
```

---

### Phase 4: Oversight Hub Integration ✅ READY

**Files to Update:**

- `web/oversight-hub/src/components/ContentManager.jsx` - Update to use FastAPI
- API base URL: `http://localhost:8000`

**Setup:**

```bash
# Start Oversight Hub
cd web/oversight-hub
npm start

# Test admin dashboard
# http://localhost:3001/content - Content management page
# Should show 3 sample posts
# Can create/edit/delete posts via UI
```

---

### Phase 5: Content Generation Integration ⏳ IN PROGRESS

**What to Update:**

- Content generation agents to save posts to FastAPI CMS
- Publishing endpoints to use FastAPI database
- Self-critique pipeline integration

**Files to Modify:**

- `src/agents/content_agent/agent.py`
- `src/cofounder_agent/routes/content_routes.py`

**Implementation:**

```python
# In content_agent.py
async def generate_blog_post(self, topic: str, auto_publish: bool = False):
    # 1. Generate content with self-critique
    content = await self.creative_agent.generate(topic)
    feedback = await self.qa_agent.critique(content)
    content = await self.creative_agent.refine(content, feedback)

    # 2. Save to FastAPI CMS
    post_data = {
        "title": topic,
        "slug": self.create_slug(topic),
        "content": content,
        "excerpt": content[:200],
        "status": "published" if auto_publish else "draft",
        "seo_title": f"{topic} | Glad Labs",
        "seo_description": f"Insights about {topic}",
    }

    # 3. Save to database
    response = await self.http_client.post(
        "http://localhost:8000/api/posts",
        json=post_data
    )
    return response.json()
```

---

## 🚀 Quick Start (5 minutes)

```bash
# 1. Start PostgreSQL
docker run --name postgres-glad -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres
sleep 5
psql -U postgres -c "CREATE DATABASE glad_labs_dev;"

# 2. Initialize database
cd src/cofounder_agent
python init_cms_schema.py

# 3. Insert sample data
python setup_cms.py

# 4. Start FastAPI server
python main.py

# 5. Test API
curl http://localhost:8000/api/posts
curl http://localhost:8000/api/cms/status

# 6. In another terminal, start frontend
cd web/public-site
npm run dev

# 7. Visit http://localhost:3000
```

---

## 📊 API Endpoints

### Posts

```bash
# List all posts
GET /api/posts?skip=0&limit=20&published_only=true

# Get single post by slug
GET /api/posts/{slug}

# Expected response:
{
  "data": {
    "id": "uuid",
    "title": "Post Title",
    "slug": "post-slug",
    "content": "# Markdown content...",
    "excerpt": "Short preview",
    "seo_title": "SEO Title",
    "seo_description": "SEO description",
    "published_at": "2025-11-13T10:00:00",
    "tags": ["tag1", "tag2"],
    "category": {"id": "uuid", "name": "Tech", "slug": "tech"}
  },
  "meta": {
    "pagination": {...},
    "tags": [...],
    "category": {...}
  }
}
```

### Categories

```bash
# List all categories
GET /api/categories

# Response:
{
  "data": [
    {"id": "uuid", "name": "Technology", "slug": "technology", "description": "..."},
    {"id": "uuid", "name": "Business", "slug": "business", "description": "..."}
  ],
  "meta": {}
}
```

### Tags

```bash
# List all tags
GET /api/tags

# Response:
{
  "data": [
    {"id": "uuid", "name": "AI", "slug": "ai", "color": "#EC4899"},
    {"id": "uuid", "name": "Automation", "slug": "automation", "color": "#06B6D4"}
  ],
  "meta": {}
}
```

### Health Check

```bash
# Check CMS status
GET /api/cms/status

# Response:
{
  "status": "healthy",
  "tables": {
    "posts": {"exists": true, "count": 3},
    "categories": {"exists": true, "count": 3},
    "tags": {"exists": true, "count": 5},
    "post_tags": {"exists": true, "count": 6}
  },
  "timestamp": "2025-11-13T10:00:00"
}
```

---

## 🧪 Testing

### Unit Tests

```bash
# Run CMS integration tests
cd src/cofounder_agent
pytest tests/test_fastapi_cms_integration.py -v

# Expected: 30+ tests passing ✅
```

### Integration Tests

```bash
# Test Next.js integration
cd web/public-site
npm test

# Expected: 63 tests passing ✅
```

### Manual Testing

```bash
# Start all services
cd src/cofounder_agent && python main.py &
cd web/public-site && npm run dev &
cd web/oversight-hub && npm start &

# Test workflows
1. Visit http://localhost:3000 - Homepage loads
2. Click on a post - Post detail page works
3. Filter by category/tag - Filtering works
4. Go to admin at http://localhost:3001 - Dashboard loads
5. Create new post - Shows in public site
```

---

## 🔧 Troubleshooting

### PostgreSQL Connection Failed

```bash
# Check if PostgreSQL is running
psql -U postgres -c "SELECT 1"

# If not, start it
docker run --name postgres-glad -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres

# Verify database exists
psql -U postgres -l | grep glad_labs_dev

# If not, create it
psql -U postgres -c "CREATE DATABASE glad_labs_dev;"
```

### Tables Not Found

```bash
# Run schema initialization
cd src/cofounder_agent
python init_cms_schema.py

# Verify tables exist
psql -U postgres -d glad_labs_dev -c "\dt"
```

### No Sample Data

```bash
# Run setup script
cd src/cofounder_agent
python setup_cms.py

# Verify data
curl http://localhost:8000/api/posts
```

### API Returns 404

```bash
# Check if FastAPI server is running
curl http://localhost:8000/docs

# If not, start it
cd src/cofounder_agent
python main.py

# Check main.py has cms_router registered
grep "app.include_router(cms_router)" main.py
```

### Next.js Can't Fetch Posts

```bash
# Check FastAPI_URL environment variable
echo $NEXT_PUBLIC_FASTAPI_URL

# Should be: http://localhost:8000

# If missing, add to .env.local
echo "NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000" >> .env.local

# Restart Next.js
cd web/public-site
npm run dev
```

---

## 📋 Verification Checklist

### Database ✅

- [ ] PostgreSQL running
- [ ] `glad_labs_dev` database exists
- [ ] All 4 tables created (posts, categories, tags, post_tags)
- [ ] Sample data inserted (3 posts, 3 categories, 5 tags)
- [ ] Indexes created for performance

### FastAPI ✅

- [ ] Server running on port 8000
- [ ] `/api/posts` returns 200
- [ ] `/api/categories` returns 200
- [ ] `/api/tags` returns 200
- [ ] `/api/cms/status` returns healthy
- [ ] All endpoints return proper JSON

### Next.js ✅

- [ ] Public site running on port 3000
- [ ] Homepage loads
- [ ] Posts display correctly
- [ ] Post detail pages work
- [ ] Category filtering works
- [ ] Tag filtering works
- [ ] SEO tags render correctly

### Oversight Hub ✅

- [ ] Dashboard running on port 3001
- [ ] Displays content list
- [ ] Can create new posts
- [ ] Can edit existing posts
- [ ] Can delete posts
- [ ] Changes reflect on public site

### Integration ✅

- [ ] All tests passing (173+ total)
- [ ] No console errors
- [ ] No API 500 errors
- [ ] Database queries fast (<200ms)
- [ ] Cache working properly

---

## 📊 Performance Targets

| Metric            | Target | How to Measure                                            |
| ----------------- | ------ | --------------------------------------------------------- |
| API Response Time | <200ms | `curl -w '%{time_total}' http://localhost:8000/api/posts` |
| Homepage Load     | <1s    | Chrome DevTools Network tab                               |
| Post Detail Load  | <500ms | Chrome DevTools Network tab                               |
| Database Query    | <50ms  | PostgreSQL query log                                      |
| Cache Hit Rate    | >90%   | Application metrics                                       |

---

## 🎉 Success Criteria

When complete, you should have:

✅ **Working CMS:**

- FastAPI backend managing content
- PostgreSQL storing posts, categories, tags
- 30+ test cases passing
- Health check endpoint working

✅ **Working Public Site:**

- Next.js consuming FastAPI content
- Homepage with featured posts
- Dynamic post pages by slug
- Category and tag filtering
- SEO metadata rendering

✅ **Working Admin Dashboard:**

- React admin UI (Oversight Hub)
- Content management interface
- Create/edit/delete posts
- Real-time updates

✅ **Full Integration:**

- Content generation → FastAPI CMS → Public Site
- Agents publishing posts automatically
- Self-critique pipeline working
- All components communicating

---

## 🚀 Next Steps After Setup

1. **Deploy to Production:**
   - Railway for FastAPI backend
   - Vercel for Next.js frontend
   - AWS RDS for PostgreSQL

2. **Add Features:**
   - Comments system
   - Newsletter integration
   - Analytics tracking
   - Content scheduling

3. **Optimize Performance:**
   - Add Redis caching
   - Optimize database queries
   - Image optimization
   - CDN for static assets

4. **Monitoring:**
   - Error tracking (Sentry)
   - Performance monitoring (New Relic)
   - Log aggregation (ELK stack)
   - Uptime monitoring

---

## 📞 Quick Reference

**Important Files:**

- Database Schema: `src/cofounder_agent/init_cms_schema.py`
- Setup Script: `src/cofounder_agent/setup_cms.py`
- API Routes: `src/cofounder_agent/routes/cms_routes.py`
- Next.js Client: `web/public-site/lib/api-fastapi.js`
- Tests: `src/cofounder_agent/tests/test_fastapi_cms_integration.py`

**Key URLs:**

- FastAPI Docs: http://localhost:8000/docs
- FastAPI API: http://localhost:8000/api/posts
- Next.js Public: http://localhost:3000
- React Admin: http://localhost:3001

**Key Commands:**

```bash
# Setup
python init_cms_schema.py
python setup_cms.py

# Start services
python main.py                    # FastAPI
npm run dev                       # Next.js Public
npm start                         # React Admin

# Test
pytest tests/ -v                  # Backend tests
npm test                          # Frontend tests
curl http://localhost:8000/docs   # API docs
```

---

**Status:** ✅ Ready to Implement  
**Estimated Time:** 1-2 hours to complete all phases  
**Complexity:** Moderate - mostly copy/paste and config

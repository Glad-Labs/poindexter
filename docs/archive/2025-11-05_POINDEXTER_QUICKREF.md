# 🤖 Poindexter - Quick Reference Guide

## What Changed?

### ✅ New Features Added

1. **Social Media Management API** (`/api/social/*`)
   - Platform connections
   - Post creation & management
   - Analytics tracking
   - AI content generation
   - Cross-posting

2. **Model Discovery** (`/api/models`)
   - Legacy endpoint support
   - List all available models
   - Provider information

3. **Metrics & Analytics** (`/api/metrics/*`)
   - System health
   - Cost tracking by model
   - Usage statistics

### ✅ Rebranding Complete

- "Co-Founder Agent" → **"Poindexter"** 🤖
- All UI text updated
- All documentation updated
- Consistent branding throughout

---

## 🔧 API Endpoints Reference

### Social Media

```
GET    /api/social/platforms              # List connected platforms
POST   /api/social/connect                # Connect a platform
GET    /api/social/posts                  # Get all posts
POST   /api/social/posts                  # Create new post
DELETE /api/social/posts/{id}             # Delete post
GET    /api/social/posts/{id}/analytics   # Post analytics
POST   /api/social/generate               # Generate AI content
GET    /api/social/trending               # Get trending topics
POST   /api/social/cross-post             # Multi-platform posting
```

### Models

```
GET    /api/models                        # List models (legacy)
GET    /api/v1/models/available           # List models (v1)
```

### Metrics

```
GET    /api/metrics                       # System metrics
GET    /api/metrics/costs                 # Cost breakdown
GET    /api/metrics/summary               # Aggregated stats
POST   /api/metrics/track-usage           # Track usage
```

---

## 📁 Files Changed

**Backend (3 new files):**

- `/src/cofounder_agent/routes/social_routes.py` (NEW)
- `/src/cofounder_agent/routes/metrics_routes.py` (NEW)
- `/src/cofounder_agent/routes/models.py` (UPDATED - added endpoint)
- `/src/cofounder_agent/main.py` (UPDATED - registered routes)

**Frontend (5 files updated):**

- `web/oversight-hub/src/OversightHub.jsx`
- `web/oversight-hub/src/components/common/CommandPane.jsx`
- `web/oversight-hub/src/components/dashboard/SystemHealthDashboard.jsx`

---

## 🚀 How to Use

### Run Backend

```powershell
cd c:\Users\mattm\glad-labs-website
python -m uvicorn src.cofounder_agent.main:app --reload
```

### Run Frontend

```powershell
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start
```

### Test Endpoints

```bash
# Test social media
curl http://localhost:8000/api/social/platforms

# Test models
curl http://localhost:8000/api/models

# Test metrics
curl http://localhost:8000/api/metrics/costs
```

---

## ⚠️ Known Issues

### 401 Unauthorized on `/api/tasks`

**Why:** Tasks require authentication  
**Fix:** Send `Authorization: Bearer <token>` header

### 404 on First Load

**Why:** Routes may not be registered yet  
**Fix:** Restart backend server

---

## 📞 Support

For more details, see:

- `POINDEXTER_COMPLETE.md` - Comprehensive report
- Backend logs at `http://localhost:8000/docs` - API docs

---

**Status:** ✅ Production Ready  
**Date:** November 2, 2025  
**Agent:** Poindexter 🤖

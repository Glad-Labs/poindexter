# 🚀 FRONTEND REFACTORING - QUICK START

**Status:** ✅ READY TO IMPLEMENT  
**Scope:** Oversight Hub (React) + Public Site (Next.js)  
**Time:** 4 hours implementation + 1 hour testing  
**Complexity:** Medium (copy-paste code provided)

---

## 📋 WHAT YOU NEED TO KNOW

### Backend Changed To:

- ✅ **OAuth 2.0** (GitHub)
- ✅ **CMS Routes** (Direct PostgreSQL)
- ✅ **Task Management** (REST API queue)
- ✅ **JWT Authentication** (Bearer tokens)

### Frontend Must:

- ✅ Implement OAuth callback handling
- ✅ Store JWT tokens in localStorage
- ✅ Add Authorization headers to API requests
- ✅ Update API client for new response formats
- ✅ Handle token expiry gracefully

---

## 🎯 IMPLEMENTATION IN 3 STEPS

### Step 1: Update Oversight Hub (1.5 hours)

**File:** `web/oversight-hub/src/services/cofounderAgentClient.js`

**What to do:**

1. Add OAuth endpoint functions (20 min)
2. Update CMS endpoint functions (20 min)
3. Add task management functions (15 min)
4. Add error handling for new response format (15 min)

**Copy from:** FRONTEND_REFACTORING_GUIDE.md § Oversight Hub Refactoring → File 1

**Then:**

**File:** `web/oversight-hub/src/services/authService.js`

1. Add OAuth exchange function (10 min)
2. Add token validation (10 min)
3. Add logout handler (5 min)

**Copy from:** FRONTEND_REFACTORING_GUIDE.md § Oversight Hub Refactoring → File 2

**Then:**

**Files:** Create `web/oversight-hub/src/pages/OAuthCallback.jsx` + Update components

1. Create OAuth callback page (15 min)
2. Add GitHub button to LoginForm (10 min)
3. Update AuthContext for OAuth flow (15 min)

**Copy from:** FRONTEND_REFACTORING_GUIDE.md § Oversight Hub Refactoring → Files 3-5

### Step 2: Update Public Site (1.5 hours)

**File:** `web/public-site/lib/api-fastapi.js`

**What to do:**

1. Add response normalization function (10 min)
2. Update all CMS functions to use new response format (30 min)
3. Add search & filtering functions (15 min)
4. Add new endpoint functions (10 min)

**Copy from:** FRONTEND_REFACTORING_GUIDE.md § Public Site Refactoring → File 1

**Then:**

**Files:** Create new pages/components

1. Create `web/public-site/pages/auth/callback.jsx` (15 min)
2. Create `web/public-site/components/LoginLink.jsx` (10 min)
3. Update `web/public-site/components/Header.js` (10 min)

**Copy from:** FRONTEND_REFACTORING_GUIDE.md § Public Site Refactoring → Files 2-4

### Step 3: Test Everything (1 hour)

**Backend Test (10 min):**

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/auth/providers
curl http://localhost:8000/api/posts
```

**Frontend OAuth Test (20 min):**

1. Start Oversight Hub: Click "Sign in with GitHub"
2. Authorize on GitHub
3. Check token in browser localStorage (F12 → Application)
4. Verify data loads

**Public Site Test (20 min):**

1. Start Public Site: Click "Sign in with GitHub"
2. Authorize on GitHub
3. Check posts load correctly
4. Verify categories/tags work

**Database Verification (10 min):**

```bash
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev
SELECT * FROM users;
SELECT * FROM oauth_accounts;
```

---

## ✅ QUICK CHECKLIST

### Pre-Implementation

- [ ] Backend running on port 8000
- [ ] PostgreSQL running on port 5432
- [ ] Oversight Hub development environment ready
- [ ] Public Site development environment ready

### Oversight Hub

- [ ] Updated cofounderAgentClient.js (15 functions added)
- [ ] Updated authService.js (6 functions added)
- [ ] Created OAuthCallback.jsx
- [ ] Added GitHub button to LoginForm
- [ ] Updated AuthContext
- [ ] No console errors
- [ ] OAuth flow works

### Public Site

- [ ] Updated lib/api-fastapi.js (normalization + functions)
- [ ] Created pages/auth/callback.jsx
- [ ] Created components/LoginLink.jsx
- [ ] Updated Header.js
- [ ] No console errors
- [ ] Posts load correctly
- [ ] Categories/tags work

### Testing

- [ ] OAuth works on both frontends
- [ ] Tokens stored in localStorage
- [ ] API requests include Authorization header
- [ ] CMS endpoints return correct data
- [ ] Users created in database
- [ ] OAuthAccounts linked correctly
- [ ] No 401 errors
- [ ] Database sync verified

---

## 🔗 RESOURCE LINKS

**Full Documentation:**

- FRONTEND_REFACTORING_GUIDE.md (2,500+ lines, complete)
- FRONTEND_REFACTORING_DELIVERY_SUMMARY.md (summary)

**Backend Reference:**

- QUICK_REFERENCE.md (commands)
- POSTGRESQL_SETUP_GUIDE.md (database)
- INTEGRATION_ACTION_PLAN.md (step-by-step)

**Code Examples:**

- All in FRONTEND_REFACTORING_GUIDE.md
- Production-ready, copy-paste compatible

---

## 📊 TIME ESTIMATE

| Phase         | Time      | Notes                    |
| ------------- | --------- | ------------------------ |
| Oversight Hub | 1.5 hrs   | 5 files, copy-paste code |
| Public Site   | 1.5 hrs   | 4 files, copy-paste code |
| Testing       | 1 hr      | Verification checklist   |
| **Total**     | **4 hrs** | Ready to deploy after    |

---

## 🎯 SUCCESS CRITERIA

After implementation, you should be able to:

✅ Sign in with GitHub on both frontends  
✅ See JWT token in localStorage  
✅ Access protected API endpoints  
✅ View posts, categories, tags  
✅ Create/edit content (if authorized)  
✅ See users in PostgreSQL database  
✅ No console errors  
✅ No 401/403 errors

---

## 🚨 COMMON MISTAKES

### ❌ Mistake 1: Forgetting Authorization Header

```javascript
// Wrong
fetch('/api/posts', {
  headers: { 'Content-Type': 'application/json' },
});

// Right
fetch('/api/posts', {
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  },
});
```

### ❌ Mistake 2: Not Normalizing Response

```javascript
// Wrong - expects Strapi format
post.attributes.title;

// Right - FastAPI flat format
post.title;
```

### ❌ Mistake 3: Using Old Endpoint Format

```javascript
// Wrong - Strapi format
/api/posts?populate=*&sort[publishedAt]=desc

// Right - FastAPI format
/api/posts?skip=0&limit=20&published_only=true
```

### ❌ Mistake 4: Not Storing Token

```javascript
// Wrong - token lost on page reload
let token = data.access_token;

// Right - persistent storage
localStorage.setItem('auth_token', data.access_token);
```

### ❌ Mistake 5: Wrong Port

```javascript
// Wrong
http://localhost:3000/api/posts  // React app port

// Right
http://localhost:8000/api/posts  // FastAPI backend port
```

---

## 💡 TIPS FOR SUCCESS

1. **Start with backend verification:**

   ```bash
   curl http://localhost:8000/api/health
   ```

2. **Copy code exactly** - All examples are production-ready

3. **Test incrementally** - Don't implement everything, then test

4. **Check browser console** - F12 → Console for error details

5. **Check backend logs** - Watch for 401/403/500 errors

6. **Use curl to test endpoints** - Before trusting frontend

7. **Verify database** - psql to confirm users/oauth_accounts created

8. **Clear localStorage** - Between testing: `localStorage.clear()`

---

## 🆘 TROUBLESHOOTING

### Issue: "Unauthorized" / 401 Error

**Solution:**

1. Check token is in localStorage: `localStorage.getItem('auth_token')`
2. Check Authorization header is sent: Check network tab (F12)
3. Verify token is valid: Check expiration time
4. Re-login if expired

### Issue: CORS Error

**Solution:**

1. Verify CORS configured in backend (should be: 3000, 3001)
2. Restart backend after CORS changes
3. Check preflight request (OPTIONS)

### Issue: "Post not found" / 404

**Solution:**

1. Verify post slug is correct
2. Check post is published (`published_at IS NOT NULL`)
3. Verify database has posts:
   ```bash
   psql -c "SELECT slug FROM posts WHERE published_at IS NOT NULL"
   ```

### Issue: Token Not Stored

**Solution:**

1. Check OAuth callback page runs
2. Verify response has `access_token` field
3. Check localStorage size limit (usually 5-10MB)
4. Clear storage and try again: `localStorage.clear()`

### Issue: API Returns Old Strapi Format

**Solution:**

1. Verify backend is actually running new code
2. Restart backend: `python -m uvicorn main:app --reload`
3. Check you're hitting right endpoint: Use curl to verify
4. Check response in network tab (F12 → Network)

---

## 📞 QUICK REFERENCE

**Backend URL:** `http://localhost:8000`  
**PostgreSQL:** `postgresql://postgres:postgres@localhost:5432/glad_labs_dev`  
**Oversight Hub:** `http://localhost:3001`  
**Public Site:** `http://localhost:3000`

**Key Endpoints:**

- `GET /api/auth/providers` → List OAuth providers
- `GET /api/auth/{provider}/login` → Start OAuth flow
- `GET /api/posts` → List posts
- `GET /api/posts/{slug}` → Get post by slug

---

## ✅ READY TO START?

1. Open FRONTEND_REFACTORING_GUIDE.md
2. Start with Step 1: Oversight Hub
3. Copy code from guide
4. Test as you go
5. Move to Step 2: Public Site
6. Run verification checklist
7. Done! 🎉

---

**Status: ✅ EVERYTHING YOU NEED IS PROVIDED**

Start implementing now. All code is ready to copy-paste.

Good luck! 🚀

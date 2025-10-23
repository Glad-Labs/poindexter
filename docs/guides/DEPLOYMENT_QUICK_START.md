# 🚀 Deployment Quick Start (5-Minute Overview)

**Got 5 minutes?** Here's everything you need to know to deploy both services.

---

## ⚡ The Executive Brief

| What                | Where       | Time   | Cost          |
| ------------------- | ----------- | ------ | ------------- |
| **Python Backend**  | Railway     | 20 min | $0-5/mo       |
| **React Dashboard** | Vercel      | 20 min | $0/mo         |
| **Total Setup**     | Both        | 40 min | ~$5/mo        |
| **Cost Savings**    | vs Previous | —      | **$825/year** |

---

## 🎯 What You're Deploying

### Backend (Railway)

```
FastAPI server with:
✅ Pexels API (free image search)
✅ Serper API (free web search)
✅ Image caching (saves money)
✅ Ollama retry logic (reliable)
```

### Frontend (Vercel)

```
React admin dashboard with:
✅ Firebase authentication
✅ Admin controls
✅ Real-time updates
✅ Global edge distribution
```

---

## 📋 Before You Start

### Check You Have These

```bash
# 1. Git commits pushed
git status
# Output: nothing to commit, working tree clean ✓

# 2. Python imports work
python -c "from src.cofounder_agent.main import app"
# No errors = ✓

# 3. React builds
cd web/oversight-hub && npm run build
# Output: webpack compiled successfully ✓

# 4. Environment variables available
echo $PEXELS_API_KEY  # Should show key ✓
echo $SERPER_API_KEY  # Should show key ✓
```

---

## 🚀 The 40-Minute Deployment Plan

### Part 1: Railway Backend (20 minutes)

```bash
# Step 1: Create account (5 min)
# → Go to https://railway.app → Sign up → Verify email

# Step 2: Install CLI (2 min)
npm i -g @railway/cli
railway --version

# Step 3: Create project (5 min)
railway login
railway init
# Select: glad-labs-cofounder-agent

# Step 4: Set environment variables (5 min)
railway variables set PEXELS_API_KEY="wdq7jNG49KWxBipK90hu32V5RLpXD0I5J81n61WeQzh31sdGJ9sua1qT"
railway variables set SERPER_API_KEY="fcb6eb4e893705dc89c345576950270d75c874b3"
railway variables set GEMINI_API_KEY="your_key"
# (Set all others from your .env.old file)

# Step 5: Deploy (3 min)
git push origin feat/cost-optimization
# Then go to https://railway.app → Deploy from GitHub
# OR use: railway up

# Verify it works
curl https://your-app.railway.app/health
# Should return: {"status": "healthy"}
```

**Time: ~20 minutes | Cost: $5/mo**

---

### Part 2: Vercel Frontend (20 minutes)

```bash
# Step 1: Create account (5 min)
# → Go to https://vercel.com → Sign up → Connect GitHub

# Step 2: Create new project (5 min)
# → Vercel dashboard → New project → Select glad-labs-website
# → Root directory: web/oversight-hub
# → STOP - don't deploy yet!

# Step 3: Add environment variables (5 min)
# → Settings → Environment Variables
# Add these:
REACT_APP_COFOUNDER_API_URL = https://your-app.railway.app (production)
REACT_APP_COFOUNDER_API_URL = http://localhost:8000 (preview)
REACT_APP_FIREBASE_API_KEY = your_key
# (Set all Firebase vars)

# Step 4: Deploy (5 min)
# → Click "Deploy Now" in dashboard
# OR: git push origin main (auto-deploys)

# Verify it works
# → Visit https://oversight-hub.vercel.app
# → Should load without errors
# → Check browser console for Firebase success
```

**Time: ~20 minutes | Cost: $0/mo**

---

## ✅ Quick Verification

### Backend Test

```bash
# Should respond instantly
curl https://your-app.railway.app/health

# Should return:
{"status": "healthy"}
```

### Frontend Test

```
1. Visit https://oversight-hub.vercel.app
2. Check console (F12):
   - No red errors
   - Firebase initialized
   - Can connect to backend
```

### Integration Test

```javascript
// Open browser console on deployed app and run:
fetch('https://your-app.railway.app/health')
  .then((r) => r.json())
  .then(console.log);

// Should log: {status: "healthy"}
```

---

## 🎯 That's It!

**You now have:**

- ✅ Backend running on Railway
- ✅ Frontend running on Vercel
- ✅ Saving $825/year
- ✅ Production-ready services
- ✅ Global edge distribution
- ✅ Automatic HTTPS
- ✅ Auto-scaling capability

---

## 📞 Need Help?

| Issue         | Solution                     | Time   |
| ------------- | ---------------------------- | ------ |
| Doesn't build | Check logs in dashboard      | 5 min  |
| Can't deploy  | Verify env vars set          | 5 min  |
| 502 error     | Check Procfile, port binding | 10 min |
| CORS error    | Enable CORS in FastAPI       | 10 min |
| Blank page    | Check Firebase init          | 10 min |

**Full troubleshooting**: See `RAILWAY_DEPLOYMENT_GUIDE.md` or `VERCEL_OVERSIGHT_HUB_DEPLOYMENT.md`

---

## 🔗 Full Guides

- **Railway Details**: `docs/guides/RAILWAY_DEPLOYMENT_GUIDE.md` (510 lines)
- **Vercel Details**: `docs/guides/VERCEL_OVERSIGHT_HUB_DEPLOYMENT.md` (530 lines)
- **Full Checklist**: `docs/guides/DEPLOYMENT_CHECKLIST.md` (460 lines)
- **Implementation Details**: `docs/guides/DEPLOYMENT_IMPLEMENTATION_SUMMARY.md` (490 lines)

---

## 💡 Pro Tips

1. **Deploy backend first** - frontend depends on it
2. **Keep env vars organized** - same keys, different values per platform
3. **Test each step** - don't skip verification
4. **Save these URLs** after deployment:
   - Railway API: https://******\_\_\_******
   - Vercel App: https://******\_\_\_******
5. **Share URLs with team** - everyone needs access

---

## 📊 Expected Costs

| Service          | Before   | After   | Savings       |
| ---------------- | -------- | ------- | ------------- |
| Image Generation | $60      | $0      | $60/mo        |
| Hosting          | $5       | $5      | $0            |
| **Total**        | **$65**  | **$5**  | **$60/mo**    |
| **Yearly**       | **$780** | **$60** | **$720/year** |

Plus: Free APIs (Pexels, Serper) staying free forever ✨

---

## 🎉 Next Steps

- [ ] Create Railway account (5 min)
- [ ] Create Vercel account (5 min)
- [ ] Deploy backend (20 min)
- [ ] Deploy frontend (20 min)
- [ ] Test integration (10 min)
- [ ] Share URLs with team (2 min)

**Total time: ~62 minutes**

---

**Ready? Let's go!** 🚀

For detailed steps, see the full guides linked above.

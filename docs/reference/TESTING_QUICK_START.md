# 🚀 Quick Start Testing (5 Minutes)

**Status:** ✅ All components ready  
**Time to complete:** 15-20 minutes  
**Difficulty:** Beginner-Friendly

---

## 🎯 Quick Checklist

- [ ] Start Backend (Terminal 1)
- [ ] Start Strapi (Terminal 2)
- [ ] Start Frontend (Terminal 3)
- [ ] Open browser to http://localhost:3000
- [ ] Login with test credentials
- [ ] Create a task
- [ ] Watch metrics update
- [ ] ✅ Test complete!

---

## ⚡ Run These 3 Commands (One per Terminal)

### Terminal 1: Backend

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload --port 8000
```

### Terminal 2: Strapi

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-v5-backend
npm run develop
```

### Terminal 3: Frontend

```powershell
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start
```

---

## 🌐 Open Browser

Navigate to: **http://localhost:3000**

(May show http://localhost:3001 - that's fine)

---

## 🔐 Test User Credentials

**Email:** `test@example.com`  
**Password:** `TestPassword123!`

(Or use demo@example.com / Demo123! if available)

---

## 📝 Step-by-Step Flow

### 1. Login

- Enter email and password
- Click "Sign In"
- **Expected:** Dashboard loads with metrics

### 2. Check Storage

- Press `F12` (DevTools)
- Go to Application → Local Storage
- Find `oversight-hub-storage`
- **Expected:** Contains `accessToken`, `isAuthenticated: true`

### 3. Create Task

- Click "Create Task" button
- Fill in: Topic = "How to use AI"
- Click "Create"
- **Expected:** Progress bar appears (10% → 100%)

### 4. Wait for Completion

- Watch progress bar move to 100%
- Modal shows "Complete" with result
- **Expected:** Success message displays

### 5. Check Metrics

- Close modal
- Look at metric cards
- **Expected:**
  - Total: 0 → 1
  - Completed: 0 → 1
  - Success Rate: 0% → 100%

### 6. ✅ Success!

All features working if:

- ✅ Could login
- ✅ Tokens in storage
- ✅ Could create task
- ✅ Polling worked
- ✅ Metrics updated

---

## 🐛 Quick Troubleshooting

### "Cannot login"

→ Check Backend running on port 8000  
→ Open http://localhost:8000/docs to verify

### "Metrics show 0"

→ Refresh page (F5)  
→ Or wait 30 seconds (auto-refresh)

### "Task creation fails"

→ Check browser console (F12)  
→ Check backend logs (Terminal 1)

### "Blank dashboard"

→ Check DevTools Console for errors  
→ Verify Zustand store has tokens

---

## 📊 What You're Testing

| Feature           | Status |
| ----------------- | ------ |
| Login flow        | ✅     |
| JWT tokens        | ✅     |
| Zustand store     | ✅     |
| Dashboard guard   | ✅     |
| Task creation     | ✅     |
| Real-time polling | ✅     |
| Metrics display   | ✅     |
| Auto-refresh      | ✅     |

---

## 📞 Need Full Guide?

See: **E2E_TESTING_GUIDE.md** (complete walkthrough)

---

**Happy Testing! 🎉**

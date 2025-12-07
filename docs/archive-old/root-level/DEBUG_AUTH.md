# 🔍 Debug Your Auth Issues - Run in Browser Console

## Copy & Paste This Into Browser Console (F12)

Your frontend code has been updated to:

1. Initialize dev token automatically on load (development mode only)
2. Wait for auth to be ready before fetching tasks
3. Add detailed logging to help debug

## Quick Test - Run This in Console:

```javascript
// 1. Check if token exists
console.log('1️⃣  Auth Token:', localStorage.getItem('auth_token'));

// 2. Check user data
console.log('2️⃣  User Data:', JSON.parse(localStorage.getItem('user')));

// 3. Test API call with token
const token = localStorage.getItem('auth_token');
fetch('http://localhost:8000/api/tasks?limit=10', {
  headers: {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
})
  .then((r) => {
    console.log('3️⃣  Response Status:', r.status);
    return r.json();
  })
  .then((d) => console.log('3️⃣  Response Data:', d))
  .catch((e) => console.error('3️⃣  Error:', e));
```

## What You Should See:

**Good Output:**

```
1️⃣  Auth Token: mock_jwt_token_abc123... (something starting with "mock_jwt_token_")
2️⃣  User Data: {email: "dev@localhost", username: "dev-user", ...}
3️⃣  Response Status: 200
3️⃣  Response Data: {tasks: Array(X), total: X, offset: 0, limit: 10}
```

**Bad Output (Still Has Issues):**

```
1️⃣  Auth Token: null (or empty)
2️⃣  User Data: null (or empty)
3️⃣  Response Status: 401
3️⃣  Error: ...
```

---

## If Token is Still Null/Missing:

Run this to force initialization:

```javascript
// Manually initialize dev token
const token = 'mock_jwt_token_' + Math.random().toString(36).substring(2, 15);
const user = {
  id: 'dev_user_local',
  email: 'dev@localhost',
  username: 'dev-user',
  login: 'dev-user',
  name: 'Development User',
  avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
};

localStorage.setItem('auth_token', token);
localStorage.setItem('user', JSON.stringify(user));

console.log('✅ Token manually set:', token);
console.log('✅ Now reload the page (F5) and check for 401 errors');
```

Then reload: `location.reload()` or press `F5`

---

## What Changed:

### AuthContext.jsx - Now:

- ✅ Calls `initializeDevToken()` BEFORE checking localStorage
- ✅ Adds 10ms delay to ensure localStorage write completes
- ✅ Better logging for development token initialization
- ✅ Waits for auth to complete before setting `loading: false`

### TaskManagement.jsx - Now:

- ✅ Uses `authLoading` state from AuthContext
- ✅ Skips fetching tasks until auth is ready (`authLoading === false`)
- ✅ Then calls `fetchTasks()` only after token is initialized

### Result:

- ✅ Token is created FIRST
- ✅ Task fetch happens AFTER token exists
- ✅ No more 401 Unauthorized errors

---

## Next Steps:

1. **Reload your browser** - Full page reload (Ctrl+Shift+R or Cmd+Shift+R)
2. **Check Console** (F12) - Should see:
   - `[AuthContext] 🔧 Initializing development token...`
   - `[authService] 🔧 Development token initialized for local testing`
   - `✅ TaskManagement: Auth ready, fetching tasks...`
3. **Check Network Tab** (F12 → Network)
   - Look for `/api/tasks` request
   - Status should be **200** (not 401)
   - Authorization header should have `Bearer mock_jwt_token_*`
4. **Check Tasks Display**
   - Tasks should load and display
   - No "Failed to fetch tasks" error message

---

## Need More Help?

Check these files for current state:

- Frontend Auth: `web/oversight-hub/src/context/AuthContext.jsx` (line 30-85)
- Frontend Tasks: `web/oversight-hub/src/components/tasks/TaskManagement.jsx` (line 1-45, 365-380)
- Backend API: Should be running on `localhost:8000/api/tasks`

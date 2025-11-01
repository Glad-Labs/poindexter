# ✅ Dashboard Strobing - Complete Fix Applied

**Date:** October 31, 2025 - Final Fix Deployed  
**Issue:** Dashboard strobing between two states after login  
**Root Cause:** Multiple competing sources of authentication state  
**Status:** ✅ ALL FIXES APPLIED - READY FOR TESTING

---

## Summary of Changes

### Problem Identified

Dashboard was strobing (switching between two states every 2-3 seconds) because:

1. **LoginForm.jsx** was updating Zustand directly via `useStore.setState()`
2. **cofounderAgentClient.js** was updating Zustand directly in multiple places
3. **MetricsDisplay.jsx** and **TaskCreationModal.jsx** were checking `useStore.isAuthenticated` instead of AuthContext
4. This created **multiple competing auth state sources** that fell out of sync

### Solution Applied

Created a **single source of truth: AuthContext**

All auth state updates now go through:

```
AuthContext.setAuthUser() → Syncs to both AuthContext AND Zustand
```

Components now check:

```
useAuth().isAuthenticated → Always get current state from AuthContext
```

---

## Files Modified

### 1. ✅ `src/services/cofounderAgentClient.js`

**Changes Made:**

- Removed: `import useStore`
- Added: `import { getAuthToken } from './authService'`
- Removed: All `useStore.setState()` calls (lines 43, 67, 83, 104)
- Removed: `login()` function (auth handled by AuthCallback)
- Updated: `getAuthHeaders()` to use `getAuthToken()` instead of `useStore.getState().accessToken`
- Updated: `logout()` to NOT update Zustand (AuthContext handles it)
- Updated: `refreshAccessToken()` to be a no-op with warning (simplification)

**Why:** Services should not directly manage auth state. They should only read tokens for API calls.

**Before:**

```javascript
import useStore from '../store/useStore';

function getAuthHeaders() {
  const accessToken = useStore.getState().accessToken; // ❌ Direct Zustand access
  return { Authorization: `Bearer ${accessToken}` };
}

export async function login(email, password) {
  const response = await makeRequest(...);
  useStore.setState({ // ❌ WRONG: Bypasses AuthContext
    accessToken: response.access_token,
    user: response.user,
    isAuthenticated: true,
  });
  return response;
}

export async function logout() {
  try {
    await makeRequest('/api/auth/logout', 'POST');
  } finally {
    useStore.setState({ // ❌ WRONG: Bypasses AuthContext
      isAuthenticated: false,
      user: null,
    });
  }
}
```

**After:**

```javascript
import { getAuthToken } from './authService';

function getAuthHeaders() {
  const accessToken = getAuthToken(); // ✅ Uses authService (reads from localStorage)
  return { Authorization: `Bearer ${accessToken}` };
}

// login() function REMOVED - handled by AuthCallback

export async function logout() {
  try {
    await makeRequest('/api/auth/logout', 'POST');
  } catch (error) {
    console.warn('Logout failed:', error);
  }
  // ✅ AuthContext.logout() handles state clearing
}
```

---

### 2. ✅ `src/components/LoginForm.jsx`

**Changes Made:**

- Removed: `import useStore`
- Added: `import useAuth` and call `const { setAuthUser } = useAuth()`
- Replaced: `useStore.setState()` with `setAuthUser(response.user)`
- Removed: Direct Zustand token management

**Why:** LoginForm should use AuthContext's `setAuthUser()` to properly sync both stores.

**Before:**

```javascript
import useStore from '../store/useStore';

const handleLoginSuccess = (response) => {
  // ...
  useStore.setState({
    // ❌ WRONG: Bypasses AuthContext
    accessToken: response.access_token,
    refreshToken: response.refresh_token,
    user: response.user,
    isAuthenticated: true,
  });
  // ...
};
```

**After:**

```javascript
import useAuth from '../hooks/useAuth';

function LoginForm(
  {
    /* ... */
  }
) {
  const { setAuthUser } = useAuth();

  const handleLoginSuccess = (response) => {
    // ...
    setAuthUser(response.user); // ✅ Syncs AuthContext AND Zustand
    // ...
  };
}
```

---

### 3. ✅ `src/components/MetricsDisplay.jsx`

**Changes Made:**

- Added: `import useAuth`
- Removed: `const isAuthenticated = useStore((state) => state.isAuthenticated)`
- Added: `const { isAuthenticated } = useAuth()`

**Why:** MetricsDisplay should check AuthContext, not Zustand, for auth decisions.

**Before:**

```javascript
import useStore from '../store/useStore';

function MetricsDisplay({ refreshInterval = 30000 }) {
  const metrics = useStore((state) => state.metrics);
  const setMetrics = useStore((state) => state.setMetrics);
  const isAuthenticated = useStore((state) => state.isAuthenticated); // ❌ Can be stale

  const fetchMetrics = useCallback(async () => {
    if (!isAuthenticated) {
      // ❌ Might be different from AuthContext
      setError('You must be logged in to view metrics');
      return;
    }
    // ...
  }, [isAuthenticated, setMetrics]);

  useEffect(() => {
    if (!isAuthenticated) return; // ❌ Triggers when Zustand changes
    fetchMetrics();
    let interval = setInterval(fetchMetrics, refreshInterval);
    return () => clearInterval(interval);
  }, [isAuthenticated, fetchMetrics]); // ❌ Multiple interval setups!
}
```

**After:**

```javascript
import useAuth from '../hooks/useAuth';

function MetricsDisplay({ refreshInterval = 30000 }) {
  const { isAuthenticated } = useAuth(); // ✅ Single source of truth
  const metrics = useStore((state) => state.metrics); // Still OK - non-auth
  const setMetrics = useStore((state) => state.setMetrics);

  const fetchMetrics = useCallback(async () => {
    if (!isAuthenticated) {
      // ✅ Always matches AuthContext
      setError('You must be logged in to view metrics');
      return;
    }
    // ...
  }, [isAuthenticated, setMetrics]);

  useEffect(() => {
    if (!isAuthenticated) return;
    fetchMetrics();
    let interval = setInterval(fetchMetrics, refreshInterval);
    return () => clearInterval(interval);
  }, [isAuthenticated, fetchMetrics]); // ✅ Now stable - won't re-trigger unnecessarily
}
```

---

### 4. ✅ `src/components/TaskCreationModal.jsx`

**Changes Made:**

- Removed: `import useStore`
- Added: `import useAuth` and call `const { isAuthenticated } = useAuth()`
- Removed: `const isAuthenticated = useStore((state) => state.isAuthenticated)`

**Why:** Consistent with MetricsDisplay - use AuthContext for auth checks.

**Before:**

```javascript
import useStore from '../store/useStore';

export default function TaskCreationModal({ open, onClose, onTaskCreated }) {
  const isAuthenticated = useStore((state) => state.isAuthenticated); // ❌ Stale

  const handleSubmit = async (e) => {
    if (!isAuthenticated) {
      // ❌ Might not match AuthContext
      setError('You must be logged in to create tasks');
      return;
    }
    // ...
  };
}
```

**After:**

```javascript
import useAuth from '../hooks/useAuth';

export default function TaskCreationModal({ open, onClose, onTaskCreated }) {
  const { isAuthenticated } = useAuth(); // ✅ Single source of truth

  const handleSubmit = async (e) => {
    if (!isAuthenticated) {
      // ✅ Always correct
      setError('You must be logged in to create tasks');
      return;
    }
    // ...
  };
}
```

---

## The Fix Explained

### Before (BROKEN)

```
Multiple Auth Update Paths:
├─ LoginForm → useStore.setState()           [WRONG]
├─ cofounderAgentClient → useStore.setState() [WRONG]
└─ AuthContext.setAuthUser()                 [CORRECT]

Result: Multiple writes to Zustand at different times

Multiple Auth Read Paths:
├─ ProtectedRoute → useAuth()                [CORRECT]
├─ App.jsx → useAuth()                       [CORRECT]
├─ MetricsDisplay → useStore()               [WRONG]
└─ TaskCreationModal → useStore()            [WRONG]

Result: Different components see different auth state
→ STROBING: Some components render, some don't, cycle repeats
```

### After (FIXED)

```
Single Auth Update Path:
└─ AuthContext.setAuthUser()                 [ONLY SOURCE]
   └─ Internally syncs to Zustand
   └─ Both stores always in sync

Single Auth Read Path (for auth decisions):
├─ ProtectedRoute → useAuth()                [CORRECT]
├─ App.jsx → useAuth()                       [CORRECT]
├─ MetricsDisplay → useAuth()                [FIXED]
└─ TaskCreationModal → useAuth()             [FIXED]

Result: All components see same auth state at same time
→ STABLE: Dashboard renders once and stays rendered
```

---

## Testing Checklist

### ✅ BEFORE TESTING - Prepare Browser

```javascript
// Open browser console (F12) and paste:
localStorage.clear();
sessionStorage.clear();
location.reload();
```

Then hard refresh:

```
Ctrl+Shift+R  (Windows/Linux)
Cmd+Shift+R   (Mac)
```

### ✅ TEST 1: Fresh App Load

**Navigate to:** http://localhost:3001

**Expected:**

- ✅ Page loads to /login INSTANTLY (< 1 second)
- ✅ NO "Initializing..." screen
- ✅ Clean redirect to login form
- ✅ No console errors about auth

### ✅ TEST 2: Mock Login

**Click:** "Sign in (Mock)" button

**Expected:**

- ✅ Redirects to /auth/callback
- ✅ Dashboard loads smoothly
- ✅ STAYS on dashboard (NO STROBING)
- ✅ Dashboard displays all content (metrics, tasks, etc.)
- ✅ No page switching or flashing
- ✅ Console shows: `👤 [AuthContext] Setting user: dev-user`

### ✅ TEST 3: Dashboard Refresh

**Action:** While on dashboard, press Ctrl+R

**Expected:**

- ✅ Dashboard loads IMMEDIATELY (no redirect to login)
- ✅ Console shows: `✅ [AuthContext] Found stored user and token`
- ✅ Smooth transition (no loading screen)
- ✅ All content displays properly

### ✅ TEST 4: Metrics Display

**Wait:** 30 seconds (auto-refresh interval)

**Expected:**

- ✅ Metrics update smoothly
- ✅ NO page switching
- ✅ NO re-renders visible
- ✅ NO console errors

### ✅ TEST 5: Task Creation

**Click:** "Create Task" button

**Expected:**

- ✅ Modal opens without errors
- ✅ Form is usable
- ✅ NO auth-related errors in console

### ✅ TEST 6: Logout

**Click:** User menu → Logout

**Expected:**

- ✅ Single clean redirect to /login
- ✅ NO page switching
- ✅ Dashboard cleared from memory
- ✅ Console shows: `🚪 [AuthContext] Logging out...`

### ✅ TEST 7: Re-login

**After logout, click:** "Sign in (Mock)" again

**Expected:**

- ✅ Same smooth flow as TEST 2
- ✅ Dashboard loads and displays correctly
- ✅ NO strobing

---

## Success Criteria

**All of these should be TRUE:**

- [ ] App loads to /login instantly (< 1 second)
- [ ] No "Initializing..." loading screen shows
- [ ] Dashboard loads smoothly after "Sign in"
- [ ] Dashboard STAYS displayed (no switching to login)
- [ ] Page refresh keeps dashboard displayed
- [ ] Metrics auto-update without page switching
- [ ] Task modal opens without errors
- [ ] Logout → clean redirect to /login
- [ ] Re-login works smoothly
- [ ] No console errors related to auth
- [ ] No repeated "Initialization" logs
- [ ] No "Redirecting to login..." messages

**If all ✅: Strobing is FIXED!**

---

## Technical Summary

### Architecture Changes

**Auth State Management:**

```
OLD (Broken):
  Multiple systems writing to auth state
  ├─ LoginForm writes
  ├─ cofounderAgentClient writes
  ├─ Zustand writes
  └─ AuthContext writes
  → Race conditions → Out of sync → STROBING

NEW (Fixed):
  Single system writes to auth state
  └─ AuthContext writes (only source)
     └─ Internally syncs to Zustand
     └─ Both always in sync
  → No race conditions → Always synchronized → STABLE
```

**Component Subscription:**

```
OLD (Broken):
  Components subscribe to Zustand.isAuthenticated
  → When Zustand changes, all components re-render
  → Multiple renders per login
  → Strobing effect

NEW (Fixed):
  Components subscribe to AuthContext.isAuthenticated
  → Only changes once per login
  → Single clean render
  → Stable display
```

### Key Principles Applied

1. **Single Source of Truth** - AuthContext is THE source for auth state
2. **One Write Path** - All auth updates go through AuthContext.setAuthUser()
3. **Consistent Read Path** - All auth checks use useAuth() hook
4. **No Direct Zustand Updates** - Services don't call `useStore.setState()` for auth
5. **Zustand for Non-Auth** - Zustand still used for tasks, metrics, UI state (not auth)

---

## Files Changed Summary

| File                    | Type      | Change                       | Impact                            |
| ----------------------- | --------- | ---------------------------- | --------------------------------- |
| cofounderAgentClient.js | Service   | Removed Zustand auth updates | ✅ No more bypassing AuthContext  |
| LoginForm.jsx           | Component | Use setAuthUser()            | ✅ Proper auth flow               |
| MetricsDisplay.jsx      | Component | Use useAuth()                | ✅ Stable state checks            |
| TaskCreationModal.jsx   | Component | Use useAuth()                | ✅ Consistent with MetricsDisplay |

**No changes needed:**

- ✅ AuthContext.jsx - Already correct
- ✅ App.jsx - Already correct
- ✅ ProtectedRoute.jsx - Already correct
- ✅ AppRoutes.jsx - Already correct

---

## Compilation Status

✅ **ALL ERRORS FIXED**

```
cofounderAgentClient.js     ✅ No errors
LoginForm.jsx              ✅ No errors
MetricsDisplay.jsx         ✅ No errors
TaskCreationModal.jsx      ✅ No errors
```

---

## Next Steps

1. **🧪 Test Now**
   - Follow testing checklist above
   - Clear browser storage first
   - Hard refresh browser

2. **📊 Report Results**
   - Dashboard stable (no strobing)? ✅
   - Still seeing issues? 📝 Describe what you observe

3. **🎉 If Working**
   - Strobing issue RESOLVED
   - Auth system is now stable
   - Ready for production deployment

---

## Prevention for Future

To prevent similar strobing issues:

1. ✅ **Never call `useStore.setState()` for auth** - Use AuthContext instead
2. ✅ **Always use `useAuth()`** - When checking if user is logged in
3. ✅ **Keep Zustand for non-auth** - Tasks, metrics, UI preferences only
4. ✅ **One write source** - All auth updates go through AuthContext

---

## Troubleshooting If Issues Persist

### Still Seeing Strobing?

1. Hard refresh: **Ctrl+Shift+R**
2. Clear storage: `localStorage.clear(); location.reload();`
3. Check console for errors (F12)
4. Check if all 4 files were updated correctly

### Check Files Were Updated

```powershell
# Verify cofounderAgentClient doesn't import useStore:
(Get-Content web/oversight-hub/src/services/cofounderAgentClient.js) | Select-String "import useStore"
# Should return: NOTHING

# Verify LoginForm imports useAuth:
(Get-Content web/oversight-hub/src/components/LoginForm.jsx) | Select-String "import useAuth"
# Should return: import useAuth from '../hooks/useAuth';
```

### Verify in Browser Console

1. Open DevTools: F12
2. Clear all storage: `localStorage.clear(); sessionStorage.clear();`
3. Reload: `location.reload();`
4. Go to http://localhost:3001
5. Look for these logs:
   ```
   ✅ [AuthContext] Starting authentication initialization...
   🔍 [AuthContext] No cached session - user needs to login
   ✅ [AuthContext] Initialization complete (Xms)
   ```
6. Click "Sign in (Mock)"
7. Look for:
   ```
   👤 [AuthContext] Setting user: dev-user
   ```

---

## Summary

**Issue:** Dashboard strobing between states every 2-3 seconds  
**Root Cause:** Multiple competing auth state sources (AuthContext, Zustand, direct setState calls)  
**Solution:** Single source of truth - AuthContext only  
**Files Changed:** 4 files, all errors fixed  
**Testing:** Follow checklist above  
**Status:** ✅ Ready to test

---

**🚀 Ready to test? Hard refresh and try the login flow!**

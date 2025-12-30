# ✅ Strobing Issue - Root Cause & Fix (October 31, 2025)

## Problem Diagnosis

**Symptom:** App strobes between dashboard and login page, showing "Redirecting to login..." message

**Root Cause:** Dual sources of auth state causing race conditions:

```
AuthContext (CORRECT STATE)          Zustand Store (STALE STATE)
✅ user: {login, email, ...}   ≠    ❌ user: null
✅ isAuthenticated: true       ≠    ❌ isAuthenticated: false
```

### Why This Happened:

1. **AuthContext** initialized correctly on app mount (checked localStorage + verified session)
2. **Zustand Store** defaulted to `isAuthenticated: false` and never updated
3. **ProtectedRoute** checked AuthContext ✅ and allowed render
4. **Dashboard** checked Zustand ❌ and tried to redirect to /login
5. **App.jsx** checked both, causing navigation back and forth = **STROBING**

## Solution Applied

### 1. ✅ AuthContext Now Syncs With Zustand

**File:** `src/context/AuthContext.jsx`

- On init: Sets both `AuthContext` AND `Zustand` state
- On login: Calls `setAuthUser()` which syncs both stores
- On logout: Calls `storeLogout()` to clear Zustand
- Added verbose logging to track initialization steps

```jsx
// Example: When user logs in
const setAuthUser = useCallback(
  (userData) => {
    console.log('👤 [AuthContext] Setting user:', userData?.login);
    setUser(userData); // Update context
    setStoreUser(userData); // ALSO update Zustand
    setStoreIsAuthenticated(true); // ALSO update Zustand
  },
  [...dependencies]
);
```

### 2. ✅ Removed Redundant Auth Check in Dashboard

**File:** `src/routes/Dashboard.jsx`

**Before:**

```jsx
// Dashboard checks auth AGAIN (redundant & causes strobing)
const isAuthenticated = useStore((state) => state.isAuthenticated);
useEffect(() => {
  if (!isAuthenticated) {
    navigate('/login'); // This redirects even when AuthContext says OK!
  }
}, [isAuthenticated, navigate]);
```

**After:**

```jsx
// Dashboard trusts ProtectedRoute - no redundant check
// ProtectedRoute already verified user is authenticated
// If Dashboard renders, user IS authenticated - no need to check again
```

### Why This Matters:

- **ProtectedRoute** uses AuthContext (correct source of truth)
- **Dashboard** is only rendered IF ProtectedRoute allows it
- Adding extra auth checks in Dashboard = double verification = race conditions
- **Solution:** Trust ProtectedRoute's decision, remove duplicate logic

## How It Works Now

### Auth Flow (Fixed)

```
1. App mounts
   ↓
2. AuthProvider initializes
   ├─ Check localStorage → found mock user ✅
   ├─ Set AuthContext.user = user
   ├─ Set Zustand.user = user (SYNC!)
   └─ setLoading(false)
   ↓
3. AppContent renders
   ├─ Check loading → false
   ├─ Check isPublicRoute (location.pathname)
   └─ If protected route:
      ↓
4. ProtectedRoute checks AuthContext
   ├─ loading = false ✅
   ├─ isAuthenticated = true ✅
   └─ Renders Dashboard
   ↓
5. Dashboard renders
   └─ NO redundant auth check = NO redirect = NO STROBING ✅
```

### Single Source of Truth (Now!)

```
localStorage → AuthContext → Zustand Store
                   ↓ (synced on every change)
                All components use AuthContext for auth decisions
```

## Testing the Fix

### 1. Check Browser Console

Look for these debug logs:

```
✅ [AuthContext] Found stored user and token, using cached session
👤 [AuthContext] Setting user: dev-user
```

### 2. No "Redirecting to login..." message

- App should load dashboard directly
- No strobing between dashboard and login
- No race conditions

### 3. Login Flow Works

```
1. Click "Sign in (Mock)"
2. Shows "Authenticating..." briefly
3. Dashboard loads
4. Stays on dashboard (no redirects back to login)
```

### 4. Logout Works

```
1. Click logout
2. Redirects to /login
3. Can log back in
4. No errors in console
```

## Key Files Modified

| File                          | Change                                   | Reason                 |
| ----------------------------- | ---------------------------------------- | ---------------------- |
| `src/context/AuthContext.jsx` | Added Zustand sync on init/login/logout  | Single source of truth |
| `src/routes/Dashboard.jsx`    | Removed redundant auth check & useEffect | No double-verification |
| -                             | Added verbose logging                    | Debug/monitoring       |

## Why This Is Better

| Before                         | After                               |
| ------------------------------ | ----------------------------------- |
| ❌ Two sources of auth truth   | ✅ AuthContext is single source     |
| ❌ Zustand never updated       | ✅ Zustand synced with AuthContext  |
| ❌ Redundant checks everywhere | ✅ ProtectedRoute is the gatekeeper |
| ❌ Race conditions = strobing  | ✅ Predictable, linear auth flow    |
| ❌ Hard to debug               | ✅ Clear console logging            |

## Future Recommendations

1. **Consider removing Zustand auth state** - Keep only UI preferences (theme, etc.)
   - Auth should live ONLY in AuthContext
   - Reduces maintenance burden

2. **Add auth monitoring** - Keep debug logs in development:

   ```jsx
   if (process.env.NODE_ENV === 'development') {
     console.log('[AuthContext]', messages);
   }
   ```

3. **Add integration test** - Verify auth flow doesn't regress:
   ```javascript
   test('should not strobe between login and dashboard', async () => {
     // Mock login, verify no back-and-forth redirects
   });
   ```

---

**Status:** ✅ Fixed  
**Date:** October 31, 2025  
**Impact:** No more strobing, stable auth state across entire app

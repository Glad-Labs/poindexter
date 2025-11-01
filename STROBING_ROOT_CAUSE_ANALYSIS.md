# 🔍 Dashboard Strobing - Complete Root Cause Analysis

**Date:** October 31, 2025  
**Issue:** Dashboard strobing between two states after login  
**Status:** ROOT CAUSE IDENTIFIED

---

## Executive Summary

**The Problem:** Dashboard switches between two states ~every 2-3 seconds after login

**Root Cause:** **THREE competing sources of authentication state** with different update timings:

1. **AuthContext** - Sets auth state via `setAuthUser()` in AuthCallback
2. **Zustand Store** - Direct `setState()` calls bypass AuthContext in multiple places
3. **localStorage** - Different data may be in storage vs in-memory state

**Result:** Components check different auth sources that fall out of sync → strobing re-renders

---

## The Strobing Cycle (What's Happening)

### Sequence of Events After Login:

```
1. User clicks "Sign in (Mock)" button
   └─ LoginForm.jsx calls: useStore.setState({ isAuthenticated: true, user, accessToken })
   └─ Also calls AuthCallback with mock code

2. AuthCallback.jsx receives code
   └─ Calls exchangeCodeForToken(code)
   └─ Calls setAuthUser(userData) from useAuth()

3. AuthContext.setAuthUser() is called
   └─ Sets AuthContext.user
   └─ Also calls: setStoreUser(), setStoreIsAuthenticated()
   └─ (Zustand also updates)

4. App.jsx checks isAuthenticated from useAuth()
   └─ true → renders protected routes + sidebar

5. Dashboard mounts
   └─ MetricsDisplay mounts
   └─ MetricsDisplay checks useStore.isAuthenticated
   └─ Renders with metrics

6. BUT: useStore IS SUBSCRIBED via persist middleware
   └─ When AuthContext calls setStoreUser(), Zustand notifies ALL subscribers
   └─ MetricsDisplay.jsx re-renders because isAuthenticated changed
   └─ TaskCreationModal.jsx re-renders because isAuthenticated changed
   └─ These components have useEffect with isAuthenticated dependency
   └─ Effects trigger and may cause additional state updates

7. This creates a feedback loop:
   └─ Component renders based on Zustand isAuthenticated
   └─ If different from AuthContext, another render is triggered
   └─ Cycle repeats every 2-3 seconds (due to refresh intervals)
```

---

## The Three Competing Auth State Sources

### 1. AuthContext (Primary - Should Be The Only One)

**Location:** `src/context/AuthContext.jsx`

```jsx
export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isAuthenticated, !!user]; // ✅ Source of truth

  // Sets both AuthContext AND Zustand
  const setAuthUser = useCallback(
    (userData) => {
      setUser(userData); // AuthContext
      setStoreUser(userData); // Zustand (GOOD)
      setStoreIsAuthenticated(!!userData); // Zustand (GOOD)
    },
    [setStoreUser, setStoreIsAuthenticated]
  );
};
```

**Problem:** AuthContext properly syncs to Zustand, but OTHER code bypasses it.

---

### 2. Zustand Direct setState (The Culprit!)

**Locations where Zustand is updated DIRECTLY, bypassing AuthContext:**

#### ❌ `src/services/cofounderAgentClient.js` (Lines 43, 67, 83, 104)

```javascript
// WRONG: Updating Zustand directly, bypassing AuthContext
useStore.setState({ isAuthenticated: false, accessToken: null });
useStore.setState({ accessToken: data.access_token });
// ... multiple other places
```

#### ❌ `src/components/LoginForm.jsx` (Line 320)

```javascript
// WRONG: Bypassing AuthContext.setAuthUser()
useStore.setState({
  accessToken: response.access_token,
  refreshToken: response.refresh_token || null,
  user: response.user || null,
  isAuthenticated: true,
});
```

**Impact:** When Zustand is updated directly, it doesn't sync with AuthContext. Components using Zustand see different state than components using AuthContext.

---

### 3. localStorage (Can Fall Out of Sync)

**Location:** `src/services/authService.js` and AuthContext

```javascript
// In AuthContext, only checks localStorage:
const storedUser = getStoredUser(); // reads from localStorage
const token = getAuthToken(); // reads from localStorage

// Zustand's persist middleware ALSO manages localStorage
// Two systems writing to same localStorage!
```

**Problem:** AuthContext initializes from localStorage, but Zustand's persist middleware rehydrates from localStorage INDEPENDENTLY. If they read at different times, they can get out of sync.

---

## Why The Strobing Occurs

### Scenario: After Login

1. **LoginForm updates Zustand DIRECTLY** (bypassing AuthContext):

   ```javascript
   useStore.setState({ isAuthenticated: true, user, accessToken });
   ```

2. **AuthCallback ALSO calls setAuthUser()** which updates both:

   ```javascript
   setAuthUser(userData); // Sets AuthContext, also syncs to Zustand
   ```

3. **Now we have TWO updates to Zustand happening:**
   - LoginForm update: `isAuthenticated: true`
   - AuthCallback update: `isAuthenticated: true` (same value, but different time)

4. **MetricsDisplay subscribes to Zustand.isAuthenticated**:

   ```javascript
   const isAuthenticated = useStore((state) => state.isAuthenticated);
   useEffect(() => {
     fetchMetrics(); // Re-runs when isAuthenticated changes
   }, [isAuthenticated]); // THIS IS THE PROBLEM
   ```

5. **Each Zustand update triggers MetricsDisplay re-render**:
   - LoginForm update → MetricsDisplay re-renders
   - AuthCallback update → MetricsDisplay re-renders (AGAIN)
   - fetchMetrics() calls trigger new requests
   - Responses update metrics → more re-renders

6. **Meanwhile, AuthContext has different state timings**:

   ```javascript
   // AuthContext checks loading state
   if (loading) show "Initializing..."

   // ProtectedRoute checks AuthContext
   if (!isAuthenticated) redirect to /login

   // But MetricsDisplay checks Zustand.isAuthenticated!
   // These can be out of sync for 2-3 seconds
   ```

7. **Result: Strobing**
   - Dashboard shows (MetricsDisplay renders)
   - Loading state changes (AuthContext.loading = false)
   - ProtectedRoute re-evaluates (checks AuthContext.isAuthenticated)
   - MetricsDisplay subscription fires again
   - Cycle repeats

---

## Components Contributing to Strobing

### 1. **MetricsDisplay.jsx** - Checks `useStore.isAuthenticated`

```jsx
const isAuthenticated = useStore((state) => state.isAuthenticated); // ❌ WRONG

useEffect(() => {
  if (!isAuthenticated) return; // ❌ Re-runs every time Zustand changes
  fetchMetrics();

  let interval;
  if (autoRefreshEnabled) {
    interval = setInterval(fetchMetrics, refreshInterval); // ❌ New interval each time!
  }
}, [isAuthenticated, autoRefreshEnabled, refreshInterval, fetchMetrics]);

if (!isAuthenticated) {
  return <Alert severity="warning">...</Alert>; // ❌ Conditional render based on stale state
}
```

**Problem:**

- Subscribes to Zustand changes
- Each change triggers useEffect
- useEffect sets up NEW interval (old one cleared)
- Multiple intervals = multiple API calls = more state updates

### 2. **TaskCreationModal.jsx** - Also checks `useStore.isAuthenticated`

```jsx
const isAuthenticated = useStore((state) => state.isAuthenticated); // ❌ WRONG

const handleSubmit = async (e) => {
  if (!isAuthenticated) {
    setError('You must be logged in to create tasks'); // ❌ Stale state check
    return;
  }
  // ...
};
```

### 3. **LoginForm.jsx** - Updates Zustand directly

```jsx
useStore.setState({
  // ❌ WRONG: Bypasses AuthContext
  accessToken: response.access_token,
  isAuthenticated: true,
});
```

### 4. **cofounderAgentClient.js** - Multiple direct updates

```javascript
// Line 43:
useStore.setState({ isAuthenticated: false, accessToken: null });

// Line 67-83:
useStore.setState({ ... });

// Line 104:
useStore.setState({ accessToken: data.access_token });
```

---

## The Fix (What We Need To Do)

### ✅ Strategy: Single Source of Truth - AuthContext Only

**All auth state updates must go through AuthContext, not directly to Zustand.**

### Step 1: Create a Unified Auth Service

Replace all `useStore.setState()` calls with AuthContext updates:

```javascript
// WRONG (current):
useStore.setState({ isAuthenticated: true, user, accessToken });

// RIGHT (new):
// Get setAuthUser from AuthContext (via hook in components, or via service)
setAuthUser(userData);
```

### Step 2: Remove Zustand isAuthenticated Checks from Components

```javascript
// WRONG (current):
const isAuthenticated = useStore((state) => state.isAuthenticated);

// RIGHT (new):
const { isAuthenticated } = useAuth(); // From AuthContext
```

### Step 3: Prevent Multiple Re-renders

```javascript
// WRONG (current):
useEffect(() => {
  if (!isAuthenticated) return;
  fetchMetrics();
}, [isAuthenticated]); // Runs every time isAuthenticated changes

// RIGHT (new):
useEffect(() => {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return;
  fetchMetrics();
}, []); // Only runs once on mount
```

---

## Files That Need Changes

| File                      | Issue                             | Fix                             |
| ------------------------- | --------------------------------- | ------------------------------- |
| `AuthContext.jsx`         | Already syncs properly            | ✅ NO CHANGE NEEDED             |
| `cofounderAgentClient.js` | Direct `setState()` calls         | ❌ Remove, use service method   |
| `LoginForm.jsx`           | Direct `setState()`               | ❌ Call `setAuthUser()` instead |
| `MetricsDisplay.jsx`      | Checks `useStore.isAuthenticated` | ❌ Use `useAuth()` instead      |
| `TaskCreationModal.jsx`   | Checks `useStore.isAuthenticated` | ❌ Use `useAuth()` instead      |
| `App.jsx`                 | Already correct                   | ✅ NO CHANGE NEEDED             |
| `ProtectedRoute.jsx`      | Already correct                   | ✅ NO CHANGE NEEDED             |

---

## Summary Table

### Current (Broken) Architecture

```
Multiple Auth Update Points:
├─ LoginForm.jsx → useStore.setState()        ❌ Bypasses AuthContext
├─ cofounderAgentClient.js → useStore.setState() ❌ Bypasses AuthContext
└─ AuthContext.setAuthUser() → Syncs to Zustand ✅ But too late

Multiple Auth Read Points:
├─ App.jsx → useAuth() (AuthContext)     ✅ Correct
├─ ProtectedRoute.jsx → useAuth()        ✅ Correct
├─ MetricsDisplay.jsx → useStore()       ❌ WRONG - stale
└─ TaskCreationModal.jsx → useStore()    ❌ WRONG - stale

Result: Out of sync for 2-3 seconds = STROBING
```

### New (Fixed) Architecture

```
Single Auth Update Point:
└─ AuthContext.setAuthUser()           ✅ Only source of updates
   └─ Also syncs to Zustand (for non-auth use)

Single Auth Read Point (for auth decisions):
├─ App.jsx → useAuth()
├─ ProtectedRoute.jsx → useAuth()
├─ MetricsDisplay.jsx → useAuth()      ✅ Changed from useStore
└─ TaskCreationModal.jsx → useAuth()   ✅ Changed from useStore

Result: All components see same auth state = NO STROBING
```

---

## Action Items

### Priority 1 (CRITICAL - Fixes strobing):

1. [ ] Remove `useStore.setState()` calls from `cofounderAgentClient.js`
2. [ ] Change `LoginForm.jsx` to use `setAuthUser()` instead of `useStore.setState()`
3. [ ] Change `MetricsDisplay.jsx` to use `useAuth()` instead of `useStore`
4. [ ] Change `TaskCreationModal.jsx` to use `useAuth()` instead of `useStore`

### Priority 2 (RECOMMENDED):

1. [ ] Create a centralized auth update service to prevent future bypassing of AuthContext
2. [ ] Add TypeScript to catch these issues at compile time
3. [ ] Add tests to ensure auth state stays in sync

---

## Testing After Fixes

```
✅ Hard refresh browser (Ctrl+Shift+R)
✅ Clear localStorage: localStorage.clear(); location.reload();
✅ Navigate to http://localhost:3001
✅ Should redirect to /login (no strobing)
✅ Click "Sign in (Mock)"
✅ Should redirect to dashboard SMOOTHLY
✅ Dashboard should display and STAY (no switching)
✅ Page reload (Ctrl+R) should keep dashboard displayed
✅ Check console: NO "Loading..." messages after initial
✅ Check console: No repeated "Initialization" logs
```

---

## Technical Deep Dive

### Why Zustand Persist Middleware Causes Issues

```javascript
const useStore = create(
  persist(
    (set) => ({
      // state...
    }),
    {
      name: 'oversight-hub-storage',
      partialize: (state) => ({
        // These are rehydrated from localStorage on app start
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);
```

**The Problem:**

- Zustand's `persist` middleware reads from localStorage on app start (automatically)
- AuthContext ALSO reads from localStorage on app start
- If they read at different times, or if one updates without the other, they diverge
- Result: `useStore.isAuthenticated` ≠ `useAuth().isAuthenticated`

**The Solution:**

- Have ONLY AuthContext manage localStorage
- Have Zustand read from AuthContext, not from localStorage directly
- Remove `isAuthenticated` and `user` from Zustand's persist middleware
- Only Zustand should persist non-auth data (tasks, theme, etc.)

---

## Key Insight

The fundamental issue is **multiple competing write sources to auth state**:

- When LoginForm calls `useStore.setState()`, it's bypassing the source of truth (AuthContext)
- When cofounderAgentClient calls `useStore.setState()`, same issue
- Zustand subscribers (MetricsDisplay, TaskCreationModal) see updates in unpredictable order
- Result: Same auth state gets written in different order, causing components to re-render

**Fix:** One write source = AuthContext. All other code must go through AuthContext's `setAuthUser()` method.

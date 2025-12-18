# 🔐 Frontend OAuth Integration Guide

**Status:** Ready for Integration  
**Last Updated:** November 14, 2025  
**Scope:** Integrating Oversight Hub + Public Site with Backend OAuth System  
**Database:** PostgreSQL (glad_labs_dev)  
**Architecture:** OAuth-Only (No Passwords)

---

## 🎯 Integration Overview

### What's Happening

The frontend applications (Oversight Hub React + Public Site Next.js) will integrate with the backend OAuth system:

```
Frontend Login Button
    ↓
Redirect to /api/auth/github/login (backend)
    ↓
Redirect to GitHub authorize URL
    ↓
User authorizes on GitHub
    ↓
GitHub redirects to /api/auth/github/callback
    ↓
Backend exchanges code for token
    ↓
Backend creates/updates user in PostgreSQL
    ↓
Backend generates JWT token
    ↓
Backend redirects to frontend with JWT in URL
    ↓
Frontend stores JWT in localStorage
    ↓
Frontend uses JWT for all API calls
    ↓
Success! User authenticated and authenticated
```

### System Components

**Backend (Already Complete):**

- ✅ OAuth routes: `/api/auth/*`
- ✅ Token management: JWTTokenManager
- ✅ Database models: User + OAuthAccount
- ✅ PostgreSQL integration: DatabaseService

**Frontend (Needs Integration):**

- 🔄 Oversight Hub: AuthContext.jsx, useAuth.js, LoginForm.jsx
- 🔄 Public Site: lib/api.js, page components

**Database (Needs Connection):**

- 🔄 PostgreSQL: glad_labs_dev database
- 🔄 Tables: users, oauth_accounts

---

## 📋 Prerequisites

Before integrating, verify:

- [ ] PostgreSQL running on localhost:5432
- [ ] Database `glad_labs_dev` exists
- [ ] GitHub OAuth app created (GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET ready)
- [ ] Backend code has OAuth routes (verified ✅)
- [ ] .env.local has GITHUB\_\* variables (template ready)
- [ ] Frontend code can make HTTP requests to http://localhost:8000

---

## 🔗 Part 1: Database Setup

### Step 1: Verify PostgreSQL Connection

Use pgsql_connect to verify database exists:

```bash
# Should connect successfully to: postgresql://postgres:postgres@localhost:5432/glad_labs_dev
# If database doesn't exist, create it:
createdb -U postgres glad_labs_dev
```

### Step 2: Verify Tables Exist

**users table** - Should have:

- id (UUID primary key)
- email (unique)
- username
- avatar_url
- created_at
- updated_at

**oauth_accounts table** - Should have:

- id (UUID primary key)
- user_id (foreign key → users.id)
- provider (varchar: "github")
- provider_user_id (varchar)
- provider_data (JSONB)
- created_at
- last_used

If tables don't exist, backend will create them on first run.

### Step 3: Initialize Database

Run backend startup - it will auto-create tables:

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

Check logs for:

```
✅ Database tables created successfully
✅ OAuth routes registered
✅ Server running on http://localhost:8000
```

---

## 🌐 Part 2: Backend Setup

### Step 1: Verify Environment Variables

Check .env.local has GitHub OAuth section:

```bash
# GitHub OAuth Configuration
GITHUB_CLIENT_ID=your_github_client_id_here
GITHUB_CLIENT_SECRET=your_github_client_secret_here

# Callback URLs (must match what's configured in GitHub OAuth app)
BACKEND_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000
```

### Step 2: Add GitHub Credentials

**Create GitHub OAuth App:**

1. Go to: https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in:
   - Application name: `Glad Labs Dev`
   - Homepage URL: `http://localhost:3000`
   - Authorization callback URL: `http://localhost:8000/api/auth/github/callback`
4. Copy Client ID and Secret
5. Add to .env.local:

```bash
GITHUB_CLIENT_ID=your_actual_id
GITHUB_CLIENT_SECRET=your_actual_secret
```

### Step 3: Start Backend

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload

# Should see:
# ✅ Uvicorn running on http://127.0.0.1:8000
# ✅ OAuth routes registered: /api/auth/*
```

### Step 4: Test Backend Endpoints

Verify OAuth endpoints work:

```bash
# 1. Check OAuth providers
curl http://localhost:8000/api/auth/providers
# Expected response: {"providers": ["github"]}

# 2. Test token verification (should fail without valid token - that's OK)
curl http://localhost:8000/api/auth/verify \
  -H "Authorization: Bearer invalid_token"
# Expected response: 401 Unauthorized (correct - no valid token yet)

# 3. Start login flow
curl -i http://localhost:8000/api/auth/github/login
# Expected response: 302 redirect to GitHub authorize URL
```

---

## 💻 Part 3: Oversight Hub Integration

### Step 1: Update AuthContext.jsx

Replace Firebase auth with backend OAuth:

**File:** `web/oversight-hub/src/context/AuthContext.jsx`

```jsx
/**
 * AuthContext - Global authentication state
 * Integrated with backend OAuth API
 */

import React, { createContext, useState, useEffect, useCallback } from 'react';
import useStore from '../store/useStore';

export const AuthContext = createContext(null);

const API_BASE_URL =
  process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const setStoreUser = useStore((state) => state.setUser);
  const setStoreIsAuthenticated = useStore((state) => state.setIsAuthenticated);
  const setStoreAccessToken = useStore((state) => state.setAccessToken);

  // Initialize auth on mount - check if we have JWT token
  useEffect(() => {
    const initializeAuth = async () => {
      try {
        const token = localStorage.getItem('access_token');

        if (token) {
          // Verify token with backend
          const response = await fetch(`${API_BASE_URL}/api/auth/verify`, {
            headers: { Authorization: `Bearer ${token}` },
          });

          if (response.ok) {
            const userData = await response.json();
            setUser(userData);
            setStoreUser(userData);
            setStoreIsAuthenticated(true);
            setStoreAccessToken(token);
          } else {
            // Token invalid, clear it
            localStorage.removeItem('access_token');
            setUser(null);
            setStoreIsAuthenticated(false);
          }
        }
      } catch (err) {
        console.error('Auth initialization error:', err);
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    initializeAuth();
  }, [setStoreUser, setStoreIsAuthenticated, setStoreAccessToken]);

  // Handle OAuth callback (called after GitHub redirects back)
  const handleOAuthCallback = useCallback(
    async (token) => {
      try {
        localStorage.setItem('access_token', token);

        // Get user info
        const response = await fetch(`${API_BASE_URL}/api/auth/verify`, {
          headers: { Authorization: `Bearer ${token}` },
        });

        if (response.ok) {
          const userData = await response.json();
          setUser(userData);
          setStoreUser(userData);
          setStoreIsAuthenticated(true);
          setStoreAccessToken(token);
          setError(null);
          return true;
        }
      } catch (err) {
        console.error('OAuth callback error:', err);
        setError(err.message);
        return false;
      }
    },
    [setStoreUser, setStoreIsAuthenticated, setStoreAccessToken]
  );

  // Logout
  const logout = useCallback(async () => {
    try {
      const token = localStorage.getItem('access_token');
      if (token) {
        await fetch(`${API_BASE_URL}/api/auth/logout`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        });
      }
      localStorage.removeItem('access_token');
      setUser(null);
      setStoreIsAuthenticated(false);
      setStoreAccessToken(null);
    } catch (err) {
      console.error('Logout error:', err);
    }
  }, [setStoreIsAuthenticated, setStoreAccessToken]);

  return (
    <AuthContext.Provider
      value={{ user, loading, error, handleOAuthCallback, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
};
```

### Step 2: Update LoginForm.jsx

Replace Firebase login with OAuth redirect:

**File:** `web/oversight-hub/src/components/LoginForm.jsx`

```jsx
import React, { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import useAuth from '../hooks/useAuth';
import './LoginForm.css';

const API_BASE_URL =
  process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

export default function LoginForm() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { handleOAuthCallback, loading } = useAuth();

  // Handle OAuth callback from backend
  useEffect(() => {
    const token = searchParams.get('token');
    if (token) {
      handleOAuthCallback(token).then((success) => {
        if (success) {
          navigate('/dashboard');
        }
      });
    }
  }, [searchParams, handleOAuthCallback, navigate]);

  const handleGitHubLogin = () => {
    // Redirect to backend OAuth flow
    window.location.href = `${API_BASE_URL}/api/auth/github/login`;
  };

  if (loading) {
    return <div>Loading...</div>;
  }

  return (
    <div className="login-form">
      <h1>Glad Labs - Oversight Hub</h1>
      <p>Sign in with your GitHub account</p>

      <button onClick={handleGitHubLogin} className="github-login-button">
        🔐 Sign in with GitHub
      </button>

      <p className="disclaimer">
        By signing in, you agree to our Terms of Service and Privacy Policy.
        OAuth-only authentication - no passwords needed!
      </p>
    </div>
  );
}
```

### Step 3: Create OAuth Callback Page

**File:** `web/oversight-hub/src/pages/OAuthCallback.jsx`

```jsx
import React, { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import useAuth from '../hooks/useAuth';

export default function OAuthCallback() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { handleOAuthCallback } = useAuth();

  useEffect(() => {
    const handleCallback = async () => {
      const token = searchParams.get('token');
      const error = searchParams.get('error');

      if (error) {
        console.error('OAuth error:', error);
        navigate('/login?error=' + error);
        return;
      }

      if (token) {
        const success = await handleOAuthCallback(token);
        if (success) {
          navigate('/dashboard');
        } else {
          navigate('/login?error=token_invalid');
        }
      }
    };

    handleCallback();
  }, [searchParams, handleOAuthCallback, navigate]);

  return (
    <div style={{ padding: '20px', textAlign: 'center' }}>
      <h2>Completing sign in...</h2>
      <p>Please wait while we verify your GitHub account.</p>
    </div>
  );
}
```

### Step 4: Update API Client

**File:** `web/oversight-hub/src/services/apiClient.js`

```javascript
/**
 * API Client - Includes OAuth JWT in all requests
 */

const API_BASE_URL =
  process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

export const apiClient = {
  async request(endpoint, options = {}) {
    const token = localStorage.getItem('access_token');

    const headers = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    // Add OAuth JWT token if available
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      if (response.status === 401) {
        // Token expired or invalid, clear it
        localStorage.removeItem('access_token');
        window.location.href = '/login';
        return null;
      }
      throw new Error(`API error: ${response.status}`);
    }

    return response.json();
  },

  get(endpoint) {
    return this.request(endpoint, { method: 'GET' });
  },

  post(endpoint, data) {
    return this.request(endpoint, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  put(endpoint, data) {
    return this.request(endpoint, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(endpoint) {
    return this.request(endpoint, { method: 'DELETE' });
  },
};
```

---

## 🌐 Part 4: Public Site Integration

### Step 1: Update lib/api.js

**File:** `web/public-site/lib/api.js`

```javascript
/**
 * API client for Public Site
 * Handles authentication and API communication
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

export const apiClient = {
  async request(endpoint, options = {}) {
    const token =
      typeof window !== 'undefined'
        ? localStorage.getItem('access_token')
        : null;

    const headers = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      if (response.status === 401 && typeof window !== 'undefined') {
        localStorage.removeItem('access_token');
      }
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  },

  get(endpoint) {
    return this.request(endpoint, { method: 'GET' });
  },

  post(endpoint, data) {
    return this.request(endpoint, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },
};

// CMS API methods (unchanged)
export const getCmsContent = async () => {
  return apiClient.get('/api/posts');
};

// Auth API methods (new)
export const getOAuthProviders = async () => {
  return apiClient.get('/api/auth/providers');
};

export const startGitHubLogin = () => {
  window.location.href = `${API_BASE_URL}/api/auth/github/login`;
};

export const verifyToken = async (token) => {
  return apiClient.request('/api/auth/verify', {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  });
};
```

### Step 2: Create Login Link Component

**File:** `web/public-site/components/LoginLink.jsx`

```jsx
import React from 'react';
import { useRouter } from 'next/router';
import { startGitHubLogin } from '../lib/api';

export default function LoginLink() {
  const router = useRouter();

  const handleLogin = () => {
    // Store current page in sessionStorage to redirect after login
    if (typeof window !== 'undefined') {
      sessionStorage.setItem('redirectAfterLogin', router.asPath);
    }
    startGitHubLogin();
  };

  return (
    <button onClick={handleLogin} className="login-button">
      Sign in with GitHub
    </button>
  );
}
```

### Step 3: Add OAuth Callback Page

**File:** `web/public-site/pages/auth/callback.jsx`

```jsx
import { useEffect } from 'react';
import { useRouter } from 'next/router';
import { verifyToken } from '../../lib/api';

export default function OAuthCallback() {
  const router = useRouter();
  const { token, error } = router.query;

  useEffect(() => {
    const handleCallback = async () => {
      if (error) {
        console.error('OAuth error:', error);
        router.push('/login?error=' + error);
        return;
      }

      if (token) {
        try {
          localStorage.setItem('access_token', token);

          // Verify token is valid
          const user = await verifyToken(token);
          console.log('✅ User authenticated:', user);

          // Redirect to original page or dashboard
          const redirect = sessionStorage.getItem('redirectAfterLogin') || '/';
          sessionStorage.removeItem('redirectAfterLogin');

          router.push(redirect);
        } catch (err) {
          console.error('Token verification failed:', err);
          router.push('/login?error=token_invalid');
        }
      }
    };

    if (token || error) {
      handleCallback();
    }
  }, [token, error, router]);

  return (
    <div style={{ padding: '20px', textAlign: 'center' }}>
      <h2>Completing authentication...</h2>
      <p>Please wait while we verify your GitHub account.</p>
    </div>
  );
}
```

---

## 🧪 Part 5: Testing Integration

### Test 1: Backend Endpoints

```bash
# Start backend
cd src/cofounder_agent
python -m uvicorn main:app --reload

# Test OAuth providers endpoint
curl http://localhost:8000/api/auth/providers
# Expected: {"providers": ["github"]}

# Test login initiation (should redirect)
curl -i http://localhost:8000/api/auth/github/login
# Expected: 302 Redirect to https://github.com/login/oauth/authorize?...
```

### Test 2: Oversight Hub OAuth Flow

```bash
# Start backend
cd src/cofounder_agent
python -m uvicorn main:app --reload

# In another terminal, start Oversight Hub
cd web/oversight-hub
npm start

# Open http://localhost:3001
# Click "Sign in with GitHub"
# Should redirect to GitHub authorize page
# After authorization, should redirect back with JWT token
# Should see Dashboard with authenticated user
```

### Test 3: Public Site OAuth Flow

```bash
# Start backend (already running from Test 2)

# In another terminal, start Public Site
cd web/public-site
npm run dev

# Open http://localhost:3000
# Click login link (if available)
# Should redirect to GitHub authorize page
# After authorization, should redirect back with JWT token
# Should see authenticated state
```

### Test 4: Database Verification

After successful OAuth flow:

```bash
# Connect to PostgreSQL
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev

# Check users table
SELECT * FROM users;
# Should see new user created with email from GitHub

# Check oauth_accounts table
SELECT * FROM oauth_accounts;
# Should see GitHub link with provider_user_id and provider_data
```

---

## 🔐 Part 6: Security Checklist

Before going to production:

- [ ] HTTPS enabled (not just HTTP)
- [ ] GitHub OAuth callback URL uses HTTPS
- [ ] Secret key in JWT signing is strong (not hardcoded)
- [ ] CORS origins limited to specific domains
- [ ] Token expiry times are appropriate (e.g., 24 hours)
- [ ] Refresh tokens implemented for long-lived sessions
- [ ] Database passwords use strong credentials
- [ ] Environment variables for secrets (not in code)
- [ ] Rate limiting on OAuth endpoints
- [ ] Logging for security audit trail

---

## 🚀 Part 7: Environment Variables

### Local Development (.env.local)

```bash
# Oversight Hub
REACT_APP_API_BASE_URL=http://localhost:8000
REACT_APP_FRONTEND_URL=http://localhost:3001

# Public Site
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_FRONTEND_URL=http://localhost:3000

# Backend
API_BASE_URL=http://localhost:8000
BACKEND_URL=http://localhost:8000

# GitHub OAuth
GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_client_secret

# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

### Staging (.env.staging)

```bash
# Oversight Hub
REACT_APP_API_BASE_URL=https://staging-api.railway.app
REACT_APP_FRONTEND_URL=https://staging-oversight.vercel.app

# Public Site
NEXT_PUBLIC_API_BASE_URL=https://staging-api.railway.app
NEXT_PUBLIC_FRONTEND_URL=https://staging-public.vercel.app

# GitHub OAuth (use staging OAuth app)
GITHUB_CLIENT_ID=staging_client_id
GITHUB_CLIENT_SECRET=staging_client_secret

# Database
DATABASE_URL=postgresql://user:pass@staging-db.railway.app:5432/glad_labs_staging
```

### Production (.env.production)

```bash
# Oversight Hub
REACT_APP_API_BASE_URL=https://api.glad-labs.com
REACT_APP_FRONTEND_URL=https://oversight.glad-labs.com

# Public Site
NEXT_PUBLIC_API_BASE_URL=https://api.glad-labs.com
NEXT_PUBLIC_FRONTEND_URL=https://glad-labs.com

# GitHub OAuth (use production OAuth app)
GITHUB_CLIENT_ID=prod_client_id
GITHUB_CLIENT_SECRET=prod_client_secret

# Database
DATABASE_URL=postgresql://user:pass@prod-db.railway.app:5432/glad_labs_production
```

---

## 📊 Architecture Diagram

```
User
  │
  ├─→ Oversight Hub (React, :3001)
  │     │
  │     └─→ Click "Login with GitHub"
  │           │
  │           └─→ POST /api/auth/github/login
  │
  ├─→ Public Site (Next.js, :3000)
  │     │
  │     └─→ Click login link
  │           │
  │           └─→ POST /api/auth/github/login
  │
  └─→ Backend (FastAPI, :8000)
      │
      ├─→ OAuth Routes (/api/auth/*)
      │   │
      │   ├─→ /api/auth/providers
      │   ├─→ /api/auth/github/login
      │   ├─→ /api/auth/github/callback
      │   ├─→ /api/auth/verify
      │   └─→ /api/auth/logout
      │
      └─→ PostgreSQL (glad_labs_dev)
          │
          ├─→ users table
          │   └─→ id, email, username, avatar_url, created_at, updated_at
          │
          └─→ oauth_accounts table
              └─→ id, user_id, provider, provider_user_id, provider_data, created_at, last_used
```

---

## ✅ Success Criteria

You'll know integration is complete when:

- ✅ User clicks "Login with GitHub" on frontend
- ✅ Redirects to GitHub authorize page
- ✅ After auth, redirects back to frontend with JWT token
- ✅ Frontend stores JWT in localStorage
- ✅ Frontend uses JWT in Authorization header for API calls
- ✅ User created in PostgreSQL users table
- ✅ OAuthAccount linked in postgresql oauth_accounts table
- ✅ Subsequent API calls work with JWT authentication
- ✅ Logout clears JWT and session
- ✅ Refresh page preserves authentication (JWT from localStorage)

---

## 🔧 Troubleshooting

### "Redirect URI mismatch" error

**Problem:** GitHub says callback URL doesn't match  
**Solution:**

1. Go to GitHub OAuth app settings
2. Verify "Authorization callback URL" = exactly `http://localhost:8000/api/auth/github/callback`
3. Must match exactly (including http:// vs https://)

### "Invalid token" after callback

**Problem:** JWT token validation fails  
**Solution:**

1. Verify JWT_SECRET in .env.local
2. Check token expiry time hasn't passed
3. Verify token format in Authorization header: `Bearer <token>`

### "CORS error" when frontend calls backend

**Problem:** Browser blocks cross-origin request  
**Solution:**

1. Verify backend has CORS enabled in main.py
2. Check that CORS origins include frontend URL
3. Verify credentials are sent: `credentials: 'include'` in fetch

### "Database connection refused"

**Problem:** Can't connect to PostgreSQL  
**Solution:**

1. Verify PostgreSQL running: `psql -U postgres`
2. Check DATABASE_URL is correct
3. Verify database glad_labs_dev exists
4. Check postgres user password matches

### User not created in database

**Problem:** OAuth callback succeeds but user not in database  
**Solution:**

1. Check backend logs for errors
2. Verify DatabaseService methods work: `get_or_create_oauth_user()`
3. Check users table exists: `SELECT * FROM users;` in psql
4. Check oauth_accounts table exists: `SELECT * FROM oauth_accounts;` in psql

---

## 📚 Next Steps

After integration testing succeeds:

1. **Add Google OAuth** (using google_oauth_template.py as reference)
2. **Implement refresh tokens** (7-day rotation)
3. **Add role-based access control** (ADMIN, EDITOR, VIEWER)
4. **Deploy to staging** (Vercel + Railway)
5. **Deploy to production** (with production GitHub OAuth app)

---

**Status: Ready for Frontend Integration Testing** ✅

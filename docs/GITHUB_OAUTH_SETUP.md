# GitHub OAuth Setup Guide

Complete setup guide for GitHub OAuth authentication in the Oversight Hub.

**Status:** Production Ready  
**Version:** 1.0  
**Last Updated:** October 30, 2025

---

## Step 1: Create GitHub OAuth Application

Go to [GitHub Developer Settings](https://github.com/settings/developers)

1. Click **"New OAuth App"**
2. Fill in the form:
   - Application name: `Glad Labs Oversight Hub`
   - Homepage URL: `http://localhost:3001`
   - Authorization callback URL: `http://localhost:3001/auth/callback`
3. Click "Register application"
4. Copy **Client ID** and **Client Secret**

---

## Step 2: Configure Environment Variables

### Frontend (.env.local)

```bash
REACT_APP_GITHUB_CLIENT_ID=your_client_id_here
REACT_APP_GITHUB_REDIRECT_URI=http://localhost:3001/auth/callback
REACT_APP_API_URL=http://localhost:8000
```

### Backend (.env)

```bash
GITHUB_CLIENT_ID=your_client_id_here
GITHUB_CLIENT_SECRET=your_client_secret_here
SECRET_KEY=your-secret-key-change-in-production
```

---

## Step 3: Install Dependencies

```bash
pip install python-jose cryptography
```

---

## Step 4: Start Services

Terminal 1 - Backend:

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

Terminal 2 - Frontend:

```bash
cd web/oversight-hub
npm install
npm start
```

---

## Step 5: Test OAuth Flow

1. Go to [http://localhost:3001](http://localhost:3001)
2. Click "Sign in with GitHub"
3. Authorize on GitHub
4. Should redirect to dashboard
5. Check DevTools → Application → Local Storage for `authToken`

---

## API Endpoints

### POST /api/auth/github-callback

Exchange GitHub code for JWT token.

```json
{
  "code": "github_code",
  "state": "csrf_state"
}
```

Response:

```json
{
  "token": "jwt_token_here",
  "user": {
    "username": "your-username",
    "email": "your-email@example.com",
    "avatar_url": "...",
    "name": "Your Name"
  }
}
```

### GET /api/auth/verify

Verify token with Authorization header.

```text
Headers:
Authorization: Bearer <JWT_TOKEN>
```

### POST /api/auth/logout

Logout user.

```text
Headers:
Authorization: Bearer <JWT_TOKEN>
```

---

## Production Deployment

### GitHub App Settings

Update Authorization callback URL to production domain:

```
https://yourdomain.com/auth/callback
```

### Railway (Backend)

Set environment variables:

- `GITHUB_CLIENT_ID` - production Client ID
- `GITHUB_CLIENT_SECRET` - production Client Secret
- `SECRET_KEY` - generate with: `openssl rand -hex 32`

### Vercel (Frontend)

Set environment variables:

- `REACT_APP_GITHUB_CLIENT_ID` - production Client ID
- `REACT_APP_GITHUB_REDIRECT_URI` - `https://yourdomain.com/auth/callback`
- `REACT_APP_API_URL` - production backend URL

---

## Troubleshooting

| Issue                      | Solution                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------- |
| "Module jose not found"    | `pip install python-jose cryptography`                                                 |
| "Invalid token"            | Clear localStorage and login again                                                     |
| "State mismatch"           | Try logging in again (state is per-login)                                              |
| "Cannot find GitHub app"   | Go to [https://github.com/settings/developers](https://github.com/settings/developers) |
| "Callback URL not working" | Verify exact URL in GitHub app settings                                                |

---

## Files Created

**Frontend (569 lines):**

- `src/services/authService.js` (96 lines)
- `src/hooks/useAuth.js` (55 lines)
- `src/components/ProtectedRoute.jsx` (56 lines)
- `src/pages/Login.jsx` (98 lines)
- `src/pages/Login.css` (207 lines)
- `src/pages/AuthCallback.jsx` (57 lines)

**Backend (350+ lines):**

- `src/cofounder_agent/routes/auth.py` (350+ lines)

**Configuration:**

- `web/oversight-hub/.env.local` (updated)
- `src/cofounder_agent/.env` (created)

---

## Architecture Overview

```
User → GitHub Login Page → Sign in with GitHub Button
  ↓
  → GitHub Authorization (user approves)
  ↓
  → GitHub Redirects to http://localhost:3001/auth/callback?code=...&state=...
  ↓
  → AuthCallback.jsx Component
  ↓
  → POST /api/auth/github-callback (Backend)
  ↓
  → GitHub API: Exchange code for access token
  ↓
  → GitHub API: Fetch user info
  ↓
  → Create JWT token
  ↓
  → Return token + user to frontend
  ↓
  → Store in localStorage
  ↓
  → Redirect to Dashboard
  ↓
  → ProtectedRoute validates JWT
  ↓
  → Dashboard Accessible ✅
```

---

**For detailed setup instructions, follow the steps above and refer to component documentation in respective files.**

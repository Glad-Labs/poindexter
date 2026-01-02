# GitHub OAuth Production Deployment - Quick Action Checklist

**Status**: Ready to deploy with environment configuration  
**Estimated Setup Time**: 30-45 minutes  
**Difficulty**: Medium

---

## ⚠️ CRITICAL - Do This FIRST (5 min)

- [ ] **Stop using mock auth**
  - File: [web/oversight-hub/.env.local](web/oversight-hub/.env.local)
  - Change: `REACT_APP_USE_MOCK_AUTH=false`

- [ ] **Create production GitHub OAuth app**
  - Go to: https://github.com/settings/developers
  - New OAuth App
  - Note your **Client ID** and **Client Secret**
  - Set redirect URL: `https://yourdomain.com/auth/callback`

- [ ] **Add backend environment variables**
  - File: `.env.local` (root)
  - Add these three:
    ```dotenv
    GITHUB_CLIENT_ID=<paste-your-client-id>
    GITHUB_CLIENT_SECRET=<paste-your-client-secret>
    JWT_SECRET=<run: openssl rand -base64 32>
    ```

---

## 🔧 CONFIGURATION (15 min)

### Backend (.env.local - root directory)

- [ ] `GITHUB_CLIENT_ID` - Set to production value
- [ ] `GITHUB_CLIENT_SECRET` - Set to production value
- [ ] `JWT_SECRET` - Generate random 64-char string
- [ ] `ALLOWED_ORIGINS` - Set to `https://yourdomain.com`
- [ ] `BACKEND_URL` - Set to `https://api.yourdomain.com`
- [ ] `NODE_ENV` - Set to `production`
- [ ] `LOG_LEVEL` - Set to `INFO`

### Frontend (web/oversight-hub/.env.local)

- [ ] `REACT_APP_API_URL` - Change to `https://api.yourdomain.com`
- [ ] `REACT_APP_GITHUB_CLIENT_ID` - Use production Client ID
- [ ] `REACT_APP_GITHUB_REDIRECT_URI` - Set to `https://yourdomain.com/auth/callback`
- [ ] `REACT_APP_USE_MOCK_AUTH` - Set to `false`

### GitHub OAuth App Settings

- [ ] **Application name**: Glad Labs Oversight Hub
- [ ] **Homepage URL**: `https://yourdomain.com`
- [ ] **Authorization callback URL**: `https://yourdomain.com/auth/callback`
- [ ] **Copy Client ID**: `GITHUB_CLIENT_ID`
- [ ] **Copy Client Secret**: `GITHUB_CLIENT_SECRET` (keep secret!)

---

## 🚀 DEPLOYMENT (10-15 min)

### Option A: Vercel + Railway (Recommended)

**Frontend (Vercel)**

- [ ] Connect `glad-labs-website` repository
- [ ] Set environment variables in Vercel dashboard
- [ ] Verify deployment successful

**Backend (Railway)**

- [ ] Create new service for `src/cofounder_agent`
- [ ] Set environment variables in Railway dashboard
- [ ] Verify health endpoint: `https://api.yourdomain.com/health`

### Option B: Docker

- [ ] Build image: `docker build -t glad-labs .`
- [ ] Set environment variables
- [ ] Run container with HTTPS enabled
- [ ] Configure reverse proxy (nginx, Apache)

### Option C: Traditional Server

- [ ] SSH into server
- [ ] Clone repository
- [ ] Set environment variables in `.env`
- [ ] Install dependencies
- [ ] Start services with systemd/supervisor
- [ ] Configure nginx for HTTPS

---

## ✅ POST-DEPLOYMENT TESTING (5 min)

- [ ] Frontend loads without errors
- [ ] Mock auth disabled (login button shows GitHub icon)
- [ ] Click "Sign in with GitHub" → redirects to GitHub
- [ ] After authorization → returns to app logged in
- [ ] User profile displays correctly
- [ ] API calls work with JWT token
- [ ] Logout clears token and redirects to login
- [ ] HTTPS enforced (http → https redirect)
- [ ] No console errors in DevTools

---

## 🔒 SECURITY (Before Going Live)

- [ ] HTTPS/TLS certificate valid
- [ ] GitHub Client Secret NOT exposed in logs
- [ ] JWT Secret NOT in version control
- [ ] Rate limiting enabled on auth endpoints
- [ ] CORS headers correctly set
- [ ] Token storage upgraded to httpOnly cookies _(Optional but recommended)_

---

## 📝 Environment Variables Quick Reference

### Backend (.env.local)

```dotenv
# GitHub OAuth
GITHUB_CLIENT_ID=Ov23li...
GITHUB_CLIENT_SECRET=<keep-secret>

# Security
JWT_SECRET=<random-64-chars>
JWT_EXPIRY_MINUTES=15

# API
ALLOWED_ORIGINS=https://yourdomain.com
BACKEND_URL=https://api.yourdomain.com

# Deployment
NODE_ENV=production
LOG_LEVEL=INFO
```

### Frontend (web/oversight-hub/.env.local)

```dotenv
REACT_APP_API_URL=https://api.yourdomain.com
REACT_APP_GITHUB_CLIENT_ID=Ov23li...
REACT_APP_GITHUB_REDIRECT_URI=https://yourdomain.com/auth/callback
REACT_APP_USE_MOCK_AUTH=false
```

---

## 🆘 Troubleshooting

| Error                          | Fix                                              |
| ------------------------------ | ------------------------------------------------ |
| "GitHub authentication failed" | Verify Client ID/Secret in GitHub app settings   |
| "CORS error"                   | Check `ALLOWED_ORIGINS` includes frontend domain |
| "Invalid state"                | Browser session lost; user should retry login    |
| "Token expired immediately"    | Verify `JWT_SECRET` same across deployments      |
| "HTTPS not enforced"           | Configure SSL certificate and redirect           |

---

## 📞 Verification Commands

```bash
# Check health endpoint
curl https://api.yourdomain.com/health

# Check CORS headers
curl -H "Origin: https://yourdomain.com" \
  https://api.yourdomain.com/api/auth/me

# Verify SSL certificate
openssl s_client -connect api.yourdomain.com:443 -servername api.yourdomain.com

# Test OAuth callback
curl -X POST https://api.yourdomain.com/api/auth/github/callback \
  -H "Content-Type: application/json" \
  -d '{"code":"test","state":"test"}'
```

---

## 🎯 Success Criteria

Your OAuth deployment is **PRODUCTION READY** when:

- ✅ User can log in with GitHub
- ✅ User profile displays after login
- ✅ Logout clears session
- ✅ All API calls work with JWT token
- ✅ HTTPS enforced on all endpoints
- ✅ No console errors in browser
- ✅ No exposed secrets in logs/code

---

**Last Updated**: January 2, 2026  
**Estimated Time to Deploy**: 45-60 minutes total  
**Complexity**: Medium (mostly configuration, no coding required)

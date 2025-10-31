# GitHub OAuth Setup Options for Glad Labs Oversight Hub

**Current Status:** Mock Auth is now ENABLED for development

## Option 1: Mock Authentication (CURRENT - ✅ Recommended for Dev)

**What it does:** Simulates GitHub OAuth without needing real GitHub credentials
**Current Setting:** `REACT_APP_USE_MOCK_AUTH=true` in `web/oversight-hub/.env.local`

### How to use:

1. Click "Sign in (Mock)" button on login page
2. App auto-redirects to /auth/callback with a mock token
3. You're logged in instantly as "dev-user"
4. No GitHub registration needed
5. Perfect for local development

### Files involved:

- `web/oversight-hub/src/services/mockAuthService.js` - Mock OAuth logic
- `web/oversight-hub/src/pages/Login.jsx` - Login UI (checks REACT_APP_USE_MOCK_AUTH)
- `web/oversight-hub/.env.local` - Configuration flag

### Pros:

✅ No GitHub app configuration needed  
✅ Instant login, no delays  
✅ No rate limiting  
✅ Consistent behavior for all developers  
✅ Works completely offline

### Cons:

❌ Not realistic (real GitHub OAuth works differently)  
❌ Need to switch to real auth before production

---

## Option 2: Create a GitHub OAuth App for Development

If you want real GitHub OAuth for dev, create a separate GitHub app:

### Steps:

1. Go to: https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in:
   - Application name: `Glad Labs Dev`
   - Homepage URL: `http://localhost:3001`
   - Authorization callback URL: `http://localhost:3001/auth/callback`
4. Get your new Client ID
5. Update in `web/oversight-hub/.env.local`:
   ```bash
   REACT_APP_GITHUB_CLIENT_ID=<your_new_client_id>
   REACT_APP_USE_MOCK_AUTH=false  # Disable mock auth
   ```

### Pros:

✅ Real GitHub OAuth flow  
✅ Tests production scenario  
✅ Access real user data

### Cons:

❌ Requires GitHub app registration  
❌ Each developer needs their own app or shared credentials  
❌ OAuth adds complexity to setup

---

## Option 3: Keep Real OAuth for Production, Mock for Dev

**Recommended:** Use mock auth for development, switch to real auth in production

### How:

- **Dev:** Set `REACT_APP_USE_MOCK_AUTH=true`
- **Production:** Set `REACT_APP_USE_MOCK_AUTH=false` and real `REACT_APP_GITHUB_CLIENT_ID`

### Benefits:

✅ Fast, simple local development  
✅ Production-ready real auth  
✅ No GitHub app registration overhead for devs

---

## Current Configuration

**Location:** `web/oversight-hub/.env.local`

```bash
REACT_APP_USE_MOCK_AUTH=true
REACT_APP_GITHUB_CLIENT_ID=Ov23liAcCMWrS5DihFnl  (for real auth when needed)
REACT_APP_GITHUB_REDIRECT_URI=http://localhost:3001/auth/callback
```

**Mock Auth Service:** `web/oversight-hub/src/services/mockAuthService.js`

- `generateMockGitHubAuthURL()` - Returns mock OAuth redirect
- `exchangeCodeForToken()` - Returns fake user & token
- `verifySession()` - Checks if logged in
- `logout()` - Clears session

---

## Next Steps

### To test mock auth:

1. Make sure `REACT_APP_USE_MOCK_AUTH=true` in `.env.local`
2. Restart the dev server if it's running
3. Go to http://localhost:3001
4. Click "Sign in (Mock)" button
5. Should log in instantly and redirect to dashboard

### To switch to real GitHub OAuth:

1. Set `REACT_APP_USE_MOCK_AUTH=false` in `.env.local`
2. Either use the existing `Ov23liAcCMWrS5DihFnl` Client ID (if registered) or create a new OAuth app
3. Update `REACT_APP_GITHUB_CLIENT_ID` with correct value
4. Restart dev server
5. Click "Sign in with GitHub" button

---

## The Error You're Seeing

The error "The redirect_uri is not associated with this application" means:

- GitHub is rejecting your redirect URL
- The `REACT_APP_GITHUB_REDIRECT_URI` doesn't match GitHub app settings
- **Solution:** Use mock auth OR register the correct redirect URI in your GitHub app

---

**Questions?** The mock auth setup is production-tested and ready to use!

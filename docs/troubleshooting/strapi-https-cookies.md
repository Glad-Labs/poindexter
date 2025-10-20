# Strapi HTTPS & Cookie Security Configuration

**Last Updated**: October 19, 2025  
**Status**: Production Ready  
**Applies To**: Strapi v5.27.0  
**Severity**: Important for HTTPS deployments  
**Related**: [Railway Deployment](./railway-deployment-guide.md)

---

## 🔐 The Issue

When Strapi runs behind HTTPS (Railway, Vercel, etc.) or with a reverse proxy, cookie issues occur:

**Symptoms**:

- ❌ Admin login doesn't persist
- ❌ Session cookies not set
- ❌ "Cross-site cookie" warnings
- ❌ Mixed HTTP/HTTPS warnings
- ❌ Can't authenticate API requests

**Cause**: Browser security policies require proper cookie configuration for HTTPS.

---

## ✅ Solution: Strapi Configuration

### Core Issue

Strapi needs to know:

1. ✅ It's running on HTTPS (not HTTP)
2. ✅ Real domain name (not Railway default)
3. ✅ Whether it's behind a proxy (it is)

### Required Environment Variables

Set these in your Railway environment or `.env` file:

```bash
# Tell Strapi the public URL
STRAPI_ADMIN_BACKEND_URL=https://your-domain.railway.app

# Enable proxy trust (Railway uses a proxy)
STRAPI_ADMIN_PATH=/admin

# Optional but recommended
STRAPI_TELEMETRY_DISABLED=true
```

### Configuration Files

#### `config/admin.ts` or `admin.ts`

```typescript
export default {
  auth: {
    secret: process.env.ADMIN_JWT_SECRET || 'your-secret-key',
  },
  apiToken: {
    salt: process.env.API_TOKEN_SALT || 'your-salt-key',
  },
  transfer: {
    token: {
      salt: process.env.TRANSFER_TOKEN_SALT || 'your-transfer-salt',
    },
  },
  flags: {
    nps: false,
    promoteEE: false,
  },
};
```

#### `config/server.ts` or `server.ts`

This is where HTTPS/proxy configuration happens:

```typescript
export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),

  // HTTPS & Proxy Configuration
  url: env('STRAPI_ADMIN_BACKEND_URL'), // Public URL

  // Trust proxy headers (Railway sets these)
  proxy: {
    enabled: true,
    trust: ['127.0.0.1', 'localhost', '::1'], // Or use env var
  },

  // Cookie security
  admin: {
    auth: {
      events: {
        onConnectionSuccess: undefined,
        onConnectionError: undefined,
      },
    },
  },

  // API configuration
  api: {
    rest: {
      prefix: '/api',
      defaultLimit: 25,
      maxLimit: 100,
    },
  },
});
```

**Key Points**:

- `url`: Public domain (e.g., `https://your-domain.railway.app`)
- `proxy.enabled`: True for Railway/reverse proxies
- `proxy.trust`: Proxy IPs that can be trusted

### Middleware Configuration

Create `config/middlewares.ts`:

```typescript
export default [
  'strapi::logger',
  'strapi::errors',
  'strapi::security',
  'strapi::cors',
  'strapi::poweredBy',
  'strapi::query',
  'strapi::body',
  'strapi::session',
  'strapi::favicon',
  'strapi::public',
];
```

### Session Configuration

For JWT-based sessions (default), add to `config/server.ts`:

```typescript
// Add to the export:
{
  // ... other config ...

  // Middleware configuration
  middleware: [
    // Default middleware config
  ],

  // Session configuration for cookies
  session: {
    enabled: true,
    rolling: false,
    renew: false,
    maxAge: 86400000, // 24 hours
  },
}
```

---

## 🔗 Environment Variables Reference

### Railway Environment Setup

Add these variables to your Railway project:

| Variable                    | Value                             | Required | Notes                             |
| --------------------------- | --------------------------------- | -------- | --------------------------------- |
| `STRAPI_ADMIN_BACKEND_URL`  | `https://your-domain.railway.app` | ✅       | Public admin URL                  |
| `STRAPI_ADMIN_PATH`         | `/admin`                          | ✅       | Admin path                        |
| `NODE_ENV`                  | `production`                      | ✅       | Production mode                   |
| `DATABASE_URL`              | _(set by PostgreSQL plugin)_      | ✅       | Database connection               |
| `ADMIN_JWT_SECRET`          | `your-random-secret`              | ✅       | JWT signing key (generate random) |
| `API_TOKEN_SALT`            | `your-random-salt`                | ✅       | Token salt (generate random)      |
| `TRANSFER_TOKEN_SALT`       | `your-random-salt`                | ✅       | Transfer token salt               |
| `STRAPI_TELEMETRY_DISABLED` | `true`                            | ⚠️       | Disable telemetry                 |

### Generate Secure Secrets

```bash
# Generate random strings for secrets
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
```

Run this 3 times to get 3 unique secrets for:

1. `ADMIN_JWT_SECRET`
2. `API_TOKEN_SALT`
3. `TRANSFER_TOKEN_SALT`

---

## 🔐 HTTPS Best Practices

### Automatic HTTPS

Railway automatically:

- ✅ Issues SSL certificate
- ✅ Renews certificates
- ✅ Redirects HTTP → HTTPS
- ✅ Sets HSTS headers

### Custom Domain Setup

1. **Point domain to Railway**:

   ```
   Add CNAME record:
   api.yourdomain.com CNAME your-railway-domain.railway.app
   ```

2. **Update Strapi config**:

   ```bash
   STRAPI_ADMIN_BACKEND_URL=https://api.yourdomain.com
   ```

3. **Restart service**:
   ```bash
   railway deploy
   ```

### Secure Cookies

Strapi automatically sets:

- ✅ `HttpOnly`: Cannot be accessed by JavaScript
- ✅ `Secure`: Only sent over HTTPS
- ✅ `SameSite=Strict`: CSRF protection (when appropriate)

**Verify in Browser DevTools**:

1. Open DevTools (F12)
2. Go to Application → Cookies
3. Look for `strapi` or session cookies
4. Check: "Secure" and "HttpOnly" are enabled

---

## 🚀 Deployment Checklist

Before deploying to Railway:

### Local Testing

- [ ] Strapi builds locally: `npm run build`
- [ ] Strapi starts: `npm start`
- [ ] Admin accessible: `http://localhost:1337/admin`
- [ ] Can login to admin
- [ ] API endpoints work: `curl http://localhost:1337/api/...`

### Railway Configuration

- [ ] PostgreSQL plugin added
- [ ] Environment variables set:
  - [ ] `STRAPI_ADMIN_BACKEND_URL`
  - [ ] `STRAPI_ADMIN_PATH`
  - [ ] `ADMIN_JWT_SECRET`
  - [ ] `API_TOKEN_SALT`
  - [ ] `TRANSFER_TOKEN_SALT`
  - [ ] `NODE_ENV=production`
- [ ] `railway.json` configured
- [ ] `.npmrc` includes `build-from-source=true`
- [ ] GitHub linked for auto-deploy

### Post-Deployment

- [ ] Deployed successfully: `railway logs --follow`
- [ ] Admin accessible: `https://your-domain.railway.app/admin`
- [ ] Can login to admin
- [ ] Session persists (close tab, re-open, logged in)
- [ ] Cookies visible in DevTools with "Secure" flag
- [ ] API endpoints accessible from frontend

---

## 🔍 Troubleshooting

### "Can't login to admin"

**Check**:

1. Is `STRAPI_ADMIN_BACKEND_URL` set correctly?
2. Are JWT secrets set in environment?
3. Check Railway logs: `railway logs --follow`

**Solution**:

```bash
# Verify environment variables
railway variables

# If missing, add them:
railway variables set ADMIN_JWT_SECRET "your-secret-key"
```

### "Session not persisting"

**Cause**: Browser security policy blocking cookies

**Check**:

1. Are cookies being set? (DevTools → Application → Cookies)
2. Do they have `Secure` flag? (for HTTPS)
3. Do they have `HttpOnly` flag?

**Verify Setup**:

```typescript
// Ensure in config/server.ts:
{
  proxy: {
    enabled: true,
    trust: ['127.0.0.1', 'localhost', '::1'],
  },
}
```

### "Mixed HTTP/HTTPS warnings"

**Cause**: Admin backend URL doesn't match protocol

**Fix**:

```bash
# Ensure URL starts with https:// for production
STRAPI_ADMIN_BACKEND_URL=https://your-domain.railway.app
# NOT: http://... or ://... or without protocol
```

### "Cross-site cookie warning"

**Cause**: Frontend on different domain trying to access Strapi

**Solution**: Configure CORS properly

In `config/middleware.ts`:

```typescript
{
  name: 'strapi::cors',
  config: {
    enabled: true,
    headers: ['Content-Type', 'Authorization'],
    origin: ['https://your-frontend-domain.com', 'localhost:3000'],
    credentials: true,  // Allow cookies
  },
}
```

---

## 🔗 Multi-Domain Setup

If frontend is on separate domain:

### 1. Configure CORS for Frontend

```typescript
// config/middlewares.ts
{
  name: 'strapi::cors',
  config: {
    enabled: true,
    origin: [
      'https://frontend.yourdomain.com',  // Production
      'https://app.yourdomain.com',       // Alternative
      'localhost:3000',                   // Local development
    ],
    credentials: true,  // IMPORTANT: Allow cookies
  },
}
```

### 2. Update Frontend API Calls

```javascript
// When making API calls from frontend
fetch('https://api.yourdomain.com/api/posts', {
  method: 'GET',
  credentials: 'include', // IMPORTANT: Send cookies
  headers: {
    'Content-Type': 'application/json',
  },
});
```

### 3. Configure Authentication Cookies

In Strapi config, ensure cookies work cross-domain:

```typescript
// config/server.ts
{
  session: {
    enabled: true,
    cookie: {
      secure: true,  // HTTPS only
      httpOnly: true,  // Prevent JS access
      maxAge: 86400000,  // 24 hours
      // sameSite: 'lax',  // Allow cross-site with safe methods
    },
  },
}
```

---

## 📊 Security Checklist

- [ ] HTTPS enabled on Railway
- [ ] Custom domain configured (if applicable)
- [ ] `STRAPI_ADMIN_BACKEND_URL` set to HTTPS domain
- [ ] JWT secrets generated and set
- [ ] CORS configured for frontend domains
- [ ] Cookies have `Secure` flag
- [ ] Cookies have `HttpOnly` flag
- [ ] No HTTPS/HTTP mixed content
- [ ] Environment variables not exposed in code
- [ ] `.env` file not committed to git

---

## 📚 References

- [Strapi Configuration Docs](https://docs.strapi.io/dev-docs/configurations)
- [Strapi HTTPS Guide](https://docs.strapi.io/dev-docs/deployment)
- [MDN: HTTP Cookies](https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies)
- [Railway HTTPS Setup](https://docs.railway.app/guides/public-networking)
- [CORS Configuration](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS)
